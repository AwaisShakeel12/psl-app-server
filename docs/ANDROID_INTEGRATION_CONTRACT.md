# Android Integration Contract (PSL AI Auction Multiplayer Backend)

This document defines the backend contract for Android client integration. **Android host app remains authoritative** for auction rules, timer, bids validation, purse/squad changes, player sold/unsold outcomes, and final results. Server provides room/auth/coordination and real-time routing only.

## 1. Base URLs and transport
- **HTTP(S) base URL:** configurable (e.g. `http://192.168.1.10:8000` for LAN dev, `https://yourdomain.com` for production)
- **API prefix:** `/api/v1`
- **WebSocket URL:** derive from base URL using same host/path:  
  - Dev/LAN: `ws://<host>/ws/v1/rooms/{room_id}/?token=<token>`  
  - Prod: `wss://<host>/ws/v1/rooms/{room_id}/?token=<token>`
- Do not hardcode environment-specific endpoint strings in app logic beyond the configurable base URL.

## 2. Authentication and roles
- **Host token:** returned only on `POST /api/v1/rooms/` (create). Grants host privileges for room/team admin and authoritative WS publishing.
- **Participant token:** returned on `POST /api/v1/rooms/{room_id}/join/`.
- **REST auth header:** `Authorization: Bearer <token>` for protected endpoints.
- **WebSocket auth:** `token` query parameter on connect. Token is room-bound, role-bound, and time-limited.
- Server validates token authenticity, room membership, expiry, and role on every protected request/WS action. Client-supplied IDs are not trusted without token binding.
- Never expose host token to participants. Do not log/store passwords or full tokens insecurely.

## 3. REST endpoints (contract)

### 3.1 Health
- `GET /api/v1/health/` -> `{ status, api_available, websocket_available, server_version, active_rooms, active_connections, memory_mb, timestamp }`

### 3.2 Create room (host) — authoritative host + team assignment
- `POST /api/v1/rooms/`
- **Server-authoritative behavior:** creator is automatically the room **host** (`role: "host"`). Host participant/session is created, selected franchise/team is validated and **assigned/reserved immediately** to host, removed from available teams. Host never sends a team acquisition request for own team. Invalid/missing selected team is rejected (`400 VALIDATION_ERROR`), never silently ignored.
- **Request (required fields):**
```json
{
  "room_name": "PSL Room 1",
  "room_password": "secret123",
  "host_participant_name": "Host User",
  "max_participants": 8,
  "room_configuration_metadata": {},
  "room_expiration_duration_minutes": 60,
  "teams": [
    {"team_id": "team_host", "name": "Host Team"},
    {"team_id": "team_1", "name": "Team 1"}
  ],
  "host_team_id": "team_host"
}
```
Accepted aliases: host team id may be sent as `host_team_id`, `selected_team_id`, `host_franchise_id`, `franchise_id`, `team_id`, `host_selected_team_id`, `selected_franchise_id`, or `host_team.team_id/id/franchise_id`; team items may use `id`/`team_name`.
- **Exact success `201 Created` response (key fields):**
```json
{
  "success": true,
  "room_id": "room_<id>",
  "host_participant_id": "p_host_<id>",
  "host_token": "<host-session-token>",
  "token": "<host-session-token>",
  "host_team_id": "team_host",
  "assigned_team": {
    "team_id": "team_host",
    "name": "Host Team",
    "is_host_team": true,
    "status": "ASSIGNED",
    "owner_participant_id": "p_host_<id>"
  },
  "room": {
    "room_id": "room_<id>",
    "invitation_id": "inv_<id>",
    "status": "READY",
    "name": "PSL Room 1",
    "max_participants": 8,
    "created_at": 1763292600.0,
    "host_participant_id": "p_host_<id>",
    "host_name": "Host User",
    "host_role": "host",
    "host_team_id": "team_host"
  },
  "host": {
    "participant_id": "p_host_<id>",
    "host_participant_id": "p_host_<id>",
    "name": "Host User",
    "role": "host",
    "token": "<host-session-token>",
    "host_token": "<host-session-token>",
    "access_token": "<host-session-token>",
    "auth_token": "<host-session-token>",
    "token_type": "Bearer",
    "token_expires_at": 1763335800.0,
    "expires_at": 1763335800.0,
    "room_id": "room_<id>",
    "team_id": "team_host",
    "assigned_team_id": "team_host"
  },
  "authentication": { "scheme": "Bearer", "token": "<host-session-token>", "host_token": "<host-session-token>", "access_token": "<host-session-token>", "participant_id": "p_host_<id>", "host_participant_id": "p_host_<id>", "role": "host", "team_id": "team_host", "expires_at": 1763335800.0, "header": "Authorization", "header_value_example": "Bearer <host-session-token>", "websocket_query_param": "token" },
  "credentials": { "token": "<host-session-token>", "host_token": "<host-session-token>", "access_token": "<host-session-token>", "participant_id": "p_host_<id>", "role": "host", "team_id": "team_host", "expires_at": 1763335800.0 },
  "lobby_state": { "... see section 3.4 ..." },
  "websocket": { "path_template": "/ws/v1/rooms/{room_id}/", "query_param": "token", "token_query_param": "token", "url_template": "/ws/v1/rooms/<room_id>/?token={token}", "authenticated_url_template": "/ws/v1/rooms/<room_id>/?token={token}" }
}
```
- **Guarantees:** valid host credential bound to room/host; same token for REST (`Authorization: Bearer <token>`) and WS auth; not regenerated on ordinary reconnect; host team reserved/assigned at creation and excluded from available teams; no plaintext password or other participants' tokens exposed.

### 3.3 Join room (participant)
- `POST /api/v1/rooms/{room_id}/join/`
- Request: `{ participant_name, room_password }`
- Success `200`: participant receives `role: "participant"`, initially no assigned team, auth credential/token, room info, `websocket` info, and `lobby_state` (viewer-specific). Response includes compatible fields: `participant_id`, `token`, `participant{...}`, `authentication{...}`, `credentials{...}`.
- Errors: not found/expired/closed/not accepting, wrong password, full, duplicate name, rate limit.

### 3.4 Room / lobby state (authoritative)
- `GET /api/v1/rooms/{room_id}/` with optional `Authorization: Bearer <token>` (recommended for viewer-specific state).
- Success `200`: `{ "room": <lobby_state>, "lobby_state": <lobby_state> }` (no tokens/passwords).
- **Exact `lobby_state` structure:**
```json
{
  "room_id": "room_<id>",
  "name": "PSL Room 1",
  "room_name": "PSL Room 1",
  "status": "READY",
  "internal_status": "READY",
  "host_participant_id": "p_host_<id>",
  "host_name": "Host User",
  "host_online": true,
  "participants_count": 2,
  "max_participants": 8,
  "participants": [
    {"participant_id": "p_host_<id>", "name": "Host User", "role": "host", "connected": true, "team_id": "team_host", "joined_at": 0.0},
    {"participant_id": "p_<id>", "name": "Player 1", "role": "participant", "connected": false, "team_id": null, "joined_at": 0.0}
  ],
  "teams": [
    {"team_id": "team_host", "name": "Host Team", "is_host_team": true, "available": false, "owner_participant_id": "p_host_<id>", "pending_participant_id": null},
    {"team_id": "team_1", "name": "Team 1", "is_host_team": false, "available": true, "owner_participant_id": null, "pending_participant_id": null}
  ],
  "available_teams": [/* available only; host team never included */],
  "team_assignments": [{"team_id": "team_host", "team_name": "Host Team", "participant_id": "p_host_<id>", "is_host_team": true}],
  "pending_team_requests": [/* host viewer only; otherwise [] */],
  "my_team_request": null,
  "my_request_status": null,
  "current_participant_id": "p_<id>",
  "current_participant_role": "participant",
  "configuration_ready": true,
  "accepting_bidding_events": false
}
```
- `pending_team_requests` entries: `{ request_id, room_id, participant_id, team_id, status, created_at, resolved_at }` (visible to host only).
- `my_team_request` / `my_request_status` reflect the viewer's current/latest request (`PENDING|APPROVED|REJECTED|REVOKED|CANCELLED`).
- Host can be reliably identified by `host_participant_id` and `participants[].role == "host"` after reconnect. No other participants' tokens exposed.

### 3.5 Leave room
- `POST /api/v1/rooms/{room_id}/leave/` (Bearer token)
- Success `200`: `{ ok: true, room_id }`
- Host cannot leave via this endpoint.

### 3.6 Delete/close room (host only)
- `DELETE /api/v1/rooms/{room_id}/` (Bearer host token)
- Success `200`: `{ ok: true, room_id, status:"CLOSED" }`
- Side effects: closes room, notifies/disconnects clients, removes temporary data.

### 3.7 Team management (server-authoritative)
- `GET /api/v1/rooms/{room_id}/teams/` (Bearer member): list teams.
- `POST /api/v1/rooms/{room_id}/teams/publish/` (Bearer host): `{ teams:[{team_id,name,is_host_team?|id?}], host_team_id? }` -> (re)publishes team setup pre-auction; host team must be specified and remains assigned/reserved to host.
- `POST /api/v1/rooms/{room_id}/teams/request/` (Bearer **participant**): `{ "team_id": "team_1" }`
  - Validates membership/role (host cannot request), team exists/available/not host-reserved, participant has no team and no pending request, no conflicting pending request.
  - `201` response: `{ "request": {"request_id","room_id","participant_id","team_id","status":"PENDING","created_at","resolved_at":null}, "team": <team state> }`
  - Broadcasts `TEAM_ACQUISITION_REQUESTED`, `TEAM_ASSIGNMENT_UPDATED` (request created), `TEAM_LIST_UPDATED`.
- `POST /api/v1/rooms/{room_id}/teams/requests/{request_id}/approve/` (Bearer **host** only)
  - `200` response: `{ "request": <status APPROVED>, "team": <assigned>, "participant_id": "<id>" }`
  - Assigns team to participant, removes from available, updates participant team; broadcasts `TEAM_ACQUISITION_APPROVED`, `TEAM_ASSIGNMENT_UPDATED` (assigned), `TEAM_LIST_UPDATED`.
- `POST /api/v1/rooms/{room_id}/teams/requests/{request_id}/reject/` (Bearer **host** only)
  - `200` response: `{ "request": <status REJECTED>, "team": <available team> }`
  - Keeps team available; broadcasts `TEAM_ACQUISITION_REJECTED`, `TEAM_ASSIGNMENT_UPDATED` (rejected), `TEAM_LIST_UPDATED`.
- `POST /api/v1/rooms/{room_id}/teams/revoke/` (Bearer **host** only): `{ "team_id": "team_1", "participant_id"? }` (pre-auction). Host's own reserved team cannot be revoked. Broadcasts `TEAM_ASSIGNMENT_UPDATED`.
- **Enforced rules:** one host per room, one team per participant, one participant per team, host team reserved at creation, only host approves/rejects, participants cannot self-assign/approve, concurrent/conflicting requests prevented, operations consistent under server-side locking.

### 3.8 REST error format
```json
{ "error": { "code": "CODE", "message": "...", "details": null } }
```
HTTP statuses used include 400/401/403/404/409/410/429/500 as applicable.

## 4. WebSocket protocol

### 4.1 Connect
- URL: `/ws/v1/rooms/{room_id}/?token=<token>`
- On success server sends `ROOM_STATE_SYNC` and may send cached `AUCTION_STATE_SNAPSHOT`.
- On auth/origin failure connection is rejected/closed.

### 4.2 Server -> client envelope
```json
{
  "room_id": "room_...",
  "event_id": "evt_...",
  "event_type": "EVENT_NAME",
  "sequence_number": 123,
  "sender": {"role": "server|host|participant", "participant_id": "...", "name": "..."},
  "timestamp": 1763292600.123,
  "payload": { }
}
```
- `sequence_number`: monotonic for host-authoritative sequenced broadcasts; may be null for some server events.
- Client should track sequences and request snapshot on gaps/missed events/reconnect.

### 4.3 Event types (server supports/routes)
`ROOM_STATE_SYNC`, `PARTICIPANT_JOINED`, `PARTICIPANT_LEFT`, `HOST_CONNECTED`, `HOST_DISCONNECTED`, `TEAM_LIST_UPDATED`, `TEAM_ACQUISITION_REQUESTED`, `TEAM_ACQUISITION_APPROVED`, `TEAM_ACQUISITION_REJECTED`, `TEAM_ASSIGNMENT_UPDATED`, `AUCTION_READY`, `AUCTION_STARTED`, `AUCTION_PAUSED`, `AUCTION_RESUMED`, `AUCTION_STOPPED`, `CURRENT_PLAYER_UPDATED`, `BID_SUBMITTED`, `BID_ACCEPTED`, `BID_REJECTED`, `TIMER_UPDATED`, `PLAYER_SOLD`, `PLAYER_UNSOLD`, `ROUND_CHANGED`, `AUCTION_COMPLETED`, `AUCTION_STATE_SNAPSHOT`, `ROOM_CLOSED`, `ERROR`.

### 4.4 Client -> server messages
General form: `{ "event_type": "...", "event_id": "unique-id", "payload": {...}, "timestamp"? }`

- **`PING`** -> server responds `PONG`.
- **Participant `BID_SUBMITTED` (request, not authoritative):**  
  `{ "event_type":"BID_SUBMITTED", "event_id":"...", "payload": { "team_id":"...", "amount":..., "player_id"?:"..." } }`  
  Server validates auth + team ownership + bidding allowed + host online + rate limit, then routes to host only. Server never broadcasts as accepted.
- **`REQUEST_STATE_SNAPSHOT` / `REQUEST_ROOM_STATE_SYNC`:** server routes request to host for fresh snapshot (or returns cached snapshot if host offline and available).

### 4.5 Team/request WebSocket events (lobby coordination)
- `ROOM_STATE_SYNC` (on connect/reconnect): payload contains authoritative `room`/`lobby_state` for viewer including `participants` (roles/connected/team_id), `teams`, `available_teams`, `team_assignments`, `pending_team_requests` (host only), `my_team_request`/`my_request_status`, and `host_participant_id`.
- `TEAM_ACQUISITION_REQUESTED`: `{ "request": {request_id,room_id,participant_id,team_id,status:"PENDING",created_at,resolved_at}, "team": {...}, "participant": {participant_id,name,role}, ... }`
- `TEAM_ACQUISITION_APPROVED`: `{ "request": {status:"APPROVED",...}, "team": {owner_participant_id,...,available:false}, "participant_id": "...", "participant": {...} }`
- `TEAM_ACQUISITION_REJECTED`: `{ "request": {status:"REJECTED",...}, "team": {available:true,pending_participant_id:null,...}, "participant": {...} }`
- `TEAM_ASSIGNMENT_UPDATED`: `{ "action": "request_created"|"assigned"|"rejected"|"revoked"|..., "team": {...}, "participant_id": "...", "request": {...}? }`
- `TEAM_LIST_UPDATED`: `{ "room_id": "...", "teams": [ ... ] }`

Clients should use `ROOM_STATE_SYNC` / authenticated `GET /api/v1/rooms/{room_id}/` as source of truth for current assignments and pending requests after reconnect; events provide real-time updates.

### 4.6 Host -> server authoritative messages
Host publishes authoritative events (same envelope fields in client message). Server validates host role, deduplicates `event_id`, assigns sequence, updates minimal routing flags, and broadcasts to room (or targeted snapshot delivery).
- `BID_ACCEPTED` / `BID_REJECTED`: payload as defined by host app (must reflect host AuctionEngine decision).
- `TIMER_UPDATED`: include `remaining_ms`, `timer_mode`, host timestamp/sequence context as available for client display sync.
- `AUCTION_STATE_SNAPSHOT`: full host-generated state; may include `target_participant_id` for targeted sync response.
- Lifecycle/state events: `AUCTION_STARTED/PAUSED/RESUMED/STOPPED/COMPLETED`, `CURRENT_PLAYER_UPDATED`, `PLAYER_SOLD`, `PLAYER_UNSOLD`, `ROUND_CHANGED`, etc.

### 4.7 Error event
`ERROR` payload: `{ "code": "...", "message": "..." }` (e.g. invalid session, forbidden, unsupported event, payload too large, host offline, bidding not allowed, ownership invalid, rate limited, duplicate).

## 5. Expected flows
1. **Create room (host):** host submits room + `teams` + selected `host_team_id` -> server creates host participant (`role: host`), assigns/reserves host team immediately, returns host credentials/IDs/team assignment and `lobby_state`.
2. **Host connects WS:** host connects with token -> receives `ROOM_STATE_SYNC` with authoritative lobby state (assignments, participants, pending requests empty initially). Host does **not** request own team.
3. **Participants join:** participant joins via REST (`role: participant`, no team) -> connects WS -> receives `ROOM_STATE_SYNC` including available teams and own request status.
4. **Team acquisition:** participant sends `POST /rooms/{id}/teams/request` -> server validates and creates pending request -> host receives `TEAM_ACQUISITION_REQUESTED` (and can see pending in lobby state). Host approves/rejects via REST -> server updates authoritative state -> participant receives `TEAM_ACQUISITION_APPROVED` or `TEAM_ACQUISITION_REJECTED` and updated assignments/availability broadcast to room.
5. **Reconnect:** any client reconnects or fetches `GET /rooms/{id}/` (authenticated) and receives current assignments, participants, and pending/own request status from authoritative state.
6. **Auction:** host starts/controls auction on device and emits authoritative events/snapshots via WS; participant bids routed to host for validation; only host-authorized results broadcast.

## 6. Reconnection, disconnect, restart
- Host disconnect: `HOST_DISCONNECTED`, bid requests rejected until host reconnect + resync. Grace period may expire room.
- Participant temporary disconnect: presence notification; participant record/token remain valid until expiry/leave.
- Room delete: `ROOM_CLOSED` and disconnects.
- Server restart: all rooms lost (v1). Clients must detect failure and host must recreate room.

## 7. Integration notes
- Keep messages compact; respect payload limits and rate limits.
- Use server sequence/timestamps to order and smooth timer display; do not treat server as auction timer authority.
- Handle `ERROR`, `ROOM_CLOSED`, and disconnects gracefully in UI.
- All endpoints/events above reflect current backend implementation; if backend version changes, re-check `server_version` from health and updated docs.
