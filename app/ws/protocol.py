from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

# Events that host may publish as authoritative and server will sequence/broadcast
HOST_AUTHORITATIVE_EVENTS = {
    "ROOM_STATE_SYNC",  # host can trigger refresh snapshot distribution via server? usually server-generated; allow host too
    "TEAM_LIST_UPDATED",
    "TEAM_ACQUISITION_APPROVED",
    "TEAM_ACQUISITION_REJECTED",
    "TEAM_ASSIGNMENT_UPDATED",
    "AUCTION_READY",
    "AUCTION_STARTED",
    "AUCTION_PAUSED",
    "AUCTION_RESUMED",
    "AUCTION_STOPPED",
    "CURRENT_PLAYER_UPDATED",
    "BID_ACCEPTED",
    "BID_REJECTED",
    "TIMER_UPDATED",
    "PLAYER_SOLD",
    "PLAYER_UNSOLD",
    "ROUND_CHANGED",
    "AUCTION_COMPLETED",
    "AUCTION_STATE_SNAPSHOT",
    "ROOM_CONFIG_UPDATED",  # host's room-owned config (teams/pool/lot order/squads)
    "ERROR",
}

# Participant request events routed to host (not broadcast as authoritative by server)
PARTICIPANT_REQUEST_EVENTS = {
    "BID_SUBMITTED",
    "REQUEST_STATE_SNAPSHOT",
    "REQUEST_ROOM_STATE_SYNC",
    # Room-owned setup requests: participant squad edits & golden bat are routed to the host,
    # who is the authority for the multiplayer auction engine.
    "MP_SQUAD_UPDATED",
    "MP_GOLDEN_BAT_REQUEST",
}

# Server-generated/broadcast events
SERVER_EVENTS = {
    "ROOM_STATE_SYNC",
    "PARTICIPANT_JOINED",
    "PARTICIPANT_LEFT",
    "HOST_CONNECTED",
    "HOST_DISCONNECTED",
    "TEAM_LIST_UPDATED",
    "TEAM_ACQUISITION_REQUESTED",
    "TEAM_ACQUISITION_APPROVED",
    "TEAM_ACQUISITION_REJECTED",
    "TEAM_ASSIGNMENT_UPDATED",
    "AUCTION_READY",
    "ROOM_CLOSED",
    "ERROR",
    "PONG",
}

ALL_KNOWN_EVENTS = HOST_AUTHORITATIVE_EVENTS | PARTICIPANT_REQUEST_EVENTS | SERVER_EVENTS | {
    "PING",
}


def now_ts() -> float:
    return time.time()


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def make_envelope(
    room_id: str,
    event_type: str,
    payload: Any = None,
    *,
    event_id: Optional[str] = None,
    sequence_number: Optional[int] = None,
    sender: Optional[Dict[str, Any]] = None,
    timestamp: Optional[float] = None,
) -> Dict[str, Any]:
    return {
        "room_id": room_id,
        "event_id": event_id,
        "event_type": event_type,
        "sequence_number": sequence_number,
        "sender": sender or {"role": "server"},
        "timestamp": timestamp if timestamp is not None else now_ts(),
        "payload": payload if payload is not None else {},
    }
