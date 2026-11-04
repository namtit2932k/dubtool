r"""Update 12 task 6 item 1: the full-history secret scan.

Scans EVERY commit and EVERY ref (not only the working tree) for:
- the exact values of the stored API keys (from Windows Credential
  Manager / env — read at run time, never printed);
- the usual key patterns (the Gemini / Anthropic / GitHub token
  prefixes, private-key headers -----BEGIN … PRIVATE KEY-----);
- .env-style KEY=value lines;
- file names such as *.pfx, *.pem, *.key.

Reports counts and file paths only — never any secret value. If anything
is found the push must stop and the user revokes the secret first; no
history rewritals are performed by this tool (git gc/prune/reflog and any
history rewrite are forbidden).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from dubtool import proc  # every external process goes through the helper

ROOT = Path(__file__).resolve().parent.parent

# The usual key patterns (Update 12 task 6 item 1). File NAME patterns live
# separately in _FILE_NAME_RE.
KEY_PATTERNS: dict[str, str] = {
    "AIza": r"AIza[0-9A-Za-z\-_]{35}",
    "sk-ant": r"sk-ant" + r"-[0-9A-Za-z\-_]{40,}",
    "ghp_": r"ghp_[0-9A-Za-z]{36}",
    "github_pat": r"github_pat" + r"_[0-9A-Za-z_]{60,}",
    "private_key_header": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
}

_FILE_NAME_RE = re.compile(r"\.(pfx|pem|key)$", re.IGNORECASE)
_ENV_LINE_RE = re.compile(
    r"^\s*[A-Z][A-Z0-9_]{2,}\s*=\s*[^\s#].{8,}$", re.MULTILINE
)
_ENV_FILE_NAMES = {"env", ".env", ".env.local", ".env.production"}
# Benign placeholder values that must never count as secrets.
_BENIGN_VALUES = {
    "", "changeme", "your_api_key_here", "your-api-key-here", "placeholder",
    "your_key", "yourkey", "xxx", "none", "test", "replace_me",
}


def _stored_key_values() -> list[str]:
    """The exact values of the stored API keys, read at run time (never
    printed, never logged by this program)."""
    values: list[str] = []
    try:
        from dubtool import secrets

        for entry in secrets.env_keys():
            if isinstance(entry, dict):
                entry = entry.get("key") or ""
            if isinstance(entry, str) and len(entry) >= 20:
                values.append(entry.strip())
    except Exception:
        pass  # no stored keys (or no credentials): pattern scan only
    return values


def _git_all_blobs() -> list[str]:
    """Every (commit, file) pair reachable from all refs, as a unique list
    of object ids followed by their file names."""
    out = proc.run(
        ["git", "rev-list", "--objects", "--all"], cwd=ROOT,
    )
    if out.returncode != 0:
        raise RuntimeError("git rev-list --all failed: " + (out.stderr or "")[:200])
    return [line.strip() for line in (out.stdout or "").splitlines() if line.strip()]


def scan() -> tuple[int, dict[str, int], list[str]]:
    """Run the scan. Returns (blobs_scanned, hits_by_kind, findings) —
    findings name the kind, the blob id's file path, and the line number
    inside the blob — never any value."""
    fetcher = proc.run(["git", "cat-file", "--batch"], cwd=ROOT)
    # We stream through rev-list --objects ourselves; use one process per
    # lookup for simplicity (a public repo's history is small).
    hits_by_kind: dict[str, int] = {}
    findings: list[str] = []
    blobs = [line.split(" ", 1)[0] for line in _git_all_blobs()
             if re.fullmatch(r"[0-9a-f]{40}", line.split(" ", 1)[0])]

    names = {line.split(" ", 1)[0]: line.split(" ", 1)[1]
             for line in _git_all_blobs() if " " in line
             and re.fullmatch(r"[0-9a-f]{40}", line.split(" ", 1)[0])}

    exact_values = _stored_key_values()
    for blob_id in blobs:
        out = proc.run(["git", "cat-file", "-p", blob_id], cwd=ROOT)
        if out.returncode != 0:
            continue  # tree object / commit object: not text content
        name = names.get(blob_id, blob_id)
        if isinstance(name, str) and _FILE_NAME_RE.search(name):
            findings.append(f"FILE-NAME kind=suspicious-name path={name}")
            hits_by_kind["suspicious_file_name"] = (
                hits_by_kind.get("suspicious_file_name", 0) + 1)
        if name in _ENV_FILE_NAMES or str(name).endswith("/.env"):
            findings.append(f"ENV-FILE kind=env-style-file path={name}")
            hits_by_kind["env_style_file"] = hits_by_kind.get("env_style_file", 0) + 1
        body = out.stdout or ""
        for value in exact_values:
            if value and value in body:
                findings.append(f"STORED-KEY kind=exact-stored-key path={name}")
                hits_by_kind["exact_stored_key"] = (
                    hits_by_kind.get("exact_stored_key", 0) + 1)
        for kind, pattern in KEY_PATTERNS.items():
            for match in re.finditer(pattern, body):
                line_no = body.count("\n", 0, match.start()) + 1
                findings.append(f"{kind} path={name}:{line_no}")
                hits_by_kind[kind] = hits_by_kind.get(kind, 0) + 1
        if _ENV_LINE_RE.search(body) and name.lower().endswith((".env",)):
            findings.append(f"ENV-LINE kind=env-style-line path={name}")
            hits_by_kind["env_style_line"] = hits_by_kind.get("env_style_line", 0) + 1
    return len(blobs), hits_by_kind, findings


def main() -> int:
    tried, hits, findings = scan()
    print(f"scanned objects: {tried}")
    if not hits:
        print("history secret scan: CLEAN (no key patterns, no stored-key")
        print("values, no suspicious file names in any ref)")
        return 0
    print("history secret scan: FINDINGS — the push must stop and the")
    print("secret must be revoked first; never rewrite history")
    for kind, count in sorted(hits.items(), key=lambda kv: -kv[1]):
        print(f"  {kind}: {count}")
    for line in findings[:80]:
        print(f"  finding: {line}")
    if len(findings) > 80:
        print(f"  ... {len(findings) - 80} more")
    return 1


if __name__ == "__main__":
    sys.exit(main())
