"""AGU10 task 6.2 & AGU15 task 1 (2.2) - installs local pre-commit and pre-push hooks.

Hooks live only in this clone (.git/hooks); this script is tracked, the
hooks themselves are not. Refuses to touch any hook it did not write.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "# installed by scripts/install_git_hooks.py (AGU10 task 6.2)"
PRE_PUSH_MARKER = "# installed by scripts/install_git_hooks.py (AGU15 task 2.2)"

SHIM_TEMPLATE = """#!/bin/sh
{marker}
# The guard logic is in scripts/git_guard.py (tracked); this shim just runs it.
git_root=$(git rev-parse --show-toplevel) || exit 0
if [ -x "$git_root/../Project_K_build/venv-build/Scripts/python.exe" ]; then
  PY="$git_root/../Project_K_build/venv-build/Scripts/python.exe"
elif command -v python >/dev/null 2>&1; then
  PY=python
elif command -v py >/dev/null 2>&1; then
  PY=py
else
  echo "pre-commit: no python found; skipping the guard" >&2
  exit 0
fi
PYTHONPATH="$git_root/src" PYTHONIOENCODING=utf-8 "$PY" "$git_root/scripts/git_guard.py"
"""

PRE_PUSH_SHIM_TEMPLATE = """#!/bin/sh
{marker}
# The pre-push guard logic is in scripts/git_push_guard.py (tracked).
git_root=$(git rev-parse --show-toplevel) || exit 1
if [ -n "$DUBTOOL_HOOK_PYTHON" ] && [ -x "$DUBTOOL_HOOK_PYTHON" ]; then
  PY="$DUBTOOL_HOOK_PYTHON"
elif [ -x "$git_root/../Project_K_build/venv-build/Scripts/python.exe" ]; then
  PY="$git_root/../Project_K_build/venv-build/Scripts/python.exe"
elif command -v python >/dev/null 2>&1; then
  PY=python
elif command -v py >/dev/null 2>&1; then
  PY=py
else
  echo "pre-push: no python found; blocking push" >&2
  exit 1
fi
PYTHONPATH="$git_root/src:$git_root/scripts" PYTHONIOENCODING=utf-8 "$PY" "$git_root/scripts/git_push_guard.py" --repo "$git_root" "$@"
"""


def hook_text() -> str:
    return SHIM_TEMPLATE.format(marker=MARKER)


def pre_push_hook_text() -> str:
    return PRE_PUSH_SHIM_TEMPLATE.format(marker=PRE_PUSH_MARKER)


def _install_one(hook: Path, marker: str, new_text: str) -> str:
    if hook.exists():
        old = hook.read_text(encoding="utf-8", errors="replace")
        if marker in old or MARKER in old:
            hook.write_text(new_text, encoding="utf-8", newline="\n")
            return "refreshed"
        return "exists-foreign-hook-left-alone"
    hook.write_text(new_text, encoding="utf-8", newline="\n")
    try:
        hook.chmod(0o755)
    except OSError:
        pass
    return "installed"


def install(root: Path = ROOT) -> str:
    """Install or refresh the pre-commit guard hook (and pre-push hook). Returns pre-commit action."""
    hooks = root / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    res_commit = _install_one(hooks / "pre-commit", MARKER, hook_text())
    _install_one(hooks / "pre-push", PRE_PUSH_MARKER, pre_push_hook_text())
    return res_commit


def install_pre_push(root: Path = ROOT) -> str:
    """Install or refresh the pre-push guard hook. Returns an action string."""
    hooks = root / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    return _install_one(hooks / "pre-push", PRE_PUSH_MARKER, pre_push_hook_text())


if __name__ == "__main__":
    c_status = install(ROOT)
    p_status = install_pre_push(ROOT)
    print(f"pre-commit hook: {c_status}")
    print(f"pre-push hook: {p_status}")
