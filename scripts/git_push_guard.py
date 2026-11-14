"""Pre-push guard for AGENT_UPDATE_15 Task 1 (2.2).

Invoked by `.git/hooks/pre-push` (installed by `scripts/install_git_hooks.py`).
Reads push ref lines `<local_ref> <local_sha> <remote_ref> <remote_sha>` from stdin:
1. Runs `scripts/preflight_gates.py --quick` for every push (and scans pushed commits).
2. Blocks any push of a ref matching `refs/tags/v*` unless `.git/dubtool-release-ok-<N>`
   exists, its `head` hash equals the pushed commit (`local_sha`), and the recorded
   review-zip hash (`review_zip_sha256`) still matches the file on disk.
   There is no bypass variable for tags.
3. Blocks any push to `main` (`refs/heads/main`) that contains a commit changing
   `packaging/update_number.txt` or `src/dubtool/__init__.py` (`__version__`)
   unless the same valid `.git/dubtool-release-ok-<N>` stamp exists.
Never contacts the network; prints only file paths and reasons (never secret values).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from dubtool import proc  # noqa: E402
import preflight_gates  # noqa: E402

ZERO_SHA = "0" * 40
TAG_RE = re.compile(r"^refs/tags/v1\.0\.(\d+)$")
ANY_V_TAG_RE = re.compile(r"^refs/tags/v.*$")
VERSION_FILES = {"packaging/update_number.txt", "src/dubtool/__init__.py"}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def _git_dir(repo: Path) -> Path:
    done = proc.run(["git", "rev-parse", "--git-dir"], cwd=str(repo))
    if done.returncode == 0 and done.stdout.strip():
        p = Path(done.stdout.strip())
        return p if p.is_absolute() else (repo / p).resolve()
    return repo / ".git"


def _read_update_number_at(repo: Path, commit: str) -> str | None:
    done = proc.run(
        ["git", "show", f"{commit}:packaging/update_number.txt"],
        cwd=str(repo),
    )
    if done.returncode == 0:
        val = done.stdout.strip()
        if val.isdigit():
            return val
    p = repo / "packaging" / "update_number.txt"
    if p.is_file():
        val = p.read_text(encoding="utf-8", errors="replace").strip()
        if val.isdigit():
            return val
    return None


def verify_release_stamp(repo: Path, pushed_sha: str, update_num: str) -> tuple[bool, str]:
    git_dir = _git_dir(repo)
    stamp_path = git_dir / f"dubtool-release-ok-{update_num}"
    if not stamp_path.is_file():
        return False, f"missing release stamp {stamp_path.name} (use scripts/release.py {update_num})"
    try:
        data = json.loads(stamp_path.read_text(encoding="utf-8"))
    except Exception:
        return False, f"invalid JSON in release stamp {stamp_path.name}"

    stamp_head = str(data.get("head") or "").strip().lower()
    if not stamp_head or stamp_head != pushed_sha.strip().lower():
        return (
            False,
            f"stale release stamp {stamp_path.name}: stamp head {stamp_head[:12]} != pushed commit {pushed_sha[:12]}",
        )

    review_zip_rel = str(data.get("review_zip_path") or "").strip()
    review_zip_sha = str(data.get("review_zip_sha256") or "").strip().lower()
    if not review_zip_rel or not review_zip_sha:
        return False, f"release stamp {stamp_path.name} missing review_zip_path or review_zip_sha256"

    review_zip = Path(review_zip_rel)
    if not review_zip.is_absolute():
        review_zip = (repo / review_zip).resolve()
    if not review_zip.is_file():
        return False, f"review zip missing on disk: {review_zip}"

    actual_sha = _sha256_file(review_zip)
    if actual_sha != review_zip_sha:
        return (
            False,
            f"review zip SHA-256 mismatch for {review_zip.name}: expected {review_zip_sha[:12]}, got {actual_sha[:12]}",
        )
    return True, "ok"


def _changed_files_in_push(repo: Path, local_sha: str, remote_sha: str) -> set[str]:
    if remote_sha and remote_sha != ZERO_SHA:
        # Check if remote_sha is an ancestor/known object locally
        cat = proc.run(["git", "cat-file", "-e", f"{remote_sha}^{{commit}}"], cwd=str(repo))
        if cat.returncode == 0:
            diff = proc.run(
                ["git", "-c", "core.quotePath=false", "diff", "--name-only", f"{remote_sha}..{local_sha}"],
                cwd=str(repo),
            )
            if diff.returncode == 0:
                return {ln.strip() for ln in diff.stdout.splitlines() if ln.strip()}
    # New branch on remote or unknown remote_sha: inspect commits not on any remote tracking branch,
    # or at least the tip commit's changed files vs parent (if parent exists).
    parent = proc.run(["git", "rev-parse", "--verify", f"{local_sha}~1"], cwd=str(repo))
    if parent.returncode == 0:
        diff = proc.run(
            ["git", "-c", "core.quotePath=false", "diff", "--name-only", f"{local_sha}~1..{local_sha}"],
            cwd=str(repo),
        )
        if diff.returncode == 0:
            return {ln.strip() for ln in diff.stdout.splitlines() if ln.strip()}
    return set()


def check_push(repo: Path, stdin_lines: list[str]) -> tuple[int, list[str]]:
    reasons: list[str] = []
    pushed_commits: list[str] = []

    parsed_Updates: list[tuple[str, str, str, str]] = []
    for raw in stdin_lines:
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts
        if local_sha == ZERO_SHA:
            continue
        parsed_Updates.append((local_ref, local_sha, remote_ref, remote_sha))
        if local_sha not in pushed_commits:
            pushed_commits.append(local_sha)

    # 1. Run preflight_gates --quick for every push
    gate_rc = preflight_gates.run_gates(
        repo=repo,
        quick=True,
        commits=pushed_commits if pushed_commits else None,
    )
    if gate_rc != 0:
        reasons.append("preflight_gates.py --quick failed")

    # 2. Check tag pushes and main version-bump pushes
    for local_ref, local_sha, remote_ref, remote_sha in parsed_Updates:
        if ANY_V_TAG_RE.match(remote_ref) or ANY_V_TAG_RE.match(local_ref):
            tag_ref = remote_ref if ANY_V_TAG_RE.match(remote_ref) else local_ref
            m = TAG_RE.match(tag_ref)
            if not m:
                reasons.append(f"blocked tag push {tag_ref}: tag must match refs/tags/v1.0.<N>")
                continue
            update_num = m.group(1)
            # Resolve annotated tag to its target commit hash if local_sha is a tag object
            rev = proc.run(["git", "rev-parse", f"{local_sha}^{{commit}}"], cwd=str(repo))
            commit_sha = rev.stdout.strip() if rev.returncode == 0 and rev.stdout.strip() else local_sha
            ok, msg = verify_release_stamp(repo, commit_sha, update_num)
            if not ok:
                reasons.append(f"blocked tag push {tag_ref}: {msg}")

        elif remote_ref == "refs/heads/main" or local_ref == "refs/heads/main":
            changed = _changed_files_in_push(repo, local_sha, remote_sha)
            touched = sorted(VERSION_FILES.intersection(changed))
            if touched:
                update_num = _read_update_number_at(repo, local_sha) or "unknown"
                ok, msg = verify_release_stamp(repo, local_sha, update_num)
                if not ok:
                    reasons.append(
                        f"blocked main push modifying {', '.join(touched)} without valid release stamp: {msg}"
                    )

    if reasons:
        return 1, reasons
    return 0, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DubTool pre-push guard")
    parser.add_argument("--repo", type=Path, default=ROOT)
    args, _unknown = parser.parse_known_args(argv)
    repo = args.repo.resolve()
    lines = sys.stdin.read().splitlines() if not sys.stdin.isatty() else []
    rc, reasons = check_push(repo, lines)
    if rc != 0:
        print("pre-push guard BLOCKED push:", file=sys.stderr)
        for r in reasons:
            print(f"  - {r}", file=sys.stderr)
        return 1
    print("pre-push guard: PASS")
    return 0


if __name__ == "__main__":
    import argparse

    raise SystemExit(main())
