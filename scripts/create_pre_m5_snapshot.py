#!/usr/bin/env python3
"""Create a complete pre-M5 frozen snapshot of the FRIDAY repository."""
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
ZIP_NAME = f"FRIDAY_AGE_V_PRE_M5_ASCENSION_SNAPSHOT_{TIMESTAMP}.zip"
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
            if name.endswith(pat[1:]):
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
                "FRIDAY/core/knowledge_graph/manager.py",
                "FRIDAY/core/living_memory/manager.py",
                "FRIDAY/core/civilization/citizen.py",
                "FRIDAY/core/runtime/v5/distributed_runtime.py",
                "FRIDAY/release/m4/AGE_V_MILESTONE4_CERTIFICATION.md",
                "FRIDAY/release/m3/AGE_V_MILESTONE3_CERTIFICATION.md",
                "FRIDAY/tests/test_knowledge_graph.py",
                "FRIDAY/tests/test_living_memory.py",
            ]
            missing = [e for e in expected if e not in names]
            if missing:
                return False, len(names), [f"MISSING: {m}" for m in missing]
            return True, len(names), []
    except Exception as e:
        return False, 0, [f"EXCEPTION: {e}"]


def main():
    print("=" * 72)
    print("FRIDAY AGE V — PRE-M5 ASCENSION SNAPSHOT")
    print("=" * 72)
    print(f"Repository: {REPO_ROOT}")
    print(f"Output:     {ZIP_PATH}")
    print()

    files = collect_files()
    print(f"[1/4] Collected {len(files)} files")

    file_count, total_bytes = create_zip(files)
    zip_size = ZIP_PATH.stat().st_size
    print(f"[2/4] Wrote {file_count} files ({total_bytes:,} bytes uncompressed)")
    print(f"      ZIP size: {zip_size:,} bytes ({zip_size / 1024 / 1024:.2f} MB)")

    ok, inside_count, sample = verify_zip(ZIP_PATH)
    if not ok:
        print(f"[3/4] ERROR: ZIP verification failed: {sample}")
        return 1
    print(f"[3/4] ZIP readable, contains {inside_count} entries, structure verified")

    sha256 = compute_sha256(ZIP_PATH)
    print(f"[4/4] SHA-256: {sha256}")

    print()
    print("=" * 72)
    print("PRE-M5 SNAPSHOT: VERIFIED")
    print(f"PATH:    {ZIP_PATH}")
    print(f"SIZE:    {zip_size:,} bytes ({zip_size / 1024 / 1024:.2f} MB)")
    print(f"FILES:   {file_count}")
    print(f"SHA256:  {sha256}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
