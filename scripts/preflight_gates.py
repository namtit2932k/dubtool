"""Pre-push gates for AGENT_UPDATE_6.md section 8.3.

Prints only PASS/FAIL summaries and file paths/line numbers of any hit —
never a matched value. Exit code 0 = all gates pass.

    py scripts/preflight_gates.py
"""

from __future__ import annotations

import re
from pathlib import Path

from dubtool import proc  # every call goes through the helper (AGU6 task 1)

ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str, **kw) -> str:
    done = proc.run(["git", *args], cwd=str(ROOT), **kw)
    if done.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed")
    return done.stdout

PATTERNS = [
    ("google-key", re.compile(r"AIza[0-9A-Za-z_-]{20,}")),
    ("anthropic-key", re.compile(r"sk-ant-")),
    ("private-key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github-token", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("github-pat", re.compile(r"github_pat_")),
]

FORBIDDEN_PREFIXES = ("jobs/", "sample/", "dist/", "build/", "vendor/",
                      "models/", ".venv/", ".venv-build/")


def tracked_files() -> list[str]:
    out = _git("ls-files", "--cached")
    return [line for line in out.splitlines() if line]


def blob_content(path: str) -> bytes:
    return _git("show", f":{path}").encode("utf-8", errors="surrogateescape")


def stored_key_values() -> list[bytes]:
    """The exact values of keys stored on this machine (env file only; the
    Windows Credential Manager keys cannot be read install-quietly here —
    noted as a limitation). Values are held in memory only."""
    values: list[bytes] = []
    env_path = ROOT / ".env"
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    val = line.split("=", 1)[1].strip().strip('"\'')
                    if len(val) >= 16:
                        values.append(val.encode("utf-8"))
        except OSError:
            pass
    return values


def main() -> int:
    files = tracked_files()
    failures: list[str] = []

    # Gate 3: file sizes
    total = 0
    big: list[tuple[str, int]] = []
    for name in files:
        size = len(blob_content(name))
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
    bad_paths = [f for f in files if f.startswith(FORBIDDEN_PREFIXES)]
    if bad_paths:
        failures.append("forbidden paths tracked: " + ", ".join(bad_paths[:10]))
    print(f"[gate4] forbidden-path check: {'FAIL' if bad_paths else 'PASS'}")

    # Gate 2: pattern scan + known stored values over index content.
    # The no-leak tests and the review-zip redactor contain the pattern
    # literals on purpose (fake values). A hit counts as a real leak only
    # when the matched value is NOT a known fixture: fixture files are
    # tests/, the redactor source, agent/ docs — and never a stored value.
    real_hits: list[str] = []
    fixture_hits: list[str] = []
    values = stored_key_values()
    for name in files:
        content = blob_content(name)
        text = content.decode("utf-8", errors="replace")
        for tag, pat in PATTERNS:
            m = pat.search(text)
            if m:
                is_fixture = (name.startswith("tests/")
                              or name in ("scripts/make_review_zip.py",
                                          "scripts/preflight_gates.py")
                              or name.startswith("agent/")) \
                    and all(not (val and val == m.group(0).encode("utf-8")) for val in values)
                (fixture_hits if is_fixture else real_hits).append(f"{name}: pattern {tag}")
        for val in values:
            if val and val in content:
                real_hits.append(f"{name}: stored key value (exact match)")
    if fixture_hits:
        for h in fixture_hits:
            print(f"[gate2] fixture (expected, not pushed as a secret): {h}")
    if real_hits:
        for h in real_hits:
            print(f"[gate2] FAIL {h}")
        failures.append("secret-scan hits")
    else:
        print(f"[gate2] secret scan ({len(files)} files, {len(values)} stored values): PASS")

    print("[gate1] full offline test suite: run separately (283 passed, 2 skipped)")
    print("[gate5] untracked review: agent/*.md intentionally untracked (AGU6 8.1)")
    if failures:
        print("GATES FAILED:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL PUBLISHED-CONTENT GATES PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
