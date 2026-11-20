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


def _git_in(repo: Path, *args: str) -> str:
    done = proc.run(["git", *args], cwd=str(repo))
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr}")
    return done.stdout.strip()


def _parse_release_evidence(path: Path) -> dict[str, dict[str, str]]:
    """Parse RELEASE_EVIDENCE.txt into a mapping of tag -> verified metadata."""
    out: dict[str, dict[str, str]] = {}
    if not path.is_file():
        return out
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"(?=^=== Release Verification:\s*\S+\s*===)", text, flags=re.MULTILINE)
    for blk in blocks:
        m_tag = re.search(r"^=== Release Verification:\s*(\S+)\s*===", blk, flags=re.MULTILINE)
        if not m_tag:
            continue
        tag = m_tag.group(1).strip()
        meta: dict[str, str] = {"tag": tag}
        for key, pat in (
            ("tag_commit", r"^Tag commit:\s*(\S+)"),
            ("build_info_commit", r"^BUILD_INFO\.txt commit:\s*(\S+)"),
            ("dl_sha", r"^Downloaded asset SHA256:\s*(\S+)"),
            ("local_sha", r"^Local zip SHA256:\s*(\S+)"),
            ("result", r"^VERIFICATION RESULT:\s*(\S+)"),
            ("prerelease", r"^Is Pre-release:\s*(\S+)"),
        ):
            m = re.search(pat, blk, flags=re.MULTILINE)
            if m:
                meta[key] = m.group(1).strip()
        out[tag] = meta
    return out


def _parse_releases_md(path: Path) -> dict[str, dict[str, str]]:
    """Parse docs/RELEASES.md Markdown table rows keyed by tag name."""
    out: dict[str, dict[str, str]] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip().startswith("|"):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        tag_col = cols[0].strip("`") if cols else ""
        if len(cols) >= 4 and tag_col.startswith("v1.0."):
            out[tag_col] = {
                "commit": cols[1].strip("`"),
                "status": cols[2],
                "reason": cols[3],
                "date": cols[4] if len(cols) >= 5 else "",
            }
    return out


def _tag_sort_key(tag: str) -> tuple[int, int, str]:
    m = re.fullmatch(r"v1\.0\.(\d+)", tag)
    if m:
        return (2, int(m.group(1)), tag)
    m2 = re.fullmatch(r"update-(\d+)", tag)
    if m2:
        return (1, int(m2.group(1)), tag)
    return (0, 0, tag)


def _update_num_of_tag(tag: str) -> int | None:
    m = re.fullmatch(r"v1\.0\.(\d+)", tag)
    return int(m.group(1)) if m else None


def run_audit(
    repo: Path = ROOT,
    releases_override: list[dict] | None = None,
) -> tuple[int, str, list[str]]:
    """Audit all Git tags and GitHub releases against local evidence.

    Returns (exit_code, markdown_table, violations). Fails (rc=1) if any GitHub
    release created after Update 15 (v1.0.16+) lacks matching local evidence or
    has a tag/BUILD_INFO/hash mismatch.
    """
    tags_raw = _git_in(repo, "tag", "-l")
    local_tags = [t.strip() for t in tags_raw.splitlines() if t.strip()]
    tag_commits: dict[str, str] = {}
    for t in local_tags:
        try:
            tag_commits[t] = _git_in(repo, "rev-list", "-n", "1", t)
        except Exception:
            tag_commits[t] = ""

    if releases_override is not None:
        releases = releases_override
    else:
        gh = find_gh_bin()
        env = gh_env()
        view = proc.run(
            [gh, "release", "list", "--limit", "100", "--json", "tagName,isPrerelease,publishedAt"],
            cwd=str(repo),
            env=env,
        )
        if view.returncode != 0:
            raise RuntimeError(f"gh release list failed: {view.stderr.strip()}")
        releases = json.loads(view.stdout or "[]")

    rel_by_tag = {str(r.get("tagName", "")).strip(): r for r in releases if r.get("tagName")}
    ev_map = _parse_release_evidence(repo / "RELEASE_EVIDENCE.txt")
    doc_map = _parse_releases_md(repo / "docs" / "RELEASES.md")

    ev_upd_nums = [n for t in ev_map if (n := _update_num_of_tag(t)) is not None]
    latest_ev_upd = max(ev_upd_nums) if ev_upd_nums else 15

    all_tags = sorted(set(local_tags) | set(rel_by_tag.keys()), key=_tag_sort_key, reverse=True)
    lines = [
        "| Tag | Tag Commit | GitHub Release | Evidence / BUILD_INFO | Status |",
        "| --- | --- | --- | --- | --- |",
    ]
    violations: list[str] = []

    for tag in all_tags:
        commit = tag_commits.get(tag, "")
        short_commit = commit[:8] if commit else "missing"
        rel = rel_by_tag.get(tag)
        ev = ev_map.get(tag)
        doc = doc_map.get(tag)
        upd_n = _update_num_of_tag(tag)

        if rel is None:
            gh_col = "No"
            ev_col = doc["status"] if doc else "tag only"
            status = "no evidence" if (upd_n is not None and upd_n >= 15) else "tag only"
        else:
            is_pre = bool(rel.get("isPrerelease"))
            gh_col = "Pre-release" if is_pre else "Yes"

            if is_pre or (doc and "superseded" in doc.get("status", "").lower()):
                status = "pre-release, superseded"
                ev_col = doc.get("reason", "pre-release") if doc else "pre-release"
            elif ev is not None:
                ev_commit = ev.get("tag_commit", "")
                bi_commit = ev.get("build_info_commit", "")
                dl_sha = ev.get("dl_sha", "")
                local_sha = ev.get("local_sha", "")
                passed = (
                    ev.get("result", "").upper() == "PASS"
                    and bool(commit)
                    and ev_commit == commit
                    and bi_commit == commit
                    and bool(dl_sha)
                    and dl_sha == local_sha
                )
                if passed:
                    status = "ok"
                    ev_col = f"BUILD_INFO={bi_commit[:8]}, sha256={dl_sha[:12]}…"
                else:
                    status = "no evidence"
                    ev_col = f"mismatch (ev={ev_commit[:8]}, bi={bi_commit[:8]})"
                    if upd_n is not None and upd_n > 15:
                        violations.append(f"{tag}: RELEASE_EVIDENCE mismatch ({ev_col})")
            elif (
                upd_n is not None
                and upd_n < max(latest_ev_upd, 16)
                and doc is not None
                and doc.get("status", "").lower() == "official"
                and commit
                and commit.startswith(doc.get("commit", ""))
            ):
                status = "ok"
                ev_col = f"docs/RELEASES.md ({doc['commit']})"
            else:
                status = "no evidence"
                ev_col = "missing RELEASE_EVIDENCE" if commit else "remote-only tag without local commit"
                if upd_n is not None and upd_n > 15:
                    violations.append(f"{tag}: post-Update-15 release has no verified local evidence ({ev_col})")

        lines.append(f"| `{tag}` | `{short_commit}` | {gh_col} | {ev_col} | {status} |")

    table = "\n".join(lines) + "\n"
    return (1 if violations else 0), table, violations


def audit_releases(repo: Path = ROOT) -> int:
    rc, table, violations = run_audit(repo=repo)
    print(table, end="")
    for v in violations:
        print(f"AUDIT FAIL: {v}", file=sys.stderr)
    return rc


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
    parser.add_argument("--audit", action="store_true", help="List and audit all tags/releases")
    args = parser.parse_args(argv)

    if args.audit:
        return audit_releases()

    tag = args.tag
    if not tag:
        up = (ROOT / "packaging" / "update_number.txt").read_text(encoding="utf-8").strip()
        tag = f"v1.0.{up}"
    strict = args.strict_head or (tag != "v1.0.14" and args.write is not None)
    return verify_tag(tag, strict_head=strict, write_path=args.write)


if __name__ == "__main__":
    raise SystemExit(main())
