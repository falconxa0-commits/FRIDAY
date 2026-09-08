#!/usr/bin/env python3
"""Create a complete pre-M4 frozen snapshot of the FRIDAY repository.

Per the Biological Ascension Forge Ω directive:
- Must happen BEFORE any M4 engineering change
- Must include all source, tests, config, scripts, docs, release artifacts
- Must exclude: dependency caches, build dirs, temp files, secrets, machine caches
- Must produce SHA-256 checksum
- Must verify ZIP integrity
- Must record everything in worklog

Output: /home/z/my-project/download/FRIDAY_AGE_V_PRE_M4_ASCENSION_SNAPSHOT_<timestamp>.zip
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

REPO_ROOT = Path("/home/z/my-project/work/FRIDAY")
DOWNLOAD_DIR = Path("/home/z/my-project/download")
TIMESTAMP = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
ZIP_NAME = f"FRIDAY_AGE_V_PRE_M4_ASCENSION_SNAPSHOT_{TIMESTAMP}.zip"
ZIP_PATH = DOWNLOAD_DIR / ZIP_NAME

EXCLUDE_DIRS = {
    "__pycache__", ".pytest_cache", ".cache", "node_modules",
    ".venv", "venv", ".eggs", ".tox", ".mypy_cache", ".ruff_cache",
    "build", "dist",
}

EXCLUDE_FILE_PATTERNS = {
    "*.pyc", "*.pyo", "*.pyd", ".DS_Store", "Thumbs.db",
    "*.swp", "*.swo", "*~", "*.log",
}


def should_exclude(path):
    parts = path.parts
    for excl in EXCLUDE_DIRS:
        if excl in parts:
            return True
    name = path.name
    for pat in EXCLUDE_FILE_PATTERNS:
        if pat.startswith("*"):
            suffix = pat[1:]
            if name.endswith(suffix):
                return True
        elif name == pat:
            return True
    return False


def collect_files():
    files = []
    for root, dirs, filenames in os.walk(REPO_ROOT):
        root_path = Path(root)
        dirs[:] = [d for d in dirs if not should_exclude(root_path / d)]
        for fname in filenames:
            fpath = root_path / fname
            if should_exclude(fpath):
                continue
            files.append(fpath)
    return files


def create_zip(files):
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_count = 0
    total_bytes = 0
    with ZipFile(ZIP_PATH, "w", ZIP_DEFLATED, compresslevel=6) as zf:
        for fpath in files:
            arcname = fpath.relative_to(REPO_ROOT.parent)
            zf.write(fpath, arcname)
            file_count += 1
            total_bytes += fpath.stat().st_size
    return file_count, total_bytes


def compute_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_zip(path):
    try:
        with ZipFile(path, "r") as zf:
            bad = zf.testzip()
            if bad is not None:
                return False, 0, [f"CORRUPT: {bad}"]
            names = zf.namelist()
            expected = [
                "FRIDAY/core/living_memory/manager.py",
                "FRIDAY/core/civilization/citizen.py",
                "FRIDAY/core/runtime/v5/distributed_runtime.py",
                "FRIDAY/release/m3/AGE_V_MILESTONE3_CERTIFICATION.md",
                "FRIDAY/tests/test_living_memory.py",
                "FRIDAY/core/ledger.py",
                "FRIDAY/core/governance/constitution.py",
            ]
            missing = [e for e in expected if e not in names]
            if missing:
                return False, len(names), [f"MISSING: {m}" for m in missing]
            return True, len(names), names[:3] + ["..."] + names[-3:]
    except Exception as e:
        return False, 0, [f"EXCEPTION: {e}"]


def main():
    print("=" * 72)
    print("FRIDAY AGE V — PRE-M4 ASCENSION SNAPSHOT")
    print("=" * 72)
    print(f"Repository: {REPO_ROOT}")
    print(f"Output:     {ZIP_PATH}")
    print()

    if not REPO_ROOT.exists():
        print(f"ERROR: Repository not found at {REPO_ROOT}")
        return 1

    print("[1/4] Collecting files (excluding caches, build dirs, temp files)...")
    files = collect_files()
    print(f"      Collected {len(files)} files")

    print("[2/4] Creating ZIP archive...")
    file_count, total_bytes = create_zip(files)
    zip_size = ZIP_PATH.stat().st_size
    print(f"      Wrote {file_count} files ({total_bytes:,} bytes uncompressed)")
    print(f"      ZIP size: {zip_size:,} bytes ({zip_size / 1024 / 1024:.2f} MB)")

    print("[3/4] Verifying ZIP integrity...")
    ok, inside_count, sample = verify_zip(ZIP_PATH)
    if not ok:
        print(f"      ERROR: ZIP verification failed: {sample}")
        return 1
    print(f"      ZIP readable, contains {inside_count} entries")
    print(f"      Expected structure verified")

    print("[4/4] Computing SHA-256 checksum...")
    sha256 = compute_sha256(ZIP_PATH)
    print(f"      SHA-256: {sha256}")

    print()
    print("=" * 72)
    print("PRE-ASCENSION SNAPSHOT: VERIFIED")
    print(f"PATH:    {ZIP_PATH}")
    print(f"SIZE:    {zip_size:,} bytes ({zip_size / 1024 / 1024:.2f} MB)")
    print(f"FILES:   {file_count} files ({inside_count} ZIP entries)")
    print(f"SHA256:  {sha256}")
    print("=" * 72)

    print()
    print("--- WORKLOG ENTRY ---")
    print(f"snapshot_path: {ZIP_PATH}")
    print(f"snapshot_size_bytes: {zip_size}")
    print(f"snapshot_file_count: {file_count}")
    print(f"snapshot_sha256: {sha256}")
    print(f"snapshot_timestamp_utc: {TIMESTAMP}")
    print("--- END WORKLOG ENTRY ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
