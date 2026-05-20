# DataMind AI — Red Hat / RHEL Production Deployment Guide

## Stack
- **OS**: RHEL 8/9 or CentOS Stream 8/9
- **Python**: 3.11+
- **Web server**: nginx (TLS termination) → uvicorn (FastAPI)
- **Process manager**: systemd
- **Database**: MySQL 8 / MariaDB 10.6+ (local or RDS)

---

## 1. System packages

```bash
sudo dnf install -y python3.11 python3.11-pip python3.11-venv nginx git
sudo dnf install -y certbot python3-certbot-nginx   # for TLS
```

---

## 2. Application user

```bash
sudo useradd -r -s /sbin/nologin datamind
sudo mkdir -p /opt/datamind /var/www/datamind /var/log/datamind
sudo chown datamind:datamind /opt/datamind /var/log/datamind
```

---

## 3. Deploy the code

```bash
sudo -u datamind git clone https://github.com/tharkadharshana/dbbuddy.git /opt/datamind/app
cd /opt/datamind/app/datamind/backend

sudo -u datamind python3.11 -m venv /opt/datamind/venv
sudo -u datamind /opt/datamind/venv/bin/pip install -r requirements.txt
```

---

## 4. Environment file

```bash
sudo cp /opt/datamind/app/datamind/backend/.env.example /opt/datamind/env
sudo chmod 600 /opt/datamind/env
sudo chown datamind:datamind /opt/datamind/env
```

Edit `/opt/datamind/env` — **minimum required changes**:

```env
# Generate: python3 -c "import secrets; print(secrets.token_urlsafe(48))"
SECRET_KEY=<strong-random-key>
ENCRYPTION_KEY=<different-strong-random-key>

DATAMIND_DB_HOST=localhost
DATAMIND_DB_NAME=datamind_db
DATAMIND_DB_USER=datamind_user
DATAMIND_DB_PASSWORD=<db-password>

# Enables HTTPS redirect + HSTS header in FastAPI
FORCE_HTTPS=true

# Lock CORS to your actual domain
EMBED_ALLOWED_ORIGINS=https://yourdomain.com

LOG_LEVEL=INFO
```

---

## 5. systemd service

Create `/etc/systemd/system/datamind.service`:

```ini
[Unit]
Description=DataMind AI Backend
After=network.target mysql.service

[Service]
Type=simple
User=datamind
Group=datamind
WorkingDirectory=/opt/datamind/app/datamind/backend
EnvironmentFile=/opt/datamind/env
ExecStart=/opt/datamind/venv/bin/uvicorn main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 4 \
    --log-level warning \
    --access-log
Restart=always
RestartSec=5

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/datamind/app/datamind/backend/data /var/log/datamind

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now datamind
sudo systemctl status datamind
```

---

## 6. nginx + TLS

```bash
sudo cp /opt/datamind/app/docs/deployment/nginx.conf /etc/nginx/conf.d/datamind.conf
# Edit yourdomain.com in the conf file first
sudo nginx -t
sudo systemctl enable --now nginx

# Obtain TLS cert (Let's Encrypt)
sudo certbot --nginx -d yourdomain.com
```

Auto-renewal is handled by certbot's systemd timer (check with `systemctl status certbot-renew.timer`).

---

## 7. SELinux

RHEL runs SELinux in enforcing mode by default. Allow nginx to proxy to localhost:

```bash
sudo setsebool -P httpd_can_network_connect 1
```

---

## 8. Firewall

```bash
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --permanent --add-service=http   # needed for certbot renewal
sudo firewall-cmd --reload

# Block direct access to the uvicorn port from outside
sudo firewall-cmd --permanent --add-rich-rule='rule family=ipv4 port port=8000 protocol=tcp reject'
sudo firewall-cmd --reload
```

---

## 9. MySQL hardening (M2 roadmap item)

```bash
sudo mysql_secure_installation
```

Add to `/etc/my.cnf.d/datamind.cnf`:

```ini
[mysqld]
bind-address       = 127.0.0.1
local_infile       = 0
skip_symbolic_links = 1

# Connection pool headroom: set > DB_POOL_SIZE × number of uvicorn workers
max_connections    = 200
```

Set `DB_POOL_SIZE=40` in `/opt/datamind/env` (double the default 20).

---

## 10. Deployment checklist before going live

- [ ] `SECRET_KEY` is a strong random value (not the default)
- [ ] `ENCRYPTION_KEY` is set separately from `SECRET_KEY`
- [ ] `FORCE_HTTPS=true` in env file
- [ ] `LOG_LEVEL=INFO` (not DEBUG)
- [ ] `EMBED_ALLOWED_ORIGINS` locked to your domain
- [ ] DeepSeek / Gemini API keys set (user-level, not in env file)
- [ ] TLS cert issued and auto-renewal timer active
- [ ] Port 8000 blocked at firewall
- [ ] SELinux `httpd_can_network_connect` enabled
- [ ] `mysql_secure_installation` run
- [ ] `.env` file permissions: `chmod 600`
- [ ] `git log -- datamind/backend/.env` — confirm .env never committed
