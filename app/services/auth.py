from __future__ import annotations

import time
from dataclasses import asdict
from typing import Optional, Tuple

from django.conf import settings

from app.state.store import Participant, Room, store


class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int = 401):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _extract_bearer_token(request) -> Optional[str]:
    auth = request.headers.get("Authorization") or request.META.get("HTTP_AUTHORIZATION")
    if not auth:
        return None
    parts = auth.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def authenticate_request(request, room_id: Optional[str] = None, require_host: bool = False) -> Tuple[Room, Participant]:
    token = _extract_bearer_token(request)
    if not token:
        raise AuthError("AUTH_REQUIRED", "Missing bearer token", 401)
    room, participant = store.get_by_token(token)
    if not room or not participant:
        raise AuthError("INVALID_TOKEN", "Invalid or expired token", 401)
    if room_id and room.room_id != room_id:
        raise AuthError("TOKEN_ROOM_MISMATCH", "Token does not belong to this room", 403)
    if room.status == "CLOSED":
        raise AuthError("ROOM_CLOSED", "Room is closed", 410)
    if require_host and participant.role != "host":
        raise AuthError("HOST_REQUIRED", "Host privileges required", 403)
    participant.last_seen_at = time.time()
    return room, participant


def validate_ws_token(token: str, room_id: str) -> Tuple[Room, Participant]:
    if not token:
        raise AuthError("AUTH_REQUIRED", "Missing token", 401)
    room, participant = store.get_by_token(token)
    if not room or not participant:
        raise AuthError("INVALID_TOKEN", "Invalid or expired token", 401)
    if room.room_id != room_id:
        raise AuthError("TOKEN_ROOM_MISMATCH", "Token does not belong to this room", 403)
    if room.status == "CLOSED":
        raise AuthError("ROOM_CLOSED", "Room is closed", 410)
    return room, participant
