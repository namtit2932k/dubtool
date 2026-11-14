"""Pre-push and release gates for DubTool.

Prints only PASS/FAIL summaries and file paths/line numbers of any hit —
never a matched value. Exit code 0 = all gates pass.

    py scripts/preflight_gates.py [--quick] [--repo PATH] [--commit SHA]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from dubtool import proc  # noqa: E402


PATTERNS = [
    ("google-key", re.compile(r"AIza[0-9A-Za-z_-]{20,}")),
    ("anthropic-key", re.compile(r"sk-ant-[0-9A-Za-z_-]{20,}")),
    ("openai-key", re.compile(r"sk-proj-[0-9A-Za-z_-]{20,}")),
    ("private-key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github-token", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("github-pat", re.compile(r"github_pat_[A-Za-z0-9_]{10,}")),
]

FORBIDDEN_PREFIXES = (
    "jobs/",
    "sample/",
    "dist/",
    "build/",
    "vendor/",
    "models/",
    ".venv/",
    ".venv-build/",
    ".freebuff/",
    "hf-cache/",
    "tmp/",
    "inventory/",
)


def _git(repo: Path, *args: str, **kw) -> str:
    done = proc.run(["git", *args], cwd=str(repo), **kw)
    if done.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed")
    return done.stdout


def tracked_files(repo: Path = ROOT, commit: str | None = None) -> list[str]:
    if commit:
        out = _git(repo, "-c", "core.quotePath=false", "ls-tree", "-r", "--name-only", commit)
    else:
        out = _git(repo, "-c", "core.quotePath=false", "ls-files", "--cached")
    return [line for line in out.splitlines() if line]


def blob_content(path: str, repo: Path = ROOT, commit: str | None = None) -> bytes:
    ref = f"{commit}:{path}" if commit else f":{path}"
    done = proc.run(
        ["git", "-c", "core.quotePath=false", "show", ref],
        cwd=str(repo),
        text=False,
    )
    if done.returncode != 0:
        raise RuntimeError(f"git show failed for {path}")
    return done.stdout


def stored_key_values(repo: Path = ROOT) -> list[bytes]:
    """Values of keys stored in .env (held in memory only, never printed)."""
    values: list[bytes] = []
    env_path = repo / ".env"
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    val = line.split("=", 1)[1].strip().strip("\"'")
                    if len(val) >= 16:
                        values.append(val.encode("utf-8"))
        except OSError:
            pass
    return values


def _is_marked_fixture_line(text: str, match_start: int, match_end: int) -> bool:
    line_start = text.rfind("\n", 0, match_start) + 1
    line_end = text.find("\n", match_end)
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end]
    return "fixture-fake-key" in line


def run_gates(repo: Path = ROOT, quick: bool = False, commits: list[str] | None = None) -> int:
    targets: list[str | None] = list(commits) if commits else [None]
    failures: list[str] = []
    values = stored_key_values(repo)

    for commit in targets:
        files = tracked_files(repo, commit=commit)
        total = 0
        big: list[tuple[str, int]] = []
        for name in files:
            size = len(blob_content(name, repo=repo, commit=commit))
            total += size
            if size > 50 * 1024 * 1024:
                failures.append(f"file over 50 MB: {name} ({size} bytes)")
            elif size > 5 * 1024 * 1024:
                big.append((name, size))
        print(f"[gate3] tracked files: {len(files)}, total {total/1024/1024:.2f} MB")
        for name, size in big:
            print(f"[gate3] warn >5 MB: {name} ({size/1024/1024:.2f} MB)")
        if total > 50 * 1024 * 1024:
            failures.append(f"tracked total over 50 MB: {total/1024/1024:.2f} MB")

        # Gate 4: forbidden paths
        bad_paths = [
            f
            for f in files
            if f.startswith(FORBIDDEN_PREFIXES)
            or f == ".env"
            or f.startswith(".env.")
            or "/.env" in f
        ]
        if bad_paths:
            failures.append("forbidden paths tracked: " + ", ".join(bad_paths[:10]))
        print(f"[gate4] forbidden-path check: {'FAIL' if bad_paths else 'PASS'}")

        # Gate 2: pattern scan + known stored values
        real_hits: list[str] = []
        fixture_hits: list[str] = []
        for name in files:
            content = blob_content(name, repo=repo, commit=commit)
            text = content.decode("utf-8", errors="replace")
            for tag, pat in PATTERNS:
                for m in pat.finditer(text):
                    is_fixture = (
                        (
                            (name.startswith("tests/") and _is_marked_fixture_line(text, m.start(), m.end()))
                            or name in (
                                "scripts/make_review_zip.py",
                                "scripts/preflight_gates.py",
                                "src/dubtool/app/failguard.py",
                                "REPORT_UPDATE_7.md",
                                "REPORT_UPDATE_9.md",
                            )
                            or name.startswith("agent/")
                        )
                        and all(not (val and val == m.group(0).encode("utf-8")) for val in values)
                    )
                    # Note: older test files in tests/ may also have fake keys on lines without comment;
                    # allow tests/ only when not in a custom commit test or when marked.
                    if not is_fixture and name.startswith("tests/") and commit is None:
                        is_fixture = all(not (val and val == m.group(0).encode("utf-8")) for val in values)
                    (fixture_hits if is_fixture else real_hits).append(f"{name}: pattern {tag}")
            for val in values:
                if val and val in content:
                    real_hits.append(f"{name}: stored key value (exact match)")

        if fixture_hits and not quick:
            for h in fixture_hits:
                print(f"[gate2] fixture (expected, not pushed as a secret): {h}")
        if real_hits:
            for h in real_hits:
                print(f"[gate2] FAIL {h}")
            failures.append("secret-scan hits")
        else:
            print(f"[gate2] secret scan ({len(files)} files, {len(values)} stored values): PASS")

        if not quick and (repo / "README.md").exists() and (repo / "README_BUILD.md").exists():
            from check_readme_links import check as check_links

            link_problems = check_links(repo, set(files))
            for p in link_problems:
                print(f"[gate6] FAIL {p}")
            print(f"[gate6] README link check: {'FAIL' if link_problems else 'PASS'}")
            if link_problems:
                failures.append("README link check failed")

    if not quick:
        print("[gate1] full offline test suite: run separately")
        print("[gate5] untracked review: agent/*.md intentionally untracked (AGU6 8.1)")
    if failures:
        print("GATES FAILED:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL PUBLISHED-CONTENT GATES PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DubTool preflight gates")
    parser.add_argument("--quick", action="store_true", help="Fast pre-push check")
    parser.add_argument("--repo", type=Path, default=ROOT, help="Repository root")
    parser.add_argument("--commit", action="append", default=[], help="Commit SHA(s) to scan")
    args = parser.parse_args(argv)
    return run_gates(repo=args.repo.resolve(), quick=args.quick, commits=args.commit or None)


if __name__ == "__main__":
    raise SystemExit(main())
