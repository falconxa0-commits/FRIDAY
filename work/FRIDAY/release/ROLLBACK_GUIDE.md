# ROLLBACK GUIDE — FRIDAY Age IV v4.0.0

## When to Rollback

Rollback if:
- Security vulnerability discovered
- Data corruption detected
- Critical functionality broken
- Runtime becomes unstable

## Rollback Procedure

### Step 1: Stop FRIDAY
```bash
# If running via Docker:
docker-compose down

# If running via systemd:
sudo systemctl stop friday

# If running directly:
# Send SIGTERM to the process
```

### Step 2: Backup Current State
```bash
python scripts/backup_restore.py backup --output /tmp/friday_rollback_backup.tar.gz
```

### Step 3: Restore Previous Version
```bash
# Restore from previous ZIP
unzip FRIDAY_previous_version.zip -d /opt/friday

# Or restore from git
git checkout <previous-tag>
```

### Step 4: Restore Data
```bash
python scripts/backup_restore.py restore --input /tmp/friday_rollback_backup.tar.gz
```

### Step 5: Verify
```bash
friday status
friday ledger
curl http://localhost:8000/health
```

### Step 6: Restart
```bash
docker-compose up -d
# OR
sudo systemctl start friday
```

## Audit Chain Recovery

If the audit chain is corrupted:
1. FRIDAY automatically archives the corrupted chain
2. A fresh chain starts from genesis
3. The archived chain is preserved at `action_ledger_chain.json.tampered.<timestamp>.json`
4. The HMAC secret at `~/.friday/ledger_secret` must be preserved for receipt verification

## Key Rotation

If the HMAC secret is compromised:
```bash
python -c "
from core.key_rotation import get_key_rotation_manager
mgr = get_key_rotation_manager()
result = mgr.rotate_key(reason='security incident')
print(result)
"
```

**Warning:** Key rotation invalidates all previous receipts. Old receipts will fail verification.

## Contact

For production incidents:
1. Check `KNOWN_LIMITATIONS.md` for known issues
2. Check `release/FINAL_CERTIFICATION.md` for verified state
3. Run `python scripts/backup_restore.py verify --input <backup.tar.gz>` to verify backup integrity
