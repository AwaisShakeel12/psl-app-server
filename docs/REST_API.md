# REST API Documentation (v1)

Base path: `/api/v1`  
Content type: `application/json` (except no body required for GET/DELETE in most cases)  
Auth: `Authorization: Bearer <token>` for protected endpoints.

## Error format
Errors return JSON:
```json
{
  "error": {
    "code": "STRING_CODE",
    "message": "Human readable message",
    "details": null
  }
}
```
Common codes include: `VALIDATION_ERROR`, `AUTH_REQUIRED`, `INVALID_TOKEN`, `TOKEN_ROOM_MISMATCH`, `HOST_REQUIRED`, `ROOM_NOT_FOUND`, `ROOM_CLOSED`, `ROOM_EXPIRED`, `ROOM_NOT_ACCEPTING`, `ROOM_FULL`, `INVALID_PASSWORD`, `DUPLICATE_PARTICIPANT`, `ROOM_LIMIT_REACHED`, `RATE_LIMITED`, `TEAM_*`, `REQUEST_*`, etc.

## Health
### `GET /api/v1/health/`
- **Auth:** none
- **Response 200:**
```json
{
  "status": "ok",
  "api_available": true,
  "websocket_available": true,
  "server_version": "0.1.0",
  "active_rooms": 0,
  "active_connections": 0,
  "memory_mb": 42.1,
  "timestamp": 1763292600.0
}
```

## Rooms
### `POST /api/v1/rooms/` (Create room)
- **Auth:** none
- **Rate limit:** create-room per IP (configurable)
- **Authoritative host behavior:** the creator is automatically created as room **host** (`role: "host"`). The host's selected franchise/team is submitted at creation, validated, **assigned and reserved immediately** to the host, and excluded from available teams. The host must never send a team acquisition request for their own team.
- **Body (required):**
```json
{
  "room_name": "PSL Room 1",
  "room_password": "secret123",
  "host_participant_name": "Host User",
  "max_participants": 8,
  "room_configuration_metadata": {"any": "json-object"},
  "room_expiration_duration_minutes": 60,
  "teams": [
    {"team_id": "team_host", "name": "Host Team"},
    {"team_id": "team_1", "name": "Team 1"},
    {"team_id": "team_2", "name": "Team 2"}
  ],
  "host_team_id": "team_host"
}
```
Notes:
- `teams` must be a non-empty list of objects containing `team_id` and `name` (aliases accepted: `id`, `team_name`).
- `host_team_id` is required and must match a team in `teams` (aliases accepted: `selected_team_id`, `host_franchise_id`, `franchise_id`, `team_id`, `host_selected_team_id`, `selected_franchise_id`, or nested `host_team.team_id`).
- Invalid/missing host team selection is rejected with `400 VALIDATION_ERROR` (not silently ignored).
- `room_password` min length 4; password never returned.

- **Exact successful response `201 Created` (key fields):**
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
  "authentication": {
    "scheme": "Bearer",
    "token_type": "Bearer",
    "token": "<host-session-token>",
    "host_token": "<host-session-token>",
    "access_token": "<host-session-token>",
    "participant_id": "p_host_<id>",
    "host_participant_id": "p_host_<id>",
    "role": "host",
    "team_id": "team_host",
    "expires_at": 1763335800.0,
    "header": "Authorization",
    "header_value_example": "Bearer <host-session-token>",
    "websocket_query_param": "token"
  },
  "credentials": {
    "token": "<host-session-token>",
    "host_token": "<host-session-token>",
    "access_token": "<host-session-token>",
    "participant_id": "p_host_<id>",
    "role": "host",
    "team_id": "team_host",
    "expires_at": 1763335800.0
  },
  "lobby_state": { "... authoritative lobby snapshot (see room state section) ..." },
  "websocket": {
    "path_template": "/ws/v1/rooms/{room_id}/",
    "query_param": "token",
    "token_query_param": "token",
    "authentication": "Pass the participant/host token as the 'token' query parameter when connecting.",
    "note": "Use ws:// for local/dev (LAN) and wss:// for production with the same path.",
    "url_template": "/ws/v1/rooms/<room_id>/?token={token}",
    "authenticated_url_template": "/ws/v1/rooms/<room_id>/?token={token}"
  }
}
```
**Compatibility/guarantees:** host credential returned in multiple explicit fields; same token valid for REST `Authorization: Bearer <token>` and WebSocket auth; not regenerated on ordinary reconnect. Server is authoritative for host role and host team reservation. No plaintext password or other participants' tokens returned.

### `POST /api/v1/rooms/{room_id}/join/` (Join room)
- **Auth:** none (room password required in body)
- **Rate limit:** join per IP+room (configurable)
- **Body:**
```json
{ "participant_name": "Player 1", "room_password": "secret123" }
```
- **Response 200:** returns participant identity and credential in `participant` plus compatibility aliases (`participant_id`, `token`, `authentication`, `credentials`) along with room and websocket info. The participant token is used as `Authorization: Bearer <token>` for REST and as `token` query param for WebSocket.
- **Rejects:** room not found/closed/expired, wrong password, full, duplicate participant name, room not accepting (e.g. in progress/completed).

### `GET /api/v1/rooms/{room_id}/` (Room / lobby state)
- **Auth:** optional `Authorization: Bearer <token>` (recommended). If provided and valid for the room, response is tailored to the viewer.
- **Response 200:** `{ "room": <lobby_state>, "lobby_state": <lobby_state> }` with authoritative room/team/participant/request state for lobby rendering (no passwords/tokens/other participants' tokens).
- **`lobby_state` fields (exact/current):**
  - `room_id`, `name`, `room_name`, `status`, `internal_status`
  - `host_participant_id`, `host_name`, `host_online`
  - `participants_count`, `max_participants`
  - `participants`: `[{ participant_id, name, role, connected, team_id, joined_at }]`
  - `teams`: `[{ team_id, name, is_host_team, available, owner_participant_id, pending_participant_id }]`
  - `available_teams`: teams available for participant requests (host-assigned/reserved and owned/pending teams excluded)
  - `team_assignments`: `[{ team_id, team_name, participant_id, is_host_team }]`
  - `pending_team_requests`: **host only** (empty for participants/anonymous) — pending requests `[{ request_id, room_id, participant_id, team_id, status, created_at, resolved_at }]`
  - `my_team_request`: viewer's latest/pending request object or `null`
  - `my_request_status`: viewer's request status or `null` (`PENDING|APPROVED|REJECTED|REVOKED|CANCELLED`)
  - `current_participant_id`, `current_participant_role` (viewer context; `null` if unauthenticated)
  - `configuration_ready`, `accepting_bidding_events`
- **Security:** pending requests are only exposed to the host viewer; tokens of other participants are never included. Host can be reliably identified via `host_participant_id` and participant roles after reconnect.

### `POST /api/v1/rooms/{room_id}/leave/`
- **Auth:** Bearer participant/host token (host leave rejected; host must delete room)
- **Response 200:** `{ "ok": true, "room_id": "..." }`
- **Behavior:** removes participant presence/session for explicit leave, releases owned/pending team claims, cancels pending requests.

### `DELETE /api/v1/rooms/{room_id}/`
- **Auth:** Bearer **host** token only
- **Response 200:** `{ "ok": true, "room_id": "...", "status": "CLOSED" }`
- **Behavior:** marks closed, broadcasts `ROOM_CLOSED`, removes temporary room data, prevents further joins/connections.

## Team coordination
All team endpoints below require `Authorization: Bearer <token>` for a participant in the room (host where specified).

### `GET /api/v1/rooms/{room_id}/teams/`
- **Auth:** any room member token
- **Response 200:** `{ "room_id": "...", "teams": [ ... ] }`

### `POST /api/v1/rooms/{room_id}/teams/publish/` (Host only)
- **Auth:** host
- **Body:**
```json
{
  "teams": [
    {"team_id": "team_host", "name": "Host Team", "is_host_team": true},
    {"team_id": "team_1", "name": "Team 1"}
  ]
}
```
- **Behavior:** host sets/republishes available teams, reserves host team, resets pending/ownership state for publish (pre-auction setup), may move room to `READY` and emits `TEAM_LIST_UPDATED`/`AUCTION_READY`.

### `POST /api/v1/rooms/{room_id}/teams/request/` (Participant only)
- **Auth:** participant (Bearer token, room member)
- **Body:** `{ "team_id": "team_1" }`
- **Behavior (server-authoritative):** validates room membership and requester role is `participant` (host cannot request), team exists, team not assigned/reserved/host-owned, participant has no existing owned team and no pending request, no conflicting pending request on team. Creates a `PENDING` team acquisition request and reserves team as pending.
- **Response 201:** `{ "request": {request_id,room_id,participant_id,team_id,status,created_at,resolved_at}, "team": <team public state> }`
- **Events:** broadcasts `TEAM_ACQUISITION_REQUESTED` (includes request/team/participant info) and `TEAM_ASSIGNMENT_UPDATED` (request created) and `TEAM_LIST_UPDATED` to room.

### `POST /api/v1/rooms/{room_id}/teams/requests/{request_id}/approve/` (Host only)
- **Auth:** host
- **Behavior:** only host may approve; validates pending request, assigns requested team to participant, sets request `APPROVED`, removes team from available teams, updates participant `team_id`. Prevents duplicate/conflicting assignment.
- **Response 200:** `{ "request": <approved>, "team": <assigned team>, "participant_id": "<id>" }`
- **Events:** `TEAM_ACQUISITION_APPROVED`, `TEAM_ASSIGNMENT_UPDATED` (assigned), `TEAM_LIST_UPDATED`.

### `POST /api/v1/rooms/{room_id}/teams/requests/{request_id}/reject/` (Host only)
- **Auth:** host
- **Behavior:** only host may reject; sets request `REJECTED`, releases pending reservation, keeps team available.
- **Response 200:** `{ "request": <rejected>, "team": <team public state> }`
- **Events:** `TEAM_ACQUISITION_REJECTED`, `TEAM_ASSIGNMENT_UPDATED` (rejected), `TEAM_LIST_UPDATED`.

### `POST /api/v1/rooms/{room_id}/teams/revoke/` (Host only)
- **Auth:** host
- **Body:** `{ "team_id": "team_1", "participant_id": "p_..." }` (`participant_id` optional validation aid)
- **Behavior:** revokes participant assignment before auction starts (`WAITING`/`READY`); host's own reserved team cannot be revoked. Releasing is server-authoritative and consistent with request state.

### `POST /api/v1/rooms/{room_id}/teams/requests/{request_id}/approve/` (Host only)
- **Auth:** host
- **Behavior:** assigns ownership if still valid, clears pending, updates participant `team_id`, emits approval/assignment events.

### `POST /api/v1/rooms/{room_id}/teams/requests/{request_id}/reject/` (Host only)
- **Auth:** host
- **Behavior:** rejects pending request and releases pending reservation.

### `POST /api/v1/rooms/{room_id}/teams/revoke/` (Host only)
- **Auth:** host
- **Body:** `{ "team_id": "team_1", "participant_id": "p_..." }` (`participant_id` optional validation aid)
- **Behavior:** revokes assignment before auction starts (`WAITING`/`READY`), releases ownership/pending.

## Security/validation notes
- Server validates token, room membership, expiry, and role on protected requests.
- Server does not trust client-supplied participant/team IDs without token-bound verification.
- Passwords/tokens are never returned in safe info endpoints; sensitive values are not logged.
- Temporary rooms/sessions are in-memory (single-process) and may be lost on restart.
