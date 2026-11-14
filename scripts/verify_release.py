"""Post-release and historical release verifier for AGENT_UPDATE_15 Task 1 (2.1 step 11 & 2.4).

Usage:
    py scripts/verify_release.py --tag v1.0.14
    py scripts/verify_release.py --tag v1.0.15 --write RELEASE_EVIDENCE.txt
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from dubtool import proc, workspace  # noqa: E402


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def find_gh_bin() -> str:
    for sub in ("tools", "vendor"):
        cand = workspace.build_root() / sub / "gh" / "bin" / "gh.exe"
        if cand.is_file():
            return str(cand)
    which_gh = shutil.which("gh")
    if which_gh:
        return which_gh
    raise RuntimeError("gh CLI not found in build_root/tools/gh/bin/gh.exe or PATH")


def gh_env() -> dict[str, str]:
    """Return environment dict with GH_TOKEN populated in memory from git credential fill if needed."""
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if not env.get("GH_TOKEN") and not env.get("GITHUB_TOKEN"):
        cp = proc.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            cwd=str(ROOT),
        )
        for line in (cp.stdout or "").splitlines():
            if line.startswith("password="):
                tok = line.split("=", 1)[1].strip()
                if tok:
                    env["GH_TOKEN"] = tok
                break
    return env


def _git(*args: str) -> str:
    done = proc.run(["git", *args], cwd=str(ROOT))
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr}")
    return done.stdout.strip()


def _extract_build_info_from_zip(zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if name.replace("\\", "/").endswith("BUILD_INFO.txt"):
                return zf.read(name).decode("utf-8", errors="replace").replace("\ufeff", "").strip()
    return ""


def verify_tag(tag: str, strict_head: bool = False, write_path: Path | None = None) -> int:
    gh = find_gh_bin()
    env = gh_env()
    head = _git("rev-parse", "HEAD")
    tag_commit = _git("rev-list", "-n", "1", tag)

    # Query release metadata via gh
    view = proc.run(
        [gh, "release", "view", tag, "--json", "url,tagName,isPrerelease,assets,body"],
        cwd=str(ROOT),
        env=env,
    )
    if view.returncode != 0:
        print(f"ERROR: gh release view {tag} failed: {view.stderr}", file=sys.stderr)
        return 1
    rel_data = json.loads(view.stdout)
    rel_url = rel_data.get("url", "")
    assets = rel_data.get("assets") or []
    zip_assets = [a for a in assets if str(a.get("name", "")).endswith(".zip")]
    if not zip_assets:
        print(f"ERROR: release {tag} has no .zip asset", file=sys.stderr)
        return 1
    asset_name = zip_assets[0]["name"]

    # Download asset to temp dir
    dl_dir = workspace.build_root() / "tmp" / f"verify-{tag}"
    if dl_dir.exists():
        shutil.rmtree(dl_dir, ignore_errors=True)
    dl_dir.mkdir(parents=True, exist_ok=True)
    dl = proc.run(
        [gh, "release", "download", tag, "-p", asset_name, "-D", str(dl_dir), "--clobber"],
        cwd=str(ROOT),
        env=env,
    )
    if dl.returncode != 0:
        print(f"ERROR: gh release download {tag} failed: {dl.stderr}", file=sys.stderr)
        return 1
    dl_zip = dl_dir / asset_name
    dl_sha = _sha256(dl_zip)
    build_info_txt = _extract_build_info_from_zip(dl_zip)
    m_git = re.search(r"^git:\s*(\S+)", build_info_txt, flags=re.MULTILINE)
    build_info_git = m_git.group(1) if m_git else "unknown"

    lines = [
        f"=== Release Verification: {tag} ===",
        f"Release URL:           {rel_url}",
        f"Is Pre-release:        {rel_data.get('isPrerelease')}",
        f"Tag commit:            {tag_commit}",
        f"Asset name:            {asset_name}",
        f"Downloaded asset SHA256: {dl_sha}",
        f"BUILD_INFO.txt commit: {build_info_git}",
        "--- BUILD_INFO.txt inside asset ---",
        build_info_txt,
    ]

    if tag == "v1.0.14":
        diff_stat = _git("diff", "--stat", "63c85a1", "045660d")
        lines += [
            "--- git diff --stat 63c85a1 045660d ---",
            diff_stat,
        ]

    failures: list[str] = []
    if strict_head:
        raw_head = _git("log", "-1", "--format=%H")
        raw_status = _git("status", "-sb")
        raw_ls_main = _git("ls-remote", "origin", "refs/heads/main")
        raw_tag_l = _git("tag", "-l")
        raw_ls_tags = _git("ls-remote", "--tags", "origin")

        local_zip = workspace.dist_dir() / asset_name
        local_sha = _sha256(local_zip) if local_zip.is_file() else "missing"

        if not raw_ls_main.startswith(head):
            failures.append(f"origin/main ({raw_ls_main.split()[0] if raw_ls_main else 'none'}) != HEAD ({head})")
        if tag_commit != head:
            failures.append(f"tag {tag} commit ({tag_commit}) != HEAD ({head})")
        # Check remote tag points to HEAD (either directly or via ^{} peeled ref)
        if f"{head}\trefs/tags/{tag}" not in raw_ls_tags and f"{head}\trefs/tags/{tag}^{{}}" not in raw_ls_tags:
            failures.append(f"remote tag {tag} in ls-remote --tags origin does not point to HEAD ({head})")
        if local_sha != dl_sha:
            failures.append(f"downloaded asset SHA-256 ({dl_sha}) != local zip SHA-256 ({local_sha})")
        if build_info_git != head:
            failures.append(f"inner BUILD_INFO.txt commit ({build_info_git}) != HEAD ({head})")

        lines += [
            f"Local zip SHA256:      {local_sha}",
            "",
            "--- git log -1 --format=%H ---",
            raw_head,
            "",
            "--- git status -sb ---",
            raw_status,
            "",
            "--- git ls-remote origin refs/heads/main ---",
            raw_ls_main,
            "",
            "--- git tag -l ---",
            raw_tag_l,
            "",
            "--- git ls-remote --tags origin ---",
            raw_ls_tags,
            "",
            f"VERIFICATION RESULT: {'PASS' if not failures else 'FAIL'}",
        ]
        for f in failures:
            lines.append(f"  - FAIL: {f}")

    output = "\n".join(lines) + "\n"
    print(output, end="")
    if write_path is not None:
        write_path.write_text(output, encoding="utf-8")
    shutil.rmtree(dl_dir, ignore_errors=True)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a GitHub Release against local repo and build")
    parser.add_argument("--tag", default=None, help="Tag to verify (e.g. v1.0.14 or v1.0.15)")
    parser.add_argument("--strict-head", action="store_true", help="Require tag, origin/main, and BUILD_INFO == HEAD")
    parser.add_argument("--write", type=Path, default=None, help="Path to write verification report (e.g. RELEASE_EVIDENCE.txt)")
    args = parser.parse_args(argv)

    tag = args.tag
    if not tag:
        up = (ROOT / "packaging" / "update_number.txt").read_text(encoding="utf-8").strip()
        tag = f"v1.0.{up}"
    strict = args.strict_head or (tag != "v1.0.14" and args.write is not None)
    return verify_tag(tag, strict_head=strict, write_path=args.write)


if __name__ == "__main__":
    raise SystemExit(main())
