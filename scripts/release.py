"""Single-entrypoint release pipeline for AGENT_UPDATE_15 Task 1 (2.1).

Usage:
    py scripts/release.py 15 [--dry-run] [--skip-build-if-matching]

Runs the 11-step fail-fast pipeline, logging every command and exit code to
`<build root>/logs/release-N.log`.
`--dry-run` performs steps 1-8 and stops before writing the stamp or pushing.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import dubtool  # noqa: E402
from dubtool import proc, workspace  # noqa: E402
import verify_release  # noqa: E402


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower()


class ReleaseLogger:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")

    def log(self, msg: str) -> None:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        line = f"[{stamp}] {msg}\n"
        print(line, end="", flush=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line)

    def run(
        self,
        cmd: list[str],
        *,
        cwd: Path = ROOT,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        check: bool = True,
    ) -> proc.CompletedProcess:
        display_cmd = " ".join(str(c) for c in cmd)
        self.log(f"RUN: {display_cmd}")
        run_env = (env or os.environ).copy()
        run_env.setdefault("PYTHONIOENCODING", "utf-8")
        run_env["DUBTOOL_HOOK_PYTHON"] = sys.executable.replace("\\", "/")
        done = proc.run(cmd, cwd=str(cwd), env=run_env, timeout=timeout)
        self.log(f"EXIT: {done.returncode} ({display_cmd})")
        if done.stdout:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(done.stdout + ("\n" if not done.stdout.endswith("\n") else ""))
        if done.stderr:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(done.stderr + ("\n" if not done.stderr.endswith("\n") else ""))
        if check and done.returncode != 0:
            raise RuntimeError(
                f"Command failed with exit code {done.returncode}: {display_cmd}\n"
                f"STDOUT:\n{done.stdout[-1000:]}\nSTDERR:\n{done.stderr[-1000:]}"
            )
        return done


def _parse_build_info(path: Path) -> dict[str, str]:
    info: dict[str, str] = {}
    if not path.is_file():
        return info
    for line in path.read_text(encoding="utf-8", errors="replace").replace("\ufeff", "").splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            info[k.strip().lower()] = v.strip()
    return info


def _write_stamp(update_n: int, head: str, zip_path: Path, review_zip: Path) -> Path:
    stamp_path = ROOT / ".git" / f"dubtool-release-ok-{update_n}"
    payload = {
        "update": update_n,
        "version": f"1.0.{update_n}",
        "head": head,
        "zip_path": str(zip_path),
        "zip_sha256": _sha256(zip_path),
        "review_zip_path": str(review_zip),
        "review_zip_sha256": _sha256(review_zip),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    stamp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return stamp_path


def _newest_review_zip() -> Path:
    review_dir = ROOT / "review"
    zips = sorted(review_dir.glob("dubtool-review-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not zips:
        raise RuntimeError("No review/dubtool-review-*.zip found after make_review_zip.py")
    return zips[0]


def run_pipeline(update_n: int, dry_run: bool = False, skip_build_if_matching: bool = False) -> int:
    build_root = workspace.build_root()
    logger = ReleaseLogger(build_root / "logs" / f"release-{update_n}.log")
    expected_ver = f"1.0.{update_n}"
    gh = verify_release.find_gh_bin()
    env = verify_release.gh_env()

    for loop_idx in range(2):
        logger.log(f"=== STEP 1: Preconditions (loop {loop_idx + 1}/2) ===")
        st = logger.run(["git", "status", "--porcelain"], env=env)
        if st.stdout.strip():
            raise RuntimeError(f"Working tree is not clean:\n{st.stdout}")
        br = logger.run(["git", "branch", "--show-current"], env=env).stdout.strip()
        if br != "main":
            raise RuntimeError(f"Current branch must be 'main', got {br!r}")
        logger.run(["git", "ls-remote", "--exit-code", "origin", "refs/heads/main"], env=env, timeout=30)
        logger.run([gh, "auth", "status"], env=env, timeout=30)

        up_txt = (ROOT / "packaging" / "update_number.txt").read_text(encoding="utf-8").strip()
        if up_txt != str(update_n):
            raise RuntimeError(f"packaging/update_number.txt is {up_txt!r}, expected {update_n!r}")
        if dubtool.__version__ != expected_ver:
            raise RuntimeError(f"dubtool.__version__ is {dubtool.__version__!r}, expected {expected_ver!r}")

        head = logger.run(["git", "rev-parse", "HEAD"], env=env).stdout.strip()
        logger.log(f"Preconditions OK: HEAD={head}, version={expected_ver}")

        logger.log("=== STEP 2: Full Offline Test Suite (pytest tests -q --timeout=120 -rs) ===")
        pytest_res = logger.run(
            [sys.executable, "-m", "pytest", "tests", "-q", "--timeout=120", "-rs"],
            env=env,
            timeout=900,
        )
        pytest_log_path = build_root / "logs" / f"pytest-{update_n}.txt"
        pytest_log_path.write_text(pytest_res.stdout + "\n" + pytest_res.stderr, encoding="utf-8")
        skips_md = (ROOT / "tests" / "SKIPS.md").read_text(encoding="utf-8")
        for test_file, reason in re.findall(r"SKIPPED\s+\[\d+\]\s+([^\s:]+)(?::\d+)?:\s*(.+)", pytest_res.stdout):
            norm_file = test_file.replace("\\", "/")
            if norm_file not in skips_md:
                raise RuntimeError(f"Undocumented skip in {norm_file}: {reason}")
        logger.log("Pytest suite & SKIPS.md check OK")

        logger.log("=== STEP 3: Preflight & History Secret Gates ===")
        logger.run([sys.executable, "scripts/preflight_gates.py"], env=env)
        logger.run([sys.executable, "scripts/scan_history_secrets.py"], env=env)

        logger.log("=== STEP 4: Build from Clean Commit ===")
        dist_dir = workspace.dist_dir()
        exe_path = dist_dir / "DubTool" / "DubTool.exe"
        zip_path = dist_dir / f"DubTool-{expected_ver}-win64.zip"
        binfo_path = dist_dir / "DubTool" / "BUILD_INFO.txt"
        binfo = _parse_build_info(binfo_path)

        if skip_build_if_matching and exe_path.is_file() and zip_path.is_file() and binfo.get("git") == head and binfo.get("version") == expected_ver:
            logger.log(f"Reusing existing clean build matching HEAD {head}")
        else:
            # Extract git archive HEAD into <build root>/tmp/release-src/ and build from clean commit
            rel_src = build_root / "tmp" / "release-src"
            if rel_src.exists():
                shutil.rmtree(rel_src, ignore_errors=True)
            # Re-point editable install back to ROOT after deleting rel_src
            logger.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "-q",
                    "-e",
                    ".",
                    "--no-deps",
                    "--cache-dir",
                    str(build_root / "pip-cache"),
                ],
                cwd=ROOT,
                env=env,
            )
            rel_src.mkdir(parents=True, exist_ok=True)
            tar_file = build_root / "tmp" / f"release-src-{head[:12]}.tar"
            logger.run(["git", "archive", "--format=tar", "-o", str(tar_file), "HEAD"], env=env)
            logger.run(["tar", "-xf", str(tar_file), "-C", str(rel_src)], env=env)
            tar_file.unlink(missing_ok=True)

            build_env = env.copy()
            build_env["DUBTOOL_GIT_HEAD"] = head
            build_env["DUBTOOL_BUILD_ROOT"] = str(build_root)
            logger.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(rel_src / "packaging" / "build_exe.ps1"),
                    "-SkipTests",
                    "-SkipScreenshots",
                ],
                cwd=rel_src,
                env=build_env,
                timeout=1200,
            )
            shutil.rmtree(rel_src, ignore_errors=True)
            # Re-point editable install back to ROOT after deleting rel_src
            logger.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "-q",
                    "-e",
                    ".",
                    "--no-deps",
                    "--cache-dir",
                    str(build_root / "pip-cache"),
                ],
                cwd=ROOT,
                env=env,
            )

        binfo = _parse_build_info(binfo_path)
        if binfo.get("git") != head:
            raise RuntimeError(f"BUILD_INFO.txt commit {binfo.get('git')!r} != HEAD {head!r}")
        logger.log(f"Build verified: BUILD_INFO.txt git == HEAD ({head})")

        logger.log("=== STEP 5: Sanitized Verification of Built Exe ===")
        shot_dir = workspace.screenshots_dir() / f"update_{update_n}_{head}"
        if shot_dir.exists():
            shutil.rmtree(shot_dir, ignore_errors=True)
        for flag_args in (
            ["--version"],
            ["--selftest"],
            ["--layout-check"],
            ["--gui-smoke"],
            ["--screenshots", str(shot_dir)],
        ):
            logger.run([str(exe_path), *flag_args], env=env, timeout=300)
        # Remove older screenshot dirs so newest_folder() resolves to shot_dir
        for d in workspace.screenshots_dir().iterdir():
            if d.is_dir() and d.resolve() != shot_dir.resolve():
                shutil.rmtree(d, ignore_errors=True)
        logger.run([sys.executable, "-m", "pytest", "tests/test_screenshots.py", "-q"], env=env, timeout=60)

        logger.log("=== STEP 6: Docs & README Images Sync ===")
        logger.run([sys.executable, "scripts/update_readme_images.py", str(shot_dir)], env=env)
        # Check if any tracked file in docs/images changed
        tracked_diff = logger.run(["git", "status", "--porcelain", "--untracked-files=no"], env=env).stdout.strip()
        if tracked_diff:
            if loop_idx == 1:
                raise RuntimeError(f"Tracked files still changed on second loop:\n{tracked_diff}")
            logger.log(f"Tracked docs/images changed; committing and restarting from Step 1:\n{tracked_diff}")
            logger.run(["git", "add", "-u", "docs/images"], env=env)
            logger.run(["git", "commit", "-m", f"Docs: sync README screenshots for Update {update_n}"], env=env)
            skip_build_if_matching = False
            continue
        logger.run([sys.executable, "scripts/check_readme_links.py"], env=env)
        break

    logger.log("=== STEP 7: Build Review Package ===")
    # Ensure placeholder or current DOD_REPORT.md and RELEASE_EVIDENCE.txt exist before initial review zip
    if not (ROOT / "RELEASE_EVIDENCE.txt").is_file():
        (ROOT / "RELEASE_EVIDENCE.txt").write_text(
            f"Pre-release evidence placeholder for v1.0.{update_n} ({head})\n", encoding="utf-8"
        )
    if not (ROOT / "DOD_REPORT.md").is_file():
        (ROOT / "DOD_REPORT.md").write_text(f"# Definition of Done — Update {update_n}\n", encoding="utf-8")
    logger.run([sys.executable, "scripts/make_review_zip.py", "--tag", f"update{update_n}"], env=env)

    logger.log("=== STEP 8: Mechanical Definition-of-Done Check ===")
    logger.run([sys.executable, "scripts/definition_of_done.py", "--update", str(update_n)], env=env)
    # Refresh review zip so it contains the freshly generated DOD_REPORT.md
    logger.run([sys.executable, "scripts/make_review_zip.py", "--tag", f"update{update_n}"], env=env)
    review_zip = _newest_review_zip()
    review_sha = _sha256(review_zip)
    logger.log(f"Review package ready: {review_zip.name} (SHA-256: {review_sha})")

    if dry_run:
        logger.log("DRY-RUN complete (stopped after Step 8 before writing stamp or publishing).")
        return 0

    logger.log("=== STEP 9: Write Release Gate Stamp ===")
    stamp_path = _write_stamp(update_n, head, zip_path, review_zip)
    logger.log(f"Wrote release stamp: {stamp_path}")

    logger.log("=== STEP 10: Publish to origin & GitHub Releases ===")
    logger.run(["git", "push", "origin", "main"], env=env, timeout=120)
    tag_name = f"v1.0.{update_n}"
    existing_tag = logger.run(["git", "tag", "-l", tag_name], env=env).stdout.strip()
    if not existing_tag:
        logger.run(["git", "tag", "-a", tag_name, head, "-m", f"DubTool {tag_name} (Update {update_n})"], env=env)
    logger.run(["git", "push", "origin", tag_name], env=env, timeout=120)

    # Render release notes from packaging/RELEASE_NOTES_TEMPLATE.md
    tmpl = (ROOT / "packaging" / "RELEASE_NOTES_TEMPLATE.md").read_text(encoding="utf-8")
    notes = tmpl.format(
        version=expected_ver,
        update=update_n,
        commit=head,
        zip_name=zip_path.name,
        zip_sha256=_sha256(zip_path),
    )
    notes_file = build_root / "tmp" / f"release-notes-{tag_name}.md"
    notes_file.parent.mkdir(parents=True, exist_ok=True)
    notes_file.write_text(notes, encoding="utf-8")
    sums_path = dist_dir / "SHA256SUMS.txt"

    # Check if release already exists; if not, create it
    view_rc = logger.run([gh, "release", "view", tag_name], env=env, check=False).returncode
    if view_rc != 0:
        logger.run(
            [
                gh,
                "release",
                "create",
                tag_name,
                str(zip_path),
                str(sums_path),
                "--title",
                f"DubTool {tag_name} (Update {update_n})",
                "--notes-file",
                str(notes_file),
            ],
            env=env,
            timeout=600,
        )
    else:
        logger.run(
            [
                gh,
                "release",
                "upload",
                tag_name,
                str(zip_path),
                str(sums_path),
                "--clobber",
            ],
            env=env,
            timeout=600,
        )
        logger.run(
            [
                gh,
                "release",
                "edit",
                tag_name,
                "--title",
                f"DubTool {tag_name} (Update {update_n})",
                "--notes-file",
                str(notes_file),
            ],
            env=env,
            timeout=120,
        )

    logger.log("=== STEP 11: Post-Release Verification ===")
    logger.run(
        [
            sys.executable,
            "scripts/verify_release.py",
            "--tag",
            tag_name,
            "--strict-head",
            "--write",
            str(ROOT / "RELEASE_EVIDENCE.txt"),
        ],
        env=env,
        timeout=600,
    )
    # Refresh DOD_REPORT.md and review zip with final RELEASE_EVIDENCE.txt and update stamp
    logger.run([sys.executable, "scripts/definition_of_done.py", "--update", str(update_n)], env=env)
    logger.run([sys.executable, "scripts/make_review_zip.py", "--tag", f"update{update_n}"], env=env)
    review_zip = _newest_review_zip()
    _write_stamp(update_n, head, zip_path, review_zip)
    logger.log(f"=== RELEASE {tag_name} COMPLETE AND VERIFIED ===")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DubTool Automated Release Pipeline")
    parser.add_argument("update", type=int, help="Update number N (e.g. 15)")
    parser.add_argument("--dry-run", action="store_true", help="Run steps 1-8 only")
    parser.add_argument("--skip-build-if-matching", action="store_true", help="Reuse existing clean build if git==HEAD")
    args = parser.parse_args(argv)
    return run_pipeline(args.update, dry_run=args.dry_run, skip_build_if_matching=args.skip_build_if_matching)


if __name__ == "__main__":
    raise SystemExit(main())
