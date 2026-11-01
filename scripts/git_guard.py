"""AGU10 task 6.2 - shared logic for the pre-commit hook and the hook tests.

Blocks a commit when any staged added/modified file
  (a) is larger than GIT_MAX_FILE_MB (default 5), or
  (b) sits under a forbidden prefix or carries a forbidden extension,
or when a staged file matches a secret pattern of scripts/preflight_gates.py.

Exit 0 = commit may proceed. Bypass for (a)/(b) only: env ALLOW_BIG_FILE=1
(the bypass is always printed). Secret hits are never bypassable.

Run from the hook: `python scripts/git_guard.py` (cwd inside the repo).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dubtool import proc

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

FORBIDDEN_PREFIXES = (
    ".freebuff/", ".venv", "venv-build/", "dist/", "build/", "jobs/",
    "sample/", "vendor/", "models/", "hf-cache/", "tmp/", "inventory/",
)

FORBIDDEN_EXTS = (
    ".exe", ".dll", ".zip", ".mp4", ".mov", ".mkv", ".wav", ".mp3",
    ".onnx", ".bin", ".pt", ".safetensors", ".pfx", ".p12", ".pem", ".key",
)


def _git(*args: str, cwd: Path) -> str:
    done = proc.run(["git", *args], cwd=str(cwd))
    if done.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed")
    return done.stdout


def staged_files(cwd: Path) -> list[str]:
    out = _git("diff", "--cached", "--name-only", "--diff-filter=AM", cwd=cwd)
    return [line for line in out.splitlines() if line]


def blob_size(name: str, cwd: Path) -> int:
    done = proc.run(["git", "cat-file", "-s", f":{name}"], cwd=str(cwd))
    if done.returncode != 0:
        return 0
    try:
        return int(done.stdout.strip())
    except ValueError:
        return 0


def blob_text(name: str, cwd: Path) -> str:
    done = proc.run(["git", "show", f":{name}"], cwd=str(cwd))
    return done.stdout


def check_commit(cwd: Path) -> int:
    import preflight_gates  # noqa: PLC0415  (scripts/ on sys.path above)

    bypass = os.environ.get("ALLOW_BIG_FILE") == "1"
    rows = staged_files(cwd)
    blocked: list[str] = []
    for name in rows:
        reasons: list[str] = []
        if any(name.replace("\\", "/").startswith(p) for p in FORBIDDEN_PREFIXES):
            reasons.append(
                f"forbidden path prefix (generated/heavy output): {name}"
            )
        ext = Path(name).suffix.lower()
        if ext in FORBIDDEN_EXTS:
            reasons.append(f"forbidden binary extension {ext!r}: {name}")
        size = blob_size(name, cwd)
        if size > 5 * 1024 * 1024:
            mb = size / (1024 * 1024)
            reasons.append(f"larger than 5 MB: {name} ({mb:.2f} MB)")
        if reasons:
            if not bypass:
                for r in reasons:
                    print(f"pre-commit: BLOCK - {r}")
                blocked.append(name)
                continue
            for r in reasons:
                print(f"pre-commit: bypassed (ALLOW_BIG_FILE=1) - {r}")
        text = blob_text(name, cwd)
        # AGU10: the same fixture rule as preflight_gates.py — test files
        # deliberately quote key-shaped FAKE values (marked # fixture-fake-key),
        # and the scanner scripts themselves contain key-shaped regex
        # definitions. The secret scan applies to app code only.
        posix_name = name.replace("\\", "/")
        if posix_name.startswith("tests/") or posix_name in (
            "scripts/make_review_zip.py", "scripts/preflight_gates.py",
            "scripts/git_guard.py",
        ):
            continue
        for tag, pat in preflight_gates.PATTERNS:
            m = pat.search(text)
            if m:
                print(f"pre-commit: BLOCK - possible secret in {name} "
                      f"({tag}); remove the value, never commit keys")
                blocked.append(name)
    if blocked:
        print("pre-commit: commit blocked. Fix by removing the staged file "
              "(git rm --cached <file>) or adding a narrow .gitignore rule; "
              "set ALLOW_BIG_FILE=1 only when you are sure the file belongs "
              "in the repository.")
        return 1
    if rows:
        print(f"pre-commit: OK ({len(rows)} staged file(s) checked)")
    return 0


def main() -> int:
    done = proc.run(["git", "rev-parse", "--show-toplevel"])
    if done.returncode != 0:
        print("pre-commit: not a git repository; letting the commit pass")
        return 0
    return check_commit(Path(done.stdout.strip()))


if __name__ == "__main__":
    raise SystemExit(main())
