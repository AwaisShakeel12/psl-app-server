# WebSocket Event Protocol Documentation (v1)

## Connection
- **Endpoint:** `/ws/v1/rooms/{room_id}/?token=<participant_or_host_token>`
- **Schemes:** `ws://` for local/dev, `wss://` for production (same path)
- **Auth:** token validated on connect (room membership, expiry, role, room status). Invalid/unauthorized connections are rejected.
- **Origin validation:** if `WS_ALLOWED_ORIGINS` is configured, browser `Origin` must match.

On successful connect server sends:
1. `ROOM_STATE_SYNC` (authoritative lobby/room state for the connected viewer, participant identity, last sequence, snapshot availability). Payload includes `room`/`lobby_state` with participants, roles, connection statuses, team assignments, available teams, pending requests visible to host, and the viewer's own request status so reconnecting clients receive current assignments/requests (not just new events).
2. Cached `AUCTION_STATE_SNAPSHOT` to the connecting client if available (best-effort resync aid)
3. Broadcast presence event (`HOST_CONNECTED` or `PARTICIPANT_JOINED` via websocket)

## Event envelope
All server-sent events use:
```json
{
  "room_id": "room_...",
  "event_id": "evt_..." ,
  "event_type": "EVENT_NAME",
  "sequence_number": 123,
  "sender": {"role": "server|host|participant", "participant_id": "...", "name": "..."},
  "timestamp": 1763292600.123,
  "payload": {}
}
```
Notes:
- `sequence_number` is monotonically increasing for **host-authoritative broadcast events** sequenced by server. Server/presence/control events may have `null` sequence.
- `event_id` used for duplicate protection (bounded in-memory tracking).
- Clients should handle out-of-order/missed sequences by requesting a snapshot.

## Supported event types
`ROOM_STATE_SYNC`, `PARTICIPANT_JOINED`, `PARTICIPANT_LEFT`, `HOST_CONNECTED`, `HOST_DISCONNECTED`, `TEAM_LIST_UPDATED`, `TEAM_ACQUISITION_REQUESTED`, `TEAM_ACQUISITION_APPROVED`, `TEAM_ACQUISITION_REJECTED`, `TEAM_ASSIGNMENT_UPDATED`, `AUCTION_READY`, `AUCTION_STARTED`, `AUCTION_PAUSED`, `AUCTION_RESUMED`, `AUCTION_STOPPED`, `CURRENT_PLAYER_UPDATED`, `BID_SUBMITTED`, `BID_ACCEPTED`, `BID_REJECTED`, `TIMER_UPDATED`, `PLAYER_SOLD`, `PLAYER_UNSOLD`, `ROUND_CHANGED`, `AUCTION_COMPLETED`, `AUCTION_STATE_SNAPSHOT`, `ROOM_CLOSED`, `ERROR`.

## Host-authoritative model
- **Android host app is the auction authority.** Server does not execute auction rules, timers, purse/squad/winner logic.
- Participants may send **requests**; server validates and routes to host. Server never marks a bid accepted on its own.
- Host publishes authoritative outcomes; server validates sender role, applies duplicate protection, assigns sequence (where applicable), updates minimal routing/lifecycle flags, and broadcasts.
- Participants cannot broadcast authoritative events (e.g. `BID_ACCEPTED`, `PLAYER_SOLD`, timer decisions).

## Client -> server messages
Client messages are JSON objects with `event_type` and optional `event_id`, `payload`, `timestamp`.

### `PING`
```json
{ "event_type": "PING" }
```
Server responds with `PONG`.

### `BID_SUBMITTED` (participant request)
```json
{
  "event_type": "BID_SUBMITTED",
  "event_id": "client-or-uuid",
  "payload": { "team_id": "team_1", "amount": 100, "player_id": "..." }
}
```
Server validates: authenticated participant, room accepting bidding, host connected, participant owns `team_id`, rate limit, duplicate `event_id`. On success server forwards `BID_SUBMITTED` **to host only** with participant sender identity. On failure server sends `ERROR` to requester.

### `REQUEST_STATE_SNAPSHOT` / `REQUEST_ROOM_STATE_SYNC`
```json
{ "event_type": "REQUEST_STATE_SNAPSHOT", "event_id": "...", "payload": { "reason": "reconnect" } }
```
Server routes snapshot request to host (includes requester/target participant id). If host offline, server may return cached `AUCTION_STATE_SNAPSHOT` to requester if available.

## Host -> server authoritative messages
Host sends events with `event_type`, `event_id`, `payload` (and optional timestamp/sender metadata). Server requires host role.

Examples (payloads are host-defined/opaque to server except routing fields):
- `AUCTION_STARTED`, `AUCTION_PAUSED`, `AUCTION_RESUMED`, `AUCTION_STOPPED`, `AUCTION_COMPLETED`
- `CURRENT_PLAYER_UPDATED`, `PLAYER_SOLD`, `PLAYER_UNSOLD`, `ROUND_CHANGED`
- `BID_ACCEPTED`, `BID_REJECTED`
- `TIMER_UPDATED` payload should include `remaining_ms`, `timer_mode`, and host event timestamp for client-side display sync
- `AUCTION_STATE_SNAPSHOT` payload may include `target_participant_id` for targeted delivery and full snapshot state for client sync
- `AUCTION_READY`, `TEAM_*` authoritative updates as needed

Server behavior:
- Assigns monotonic `sequence_number` for authoritative broadcasts
- Deduplicates by `event_id` (bounded memory)
- Broadcasts to room, except targeted `AUCTION_STATE_SNAPSHOT` delivered to specific participant when `target_participant_id` connected
- Caches latest snapshot payload/sequence in memory for reconnect assistance

## Server-generated / room coordination events
- `ROOM_STATE_SYNC`: on connect/reconnect and sync responses (includes authoritative lobby state for viewer)
- `HOST_CONNECTED` / `HOST_DISCONNECTED`: host presence changes
- `PARTICIPANT_JOINED` / `PARTICIPANT_LEFT`: join/connect/disconnect/leave notifications
- `ROOM_CLOSED`: room deleted/closed
- `TEAM_ACQUISITION_REQUESTED`: participant submitted a team request (pending). Payload includes `request`, `team`, and participant info.
- `TEAM_ACQUISITION_APPROVED`: host approved request. Payload includes `request` (status `APPROVED`), assigned `team`, and `participant_id`/participant info.
- `TEAM_ACQUISITION_REJECTED`: host rejected request. Payload includes `request` (status `REJECTED`) and `team` (available/pending cleared).
- `TEAM_ASSIGNMENT_UPDATED`: assignment/request state changed (actions include request created, assigned, rejected, revoked, participant left as applicable). Payload includes relevant `team`, `participant_id`, and/or `request`.
- `TEAM_LIST_UPDATED`: team availability/ownership state changed (payload includes `teams`).
- `ERROR`: validation/protocol/routing errors

Clients should treat `GET /api/v1/rooms/{room_id}/` (authenticated) and `ROOM_STATE_SYNC` as authoritative sources for current assignments and pending requests; events are incremental notifications.

## Disconnect and reconnect behavior
- **Host disconnect:** server marks host offline, broadcasts `HOST_DISCONNECTED`, rejects new bid requests, stops routing to host. Grace period (`ROOM_ABANDONED_HOST_GRACE_MINUTES`) applies before automatic close/expiry per policy.
- **Host reconnect:** with valid token, server restores host presence, broadcasts `HOST_CONNECTED`, sends sync/snapshot-if-cached; host should provide fresh authoritative snapshot to fully restore operation.
- **Participant disconnect:** temporary; participant record remains, server notifies. Reconnect via same token re-establishes WS and receives sync/cached snapshot.
- **Participant leave (REST):** explicit removal/release and notification.

## Limits and safety
- Max payload size enforced (`MAX_WS_PAYLOAD_BYTES`).
- Bid request throttling (`BID_REQUEST_RATE_LIMIT_PER_MINUTE`).
- No unlimited server-side event history; bounded duplicate-id tracking and cached latest snapshot only.
- Sensitive tokens/passwords are not included in normal protocol payloads/logs.
