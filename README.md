# PSL AI Auction — Multiplayer Backend Server

Lightweight Django + Django REST Framework + Django Channels (ASGI) backend for coordinating multiplayer PSL AI Auction rooms.

**Important:** The **Android host app is the authoritative auction controller** (auction rules, timer, bid validation, purse/squad changes, player sold/unsold, winners). This server handles **communication and coordination only**: room management, participant/session management, authentication, team acquisition request coordination, WebSocket routing, host connection monitoring, and temporary lifecycle/cleanup.

## Project status
- **Phase 1:** complete (foundation, core REST, room/team domain, basic WS/auth, initial tests/docs)
- **Phase 2:** complete (full event routing/protocol behaviors, host-authoritative relay gates, sequencing/duplicate protection, snapshot routing/caching, reconnect/lifecycle/cleanup hardening, expanded tests, full documentation set)

## Technology
- Python, Django 5.2, Django REST Framework, Django Channels 4.x, ASGI
- Daphne/Uvicorn compatible, **single-process** with **in-memory channel layer** (no Redis)
- SQLite configured for Django built-ins; multiplayer room state is **in-memory/temporary** (lost on restart)

## Key capabilities
- Room APIs: create/join/info/leave/delete with password hashing, secure IDs/tokens, role-based access
- Team coordination: host-published teams, participant requests, host approve/reject/revoke, ownership validation
- WebSocket room hub: `/ws/v1/rooms/{room_id}/?token=...` authenticated connections, structured event envelopes
- Host-authoritative relay: participant bid requests validated and routed to host; host emits authoritative results/events which server sequences and broadcasts (server never accepts bids or computes auction outcomes)
- Reliability features: host disconnect/reconnect handling, reconnect sync aids (state sync + cached snapshot), snapshot request routing, duplicate event-id protection, bounded in-memory tracking
- Health/diagnostics: `/api/v1/health/` with status/version/active rooms/connections/approx memory

## Repository layout
- `project/` – Django settings/ASGI/URLs
- `app/state/` – in-memory store, models, rate limiting, sequencing/snapshot cache
- `app/services/` – auth, room, team, notification services
- `app/api/` – REST views and URL routes
- `app/ws/` – Channels consumer, routing, protocol helpers
- `app/tests.py` – automated test suite
- `docs/` – API/protocol/lifecycle/dev/deployment/memory/Android integration docs
- `PLAN.md`, `requirements.txt`, `.env.example`, `measure_memory.py`

## Quick start (local)
1. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
2. Configure environment:
   - copy `.env.example` to `.env`
   - set `SECRET_KEY`, `DEBUG`, and `ALLOWED_HOSTS` (include LAN IP for device testing)
3. Migrate Django built-ins:
   ```powershell
   python manage.py migrate
   ```
4. Run ASGI server:
   ```powershell
   daphne -b 0.0.0.0 -p 8000 project.asgi:application
   ```
   or
   ```powershell
   uvicorn project.asgi:application --host 0.0.0.0 --port 8000
   ```
5. Endpoints:
   - API: `http://<host>:8000/api/v1/`
   - WS: `ws://<host>:8000/ws/v1/rooms/{room_id}/?token=<token>`
   - For phones/other devices use the machine LAN IP (not `localhost`).

## Automated tests (actual results)
Executed in the development environment after Phase 2 implementation (Channels/Daphne installed):
- `python manage.py check` -> **OK** (`System check identified no issues`)
- `python manage.py test app` -> **OK** (`Ran 25 tests ... OK`)

Coverage in suite includes: REST room flows (create/join/wrong password/expired/full/duplicate/safe info/leave/delete permissions), team request/approve/reject/duplicate ownership/single-team/revoke/permission checks, health, lifecycle/cleanup cases, and WebSocket tests (auth, room broadcasts, bid request routing to host, host-authoritative result broadcast, participant cannot publish authoritative results, duplicate event protection, host disconnect/reconnect, room delete notification, snapshot request/routing, participant reconnect sync/cached snapshot, auction event relay/sequencing).

## Memory measurement (simulated)
Ad-hoc simulation (`python measure_memory.py`, in-memory channel layer, 10 rooms, 50 concurrent WS connections = host + 4 participants per room):
- Baseline RSS: **72.62 MB**
- After setup (10 rooms, 50 connections): **84.91 MB**
- After event traffic: **84.91 MB**
- After disconnect (rooms retained): **84.93 MB**
- Counters observed: 10 active rooms, 50 active connections during run; 0 connections after disconnect

See `docs/MEMORY_OPTIMIZATION.md` for interpretation and tuning guidance. Monitor real VPS usage via `/api/v1/health/` and OS metrics.

## Documentation
- `docs/REST_API.md` – REST endpoint contract
- `docs/WEBSOCKET_PROTOCOL.md` – WebSocket event protocol and routing rules
- `docs/ROOM_LIFECYCLE.md` – room states, disconnect/reconnect, expiration/cleanup, restart behavior
- `docs/ANDROID_INTEGRATION_CONTRACT.md` – integration contract for Android client/server (endpoints, auth, events, payloads, permissions, flows)
- `docs/LOCAL_DEVELOPMENT.md` – local/dev/LAN/test instructions
- `docs/DEPLOYMENT_GUIDE.md` – 512 MB VPS production deployment guide (ASGI, reverse proxy, HTTPS/WSS, process mgmt, restart/rollback)
- `docs/MEMORY_OPTIMIZATION.md` – constraints, knobs, measured memory
- `PLAN.md` – implementation plan and status

## Known limitations / notes
- **Single-process only:** in-memory channel layer and in-memory room state do not support multiple ASGI workers/instances.
- **Ephemeral state:** process restart loses all rooms/tokens/connections (v1 accepted behavior); Android host must recreate room.
- **Host authority boundary:** server validates/routs/gates events but does not execute auction rules/timers/purse/squad logic; auction correctness depends on host app.
- **Best-effort in-memory controls:** rate limiting, duplicate tracking, and snapshot caching are bounded and process-local.
- **No VPS deployment performed** as part of this implementation; deployment guide is provided for future rollout.

## Final implementation summary
Backend implemented and locally verified with documented APIs/protocol, host-authoritative event relay model, lifecycle/reconnect handling, and automated tests passing in the current environment. Remaining operational work is environment-specific deployment, production secret/TLS configuration, and any Android-side integration adjustments based on the provided contract. 
