# Local Development Instructions

## Prerequisites
- Python 3.11+ (project tested with Python 3.11 / Django 5.2)
- Windows PowerShell (examples below) or equivalent shell

## Setup
1. Open project root:
   - `E:\data_e\data_e\django-data\django-data\new devlopment\psl api\project`
2. (Optional) create/activate virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
3. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
4. Configure environment:
   - Copy `.env.example` to `.env`
   - Set at minimum: `SECRET_KEY`, `DEBUG=True`, `ALLOWED_HOSTS` (include `localhost,127.0.0.1,<your-lan-ip>` for device testing), and any limits/timeouts as needed
5. Apply Django migrations (built-in apps/session/admin support):
   ```powershell
   python manage.py migrate
   ```

## Run server
### ASGI (recommended for HTTP + WebSocket)
```powershell
daphne -b 0.0.0.0 -p 8000 project.asgi:application
```
or
```powershell
uvicorn project.asgi:application --host 0.0.0.0 --port 8000
```

Server URLs:
- HTTP API: `http://<host>:8000/api/v1/`
- Health: `http://<host>:8000/api/v1/health/`
- WebSocket: `ws://<host>:8000/ws/v1/rooms/{room_id}/?token=<token>`

### LAN/device testing
- Do **not** use `localhost` from another phone/device. Use the development machine's LAN IP (e.g. `http://192.168.1.10:8000`).
- Add LAN IP to `ALLOWED_HOSTS` in `.env`.
- Allow inbound TCP port 8000 through Windows Firewall for private networks as needed.
- If testing across networks, use an appropriate temporary tunnel/staging server (not included).

## Run automated tests
```powershell
python manage.py check
python manage.py test app
```
Current suite includes REST, team, health, lifecycle, and Channels WebSocket tests (WebSocket tests auto-skip if `channels` is unavailable).

## Manual multi-client testing tips
- Create room via `POST /api/v1/rooms/`, join multiple participants via `POST /api/v1/rooms/{room_id}/join/`.
- Connect host/participants to WS with their tokens; verify broadcasts and host-authoritative routing.
- Use multiple devices/emulators on same LAN and create multiple independent rooms to verify isolation.
- Monitor memory via `GET /api/v1/health/` (`memory_mb`) and OS tools (Task Manager / `psutil`).

## Notes
- In-memory room/channel state is single-process only and lost on restart.
- For production, run behind a reverse proxy with HTTPS/WSS and environment-specific secrets (see deployment guide). No VPS deployment performed in this project phase.
