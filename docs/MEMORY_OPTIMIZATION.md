# Memory Optimization Notes (512 MB VPS target)

## Implemented constraints and choices
- **Single ASGI process, in-memory channel layer:** avoids Redis overhead but requires single-worker operation (no cross-worker messaging).
- **In-memory temporary state only:** rooms/participants/tokens/teams/requests stored in process memory; no large persistent auction DB or bid history stored on server.
- **Compact relay model:** server relays events/snapshots; host Android app retains full auction state and generates snapshots. No unlimited server-side event history.
- **Bounded structures:**
  - Seen event IDs tracked with bounded deque/set (capped, trimmed) for duplicate protection.
  - Latest snapshot cached per room only (not history).
  - Rate limiter uses time-window deques per key with trimming.
  - Room/participant/connection limits enforced/configurable (`MAX_ACTIVE_ROOMS`, `MAX_ROOM_PARTICIPANTS`).
- **Payload controls:** `MAX_WS_PAYLOAD_BYTES` enforced in WebSocket receive path; compact JSON envelopes.
- **Cleanup:** expired/abandoned/closed rooms removed via on-access cleanup and tombstone windows; disconnected sessions cleaned from connection state.
- **No Celery/background workers/Redis** in v1.

## Configuration knobs (tune for 512 MB)
- `MAX_ACTIVE_ROOMS` (default 100 in settings example; recommend lower in production, e.g. 25-50)
- `MAX_ROOM_PARTICIPANTS`
- `MAX_WS_PAYLOAD_BYTES`
- `ROOM_WAITING_EXPIRY_MINUTES`, `ROOM_ABANDONED_HOST_GRACE_MINUTES`, `ROOM_COMPLETED_RETENTION_MINUTES`
- Rate limits: `CREATE_ROOM_RATE_LIMIT_PER_MINUTE`, `JOIN_RATE_LIMIT_PER_MINUTE`, `BID_REQUEST_RATE_LIMIT_PER_MINUTE`
- `TOKEN_EXPIRY_MINUTES`

## Measured memory (local simulation)
Environment: Windows dev machine, Python 3.11, Django/DRF/Channels (in-memory layer), ad-hoc script using `channels.testing.WebsocketCommunicator` + REST client in-process.

Command/run: `python measure_memory.py` (10 rooms, 5 connections per room = 50 WS connections total, host + 4 participants each).

Results (RSS, approximate):
- **Baseline:** 72.62 MB
- **After setup (10 rooms, 50 active WS connections):** 84.91 MB
- **After event traffic:** 84.91 MB
- **After disconnect (connections closed, rooms still in memory):** 84.93 MB
- Store counters: `ACTIVE_ROOMS=10`, `ACTIVE_CONNECTIONS=50` during run; `ACTIVE_CONNECTIONS=0` after disconnect

Interpretation: incremental cost for this simulated load was ~12.3 MB above script baseline in-process. Real ASGI server baseline and per-connection overhead will vary by platform, TLS termination, proxy, and workload; monitor `/api/v1/health/` and OS memory on VPS and reduce limits accordingly.

## Operational recommendations
- Run **exactly one** ASGI worker/process with in-memory channel layer.
- Place Nginx/Caddy in front for TLS/WSS; monitor proxy memory separately.
- Set `MemoryMax`/alerts in systemd or VPS monitoring; restart policy will drop rooms (expected).
- Prefer smaller `MAX_ACTIVE_ROOMS` and shorter retention/grace periods on 512 MB.
- For higher concurrency or HA, plan migration to shared channel layer + externalized state (future work).
