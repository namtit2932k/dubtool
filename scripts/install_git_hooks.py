"""AGU10 task 6.2 - installs the local pre-commit hook.

Hooks live only in this clone (.git/hooks); this script is tracked, the
hook itself is not. Refuses to touch a pre-commit hook it did not write.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "# installed by scripts/install_git_hooks.py (AGU10 task 6.2)"
SHIM_TEMPLATE = """#!/bin/sh
{marker}
# The guard logic is in scripts/git_guard.py (tracked); this shim just runs it.
git_root=$(git rev-parse --show-toplevel) || exit 0
if command -v python >/dev/null 2>&1; then
  PY=python
elif command -v py >/dev/null 2>&1; then
  PY=py
else
  echo "pre-commit: no python found; skipping the guard" >&2
  exit 0
fi
PYTHONPATH="$git_root/src" PYTHONIOENCODING=utf-8 "$PY" "$git_root/scripts/git_guard.py"
"""


def hook_text() -> str:
    return SHIM_TEMPLATE.format(marker=MARKER)


def install(root: Path = ROOT) -> str:
    """Install or refresh the guard hook. Returns an action string."""
    hooks = root / ".git" / "hooks"
    hook = hooks / "pre-commit"
    hooks.mkdir(parents=True, exist_ok=True)
    new = hook_text()
    if hook.exists():
        old = hook.read_text(encoding="utf-8", errors="replace")
        if MARKER in old:
            hook.write_text(new, encoding="utf-8", newline="\n")
            return "refreshed"
        return "exists-foreign-hook-left-alone"
    hook.write_text(new, encoding="utf-8", newline="\n")
    try:
        hook.chmod(0o755)
    except OSError:
        pass
    return "installed"


if __name__ == "__main__":
    print(f"pre-commit hook: {install()}")
