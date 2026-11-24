"""Fetch and SHA-256 verify pinned external vendor assets (FFmpeg + Noto Sans).

Reads ``packaging/vendor_manifest.json`` and populates ``<build_root>/vendor/``
(resolved from ``DUBTOOL_BUILD_ROOT`` or sibling ``<project>_build``). Stdlib
only so it runs cleanly in both CI (Python 3.11 / 3.13) and local build shells
before installing project dependencies.

Usage:
    python scripts/fetch_vendor.py                   # fetch & verify all assets
    python scripts/fetch_vendor.py --only ffmpeg     # fetch & verify FFmpeg only
    python scripts/fetch_vendor.py --verify-only     # verify existing files only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "packaging" / "vendor_manifest.json"


def _resolve_build_root() -> Path:
    override = os.environ.get("DUBTOOL_BUILD_ROOT", "").strip()
    if override:
        br = Path(override).resolve()
    else:
        br = (ROOT.parent / f"{ROOT.name}_build").resolve()
    if br == ROOT or ROOT in br.parents:
        raise SystemExit(f"DUBTOOL_BUILD_ROOT must live outside the project root: {br}")
    return br


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def _download_with_retry(url: str, dest: Path, *, attempts: int = 3, timeout: int = 120) -> None:
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "DubTool-VendorFetch/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp, dest.open("wb") as out:
                shutil.copyfileobj(resp, out)
            return
        except Exception as exc:
            last_err = exc
            print(f"attempt {attempt}/{attempts} failed for {url}: {exc}", file=sys.stderr, flush=True)
            if attempt < attempts:
                time.sleep(3 * attempt)
    raise RuntimeError(f"failed to download {url} after {attempts} attempts: {last_err}")


def _ensure_asset(name: str, spec: dict, vendor_dir: Path, *, verify_only: bool) -> None:
    dest = vendor_dir / spec["dest"]
    expected_sha = spec["sha256"].lower()
    if dest.is_file():
        actual = _sha256_file(dest)
        if actual == expected_sha:
            print(f"[ok] {name}: {dest} ({dest.stat().st_size} bytes, sha256={actual[:16]}...)", flush=True)
            return
        if verify_only:
            raise SystemExit(
                f"SHA-256 mismatch for {name} ({dest}): expected {expected_sha}, got {actual}"
            )
        print(f"[refresh] {name}: hash mismatch ({actual[:12]} != {expected_sha[:12]}), re-fetching...", flush=True)
    elif verify_only:
        raise SystemExit(f"missing required vendor asset {name}: {dest}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(dest.parent), prefix=f".fetch-{name}-") as td:
        tmp_dir = Path(td)
        if "archive_member" in spec:
            zip_tmp = tmp_dir / "archive.zip"
            print(f"[fetch] {name}: {spec['url']}", flush=True)
            _download_with_retry(spec["url"], zip_tmp)
            expected_zip_sha = spec["zip_sha256"].lower()
            actual_zip_sha = _sha256_file(zip_tmp)
            if actual_zip_sha != expected_zip_sha:
                raise SystemExit(
                    f"ZIP SHA-256 mismatch for {name}: expected {expected_zip_sha}, got {actual_zip_sha}"
                )
            extracted = tmp_dir / "extracted.bin"
            member = spec["archive_member"]
            with zipfile.ZipFile(zip_tmp) as zf:
                with zf.open(member) as src, extracted.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
            staged = extracted
        else:
            staged = tmp_dir / "asset.bin"
            print(f"[fetch] {name}: {spec['url']}", flush=True)
            _download_with_retry(spec["url"], staged)

        actual_sha = _sha256_file(staged)
        if actual_sha != expected_sha:
            raise SystemExit(
                f"SHA-256 mismatch for {name}: expected {expected_sha}, got {actual_sha}"
            )
        os.replace(staged, dest)
        print(f"[verified] {name}: {dest} ({dest.stat().st_size} bytes, sha256={actual_sha})", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch & SHA-256 verify DubTool vendor assets.")
    parser.add_argument(
        "--vendor-dir",
        type=Path,
        default=None,
        help="Target vendor directory (defaults to <DUBTOOL_BUILD_ROOT>/vendor).",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Only fetch/verify specific asset key(s) from packaging/vendor_manifest.json.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Do not download; fail if any selected asset is missing or has a wrong SHA-256.",
    )
    args = parser.parse_args(argv)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assets: dict[str, dict] = manifest["assets"]
    selected = args.only or list(assets.keys())
    unknown = [k for k in selected if k not in assets]
    if unknown:
        raise SystemExit(f"unknown asset key(s): {unknown}; valid: {sorted(assets)}")

    vendor_dir = args.vendor_dir.resolve() if args.vendor_dir else (_resolve_build_root() / "vendor")
    vendor_dir.mkdir(parents=True, exist_ok=True)
    for key in selected:
        _ensure_asset(key, assets[key], vendor_dir, verify_only=args.verify_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
