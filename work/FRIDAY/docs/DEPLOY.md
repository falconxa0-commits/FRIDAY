# Friday Deployment Guide

## Ubuntu VPS Deployment

### 1. Install dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv nginx
```

### 2. Create Friday user + directory

```bash
sudo useradd -r -s /bin/false friday
sudo mkdir -p /opt/friday /opt/friday/data
sudo chown friday:friday /opt/friday /opt/friday/data
```

### 3. Clone + install

```bash
sudo -u friday git clone <repo-url> /opt/friday
cd /opt/friday
sudo -u friday python3 -m venv venv
sudo -u friday venv/bin/pip install -r requirements.txt
```

### 4. Configure environment

```bash
sudo -u friday cp .env.example .env
# Edit .env with your GLM_API_KEY and generated FRIDAY_API_TOKEN
sudo -u friday venv/bin/python -c "import secrets; print(f'FRIDAY_API_TOKEN={secrets.token_urlsafe(32)}')" >> .env
```

### 5. Install systemd service

```bash
sudo cp deploy/systemd/friday.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable friday
sudo systemctl start friday
sudo systemctl status friday
```

### 6. Configure Nginx + TLS

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/friday
sudo ln -s /etc/nginx/sites-available/friday /etc/nginx/sites-enabled/
sudo certbot --nginx -d friday.example.com
sudo nginx -t
sudo systemctl reload nginx
```

### 7. Verify

```bash
curl http://localhost:8000/health
# Should return {"status": "healthy", ...}
```

## Docker Deployment

```bash
docker build -t friday:latest .
docker run -d --name friday \
  -p 8000:8000 \
  -e GLM_API_KEY=your-key \
  -e FRIDAY_API_TOKEN=your-token \
  -v friday_data:/app/data \
  friday:latest
```

## Kubernetes Deployment

```bash
kubectl apply -f deploy/kubernetes/
```
