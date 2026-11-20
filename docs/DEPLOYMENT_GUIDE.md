# Production Deployment Guide (512 MB VPS, single-process)

> **Status:** Prepared after local implementation/testing. **Do not deploy blindly**; validate in a staging environment and follow your VPS security practices. Local tests passed in development environment (see README/plan/report).

## Architecture constraints (important)
- **Single ASGI process** with **in-memory Channels layer** (`channels.layers.InMemoryChannelLayer`).
- No Redis/Celery/extra workers in v1. **Do not run multiple ASGI workers** with in-memory channel layer (cross-worker messaging not supported).
- All room/participant/token/team state is **in-memory and temporary**; process restart loses rooms. Android host must detect and recreate rooms.
- Target footprint: lightweight deployment suitable for ~512 MB RAM (tune limits conservatively).

## 1. Server prerequisites
- Linux VPS (Ubuntu/Debian recommended), Python 3.11+, `pip`, `venv`, build tools as needed
- Reverse proxy (Nginx or Caddy) for TLS termination and WSS proxying
- Domain name with DNS pointing to VPS (for HTTPS/WSS)

## 2. Application setup
```bash
# as deploy user
sudo mkdir -p /opt/psl-auction && sudo chown $USER:$USER /opt/psl-auction
cd /opt/psl-auction
# copy project files (git clone/rsync/scp)
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Create `.env` (do not commit secrets):
```env
SECRET_KEY=<strong-random-secret>
DEBUG=False
ALLOWED_HOSTS=yourdomain.com
CSRF_TRUSTED_ORIGINS=https://yourdomain.com
SERVER_VERSION=0.1.0
WS_ALLOWED_ORIGINS=https://yourdomain.com
ROOM_WAITING_EXPIRY_MINUTES=60
ROOM_ABANDONED_HOST_GRACE_MINUTES=10
ROOM_COMPLETED_RETENTION_MINUTES=120
TOKEN_EXPIRY_MINUTES=720
MAX_ROOM_PARTICIPANTS=20
MAX_ACTIVE_ROOMS=50
MAX_WS_PAYLOAD_BYTES=65536
BID_REQUEST_RATE_LIMIT_PER_MINUTE=60
JOIN_RATE_LIMIT_PER_MINUTE=30
CREATE_ROOM_RATE_LIMIT_PER_MINUTE=20
```
Adjust limits downward if memory pressure observed.

Run migrations (built-in Django apps):
```bash
source .venv/bin/activate
python manage.py migrate --noinput
python manage.py check
```

## 3. ASGI server (single process)
Example with Daphne (systemd-managed):
```bash
/opt/psl-auction/.venv/bin/daphne -b 127.0.0.1 -p 8000 project.asgi:application
```
Or Uvicorn (single worker):
```bash
/opt/psl-auction/.venv/bin/uvicorn project.asgi:application --host 127.0.0.1 --port 8000 --workers 1
```
**Do not increase workers** while using in-memory channel layer.

## 4. Process management (systemd example)
`/etc/systemd/system/psl-auction.service`:
```ini
[Unit]
Description=PSL Auction Multiplayer ASGI
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/psl-auction
EnvironmentFile=/opt/psl-auction/.env
ExecStart=/opt/psl-auction/.venv/bin/daphne -b 127.0.0.1 -p 8000 project.asgi:application
Restart=always
RestartSec=3
# Optional hardening/tuning (adjust for 512MB VPS)
# MemoryMax=350M

[Install]
WantedBy=multi-user.target
```
Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now psl-auction
sudo systemctl status psl-auction
```

## 5. Reverse proxy & HTTPS/WSS (Nginx example)
- Terminate TLS (Let's Encrypt/certbot).
- Proxy HTTP to `127.0.0.1:8000`.
- Proxy WebSocket upgrade headers for `/ws/`.

```nginx
server {
    listen 443 ssl http2;
    server_name yourdomain.com;
    # ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    # ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600;
    }
}
```
After config test/reload, clients use `https://yourdomain.com/api/v1/...` and `wss://yourdomain.com/ws/v1/rooms/{room_id}/?token=...`

## 6. Restart behavior
- `systemctl restart psl-auction` or crashes drop all in-memory state (rooms, participants, tokens, WS connections).
- Health endpoint will show zero active rooms/connections after restart.
- Android host must handle room-not-found/connection loss and recreate room; clients must rejoin with new tokens.

## 7. Memory monitoring
- `GET /api/v1/health/` for `memory_mb`, `active_rooms`, `active_connections` (lightweight).
- OS-level: `ps aux`, `top`, `free -m`, `systemd-cgtop`, container/VPS dashboards.
- Set conservative `MAX_ACTIVE_ROOMS`, `MAX_ROOM_PARTICIPANTS`, and payload/rate limits for 512 MB.

## 8. Rollback instructions
1. Keep previous release artifacts (previous commit/tag or copied directory, e.g. `/opt/psl-auction-prev`).
2. Stop service: `sudo systemctl stop psl-auction`.
3. Restore previous code/config (and `.env` if compatible): symlink or copy back previous release.
4. Reinstall deps if changed: `pip install -r requirements.txt`.
5. Run `python manage.py migrate` only if schema-compatible and required (note: core multiplayer state is in-memory; DB used mainly for Django built-ins).
6. Start service: `sudo systemctl start psl-auction` and verify `/api/v1/health/`.
7. If rollback fails, restore from VPS snapshot/backup per provider procedure.

## 9. Future scaling note
If multi-worker or multi-instance deployment is required, replace in-memory channel layer with a shared layer (e.g. Redis via `channels_redis`) and redesign state persistence/distribution accordingly. This is a separate upgrade outside v1 constraints.
