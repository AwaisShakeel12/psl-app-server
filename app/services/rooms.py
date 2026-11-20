from __future__ import annotations

import time
from datetime import timedelta
from typing import Any, Dict, Optional

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password

from app.state.store import (
    Participant,
    Room,
    Team,
    ROOM_CLOSED,
    ROOM_COMPLETED,
    ROOM_IN_PROGRESS,
    ROOM_PAUSED,
    ROOM_READY,
    ROOM_WAITING,
    generate_id,
    generate_token,
    store,
)


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def _now() -> float:
    return time.time()


def _token_expiry() -> float:
    return _now() + getattr(settings, "TOKEN_EXPIRY_MINUTES", 720) * 60


def _room_expiry(status: str, optional_minutes: Optional[int] = None) -> float:
    if optional_minutes and optional_minutes > 0:
        return _now() + optional_minutes * 60
    if status == ROOM_WAITING:
        return _now() + getattr(settings, "ROOM_WAITING_EXPIRY_MINUTES", 60) * 60
    if status == ROOM_COMPLETED:
        return _now() + getattr(settings, "ROOM_COMPLETED_RETENTION_MINUTES", 120) * 60
    # in-progress/ready/paused: no fixed expiry until host disconnect/close logic applies
    return 0.0


def _ws_base_hint() -> dict:
    return {
        "path_template": "/ws/v1/rooms/{room_id}/",
        "query_param": "token",
        "token_query_param": "token",
        "authentication": "Pass the participant/host token as the 'token' query parameter when connecting.",
        "note": "Use ws:// for local/dev (LAN) and wss:// for production with the same path.",
    }


def _extract_host_team_id(payload: Dict[str, Any]) -> str:
    for key in (
        "host_team_id",
        "selected_team_id",
        "host_franchise_id",
        "franchise_id",
        "team_id",
        "host_selected_team_id",
        "selected_franchise_id",
    ):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    host_team = payload.get("host_team")
    if isinstance(host_team, dict):
        for key in ("team_id", "id", "franchise_id"):
            val = host_team.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _extract_teams(payload: Dict[str, Any]) -> list[dict]:
    teams = payload.get("teams")
    if isinstance(teams, list):
        return teams
    # Some clients may nest under room/config
    for key in ("available_teams", "franchises", "team_list"):
        val = payload.get(key)
        if isinstance(val, list):
            return val
    cfg = payload.get("room_configuration_metadata")
    if isinstance(cfg, dict):
        for key in ("teams", "available_teams", "franchises"):
            val = cfg.get(key)
            if isinstance(val, list):
                return val
    return []


def _init_room_teams(room: Room, host: Participant, teams_in: list[dict], host_team_id: str) -> Team:
    if not isinstance(teams_in, list) or len(teams_in) == 0:
        raise ServiceError("VALIDATION_ERROR", "teams must be provided at room creation with the host's selected team", 400)
    if not host_team_id:
        raise ServiceError("VALIDATION_ERROR", "host selected team is required (host_team_id)", 400)

    seen_ids = set()
    host_team: Optional[Team] = None
    for item in teams_in:
        if not isinstance(item, dict):
            raise ServiceError("VALIDATION_ERROR", "Each team must be an object", 400)
        tid = str(item.get("team_id") or item.get("id") or item.get("franchise_id") or "").strip()
        name = str(item.get("name") or item.get("team_name") or item.get("franchise_name") or "").strip()
        if not tid or not name:
            raise ServiceError("VALIDATION_ERROR", "Each team must include team_id and name", 400)
        if tid in seen_ids:
            raise ServiceError("VALIDATION_ERROR", f"Duplicate team_id: {tid}", 400)
        seen_ids.add(tid)
        is_host = tid == host_team_id
        # A host can create the room with only a subset of franchises enabled. Persist that
        # flag so Available Teams reflects it from the very first lobby snapshot (Issue 3).
        is_participating = bool(item.get("is_participating", True)) or is_host
        team = Team(team_id=tid, name=name, is_host_team=is_host, is_participating=is_participating)
        if is_host:
            team.owner_participant_id = host.participant_id
            host.team_id = tid
            host_team = team
        room.teams[tid] = team

    if host_team is None:
        raise ServiceError("VALIDATION_ERROR", f"Selected host team '{host_team_id}' is not a valid team in this room", 400)
    return host_team


def create_room(payload: Dict[str, Any]) -> Dict[str, Any]:
    store.cleanup_expired()
    name = (payload.get("room_name") or "").strip()
    password = payload.get("room_password") or ""
    host_name = (payload.get("host_participant_name") or "Host").strip() or "Host"
    max_participants = payload.get("max_participants")
    metadata = payload.get("room_configuration_metadata") or {}
    expiry_minutes = payload.get("room_expiration_duration_minutes")

    if not name:
        raise ServiceError("VALIDATION_ERROR", "Room name is required", 400)
    if not password or len(str(password)) < 4:
        raise ServiceError("VALIDATION_ERROR", "Room password must be at least 4 characters", 400)
    try:
        max_participants = int(max_participants)
    except Exception:
        raise ServiceError("VALIDATION_ERROR", "max_participants must be an integer", 400)
    if max_participants < 2 or max_participants > getattr(settings, "MAX_ROOM_PARTICIPANTS", 20):
        raise ServiceError("VALIDATION_ERROR", "max_participants out of allowed range", 400)
    if not isinstance(metadata, dict):
        raise ServiceError("VALIDATION_ERROR", "room_configuration_metadata must be an object", 400)

    teams_in = _extract_teams(payload)
    host_team_id = _extract_host_team_id(payload)

    room_id = generate_id("room_")
    invitation_id = generate_id("inv_")
    host_participant_id = generate_id("p_host_")
    host_token = generate_token()

    with store._lock:
        room = Room(
            room_id=room_id,
            invitation_id=invitation_id,
            name=name,
            password_hash=make_password(str(password)),
            host_participant_id=host_participant_id,
            max_participants=max_participants,
            status=ROOM_READY,  # host team assigned at creation; room ready for participant joins/requests
            expires_at=_room_expiry(ROOM_WAITING, expiry_minutes),
            room_config_metadata=metadata,
        )
        host = Participant(
            participant_id=host_participant_id,
            name=host_name,
            role="host",
            token=host_token,
            token_expires_at=_token_expiry(),
            connected=False,
        )
        host_team = _init_room_teams(room, host, teams_in, host_team_id)
        store.create_room(room, host)
        lobby = room.lobby_state(host)

    host_auth = {
        "participant_id": host.participant_id,
        "host_participant_id": host.participant_id,
        "name": host.name,
        "role": host.role,
        "token": host.token,
        "host_token": host.token,
        "access_token": host.token,
        "auth_token": host.token,
        "token_type": "Bearer",
        "token_expires_at": host.token_expires_at,
        "expires_at": host.token_expires_at,
        "room_id": room.room_id,
        "team_id": host.team_id,
        "assigned_team_id": host.team_id,
    }
    ws_url_template = f"/ws/v1/rooms/{room.room_id}/?token={{token}}"
    host_team_info = {
        "team_id": host_team.team_id,
        "name": host_team.name,
        "is_host_team": True,
        "status": "ASSIGNED",
        "owner_participant_id": host.participant_id,
    }
    return {
        "success": True,
        "room_id": room.room_id,
        "host_participant_id": host.participant_id,
        "host_token": host.token,
        "token": host.token,
        "host_team_id": host.team_id,
        "assigned_team": host_team_info,
        "room": {
            "room_id": room.room_id,
            "invitation_id": room.invitation_id,
            "status": room.status,
            "name": room.name,
            "max_participants": room.max_participants,
            "created_at": room.created_at,
            "host_participant_id": host.participant_id,
            "host_name": host.name,
            "host_role": host.role,
            "host_team_id": host.team_id,
        },
        "host": host_auth,
        "authentication": {
            "scheme": "Bearer",
            "token_type": "Bearer",
            "token": host.token,
            "host_token": host.token,
            "access_token": host.token,
            "participant_id": host.participant_id,
            "host_participant_id": host.participant_id,
            "role": host.role,
            "team_id": host.team_id,
            "expires_at": host.token_expires_at,
            "header": "Authorization",
            "header_value_example": f"Bearer {host.token}",
            "websocket_query_param": "token",
        },
        "credentials": {
            "token": host.token,
            "host_token": host.token,
            "access_token": host.token,
            "participant_id": host.participant_id,
            "role": host.role,
            "team_id": host.team_id,
            "expires_at": host.token_expires_at,
        },
        "lobby_state": lobby,
        "websocket": {
            **_ws_base_hint(),
            "url_template": ws_url_template,
            "authenticated_url_template": ws_url_template,
            "token_query_param": "token",
        },
    }


def join_room(room_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    store.cleanup_expired()
    room = store.get_room(room_id)
    if not room:
        raise ServiceError("ROOM_NOT_FOUND", "Room not found", 404)
    if room.status == ROOM_CLOSED:
        raise ServiceError("ROOM_CLOSED", "Room is closed", 410)
    if room.expires_at and _now() >= room.expires_at:
        room.status = ROOM_CLOSED
        store.delete_room(room.room_id)
        raise ServiceError("ROOM_EXPIRED", "Room has expired", 410)
    if room.status in (ROOM_IN_PROGRESS, ROOM_PAUSED, ROOM_COMPLETED, ROOM_CLOSED):
        # restricted state for joining in general; participants cannot join mid-auction/completed/paused unless specific reconnect flow (handled separately later)
        raise ServiceError("ROOM_NOT_ACCEPTING", "Room is not accepting new participants", 409)

    participant_name = (payload.get("participant_name") or "").strip()
    password = payload.get("room_password") or ""
    if not participant_name:
        raise ServiceError("VALIDATION_ERROR", "Participant name is required", 400)
    if not check_password(str(password), room.password_hash):
        raise ServiceError("INVALID_PASSWORD", "Incorrect room password", 403)
    if room.active_participant_count() >= room.max_participants:
        raise ServiceError("ROOM_FULL", "Room participant limit reached", 409)

    # Reject duplicate participant sessions by same name among active participants (simple Phase 1 rule)
    for p in room.participants.values():
        if p.role == "participant" and p.name.lower() == participant_name.lower():
            raise ServiceError("DUPLICATE_PARTICIPANT", "Participant name already in use in this room", 409)

    participant_id = generate_id("p_")
    token = generate_token()
    participant = Participant(
        participant_id=participant_id,
        name=participant_name,
        role="participant",
        token=token,
        token_expires_at=_token_expiry(),
        connected=False,
    )
    store.add_participant(room.room_id, participant)

    participant_auth = {
        "participant_id": participant.participant_id,
        "name": participant.name,
        "role": participant.role,
        "token": participant.token,
        "access_token": participant.token,
        "auth_token": participant.token,
        "token_type": "Bearer",
        "token_expires_at": participant.token_expires_at,
        "expires_at": participant.token_expires_at,
        "room_id": room.room_id,
    }
    ws_url_template = f"/ws/v1/rooms/{room.room_id}/?token={{token}}"
    return {
        "success": True,
        "room_id": room.room_id,
        "participant_id": participant.participant_id,
        "token": participant.token,
        "participant": participant_auth,
        "room": {
            "room_id": room.room_id,
            "name": room.name,
            "status": room.status,
            "invitation_id": room.invitation_id,
            "host_participant_id": room.host_participant_id,
        },
        "authentication": {
            "scheme": "Bearer",
            "token_type": "Bearer",
            "token": participant.token,
            "access_token": participant.token,
            "participant_id": participant.participant_id,
            "role": participant.role,
            "expires_at": participant.token_expires_at,
            "header": "Authorization",
            "header_value_example": f"Bearer {participant.token}",
            "websocket_query_param": "token",
        },
        "credentials": {
            "token": participant.token,
            "access_token": participant.token,
            "participant_id": participant.participant_id,
            "role": participant.role,
            "expires_at": participant.token_expires_at,
        },
        "lobby_state": room.lobby_state(participant),
        "websocket": {
            **_ws_base_hint(),
            "url_template": ws_url_template,
            "authenticated_url_template": ws_url_template,
            "token_query_param": "token",
        },
    }


def get_room_info(room_id: str, viewer: Optional[Participant] = None) -> Dict[str, Any]:
    store.cleanup_expired()
    room = store.get_room(room_id)
    if not room:
        raise ServiceError("ROOM_NOT_FOUND", "Room not found", 404)
    state = room.lobby_state(viewer)
    # For anonymous viewers, do not expose pending requests / own request specifics beyond public-safe fields
    if viewer is None:
        state = {**state, "pending_team_requests": [], "my_team_request": None, "my_request_status": None, "current_participant_id": None, "current_participant_role": None}
    return {"room": state, "lobby_state": state}


def leave_room(room_id: str, participant: Participant) -> Dict[str, Any]:
    room = store.get_room(room_id)
    if not room:
        raise ServiceError("ROOM_NOT_FOUND", "Room not found", 404)
    if participant.role == "host":
        raise ServiceError("HOST_CANNOT_LEAVE", "Host must delete/close the room instead of leaving", 400)
    removed = store.remove_participant(room_id, participant.participant_id)
    if not removed:
        raise ServiceError("NOT_IN_ROOM", "Participant not in room", 404)
    return {"ok": True, "room_id": room_id}


def delete_room(room_id: str, host_participant: Participant) -> Dict[str, Any]:
    room = store.get_room(room_id)
    if not room:
        raise ServiceError("ROOM_NOT_FOUND", "Room not found", 404)
    if host_participant.role != "host" or room.host_participant_id != host_participant.participant_id:
        raise ServiceError("HOST_REQUIRED", "Only the authenticated host can delete the room", 403)
    room.status = ROOM_CLOSED
    room.expires_at = _now()
    room.touch()
    # actual removal of room data happens via cleanup/delete; keep delete explicit
    store.delete_room(room_id)
    return {"ok": True, "room_id": room_id, "status": ROOM_CLOSED}
