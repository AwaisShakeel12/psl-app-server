# Room Lifecycle Documentation

## States
- **WAITING**: Room created, accepting joins/team setup (subject to limits/expiry).
- **READY**: Teams published/setup ready (host can start auction flow from Android app).
- **IN_PROGRESS**: Auction active; host-authoritative events streamed. Bidding requests accepted only when host connected and room marked accepting bidding events by host flow.
- **PAUSED**: Auction paused by host; bidding requests rejected until resumed.
- **COMPLETED**: Auction completed by host; retained for a configurable period, then closed/cleaned up.
- **CLOSED**: Terminal state; no new joins/connections/actions; room data removed after tombstone/cleanup.
- **HOST_DISCONNECTED** (overlay/public status): Exposed in room info when host is offline during active/ready states; internal lifecycle state remains as set by host events until reconnect/expiry/close.

## Creation and joining
- Host creates room via `POST /api/v1/rooms/` with name, hashed password, host identity, max participants, metadata, optional expiry.
- Participants join via `POST /api/v1/rooms/{room_id}/join/` with correct password while room is accepting participants.
- Safe room info (`GET /api/v1/rooms/{room_id}/`) exposes status, counts, host online, teams (no secrets).

## Team coordination (pre-auction)
- Host publishes team list; host team reserved.
- Participants request available teams; host approves/rejects.
- One owner per team, one team per participant (initial), pending requests tracked, rejections release reservation, host can revoke before auction start.

## Active auction coordination
- Host Android app controls auction engine and timer.
- Server routes participant `BID_SUBMITTED` requests to host after validation (auth, ownership, room accepting bids, host online, rate limit).
- Host emits authoritative results/events (`BID_ACCEPTED`/`BID_REJECTED`, player/timer/round/state events). Server sequences/broadcasts; server does not compute auction outcomes.

## Host disconnect/reconnect
- On host disconnect: host marked offline, `HOST_DISCONNECTED` broadcast, bidding requests rejected, routing to host stops.
- Reconnect grace period: `ROOM_ABANDONED_HOST_GRACE_MINUTES` (config). If exceeded, room may be auto-closed/expired.
- On host reconnect (valid token): host presence restored, `HOST_CONNECTED` broadcast, sync/snapshot exchange enables host to resync clients. Server never auto-promotes participant to host.

## Expiration and cleanup
- **Waiting rooms:** expire after `ROOM_WAITING_EXPIRY_MINUTES` (or provided duration) -> closed with tombstone then removed.
- **Completed rooms:** retained for `ROOM_COMPLETED_RETENTION_MINUTES` -> closed/removed.
- **Abandoned (host offline past grace):** closed/expired by cleanup policy.
- **Explicit delete:** host-only `DELETE /api/v1/rooms/{room_id}/` closes immediately, broadcasts `ROOM_CLOSED`, removes temporary data.
- Cleanup runs on access/operations in single-process in-memory store and uses bounded structures.
- **Restart behavior:** all in-memory rooms/tokens/connections are lost on process restart (acceptable v1). Android host must detect room loss and recreate the room.

## Memory/resource constraints
- Designed for ~512 MB VPS: single ASGI process, in-memory channel layer, no Redis/Celery/large datasets, compact messages, bounded caches (event-id history, rate limiter windows), connection/room limits.
