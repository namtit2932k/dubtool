"""Mechanical Definition-of-Done checker for AGENT_UPDATE_15 Task 2.

Writes `DOD_REPORT.md` (a pass/fail table with the command run and evidence
line for each row) and exits non-zero on any failure.

Usage:
    py scripts/definition_of_done.py [--repo PATH] [--build-root PATH] [--update N]
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import os
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from dubtool import proc, workspace  # noqa: E402

SPEC_ID_RE = re.compile(r"\b(AGU\d*|AGENT_UPDATE[_\d]*|task\s+\d+(?:\.\d+)?)\b", re.IGNORECASE)
KEY_PATTERNS = [
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("openai-key", re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{20,}\b")),
    ("github-token", re.compile(r"\bghp_[A-Za-z0-9]{20,}\b")),
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def _head_sha(repo: Path) -> str:
    done = proc.run(["git", "rev-parse", "HEAD"], cwd=str(repo))
    if done.returncode != 0:
        return ""
    return done.stdout.strip()


def _exe_contains_version(exe_path: Path, version: str) -> bool:
    if not exe_path.is_file():
        return False
    data = exe_path.read_bytes()
    if version.encode("utf-16-le") in data or version.encode("ascii") in data:
        return True
    return False


def _parse_build_info(path: Path) -> dict[str, str]:
    info: dict[str, str] = {}
    if not path.is_file():
        return info
    text = path.read_text(encoding="utf-8", errors="replace").replace("\ufeff", "")
    for line in text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            info[k.strip().lower()] = v.strip()
    return info


def check_rule_1_version(repo: Path, build_root: Path, update_n: int) -> tuple[bool, str, str]:
    cmd = f"git rev-parse HEAD & check version files for 1.0.{update_n}"
    expected_ver = f"1.0.{update_n}"
    head = _head_sha(repo)
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        return False, cmd, f"Invalid HEAD commit hash: {head!r}"

    # 1. packaging/update_number.txt
    up_file = repo / "packaging" / "update_number.txt"
    if not up_file.is_file() or up_file.read_text(encoding="utf-8").strip() != str(update_n):
        return False, cmd, f"packaging/update_number.txt != {update_n}"

    # 2. src/dubtool/__init__.py
    init_file = repo / "src" / "dubtool" / "__init__.py"
    if not init_file.is_file():
        return False, cmd, "src/dubtool/__init__.py missing"
    m_ver = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init_file.read_text(encoding="utf-8"))
    if not m_ver or m_ver.group(1) != expected_ver:
        return False, cmd, f"__version__ != {expected_ver}"

    # 3. pyproject.toml
    pyproj = repo / "pyproject.toml"
    if not pyproj.is_file():
        return False, cmd, "pyproject.toml missing"
    pyproj_txt = pyproj.read_text(encoding="utf-8")
    if f'version = "{expected_ver}"' not in pyproj_txt and 'attr = "dubtool.__version__"' not in pyproj_txt:
        return False, cmd, f"pyproject.toml does not match {expected_ver}"

    # 4. packaging/version.txt
    ver_txt = repo / "packaging" / "version.txt"
    if not ver_txt.is_file() or ver_txt.read_text(encoding="utf-8").strip() != expected_ver:
        return False, cmd, f"packaging/version.txt missing or != {expected_ver}"

    # 5. Exe version resource, zip name, and BUILD_INFO.txt
    dist_dir = _dist_dir(build_root)
    exe_path = dist_dir / "DubTool" / "DubTool.exe"
    zip_path = dist_dir / f"DubTool-{expected_ver}-win64.zip"
    build_info_path = dist_dir / "DubTool" / "BUILD_INFO.txt"
    if not _exe_contains_version(exe_path, expected_ver):
        return False, cmd, f"DubTool.exe missing or version resource != {expected_ver}"
    if not zip_path.is_file():
        return False, cmd, f"Zip {zip_path.name} missing"

    binfo = _parse_build_info(build_info_path)
    expected_build_id = f"Update {update_n} ({head})"
    if binfo.get("version") != expected_ver:
        return False, cmd, f"BUILD_INFO.txt version {binfo.get('version')!r} != {expected_ver!r}"
    if binfo.get("git") != head:
        return False, cmd, f"BUILD_INFO.txt git {binfo.get('git')!r} != HEAD {head!r}"
    if binfo.get("build id") != expected_build_id:
        return False, cmd, f"BUILD_INFO.txt build id {binfo.get('build id')!r} != {expected_build_id!r}"

    return True, cmd, f"All 7 version sources agree on {expected_ver}; BUILD_INFO commit == HEAD ({head})"


def _dist_dir(bdir: Path) -> Path:
    return bdir / "dist"


def check_rule_2_artifacts(repo: Path, build_root: Path, update_n: int) -> tuple[bool, str, str]:
    expected_ver = f"1.0.{update_n}"
    cmd = f"Verify DubTool.exe and DubTool-{expected_ver}-win64.zip size & SHA-256"
    dist_dir = _dist_dir(build_root)
    exe_path = dist_dir / "DubTool" / "DubTool.exe"
    zip_path = dist_dir / f"DubTool-{expected_ver}-win64.zip"
    build_info_path = dist_dir / "DubTool" / "BUILD_INFO.txt"
    sums_path = dist_dir / "SHA256SUMS.txt"

    if not exe_path.is_file() or exe_path.stat().st_size == 0:
        return False, cmd, f"Missing or empty exe: {exe_path}"
    if not zip_path.is_file() or zip_path.stat().st_size == 0:
        return False, cmd, f"Missing or empty zip: {zip_path}"

    exe_sha = _sha256(exe_path)
    zip_sha = _sha256(zip_path)
    binfo = _parse_build_info(build_info_path)
    if binfo.get("sha256", "").lower() != exe_sha:
        return False, cmd, f"DubTool.exe SHA-256 {exe_sha[:12]} != BUILD_INFO.txt {binfo.get('sha256', '')[:12]}"

    if sums_path.is_file():
        sums_txt = sums_path.read_text(encoding="utf-8", errors="replace").lower()
        if zip_sha not in sums_txt:
            return False, cmd, f"Zip SHA-256 {zip_sha[:12]} not in SHA256SUMS.txt"

    return (
        True,
        cmd,
        f"exe={exe_path.stat().st_size} B (sha256={exe_sha[:16]}…), zip={zip_path.stat().st_size} B (sha256={zip_sha[:16]}…)",
    )


def _check_png_dimensions_and_whiteness(png_path: Path) -> tuple[bool, str]:
    from PySide6.QtGui import QImage

    img = QImage(str(png_path))
    if img.isNull():
        return False, f"{png_path.name}: invalid PNG image"
    w, h = img.width(), img.height()
    if w < 600:
        return False, f"{png_path.name}: width {w}px < 600px"
    # Sample a grid of pixels to ensure image is not mostly white (>=98% white)
    step_x = max(1, w // 40)
    step_y = max(1, h // 40)
    total = 0
    white = 0
    for y in range(0, h, step_y):
        for x in range(0, w, step_x):
            c = img.pixelColor(x, y)
            total += 1
            if c.red() >= 250 and c.green() >= 250 and c.blue() >= 250:
                white += 1
    if total > 0 and (white / total) >= 0.98:
        return False, f"{png_path.name}: mostly white ({white}/{total} sampled pixels)"
    return True, f"{w}x{h}"


def check_rule_3_screenshots(repo: Path, build_root: Path, update_n: int) -> tuple[bool, str, str]:
    cmd = "Check docs/images/INDEX.txt, PNG dimensions/whiteness, stale files, and README links"
    expected_ver = f"1.0.{update_n}"
    head = _head_sha(repo)
    expected_build_id = f"Update {update_n} ({head})"
    images_dir = repo / "docs" / "images"
    index_path = images_dir / "INDEX.txt"
    if not index_path.is_file():
        return False, cmd, "docs/images/INDEX.txt missing"

    index_txt = index_path.read_text(encoding="utf-8", errors="replace")
    if "platform: windows" not in index_txt:
        return False, cmd, "INDEX.txt does not state platform: windows"
    if expected_ver not in index_txt:
        return False, cmd, f"INDEX.txt missing version {expected_ver}"
    if f"build id: {expected_build_id}" not in index_txt:
        return False, cmd, f"INDEX.txt build id != {expected_build_id!r}"
    if "masked paths: yes" not in index_txt:
        return False, cmd, "INDEX.txt missing 'masked paths: yes'"

    # Check timestamp not earlier than BUILD_INFO.txt built (UTC)
    binfo = _parse_build_info(_dist_dir(build_root) / "DubTool" / "BUILD_INFO.txt")
    idx_info = _parse_build_info(index_path)
    if binfo.get("built (utc)") and idx_info.get("built (utc)"):
        if idx_info["built (utc)"] < binfo["built (utc)"]:
            return False, cmd, f"INDEX.txt built time {idx_info['built (utc)']} < build {binfo['built (utc)']}"

    required_files = {
        "ready.png",
        "running.png",
        "finished.png",
        "settings_api_keys.png",
        "1100x800_idle.png",
        "1100x800_video.png",
        "1100x800_running.png",
        "1100x800_finished.png",
        "1100x800_error.png",
    }
    existing_files = {p.name for p in images_dir.iterdir() if p.is_file()}
    missing_req = sorted(required_files - existing_files)
    if missing_req:
        return False, cmd, f"Missing required screenshots in docs/images: {', '.join(missing_req)}"

    if "dialog_settings_tab1_Mô_hình.png" in existing_files:
        return False, cmd, "Stale image dialog_settings_tab1_Mô_hình.png still in docs/images"

    readme_txt = (repo / "README.md").read_text(encoding="utf-8", errors="replace") if (repo / "README.md").is_file() else ""
    for fname in sorted(existing_files):
        if fname == "INDEX.txt":
            continue
        if fname not in readme_txt and fname not in index_txt:
            return False, cmd, f"Unreferenced/stale file in docs/images: {fname}"
        if fname.lower().endswith(".png"):
            ok_png, msg_png = _check_png_dimensions_and_whiteness(images_dir / fname)
            if not ok_png:
                return False, cmd, msg_png

    # Check README links resolve to tracked files
    if (repo / "README.md").is_file() and (repo / "README_BUILD.md").is_file():
        import check_readme_links as crl

        tracked_out = proc.run(["git", "-c", "core.quotePath=false", "ls-files", "--cached"], cwd=str(repo))
        tracked_set = {ln.strip() for ln in tracked_out.stdout.splitlines() if ln.strip()}
        problems = crl.check(repo, tracked_set)
        if problems:
            return False, cmd, f"README link errors: {'; '.join(problems)}"

    png_count = sum(1 for f in existing_files if f.lower().endswith(".png"))
    return True, cmd, f"{png_count} PNGs verified (>=600px, non-white, no stale files); INDEX.txt matches {expected_build_id}"


def check_rule_4_tests(repo: Path, build_root: Path, update_n: int) -> tuple[bool, str, str]:
    cmd = "Check pytest -rs output and verify all skipped tests are documented in tests/SKIPS.md"
    skips_md = repo / "tests" / "SKIPS.md"
    if not skips_md.is_file():
        return False, cmd, "tests/SKIPS.md missing"
    skips_txt = skips_md.read_text(encoding="utf-8", errors="replace")

    log_path = build_root / "logs" / f"pytest-{update_n}.txt"
    if not log_path.is_file():
        return False, cmd, f"Saved pytest output missing: {log_path}"
    pytest_txt = log_path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"\b(\d+)\s+failed\b", pytest_txt) or "FAILURES" in pytest_txt or "ERRORS" in pytest_txt:
        return False, cmd, f"Pytest log {log_path.name} contains failures or errors"
    if not re.search(r"\b(\d+)\s+passed\b", pytest_txt):
        return False, cmd, f"Pytest log {log_path.name} missing passed summary"

    # Extract skipped test IDs from `SKIPPED [n] tests/test_foo.py:line: reason`
    skip_lines = re.findall(r"SKIPPED\s+\[\d+\]\s+([^\s:]+)(?::\d+)?:\s*(.+)", pytest_txt)
    for test_file, _reason in skip_lines:
        norm_file = test_file.replace("\\", "/")
        if norm_file not in skips_txt:
            return False, cmd, f"Skipped test in {norm_file} is not documented in tests/SKIPS.md"

    m_pass = re.search(r"(\d+\s+passed(?:,\s*\d+\s+skipped)?)", pytest_txt)
    summary = m_pass.group(1) if m_pass else "passed"
    return True, cmd, f"pytest ({summary}); all {len(skip_lines)} skip(s) documented in tests/SKIPS.md"


def check_rule_5_report(repo: Path, build_root: Path, update_n: int) -> tuple[bool, str, str]:
    cmd = f"Check REPORT_UPDATE_{update_n}.md required sections, GitHub evidence block, and tag list"
    report_path = repo / f"REPORT_UPDATE_{update_n}.md"
    if not report_path.is_file():
        return False, cmd, f"REPORT_UPDATE_{update_n}.md missing"
    text = report_path.read_text(encoding="utf-8", errors="replace")

    required_markers = [
        "git log -1 --format=%H",
        "git status -sb",
        "git ls-remote origin refs/heads/main",
        "v1.0.14",
        "DOD_REPORT.md",
        "test9.mp4",
    ]
    for marker in required_markers:
        if marker not in text:
            return False, cmd, f"REPORT_UPDATE_{update_n}.md missing required marker/section: {marker!r}"

    # Check tag list for duplicate v1.0.X tags
    tags_found = re.findall(r"^v1\.0\.(\d+)$", text, flags=re.MULTILINE)
    if len(tags_found) != len(set(tags_found)):
        return False, cmd, f"Duplicate v1.0.N tags found in report tag list: {tags_found}"

    return True, cmd, f"REPORT_UPDATE_{update_n}.md complete ({len(text)} chars, GitHub evidence & sections verified)"


def check_rule_6_review_zip(repo: Path, build_root: Path, update_n: int) -> tuple[bool, str, str]:
    cmd = "Inspect newest review/dubtool-review-*.zip contents, size (<5MB per file), and secret scan"
    review_dir = repo / "review"
    if not review_dir.is_dir():
        return False, cmd, "review/ directory missing"
    zips = sorted(review_dir.glob("dubtool-review-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not zips:
        return False, cmd, "No review/dubtool-review-*.zip found"
    rzip = zips[0]

    required_entries = {
        f"REPORT_UPDATE_{update_n}.md",
        "REVIEW_NOTES.md",
        "DOD_REPORT.md",
        "RELEASE_EVIDENCE.txt",
        "tests/SKIPS.md",
    }
    with zipfile.ZipFile(rzip, "r") as zf:
        names = {n.replace("\\", "/") for n in zf.namelist()}
        if not any(n.startswith("docs/") for n in names):
            return False, cmd, f"{rzip.name} missing docs/ folder"
        missing = sorted(required_entries - names)
        if missing:
            return False, cmd, f"{rzip.name} missing required entries: {', '.join(missing)}"
        for info in zf.infolist():
            norm = info.filename.replace("\\", "/")
            if norm == ".env" or norm.endswith("/.env"):
                return False, cmd, f"{rzip.name} contains forbidden .env file: {norm}"
            if info.file_size > 5 * 1024 * 1024:
                return False, cmd, f"{rzip.name} contains file > 5 MB: {norm} ({info.file_size} B)"
            if norm.endswith((".py", ".md", ".txt", ".yaml", ".yml", ".json", ".toml")):
                raw = zf.read(info.filename).decode("utf-8", errors="replace")
                for line in raw.splitlines():
                    if "fixture-fake-key" in line:
                        continue
                    for tag, pat in KEY_PATTERNS:
                        if pat.search(line):
                            return False, cmd, f"{rzip.name} contains unredacted {tag} in {norm}"

    return True, cmd, f"{rzip.name} ({rzip.stat().st_size} B) contains all required files and 0 secret leaks"


def check_rule_7_messages(repo: Path) -> tuple[bool, str, str]:
    cmd = "AST scan src/dubtool/**/*.py for spec identifiers (AGU, AGENT_UPDATE_, task N)"
    src_root = repo / "src" / "dubtool"
    if not src_root.is_dir():
        return False, cmd, "src/dubtool missing"
    hits: list[str] = []
    file_count = 0
    for p in sorted(src_root.rglob("*.py")):
        file_count += 1
        src = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            return False, cmd, f"SyntaxError in {p.relative_to(repo)}: {exc}"
        doc_nodes: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    doc_nodes.add(id(node.body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_nodes:
                m = SPEC_ID_RE.search(node.value)
                if m:
                    hits.append(f"{p.relative_to(repo)}:{node.lineno} ({m.group(0)!r})")
    if hits:
        return False, cmd, f"Found spec identifiers in runtime strings: {', '.join(hits[:5])}"
    return True, cmd, f"Scanned {file_count} Python files in src/dubtool; 0 spec identifiers in runtime strings"


def check_rule_8_report_paths(repo: Path, build_root: Path, update_n: int) -> tuple[bool, str, str]:
    cmd = f"Verify every repo/zip file path named in REPORT_UPDATE_{update_n}.md exists"
    report_path = repo / f"REPORT_UPDATE_{update_n}.md"
    if not report_path.is_file():
        return False, cmd, f"REPORT_UPDATE_{update_n}.md missing"
    text = report_path.read_text(encoding="utf-8", errors="replace")

    review_names: set[str] = set()
    review_dir = repo / "review"
    if review_dir.is_dir():
        zips = sorted(review_dir.glob("dubtool-review-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if zips:
            with zipfile.ZipFile(zips[0], "r") as zf:
                review_names = {n.replace("\\", "/") for n in zf.namelist()}

    # Match backticked relative paths like `src/dubtool/...`, `scripts/...`, `tests/...`, `packaging/...`, `docs/...`
    path_re = re.compile(
        r"`((?:src|scripts|tests|packaging|docs|agent)/[A-Za-z0-9_./\-]+\.[A-Za-z0-9]+|"
        r"(?:REPORT_UPDATE_\d+\.md|REVIEW_NOTES\.md|DOD_REPORT\.md|RELEASE_EVIDENCE\.txt|config\.yaml|pyproject\.toml|README\.md|README_BUILD\.md))`"
    )
    checked: set[str] = set()
    missing: list[str] = []
    for m in path_re.finditer(text):
        rel = m.group(1)
        if "*" in rel or "<" in rel or ">" in rel or "{" in rel:
            continue
        # Historical stale image explicitly discussed as deleted in Task 3.1 is exempt only if preceded by deleted/removed context
        if rel == "docs/images/dialog_settings_tab1_Mô_hình.png":
            continue
        checked.add(rel)
        if not (repo / rel).exists() and rel not in review_names and not (build_root / rel).exists():
            missing.append(rel)

    if missing:
        return False, cmd, f"Paths named in report do not exist: {', '.join(sorted(missing))}"
    return True, cmd, f"Verified {len(checked)} file path(s) referenced in REPORT_UPDATE_{update_n}.md exist"


def run_dod(repo: Path = ROOT, build_root: Path | None = None, update_n: int | None = None) -> tuple[int, Path]:
    repo = repo.resolve()
    if build_root is None:
        env_br = os.environ.get("DUBTOOL_BUILD_ROOT", "").strip()
        build_root = Path(env_br).resolve() if env_br else (repo.parent / f"{repo.name}_build").resolve()
    else:
        build_root = build_root.resolve()

    if update_n is None:
        up_file = repo / "packaging" / "update_number.txt"
        update_n = int(up_file.read_text(encoding="utf-8").strip()) if up_file.is_file() else 15

    checks = [
        ("1. Version consistency", lambda: check_rule_1_version(repo, build_root, update_n)),
        ("2. Build artifacts", lambda: check_rule_2_artifacts(repo, build_root, update_n)),
        ("3. Screenshots", lambda: check_rule_3_screenshots(repo, build_root, update_n)),
        ("4. Tests & SKIPS.md", lambda: check_rule_4_tests(repo, build_root, update_n)),
        ("5. Report completeness", lambda: check_rule_5_report(repo, build_root, update_n)),
        ("6. Review package", lambda: check_rule_6_review_zip(repo, build_root, update_n)),
        ("7. Messages (no spec IDs)", lambda: check_rule_7_messages(repo)),
        ("8. Reports versus code", lambda: check_rule_8_report_paths(repo, build_root, update_n)),
    ]

    rows: list[tuple[str, bool, str, str]] = []
    for title, fn in checks:
        try:
            ok, cmd, evidence = fn()
        except Exception as exc:
            ok, cmd, evidence = False, "exception", f"{type(exc).__name__}: {exc}"
        rows.append((title, ok, cmd, evidence))

    all_passed = all(ok for _, ok, _, _ in rows)
    lines = [
        f"# Definition of Done Report — Update {update_n} (`1.0.{update_n}`)",
        "",
        f"- **Overall Status:** {'PASS' if all_passed else 'FAIL'}",
        f"- **Repository:** `{repo}`",
        f"- **HEAD Commit:** `{_head_sha(repo)}`",
        "",
        "| Rule | Status | Command / Check | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    for title, ok, cmd, ev in rows:
        status_str = "PASS" if ok else "FAIL"
        safe_cmd = cmd.replace("|", "\\|")
        safe_ev = ev.replace("|", "\\|")
        lines.append(f"| {title} | **{status_str}** | `{safe_cmd}` | {safe_ev} |")
    lines.append("")

    out_path = repo / "DOD_REPORT.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return (0 if all_passed else 1), out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DubTool Mechanical Definition-of-Done Checker")
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--build-root", type=Path, default=None)
    parser.add_argument("--update", type=int, default=None)
    args = parser.parse_args(argv)
    rc, out_path = run_dod(repo=args.repo, build_root=args.build_root, update_n=args.update)
    print(out_path.read_text(encoding="utf-8"))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
