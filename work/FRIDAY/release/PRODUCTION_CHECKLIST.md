# PRODUCTION CHECKLIST — FRIDAY Age IV v4.0.0

## Pre-Deployment

- [ ] Set `GLM_API_KEY` in `.env` (required for AI functionality)
- [ ] Set `FRIDAY_API_TOKEN` in `.env` (required for API auth)
- [ ] Set `FRIDAY_USE_RUNTIME=1` in `.env` (enables PromptShield + PolicyEngine)
- [ ] Set `AUTONOMY_PROFILE=GUEST` in `.env` (safest default)
- [ ] Set `FRIDAY_DEV_MODE=0` in `.env` (disable dev mode in production)
- [ ] Configure `SUPABASE_URL` and `SUPABASE_KEY` (optional, for persistence)
- [ ] Verify `.env` is not world-readable (`chmod 600 .env`)

## Deployment

- [ ] Build Docker image: `docker build -t friday:v4.0.0 .`
- [ ] Verify image: `docker run friday:v4.0.0 friday status`
- [ ] Deploy with: `docker-compose up -d`
- [ ] Verify health: `curl http://localhost:8000/health`
- [ ] Verify auth: `curl -H "Authorization: Bearer <token>" http://localhost:8000/api/health/deep`
- [ ] Verify metrics: `curl http://localhost:8000/metrics`

## Post-Deployment

- [ ] Test CLI: `friday status`
- [ ] Test chat: `friday chat "hello"`
- [ ] Test ledger: `friday ledger`
- [ ] Test memory: `friday memory list`
- [ ] Set up backup: `python scripts/backup_restore.py backup`
- [ ] Schedule regular backups (cron)
- [ ] Monitor logs for errors
- [ ] Monitor metrics endpoint

## Security Hardening

- [ ] Generate unique `FRIDAY_API_TOKEN` (not the default)
- [ ] Generate unique `FRIDAY_MCP_TOKEN`
- [ ] Set `FRIDAY_LEDGER_HMAC_SECRET` to a unique value
- [ ] Enable HTTPS (configure nginx with TLS)
- [ ] Enable firewall (restrict ports 8000 to localhost)
- [ ] Review all integrations and disable unused ones

## Rollback Plan

See `release/ROLLBACK_GUIDE.md`
