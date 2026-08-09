# FRIDAY Deployment Guide

This guide covers installing, configuring, deploying, hardening, monitoring,
and scaling FRIDAY v3.2 in production. It supersedes the shorter
[`docs/DEPLOY.md`](./DEPLOY.md) (which is kept for quick reference).

> **Production Readiness: Beta.** FRIDAY runs end-to-end on a free Z.ai key,
> but several hardening items (sandbox, RBAC, observability stack) are still
> pending — see [Security Model](./SECURITY_MODEL.md) and
> [Threat Model](./THREAT_MODEL.md) for details.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Quick Start (5 minutes)](#2-quick-start-5-minutes)
3. [Docker Deployment](#3-docker-deployment)
4. [Production Deployment](#4-production-deployment)
5. [Environment Variables](#5-environment-variables)
6. [Security Hardening Checklist](#6-security-hardening-checklist)
7. [Backup and Recovery](#7-backup-and-recovery)
8. [Monitoring](#8-monitoring)
9. [Scaling Considerations](#9-scaling-considerations)

---

## 1. Prerequisites

### Required

| Component | Min version | Why |
|-----------|-------------|-----|
| Python | 3.10+ | f-string syntax, `match` statement, asyncio improvements |
| pip | 23+ | For dependency resolution |
| Z.ai (ZhipuAI) API key | n/a | Free GLM-4-Flash tier — get one at <https://open.bigmodel.cn> |

### Optional (per feature)

| Feature | Component | Install |
|---------|-----------|---------|
| Persistent memory | Supabase project | <https://supabase.com> — free tier supports pgvector |
| Voice (TTS) | ElevenLabs key | <https://elevenlabs.io> (paid) or `pip install pyttsx3` (free, local) |
| Voice (STT) | `openai-whisper` | `pip install openai-whisper pyaudio` |
| Wake word | Picovoice access key | <https://picovoice.ai> (free tier) |
| Vision | `mss` + `pytesseract` + `pyautogui` | `pip install 'friday-ai[vision]'` + `apt install tesseract-ocr` |
| Browser automation | Playwright | `pip install playwright && playwright install` |
| Anthropic Claude | Anthropic API key | paid |
| Google Gemini | Gemini API key | paid |
| Local LLM | Ollama | <https://ollama.com> + `ollama pull llama3` |
| Smart Home | Home Assistant token | running HA instance |
| Spotify | Spotify Developer app | <https://developer.spotify.com> |

### Operating systems supported

| OS | Status | Notes |
|----|--------|-------|
| Linux (Ubuntu 22.04+, Debian 12+) | ✅ Primary target | Best support, all features work |
| macOS 13+ | ✅ Supported | Picovoice wake word works natively |
| Windows 10/11 | ⚠️ Works | Use `install.ps1`; some path-handling edge cases |
| Docker | ✅ Supported | See [Docker Deployment](#3-docker-deployment) |
| Kubernetes | ⚠️ Not provided | No manifest shipped — write your own or use Docker |

---

## 2. Quick Start (5 minutes)

The fastest path to a working FRIDAY.

```bash
# 1. Clone (or extract release tarball)
git clone <repo-url> FRIDAY
cd FRIDAY

# 2. Install Python deps + console script
bash install.sh          # Linux / macOS
# or:  .\install.ps1     # Windows PowerShell

# 3. Get a free GLM key and add to .env
#    (visit https://open.bigmodel.cn — sign up, create app, copy API key)
echo "GLM_API_KEY=your_real_key_here" >> .env

# 4. Run the CLI TUI
friday
```

### What `install.sh` does

1. Checks for `python3` and `pip3`
2. Runs `pip3 install -r requirements.txt`
3. Creates `.env` (or copies `.env.example` if present); generates a
   32-byte `secrets.token_urlsafe(32)` token and writes it to
   `FRIDAY_API_TOKEN`
4. Installs FRIDAY as a console script via `pip3 install -e .`
5. Verifies `friday` is on PATH

### Verify the install

```bash
friday status      # brain provider, skills, integrations
friday trust       # run the hellfire audit (8 security checks)
curl http://localhost:8000/health   # if you started the API
```

### Run the web dashboard instead

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
# Open http://localhost:8000  (auth via ?token=<FRIDAY_API_TOKEN>)
```

---

## 3. Docker Deployment

The shipped [`Dockerfile`](../Dockerfile) builds a slim image with only the
default (GLM) provider. Voice, vision, Supabase, Playwright, and paid
providers are **not** installed in the image — install them via
`requirements-optional.txt` or use a multi-stage build for feature-specific
images.

### 3.1 Build

```bash
docker build -t friday:latest .
```

**What the image includes:**
- `python:3.11-slim` base
- Non-root user `friday` (UID/GID system)
- `requirements.txt` deps (FastAPI, uvicorn, zhipuai, httpx, rich, Pillow, …)
- App code at `/app`
- Healthcheck: `curl -f http://localhost:8000/health`
- Default env: `BRAIN_PROVIDER=glm`, `AUTONOMY_PROFILE=GUEST`, `LOG_LEVEL=INFO`
- Exposed port: `8000`
- Entrypoint: `uvicorn api.main:app --host 0.0.0.0 --port 8000`

### 3.2 Run a single container

```bash
docker run -d --name friday \
  -p 8000:8000 \
  -e GLM_API_KEY=your-key \
  -e FRIDAY_API_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))") \
  -e AUTONOMY_PROFILE=STANDARD \
  -v friday_data:/app/data \
  -v friday_chain:/app/action_ledger_chain.json \
  friday:latest
```

### 3.3 docker-compose

The shipped [`docker-compose.yml`](../docker-compose.yml) brings up FRIDAY
plus a Supabase Postgres container.

```bash
# 1. Edit .env: set GLM_API_KEY, FRIDAY_API_TOKEN, SUPABASE_URL, SUPABASE_KEY
# 2. Start the stack
docker-compose up -d

# 3. Check health
docker-compose ps
curl http://localhost:8000/health
docker-compose logs -f friday-api
```

**Caveats in the shipped compose file** (operators should fix these):

| Issue | Fix |
|-------|-----|
| `POSTGRES_PASSWORD: friday_secret` is hardcoded | Use `${POSTGRES_PASSWORD}` from `.env` |
| Postgres port `5432` exposed to host | Bind to `127.0.0.1:5432:5432` if you only need local access |
| `./friday.log` is bind-mounted as a file (breaks if missing) | Pre-create it on host: `touch friday.log`, or switch to Docker logging |
| No resource limits | Add `deploy.resources.limits` (memory: 2G, cpus: '2.0') |
| No log rotation | Add `logging: {driver: json-file, options: {max-size: "10m", max-file: "5"}}` |

### 3.4 Install optional features in the container

To enable voice, vision, or paid providers in the container, build a derived
image:

```dockerfile
FROM friday:latest
COPY requirements-optional.txt .
RUN pip install --no-cache-dir -r requirements-optional.txt \
    && playwright install --with-deps chromium
```

---

## 4. Production Deployment

### 4.1 Systemd + nginx on Ubuntu VPS

This is the recommended path for a single-node production deployment.

#### Step 1 — System packages

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx
```

#### Step 2 — Create `friday` user + directory

```bash
sudo useradd -r -s /bin/false -d /opt/friday friday
sudo mkdir -p /opt/friday /opt/friday/data /var/log/friday
sudo chown -R friday:friday /opt/friday /var/log/friday
```

#### Step 3 — Clone + install (as `friday`)

```bash
sudo -u friday git clone <repo-url> /opt/friday
cd /opt/friday
sudo -u friday python3 -m venv venv
sudo -u friday venv/bin/pip install --upgrade pip
sudo -u friday venv/bin/pip install -r requirements.txt
sudo -u friday venv/bin/pip install -e .
```

#### Step 4 — Configure environment

```bash
sudo -u friday cp .env.example .env
sudo -u friday venv/bin/python -c "import secrets; print(f'FRIDAY_API_TOKEN={secrets.token_urlsafe(32)}')" \
  | sudo -u friday tee -a /opt/friday/.env > /dev/null
# Edit .env: set GLM_API_KEY, BRAIN_PROVIDER, AUTONOMY_PROFILE
sudo chmod 600 /opt/friday/.env
sudo chown friday:friday /opt/friday/.env
```

#### Step 5 — Install systemd unit

The shipped [`deploy/systemd/friday.service`](../deploy/systemd/friday.service)
is a good baseline but **lacks resource limits**. Replace with:

```ini
# /etc/systemd/system/friday.service
[Unit]
Description=Friday AI Assistant
After=network.target

[Service]
Type=simple
User=friday
Group=friday
WorkingDirectory=/opt/friday
EnvironmentFile=/opt/friday/.env
Environment=PYTHONPATH=/opt/friday
ExecStart=/opt/friday/venv/bin/uvicorn api.main:app \
    --host 127.0.0.1 --port 8000 \
    --workers 1 \
    --proxy-headers \
    --forwarded-allow-ips=127.0.0.1
Restart=on-failure
RestartSec=5

# Resource limits
MemoryMax=2G
CPUQuota=200%
LimitNOFILE=65536

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/opt/friday/data /var/log/friday
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo cp deploy/systemd/friday.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now friday
sudo systemctl status friday
```

#### Step 6 — Configure nginx (with the `limit_req_zone` fix)

The shipped [`deploy/nginx.conf`](../deploy/nginx.conf) puts
`limit_req_zone` inside the `server {}` block, which nginx **rejects** — it
must be in the `http {}` block (i.e. in `/etc/nginx/nginx.conf`, not in a
site conf). The corrected config below splits the directive correctly.

Add to `/etc/nginx/nginx.conf` (in the `http {}` block):

```nginx
http {
    # ... existing config ...

    # Rate limit zone for the Friday API (per-IP, 30 req/min average)
    limit_req_zone $binary_remote_addr zone=friday_api:10m rate=30r/m;
}
```

Then create `/etc/nginx/sites-available/friday`:

```nginx
upstream friday_backend {
    server 127.0.0.1:8000;
    keepalive 32;
}

# HTTP → HTTPS redirect
server {
    listen 80;
    server_name friday.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name friday.example.com;

    # TLS — managed by certbot
    ssl_certificate     /etc/letsencrypt/live/friday.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/friday.example.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # Security headers
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer" always;

    # Body size limit (for visual-memory image uploads)
    client_max_body_size 10m;

    # Main reverse proxy
    location / {
        proxy_pass http://friday_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
    }

    # WebSocket — needs Upgrade headers
    location /api/stream {
        proxy_pass http://friday_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }

    # SSE — must disable buffering
    location /api/chat/stream {
        proxy_pass http://friday_backend;
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400;
    }

    # Apply the rate limit zone to chat endpoints
    location /api/chat {
        limit_req zone=friday_api burst=10 nodelay;
        proxy_pass http://friday_backend;
        proxy_set_header Host $host;
    }
}
```

Enable and reload:

```bash
sudo ln -s /etc/nginx/sites-available/friday /etc/nginx/sites-enabled/
sudo certbot --nginx -d friday.example.com
sudo nginx -t
sudo systemctl reload nginx
```

#### Step 7 — Verify

```bash
curl https://friday.example.com/health
# {"status":"healthy","version":"1.0.0","rate_limiting":true}

curl -H "Authorization: Bearer $FRIDAY_API_TOKEN" \
     https://friday.example.com/api/health/deep
```

---

## 5. Environment Variables

Complete reference. All variables are loaded via `python-dotenv` from `.env`
in the working directory. See [`config/settings.py`](../config/settings.py)
for the loader and `.env.example` for a template.

### 5.1 Brain providers

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BRAIN_PROVIDER` | Yes | `glm` | One of `glm`, `claude`, `gemini`, `ollama`, `council` |
| `GLM_API_KEY` | Yes (if provider=glm) | — | Z.ai / ZhipuAI API key |
| `GLM_MODEL` | No | `glm-4-flash` | GLM model name (`glm-4`, `glm-4-plus`, `glm-4v`, `glm-4-long`) |
| `ANTHROPIC_API_KEY` | Yes (if provider=claude) | — | Anthropic API key |
| `CLAUDE_MODEL` | No | `claude-sonnet-4-20250514` | Claude model name |
| `GEMINI_API_KEY` | Yes (if provider=gemini) | — | Google Gemini API key |
| `GEMINI_MODEL` | No | `gemini-2.0-flash` | Gemini model name |
| `OPENAI_API_KEY` | No | — | Reserved for future use |
| `OLLAMA_MODEL` | No | `llama3` | Local LLM model name |
| `OLLAMA_BASE_URL` | No | `http://localhost:11434` | Ollama HTTP endpoint |

### 5.2 Database & memory

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SUPABASE_URL` | No | — | Supabase project URL (enables persistent memory + vector store) |
| `SUPABASE_KEY` | No | — | Supabase anon key |

### 5.3 Web search

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TAVILY_API_KEY` | No | — | Tavily API key (alternative web search) |

### 5.4 Voice & vision

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ELEVENLABS_API_KEY` | No | — | ElevenLabs TTS key (falls back to pyttsx3) |
| `PICOVOICE_ACCESS_KEY` | No | — | Picovoice wake word key |

### 5.5 Integrations

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENWEATHERMAP_API_KEY` | No | — | OpenWeatherMap API key |
| `SPOTIFY_CLIENT_ID` | No | — | Spotify app client ID |
| `SPOTIFY_CLIENT_SECRET` | No | — | Spotify app client secret |
| `HOME_ASSISTANT_TOKEN` | No | — | Home Assistant long-lived access token |

### 5.6 Skills

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `FRIDAY_DEFAULT_LOCATION` | No | `Lagos` | Default city for `MorningBriefing` skill |

### 5.7 Security

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `FRIDAY_API_TOKEN` | **Yes (prod)** | auto-generated | Bearer token for `/api/*` routes. Auto-generated on first start if empty. |
| `FRIDAY_DEV_MODE` | No | `0` | Set to `1` ONLY for local dev (disables auth). **Never in production.** |
| `FRIDAY_LEDGER_HMAC_SECRET` | No | derived | Override HMAC secret for audit chain (defaults to `FRIDAY_API_TOKEN` or `~/.friday/ledger_secret`) |
| `AUTONOMY_PROFILE` | No | `GUEST` | One of `GUEST`, `STANDARD`, `POWER` |
| `ALLOWED_ORIGINS` | No | `http://localhost:3001,http://localhost:8000` | Comma-separated CORS allow-list |

### 5.8 Identity

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `FRIDAY_IDENTITY` | No | `General` | One of `General`, `Strategist`, `Creative`, `Debugger`, `Guardian` |

### 5.9 Logging

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LOG_LEVEL` | No | `INFO` | Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_FILE` | No | `friday.log` | Path to log file (set up `RotatingFileHandler` in production) |

### 5.10 Conversation history

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HISTORY_LIMIT` | No | `50` | Max messages kept in conversation history |

### 5.11 Webhook secrets

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GITHUB_WEBHOOK_SECRET` | No | — | If unset, GitHub webhook signatures are NOT verified (fail-open). Set this in production. |
| `STRIPE_WEBHOOK_SECRET` | No | — | Reserved — Stripe signature verification is currently a stub. |

### 5.12 Observability

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SENTRY_DSN` | No | — | Sentry project DSN. When set, unhandled exceptions are captured. |
| `SENTRY_ENVIRONMENT` | No | `production` | Sentry environment tag (e.g. `staging`, `production`) |
| `SENTRY_TRACES_SAMPLE_RATE` | No | `0.0` | Fraction of transactions traced (0.0–1.0). Set to `0.1` for 10%. |
| `METRICS_ALLOW_EXTERNAL` | No | `0` | Set to `1` to allow non-localhost access to `/metrics`. **Use with caution** — prefer nginx IP allowlist. |

---

## 6. Security Hardening Checklist

Run through this checklist before exposing FRIDAY to the internet.

### 6.1 Authentication

- [ ] `FRIDAY_API_TOKEN` is set to a 32-byte random value
      (`python -c "import secrets; print(secrets.token_urlsafe(32))"`)
- [ ] `FRIDAY_DEV_MODE` is **not** set (or is `0`)
- [ ] `.env` file has `chmod 600` and is owned by the `friday` user
- [ ] `FRIDAY_LEDGER_HMAC_SECRET` is set to a value independent of
      `FRIDAY_API_TOKEN` (defence-in-depth: an attacker who learns the API
      token still cannot forge audit entries)
- [ ] `~/.friday/ledger_secret` (if used) has `chmod 600`

### 6.2 Authorization

- [ ] `AUTONOMY_PROFILE` is set to `GUEST` or `STANDARD` (never `POWER` for
      internet-facing deployments)
- [ ] Components in `NEVER_AUTO_APPROVE_COMPONENTS` (ImageGen, VideoGen,
      CodeExecution, Printer, Printer3D, Finance, Commerce) are still in
      the allowlist — verify with `friday trust`
- [ ] Team-mode `FRIDAY_USER_TOKEN`s are issued per-user and rotated on
      member offboarding

### 6.3 Transport

- [ ] HTTPS-only via Let's Encrypt + nginx (HTTP redirects to HTTPS)
- [ ] HSTS header (`Strict-Transport-Security: max-age=63072000; includeSubDomains`)
- [ ] `X-Frame-Options: DENY` and `X-Content-Type-Options: nosniff`
- [ ] `client_max_body_size 10m` to limit upload abuse
- [ ] WebSocket `/api/stream` has `proxy_read_timeout 86400`

### 6.4 Firewall (ufw example)

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp           # SSH (consider limiting to your IP)
sudo ufw allow 80/tcp           # HTTP (for certbot + redirect)
sudo ufw allow 443/tcp          # HTTPS
sudo ufw deny 5432              # Block direct Postgres access
sudo ufw deny 8000              # Block direct uvicorn access — go through nginx
sudo ufw enable
```

### 6.5 Process isolation (systemd)

- [ ] `User=friday`, `Group=friday` (non-root)
- [ ] `NoNewPrivileges=true`
- [ ] `ProtectSystem=strict`
- [ ] `ProtectHome=true`
- [ ] `PrivateTmp=true`
- [ ] `CapabilityBoundingSet=` (empty)
- [ ] `MemoryMax=2G`, `CPUQuota=200%`, `LimitNOFILE=65536`

### 6.6 Webhook signature verification

- [ ] `GITHUB_WEBHOOK_SECRET` is set (otherwise GitHub webhooks are accepted
      unverified — fail-open behaviour)
- [ ] Stripe signature verification is implemented (currently a stub —
      tracked for the Security Agent)

### 6.7 Plugin marketplace

- [ ] Only install plugins from trusted authors
- [ ] Run `friday plugin install <name>` (AST scan runs automatically)
- [ ] If you need to bypass the AST scan for a trusted plugin, copy it
      manually and document why in your ops runbook

### 6.8 Audit chain

- [ ] `action_ledger_chain.json` is on a volume that is backed up
- [ ] `action_ledger_pending.json` is on the same volume
- [ ] Periodic `GET /api/actions/verify` (e.g., hourly via cron) alerts if
      the chain is ever broken
- [ ] Tampered chain archives (`*.tampered.*.json`) are forwarded to your
      security team

---

## 7. Backup and Recovery

### 7.1 What to back up

| Artifact | Path | Frequency | Why |
|----------|------|-----------|-----|
| Audit chain | `action_ledger_chain.json` | Daily + on change | Tamper-evident audit log — the most important artifact |
| Pending actions | `action_ledger_pending.json` | Daily | Pending approval queue |
| Cost tracker | `cost_tracker_data.json` | Daily | Token usage and cost history |
| Conversation memory | (in-memory) → Supabase | Continuous | Lost on restart without Supabase |
| Visual memory store | (in-memory) → Supabase | Continuous | Lost on restart without Supabase |
| `.env` | `/opt/friday/.env` | On change | Contains all API keys + tokens |
| `~/.friday/ledger_secret` | `~/.friday/ledger_secret` | On change | HMAC secret for audit chain |
| Supabase database | (managed by Supabase) | Daily snapshot | All persisted memory + vectors |
| `friday_workspace/` | `/opt/friday/friday_workspace/` | Weekly | User-generated files |

### 7.2 Backup script (cron daily)

```bash
#!/usr/bin/env bash
# /opt/friday/scripts/backup.sh
set -euo pipefail

BACKUP_DIR="/var/backups/friday/$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"

cp /opt/friday/action_ledger_chain.json      "$BACKUP_DIR/"
cp /opt/friday/action_ledger_pending.json    "$BACKUP_DIR/" 2>/dev/null || true
cp /opt/friday/cost_tracker_data.json        "$BACKUP_DIR/" 2>/dev/null || true
cp /opt/friday/.env                          "$BACKUP_DIR/env.backup"
cp /home/friday/.friday/ledger_secret        "$BACKUP_DIR/" 2>/dev/null || true
tar czf "$BACKUP_DIR/workspace.tar.gz" -C /opt/friday friday_workspace 2>/dev/null || true

# Supabase: use `pg_dump` via the Supabase CLI or dashboard

# Retain 30 days
find /var/backups/friday/ -maxdepth 1 -type d -mtime +30 -exec rm -rf {} +
```

```cron
# crontab -u root
0 3 * * * /opt/friday/scripts/backup.sh >> /var/log/friday/backup.log 2>&1
```

### 7.3 Restore procedure

1. Stop the service: `sudo systemctl stop friday`
2. Restore files: `cp /var/backups/friday/<date>/* /opt/friday/`
3. Restore `.env` and `ledger_secret` to their original paths
4. (Optional) Restore Supabase from a snapshot via the Supabase dashboard
5. Restart: `sudo systemctl start friday`
6. Verify: `curl http://localhost:8000/api/actions/verify` → `{"valid": true, ...}`

### 7.4 Disaster recovery — tampered audit chain

If `GET /api/actions/verify` returns `valid: false`:

1. The tampered chain has already been archived to
   `action_ledger_chain.json.tampered.<timestamp>.json` (FRIDAY does this
   automatically on startup)
2. Forward the archived file to your security team for forensic analysis
3. A fresh chain starts automatically from genesis — there is no automatic
   "re-trust" path; the breakage itself is the audit signal
4. Investigate who had filesystem write access to the chain file

---

## 8. Monitoring

FRIDAY ships a built-in observability stack: structured JSON logging with
correlation IDs, a Prometheus `/metrics` endpoint, and optional Sentry
error tracking. All three are configured via environment variables.

### 8.1 What exists in v3.2

| Source | How to access | What it shows |
|--------|---------------|---------------|
| `GET /metrics` | Bearer token (or localhost) | Prometheus text exposition — 10+ metrics (chat QPS, latency histograms, token burn, integration status, ledger chain validity) |
| `GET /api/health/deep` | Public (no auth — see [Security Model §7.7](./SECURITY_MODEL.md#77-deep-health-endpoint-is-unauthenticated)) | 10 subsystem checks with latency |
| `GET /api/stats` | Bearer token | Provider breakdown, token counts, cost, rate-limit warnings |
| `GET /api/actions/audit` | Bearer token | Full hash-chained audit log |
| `core/cost_tracker.py` | In-memory, persists to `cost_tracker_data.json` | Token spend per provider |
| Structured logs | stdout / `friday.log` | JSON with `correlation_id`, `request_id`, timestamp |
| Sentry | Optional via `SENTRY_DSN` | Unhandled exceptions + performance traces |

### 8.2 Prometheus metrics

The `/metrics` endpoint is exposed by [`api/routes/metrics.py`](../api/routes/metrics.py)
and serves the standard Prometheus text exposition format. It is
**unauthenticated** (Prometheus scrapers typically present no credentials)
but is **restricted to localhost** unless `METRICS_ALLOW_EXTERNAL=1` is set.

**Metrics exposed**

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `friday_chat_requests_total` | counter | `provider`, `status` | Chat request count |
| `friday_chat_duration_seconds` | histogram | `provider` | Chat latency distribution |
| `friday_tokens_total` | counter | `provider`, `direction` | Token usage (in/out) |
| `friday_cost_usd_total` | counter | `provider` | Estimated spend |
| `friday_integration_status` | gauge | `integration_name` | 1=available, 0=unavailable |
| `friday_memory_count` | gauge | — | In-memory store size |
| `friday_ledger_chain_valid` | gauge | — | 1=valid, 0=tampered |
| `friday_pending_actions` | gauge | — | Pending approval count |
| `friday_active_websockets` | gauge | — | Open WS connections |
| `friday_error_total` | counter | `module` | Unhandled exception count |

**Prometheus scrape config**

```yaml
scrape_configs:
  - job_name: 'friday'
    scrape_interval: 15s
    static_configs:
      - targets: ['localhost:8000']
    # If scraping from a different host, set METRICS_ALLOW_EXTERNAL=1
    # in FRIDAY's environment and add a basic_auth header at the nginx
    # layer (or restrict by source IP).
```

**Grafana dashboard suggested panels**

1. Chat QPS by provider (counter rate)
2. p95 latency by provider (histogram quantile)
3. Token burn rate per minute
4. Cost per hour (counter rate × 3600)
5. Error rate by module
6. Integration availability heatmap (integration_status gauge)
7. Ledger chain validity (single stat — alert if 0)
8. Pending actions backlog (gauge)
9. Active WebSocket connections (gauge)
10. Memory store growth (gauge over time)

### 8.3 Sentry error tracking

Sentry is initialised in [`api/main.py`](../api/main.py) via
[`core/observability.py::init_sentry`](../core/observability.py). Set
`SENTRY_DSN` in `.env` to enable:

```bash
SENTRY_DSN=https://<key>@sentry.io/<project>
SENTRY_ENV=production           # optional — defaults to "development"
SENTRY_TRACES_SAMPLE_RATE=0.1   # optional — 10% of transactions traced
```

Once enabled, all unhandled exceptions in FastAPI handlers and
background tasks are captured automatically. The correlation ID from
`CorrelationIdMiddleware` is included as a Sentry tag, so you can
correlate a specific request's logs with its Sentry event.

### 8.4 Structured logging

[`core/observability.py`](../core/observability.py) provides a
`StructuredLogger` that emits JSON lines with:

```json
{
  "timestamp": "2026-08-09T20:51:38.123Z",
  "level": "INFO",
  "logger": "friday.api.chat",
  "message": "Chat request completed",
  "correlation_id": "abc-123",
  "provider": "glm",
  "tokens_in": 42,
  "tokens_out": 128,
  "duration_ms": 842.3
}
```

The `CorrelationIdMiddleware` adds a `correlation_id` to every request
(either from the `X-Correlation-ID` header or auto-generated) and
propagates it through the logging context. Use this ID to trace a single
request across logs, metrics, and Sentry events.

For log rotation, configure Python's `RotatingFileHandler` in production:

```python
import logging
from logging.handlers import RotatingFileHandler

handler = RotatingFileHandler(
    '/var/log/friday/friday.log',
    maxBytes=10_000_000,  # 10 MB
    backupCount=5,
)
handler.setFormatter(json_formatter)
logging.getLogger().addHandler(handler)
```

### 8.5 Health-check cron

```bash
# /etc/cron.hourly/friday-health
#!/usr/bin/env bash
TOKEN=$(grep FRIDAY_API_TOKEN /opt/friday/.env | cut -d= -f2)
HEALTH=$(curl -fsS -H "Authorization: Bearer $TOKEN" \
    https://friday.example.com/api/actions/verify)
echo "$(date) $HEALTH" >> /var/log/friday/health.log
echo "$HEALTH" | grep -q '"valid":true' || \
    mail -s "FRIDAY audit chain BROKEN" ops@example.com <<< "$HEALTH"
```

For richer alerting, configure Prometheus alerting rules on the
`friday_ledger_chain_valid` gauge:

```yaml
groups:
  - name: friday
    rules:
      - alert: FridayLedgerChainBroken
        expr: friday_ledger_chain_valid == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "FRIDAY audit chain is broken (possible tampering)"
          description: "verify_chain() returned false on {{ $labels.instance }}"
```

---

## 9. Scaling Considerations

FRIDAY is currently a **single-process, single-user** application. The
[`FridayBrain`](../core/brain.py) is a ~1200-line god class holding
conversation history, memory, emotions, personality, and skill registry in
process memory. Scaling requires either vertical growth or architectural
changes.

### 9.1 Current limits

| Dimension | Limit | Reason |
|-----------|-------|--------|
| Concurrent users | 1 | Singleton `FridayBrain` has shared mutable state |
| Requests/sec | ~10–20 | Single uvicorn worker, GIL-bound Python, GLM rate limit (60 RPM) |
| WebSocket connections | ~50 | Heartbeat task per connection; `asyncio.wait_for` polling |
| Memory size | ~2 GB | Conversation history + in-memory store; configure `HISTORY_LIMIT=50` |
| Audit chain size | unbounded | JSON file grows linearly with actions |

### 9.2 When to add Supabase

Configure `SUPABASE_URL` + `SUPABASE_KEY` when:
- You need conversation history to survive restarts
- You want vector search across large memory stores (>10K entries)
- You're running multiple FRIDAY instances that need shared state (advanced —
  the brain itself is still single-process per instance; this is for memory
  only)

Run the migrations in [`database/migrations/`](../database/migrations/) on
first connect.

### 9.3 When to self-host GLM

Z.ai's free tier is ~60 RPM and 100K tokens/day. Switch to:
- **Self-hosted GLM** (ZhipuAI's open weights on a GPU box) when you exceed
  100K tokens/day or need <100ms latency
- **Ollama + llama3** for offline / privacy-sensitive workloads (set
  `BRAIN_PROVIDER=ollama`)
- **Council mode** (`BRAIN_PROVIDER=council`) to fan out to multiple
  providers in parallel — useful for high-availability

### 9.4 When to run multiple instances

You generally **shouldn't** until:
- The brain is refactored to be stateless (planned for v4.0)
- The audit chain moves to a database (currently a JSON file — concurrent
  writers would corrupt it)

If you must run multiple instances today:
- Put each behind a sticky-session load balancer
- Give each instance its own `action_ledger_chain.json` (no shared writes)
- Use Supabase as the shared memory layer (not the in-memory store)

### 9.5 Horizontal scaling roadmap (v4.0+)

1. Refactor `FridayBrain` into stateless handlers + Redis-backed session
   store
2. Move audit chain to Postgres with `SELECT FOR UPDATE` on append
3. Add a worker queue (Celery / RQ) for long-running actions (image gen,
   video gen, code execution)
4. Containerise with a real process manager (Supervisor / systemd
   templates) and autoscale on CPU + queue depth

---

## See Also

- [API Reference](./API_REFERENCE.md)
- [Developer Guide](./DEVELOPER_GUIDE.md)
- [Security Model](./SECURITY_MODEL.md)
- [Architecture](./ARCHITECTURE.md)
- [Limitations](./LIMITATIONS.md)
