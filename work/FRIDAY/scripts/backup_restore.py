#!/usr/bin/env python3
"""FRIDAY Backup & Restore — disaster recovery for engineering data.

Usage:
    python scripts/backup_restore.py backup [--output PATH]
    python scripts/backup_restore.py restore --input PATH
    python scripts/backup_restore.py list
    python scripts/backup_restore.py verify --input PATH

Backs up:
    - .friday/ (tasks, knowledge, receipts, releases, research)
    - action_ledger_chain.json (audit chain)
    - action_ledger_pending.json (pending actions)
    - cost_data.json (cost tracking)
    - friday_memories_*.json (memory exports)

Does NOT back up:
    - Source code (use git)
    - .env (contains secrets — back up separately with encryption)
    - Virtual environments
"""
import argparse
import json
import os
import shutil
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_ITEMS = [
    ".friday",
    "action_ledger_chain.json",
    "action_ledger_pending.json",
    "cost_data.json",
]
BACKUP_GLOB_PATTERNS = ["friday_memories_*.json"]


def create_backup(output_path: str = None) -> Path:
    """Create a tar.gz backup of all engineering data."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if output_path is None:
        output_path = str(PROJECT_ROOT / f"friday_backup_{timestamp}.tar.gz")

    # Collect files to back up
    files_to_backup = []
    for item in BACKUP_ITEMS:
        path = PROJECT_ROOT / item
        if path.exists():
            files_to_backup.append((path, item))
    for pattern in BACKUP_GLOB_PATTERNS:
        for path in PROJECT_ROOT.glob(pattern):
            files_to_backup.append((path, path.name))

    if not files_to_backup:
        print("WARNING: No engineering data found to back up.")
        return Path(output_path)

    # Create manifest
    manifest = {
        "backup_timestamp": timestamp,
        "backup_date": datetime.now().isoformat(),
        "project_root": str(PROJECT_ROOT),
        "items": [],
    }

    # Create tar.gz
    with tarfile.open(output_path, "w:gz") as tar:
        for source_path, arcname in files_to_backup:
            if source_path.is_dir():
                # Add directory recursively
                for file_path in sorted(source_path.rglob("*")):
                    if file_path.is_file():
                        rel = file_path.relative_to(PROJECT_ROOT)
                        tar.add(file_path, arcname=str(rel))
                        manifest["items"].append(str(rel))
            else:
                tar.add(source_path, arcname=arcname)
                manifest["items"].append(arcname)

        # Add manifest to archive
        manifest_data = json.dumps(manifest, indent=2).encode()
        import io
        info = tarfile.TarInfo(name="backup_manifest.json")
        info.size = len(manifest_data)
        tar.addfile(info, io.BytesIO(manifest_data))

    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    print(f"✓ Backup created: {output_path} ({size_mb:.1f} MB)")
    print(f"  Items: {len(manifest['items'])} files/directories")
    return Path(output_path)


def restore_backup(input_path: str) -> None:
    """Restore engineering data from a backup archive."""
    if not Path(input_path).exists():
        print(f"ERROR: Backup file not found: {input_path}")
        sys.exit(1)

    with tarfile.open(input_path, "r:gz") as tar:
        # Read manifest first
        try:
            manifest_file = tar.extractfile("backup_manifest.json")
            manifest = json.loads(manifest_file.read())
            print(f"Backup from: {manifest.get('backup_date', 'unknown')}")
            print(f"Items: {len(manifest.get('items', []))} files")
        except KeyError:
            print("WARNING: No manifest in backup, extracting all files")
            manifest = {"items": []}

        # Extract all files to project root
        print("Extracting...")
        tar.extractall(PROJECT_ROOT)

    print(f"✓ Restore complete. Files restored to {PROJECT_ROOT}")


def list_backups() -> None:
    """List available backups in the project root."""
    backups = sorted(PROJECT_ROOT.glob("friday_backup_*.tar.gz"))
    if not backups:
        print("No backups found.")
        return

    print(f"Found {len(backups)} backup(s):")
    for bp in backups:
        size_mb = bp.stat().st_size / (1024 * 1024)
        print(f"  {bp.name}  ({size_mb:.1f} MB)")


def verify_backup(input_path: str) -> bool:
    """Verify a backup archive is valid and readable."""
    if not Path(input_path).exists():
        print(f"ERROR: Backup file not found: {input_path}")
        return False

    try:
        with tarfile.open(input_path, "r:gz") as tar:
            members = tar.getmembers()
            print(f"✓ Archive is valid: {len(members)} entries")

            # Check for manifest
            if "backup_manifest.json" in [m.name for m in members]:
                manifest_file = tar.extractfile("backup_manifest.json")
                manifest = json.loads(manifest_file.read())
                print(f"  Backup date: {manifest.get('backup_date', 'unknown')}")
                print(f"  Items: {len(manifest.get('items', []))} files")
            else:
                print("  WARNING: No manifest found")

            # Verify each file is readable
            errors = 0
            for member in members:
                if member.isfile():
                    try:
                        f = tar.extractfile(member)
                        if f:
                            f.read()
                    except Exception as exc:
                        print(f"  ERROR reading {member.name}: {exc}")
                        errors += 1

            if errors == 0:
                print(f"✓ All {len(members)} entries verified successfully")
                return True
            else:
                print(f"✗ {errors} entries failed verification")
                return False

    except tarfile.TarError as exc:
        print(f"ERROR: Invalid archive: {exc}")
        return False


def main():
    parser = argparse.ArgumentParser(description="FRIDAY backup & restore")
    sub = parser.add_subparsers(dest="command")

    bp = sub.add_parser("backup", help="Create a backup")
    bp.add_argument("--output", "-o", default=None)

    rp = sub.add_parser("restore", help="Restore from a backup")
    rp.add_argument("--input", "-i", required=True)

    sub.add_parser("list", help="List available backups")

    vp = sub.add_parser("verify", help="Verify a backup archive")
    vp.add_argument("--input", "-i", required=True)

    args = parser.parse_args()

    if args.command == "backup":
        create_backup(args.output)
    elif args.command == "restore":
        restore_backup(args.input)
    elif args.command == "list":
        list_backups()
    elif args.command == "verify":
        verify_backup(args.input)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
