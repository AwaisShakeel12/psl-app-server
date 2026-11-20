from __future__ import annotations

import json
import logging
import time
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

from app.services.auth import AuthError, validate_ws_token
from app.services.notify import room_group_name
from app.state.store import ROOM_CLOSED, ROOM_COMPLETED, ROOM_IN_PROGRESS, ROOM_PAUSED, ROOM_READY, ROOM_WAITING, store
from app.ws.protocol import (
    HOST_AUTHORITATIVE_EVENTS,
    PARTICIPANT_REQUEST_EVENTS,
    make_envelope,
    new_event_id,
    now_ts,
)

logger = logging.getLogger(__name__)


class RoomConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.room_id = self.scope["url_route"]["kwargs"]["room_id"]
        self.group_name = room_group_name(self.room_id)
        self.participant = None
        self.room = None
        self._joined_group = False

        headers = {k.decode().lower(): v.decode() for k, v in (self.scope.get("headers") or [])}
        origin = headers.get("origin")

        # Origin validation: native mobile clients may omit Origin; in DEBUG allow to support local/LAN dev.
        # In production, if WS_ALLOWED_ORIGINS is configured, require browser origins to match when present.
        allowed_origins = getattr(settings, "WS_ALLOWED_ORIGINS", []) or []
        debug = bool(getattr(settings, "DEBUG", False))
        if not self._is_origin_allowed(origin, allowed_origins, debug):
            logger.warning(
                "WebSocket rejected: origin not allowed (room_id=%s, has_origin=%s)",
                self.room_id,
                bool(origin),
            )
            await self.close(code=4403)
            return

        token = self._extract_ws_token(headers)
        if not token:
            logger.warning("WebSocket rejected: missing authentication credential (room_id=%s)", self.room_id)
            await self.close(code=4401)
            return

        try:
            room, participant = await database_sync_to_async(validate_ws_token)(token, self.room_id)
        except AuthError as exc:
            reason = {
                "AUTH_REQUIRED": "missing authentication credential",
                "INVALID_TOKEN": "invalid token",
                "TOKEN_ROOM_MISMATCH": "token does not belong to room",
                "ROOM_CLOSED": "room is closed",
            }.get(getattr(exc, "code", ""), "authentication failed")
            logger.warning("WebSocket rejected: %s (room_id=%s)", reason, self.room_id)
            await self.close(code=4401)
            return
        except Exception:
            logger.warning("WebSocket rejected: authentication error (room_id=%s)", self.room_id)
            await self.close(code=4401)
            return

        self.room = room
        self.participant = participant

        await self.accept()
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        self._joined_group = True

        conn_info = await database_sync_to_async(self._on_connect_state)()
        await self.send_json(
            make_envelope(
                self.room_id,
                "ROOM_STATE_SYNC",
                {
                    "room": conn_info["room_state"],
                    "lobby_state": conn_info["room_state"],
                    "participant": {
                        "participant_id": self.participant.participant_id,
                        "name": self.participant.name,
                        "role": self.participant.role,
                        "team_id": self.participant.team_id,
                        "connected": True,
                    },
                    "current_participant_id": self.participant.participant_id,
                    "current_participant_role": self.participant.role,
                    "last_sequence_number": conn_info["last_seq"],
                    "last_snapshot_available": conn_info["has_snapshot"],
                },
                sender={"role": "server"},
            )
        )

        # If snapshot cached, send to reconnecting/connecting client for quick sync
        if conn_info.get("snapshot"):
            await self.send_json(
                make_envelope(
                    self.room_id,
                    "AUCTION_STATE_SNAPSHOT",
                    conn_info["snapshot"],
                    event_id=conn_info["snapshot"].get("event_id") if isinstance(conn_info["snapshot"], dict) else None,
                    sequence_number=conn_info.get("snapshot_seq"),
                    sender={"role": "host", "participant_id": self.room.host_participant_id},
                )
            )

        if self.participant.role == "host":
            await self._group_broadcast_server(
                "HOST_CONNECTED",
                {"participant_id": self.participant.participant_id, "name": self.participant.name, "room_status": conn_info["room_public"]["status"]},
            )
        else:
            await self._group_broadcast_server(
                "PARTICIPANT_JOINED",
                {
                    "participant": {
                        "participant_id": self.participant.participant_id,
                        "name": self.participant.name,
                        "role": self.participant.role,
                    },
                    "via": "websocket",
                },
            )

    def _is_origin_allowed(self, origin: str | None, allowed_origins: list[str], debug: bool) -> bool:
        # Native/non-browser clients often do not send Origin.
        if not origin:
            return True
        if debug:
            return True
        if not allowed_origins:
            # If not explicitly configured in production, be permissive rather than breaking native clients;
            # token auth remains enforced. (Prefer configuring WS_ALLOWED_ORIGINS in production.)
            return True
        if origin in allowed_origins:
            return True
        # Allow same-host origins commonly seen in local/dev/proxied setups.
        req_host = None
        for k, v in (self.scope.get("headers") or []):
            if k.decode().lower() == "host":
                req_host = v.decode()
                break
        if req_host and (origin.endswith("://" + req_host) or origin.endswith("://" + req_host + "/")):
            return True
        return False

    def _extract_ws_token(self, headers: dict) -> str | None:
        query = parse_qs(self.scope.get("query_string", b"").decode())
        # Common query param names (compat with existing/mobile clients)
        for key in ("token", "access_token", "auth_token", "host_token", "participant_token"):
            vals = query.get(key)
            if vals and vals[0]:
                return vals[0].strip()
        # Authorization: Bearer <token>
        auth = headers.get("authorization")
        if auth:
            parts = auth.split(" ", 1)
            if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
                return parts[1].strip()
            # Some clients may send raw token
            if len(parts) == 1 and auth.strip():
                return auth.strip()
        # Fallback: some proxies/clients may use custom header
        for h in ("x-auth-token", "x-access-token"):
            if headers.get(h):
                return headers[h].strip()
        return None


    async def disconnect(self, close_code):
        if getattr(self, "_joined_group", False):
            try:
                await self.channel_layer.group_discard(self.group_name, self.channel_name)
            except Exception:
                pass
        if getattr(self, "participant", None) and getattr(self, "room", None):
            try:
                info = await database_sync_to_async(self._on_disconnect_state)()
            except Exception:
                info = None
            if self.participant.role == "host":
                await self._group_broadcast_server(
                    "HOST_DISCONNECTED",
                    {
                        "participant_id": self.participant.participant_id,
                        "name": self.participant.name,
                        "room_status": (info or {}).get("room_public", {}).get("status"),
                    },
                )
            else:
                # Temporary disconnect; participant record remains (explicit leave handled via REST)
                await self._group_broadcast_server(
                    "PARTICIPANT_LEFT",
                    {
                        "participant": {
                            "participant_id": self.participant.participant_id,
                            "name": self.participant.name,
                            "role": self.participant.role,
                        },
                        "reason": "disconnected",
                    },
                )

    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        # Payload size guard
        max_bytes = getattr(settings, "MAX_WS_PAYLOAD_BYTES", 65536)
        if text_data is not None and len(text_data.encode("utf-8")) > max_bytes:
            await self._send_error("PAYLOAD_TOO_LARGE", "WebSocket payload exceeds maximum allowed size")
            return
        if bytes_data is not None and len(bytes_data) > max_bytes:
            await self._send_error("PAYLOAD_TOO_LARGE", "WebSocket payload exceeds maximum allowed size")
            return
        try:
            if text_data is not None:
                content = json.loads(text_data)
            elif bytes_data is not None:
                content = json.loads(bytes_data.decode("utf-8"))
            else:
                content = {}
        except Exception:
            await self._send_error("INVALID_JSON", "Invalid JSON payload")
            return
        await self.receive_json(content)

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            await self._send_error("INVALID_PAYLOAD", "Message must be a JSON object")
            return

        # Refresh room/participant references
        try:
            refreshed = await database_sync_to_async(self._refresh_state)()
            if not refreshed:
                await self._send_error("INVALID_SESSION", "Session no longer valid", fatal=True)
                return
        except Exception:
            await self._send_error("INTERNAL_ERROR", "Failed to validate session")
            return

        event_type = content.get("event_type") or content.get("type")
        if not event_type or not isinstance(event_type, str):
            await self._send_error("MISSING_EVENT_TYPE", "event_type is required")
            return

        if event_type == "PING":
            await self.send_json(make_envelope(self.room_id, "PONG", {"ts": now_ts()}, sender={"role": "server"}))
            return

        # Participant request events
        if event_type in PARTICIPANT_REQUEST_EVENTS:
            await self._handle_participant_request(event_type, content)
            return

        # Host authoritative events
        if event_type in HOST_AUTHORITATIVE_EVENTS:
            await self._handle_host_authoritative(event_type, content)
            return

        await self._send_error("UNSUPPORTED_EVENT", f"Event type not supported: {event_type}")

    async def room_event(self, event):
        payload = event.get("event") or {}
        payload.setdefault("timestamp", now_ts())
        await self.send_json(payload)

    async def room_direct(self, event):
        payload = event.get("event") or {}
        payload.setdefault("timestamp", now_ts())
        await self.send_json(payload)

    # ----- handlers -----
    async def _handle_participant_request(self, event_type: str, content: dict):
        if self.participant.role == "host":
            # Host may request snapshot broadcast too via REQUEST_STATE_SNAPSHOT
            if event_type not in ("REQUEST_STATE_SNAPSHOT", "REQUEST_ROOM_STATE_SYNC"):
                await self._send_error("INVALID_SENDER", "Host cannot submit participant bid requests")
                return

        if event_type == "BID_SUBMITTED":
            await self._handle_bid_request(content)
            return

        if event_type in ("REQUEST_STATE_SNAPSHOT", "REQUEST_ROOM_STATE_SYNC"):
            await self._handle_snapshot_request(content)
            return

        if event_type in ("MP_SQUAD_UPDATED", "MP_GOLDEN_BAT_REQUEST"):
            await self._forward_participant_request_to_host(event_type, content)
            return

    async def _handle_bid_request(self, content: dict):
        # Validate room accepting bidding, participant owns team, rate limit, host connected
        ok, err = await database_sync_to_async(self._validate_bid_request)(content)
        if not ok:
            await self._send_error(err["code"], err["message"])
            return

        host_channel = await database_sync_to_async(self._get_host_channel_name)()
        if not host_channel:
            await self._send_error("HOST_OFFLINE", "Host is not connected; bid request cannot be routed")
            return

        payload = content.get("payload") or {}
        event_id = content.get("event_id") or new_event_id()
        # Dedup incoming request ids best-effort
        is_new = await database_sync_to_async(lambda: store.register_event_id(self.room, event_id))()
        if not is_new:
            await self._send_error("DUPLICATE_EVENT", "Duplicate event_id ignored")
            return

        envelope = make_envelope(
            self.room_id,
            "BID_SUBMITTED",
            payload,
            event_id=event_id,
            sequence_number=None,
            sender={
                "role": "participant",
                "participant_id": self.participant.participant_id,
                "name": self.participant.name,
                "team_id": self.participant.team_id,
            },
            timestamp=content.get("timestamp") or now_ts(),
        )
        await self.channel_layer.send(host_channel, {"type": "room.direct", "event": envelope})

    async def _forward_participant_request_to_host(self, event_type: str, content: dict):
        """Relay room-setup / golden-bat requests to the authoritative host device.

        No bid validation here: these requests only carry the participant's own team squad
        edits or a golden-bat activation request. The host validates them against the room's
        team ownership before acting, exactly like bid requests.
        """
        host_channel = await database_sync_to_async(self._get_host_channel_name)()
        if not host_channel:
            await self._send_error("HOST_OFFLINE", "Host is not connected; request cannot be routed")
            return

        payload = content.get("payload") or {}
        event_id = content.get("event_id") or new_event_id()
        is_new = await database_sync_to_async(lambda: store.register_event_id(self.room, event_id))()
        if not is_new:
            await self._send_error("DUPLICATE_EVENT", "Duplicate event_id ignored")
            return

        envelope = make_envelope(
            self.room_id,
            event_type,
            payload,
            event_id=event_id,
            sequence_number=None,
            sender={
                "role": "participant",
                "participant_id": self.participant.participant_id,
                "name": self.participant.name,
                "team_id": self.participant.team_id,
            },
            timestamp=content.get("timestamp") or now_ts(),
        )
        await self.channel_layer.send(host_channel, {"type": "room.direct", "event": envelope})

    async def _handle_snapshot_request(self, content: dict):
        host_channel = await database_sync_to_async(self._get_host_channel_name)()
        if not host_channel:
            # fallback: send cached snapshot to requester if available
            snap = await database_sync_to_async(lambda: (self.room.last_snapshot, self.room.last_snapshot_seq))()
            if snap[0]:
                await self.send_json(
                    make_envelope(
                        self.room_id,
                        "AUCTION_STATE_SNAPSHOT",
                        snap[0],
                        sequence_number=snap[1],
                        sender={"role": "server", "note": "cached_snapshot"},
                    )
                )
                return
            await self._send_error("HOST_OFFLINE", "Host is not connected; snapshot unavailable")
            return
        envelope = make_envelope(
            self.room_id,
            "REQUEST_STATE_SNAPSHOT",
            {
                "requested_by": {
                    "participant_id": self.participant.participant_id,
                    "role": self.participant.role,
                },
                "target_participant_id": self.participant.participant_id,
                "reason": content.get("payload", {}).get("reason") if isinstance(content.get("payload"), dict) else None,
            },
            event_id=content.get("event_id") or new_event_id(),
            sender={"role": self.participant.role, "participant_id": self.participant.participant_id},
        )
        await self.channel_layer.send(host_channel, {"type": "room.direct", "event": envelope})

    async def _handle_host_authoritative(self, event_type: str, content: dict):
        if self.participant.role != "host":
            await self._send_error("FORBIDDEN", "Only host can publish authoritative auction events")
            return

        event_id = content.get("event_id") or new_event_id()
        is_new = await database_sync_to_async(lambda: store.register_event_id(self.room, event_id))()
        if not is_new:
            # duplicate protection: ignore silently
            return

        payload = content.get("payload") if isinstance(content.get("payload"), dict) or content.get("payload") is not None else {}

        # Room-owned setup (teams/pool/lot order/squads/rules) lives on the server's room
        # metadata so late joiners receive it on connect/restore. The event itself is still
        # broadcast below so already-connected participants update immediately.
        if event_type == "ROOM_CONFIG_UPDATED" and isinstance(payload, dict):
            cfg = payload.get("auction_config")
            if isinstance(cfg, dict):
                await database_sync_to_async(self._merge_room_config)(cfg)

        target_participant_id = None
        if event_type == "AUCTION_STATE_SNAPSHOT" and isinstance(payload, dict):
            target_participant_id = payload.get("target_participant_id") or content.get("target_participant_id")

        seq, snapshot_cached = await database_sync_to_async(self._prepare_host_broadcast)(event_type, payload, event_id)
        sender = content.get("sender") if isinstance(content.get("sender"), dict) else {}
        sender = {
            "role": "host",
            "participant_id": self.participant.participant_id,
            "name": self.participant.name,
            **{k: v for k, v in sender.items() if k not in ("role", "participant_id", "name")},
        }
        envelope = make_envelope(
            self.room_id,
            event_type,
            payload,
            event_id=event_id,
            sequence_number=seq,
            sender=sender,
            timestamp=content.get("timestamp") or now_ts(),
        )

        if event_type == "AUCTION_STATE_SNAPSHOT" and target_participant_id:
            target_channel = await database_sync_to_async(self._get_participant_channel_name)(target_participant_id)
            if target_channel:
                await self.channel_layer.send(target_channel, {"type": "room.direct", "event": envelope})
                return
            # fallback to broadcast if target not connected

        await self.channel_layer.group_send(self.group_name, {"type": "room.event", "event": envelope})

    # ----- state helpers (sync, called via database_sync_to_async) -----
    def _refresh_state(self) -> bool:
        room, participant = store.get_by_token(self.participant.token)
        if not room or not participant or room.room_id != self.room_id:
            return False
        self.room = room
        self.participant = participant
        return True

    def _on_connect_state(self):
        store.set_connection(self.participant, True, self.channel_name)
        store.connection_counts = max(0, store.connection_counts) + 1
        if self.participant.role == "host":
            self.room.host_connected = True
            self.room.host_disconnected_at = None
        self.room.touch()
        return {
            "room_state": self.room.lobby_state(self.participant),
            "room_public": self.room.public_info(),
            "last_seq": self.room.authoritative_seq,
            "has_snapshot": self.room.last_snapshot is not None,
            "snapshot": self.room.last_snapshot,
            "snapshot_seq": self.room.last_snapshot_seq,
        }

    def _on_disconnect_state(self):
        store.set_connection(self.participant, False, None)
        store.connection_counts = max(0, store.connection_counts - 1)
        if self.participant.role == "host":
            self.room.host_connected = False
            self.room.host_disconnected_at = time.time()
            self.room.accepting_bidding_events = False
        self.room.touch()
        return {"room_public": self.room.public_info(), "room_state": self.room.lobby_state(self.participant)}

    def _validate_bid_request(self, content: dict):
        payload = content.get("payload") or {}
        team_id = payload.get("team_id") if isinstance(payload, dict) else None
        if self.participant.role != "participant":
            return False, {"code": "FORBIDDEN", "message": "Only participants can submit bid requests"}
        if self.room.status == ROOM_CLOSED:
            return False, {"code": "ROOM_CLOSED", "message": "Room is closed"}
        if not self.room.host_connected:
            return False, {"code": "HOST_OFFLINE", "message": "Host is not connected"}
        if not store.is_bidding_allowed(self.room):
            return False, {"code": "BIDDING_NOT_ALLOWED", "message": "Room is not accepting bidding events"}
        # ownership validation
        if not team_id or self.participant.team_id != team_id:
            return False, {"code": "TEAM_OWNERSHIP_INVALID", "message": "Participant does not own the specified team"}
        team = self.room.teams.get(team_id)
        if not team or team.owner_participant_id != self.participant.participant_id:
            return False, {"code": "TEAM_OWNERSHIP_INVALID", "message": "Team ownership validation failed"}
        # rate limit
        limit = getattr(settings, "BID_REQUEST_RATE_LIMIT_PER_MINUTE", 60)
        key = f"bid:{self.room.room_id}:{self.participant.participant_id}"
        if not store.rate_limiter.allow(key, limit, 60):
            return False, {"code": "RATE_LIMITED", "message": "Bid request rate limit exceeded"}
        return True, None

    def _get_host_channel_name(self):
        host = self.room.get_host_participant()
        if host and host.connected and host.channel_name:
            return host.channel_name
        # fallback: search by role
        for p in self.room.participants.values():
            if p.role == "host" and p.connected and p.channel_name:
                return p.channel_name
        return None

    def _get_participant_channel_name(self, participant_id: str):
        p = self.room.participants.get(participant_id)
        if p and p.connected and p.channel_name:
            return p.channel_name
        return None

    def _merge_room_config(self, cfg: dict):
        current = dict(self.room.room_config_metadata or {})
        current.update(cfg)
        self.room.room_config_metadata = current

        # The host's room-owned team list is the authority for enable/disable (Issue 3).
        # Mirror is_participating onto the server teams so Available Teams stays consistent
        # for late joiners and for clients that read the HTTP lobby snapshot. Teams introduce
        # no AI-mode server state — this is the multiplayer room's own team list only.
        mp_teams_raw = current.get("mp_teams")
        if isinstance(mp_teams_raw, str) and mp_teams_raw.strip():
            try:
                mp_teams = json.loads(mp_teams_raw)
            except (ValueError, TypeError):
                mp_teams = None
            if isinstance(mp_teams, list):
                for item in mp_teams:
                    if not isinstance(item, dict):
                        continue
                    tid = str(item.get("id") or item.get("team_id") or "").strip()
                    team = self.room.teams.get(tid)
                    if team is None:
                        continue
                    team.is_participating = bool(item.get("is_participating", True)) or team.is_host_team
        self.room.touch()

    def _prepare_host_broadcast(self, event_type: str, payload, event_id: str):
        seq = store.next_event_seq(self.room)
        store.mark_bidding_state_from_event(self.room, event_type)
        if event_type == "AUCTION_STATE_SNAPSHOT" and isinstance(payload, dict):
            store.save_snapshot(self.room, payload, seq)
        return seq, self.room.last_snapshot is not None

    async def _group_broadcast_server(self, event_type: str, payload: dict):
        envelope = make_envelope(
            self.room_id,
            event_type,
            payload,
            event_id=new_event_id(),
            sequence_number=None,
            sender={"role": "server"},
        )
        await self.channel_layer.group_send(self.group_name, {"type": "room.event", "event": envelope})

    async def _send_error(self, code: str, message: str, fatal: bool = False):
        await self.send_json(make_envelope(self.room_id, "ERROR", {"code": code, "message": message}, sender={"role": "server"}))
        if fatal:
            await self.close(code=4400)
