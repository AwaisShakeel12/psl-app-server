from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional, Tuple

from django.conf import settings


ROOM_WAITING = "WAITING"
ROOM_READY = "READY"
ROOM_IN_PROGRESS = "IN_PROGRESS"
ROOM_PAUSED = "PAUSED"
ROOM_COMPLETED = "COMPLETED"
ROOM_CLOSED = "CLOSED"
ROOM_HOST_DISCONNECTED = "HOST_DISCONNECTED"


@dataclass
class Team:
    team_id: str
    name: str
    is_host_team: bool = False
    owner_participant_id: Optional[str] = None
    pending_participant_id: Optional[str] = None
    is_participating: bool = True

    def to_public(self) -> dict:
        return {
            "team_id": self.team_id,
            "name": self.name,
            "is_host_team": self.is_host_team,
            # Disabled (cut) teams must never appear in Available Teams (Issue 3).
            "is_participating": self.is_participating,
            "available": self.is_participating and self.owner_participant_id is None and self.pending_participant_id is None and not self.is_host_team,
            "owner_participant_id": self.owner_participant_id,
            "pending_participant_id": self.pending_participant_id,
        }


@dataclass
class Participant:
    participant_id: str
    name: str
    role: str  # host|participant
    token: str
    token_expires_at: float
    connected: bool = False
    channel_name: Optional[str] = None
    team_id: Optional[str] = None
    joined_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    last_seq_seen: int = 0

    def is_token_valid(self, now: Optional[float] = None) -> bool:
        now = now or time.time()
        return self.token_expires_at > now

    def to_public(self) -> dict:
        return {
            "participant_id": self.participant_id,
            "name": self.name,
            "role": self.role,
            "connected": self.connected,
            "team_id": self.team_id,
            "joined_at": self.joined_at,
        }


@dataclass
class TeamRequest:
    request_id: str
    room_id: str
    participant_id: str
    team_id: str
    status: str = "PENDING"  # PENDING|APPROVED|REJECTED|REVOKED|CANCELLED
    created_at: float = field(default_factory=time.time)
    resolved_at: Optional[float] = None

    def to_public(self) -> dict:
        return {
            "request_id": self.request_id,
            "room_id": self.room_id,
            "participant_id": self.participant_id,
            "team_id": self.team_id,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
        }


@dataclass
class Room:
    room_id: str
    invitation_id: str
    name: str
    password_hash: str
    host_participant_id: str
    max_participants: int
    status: str = ROOM_WAITING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    completed_at: Optional[float] = None
    host_connected: bool = False
    host_disconnected_at: Optional[float] = None
    room_config_metadata: dict = field(default_factory=dict)
    participants: Dict[str, Participant] = field(default_factory=dict)
    teams: Dict[str, Team] = field(default_factory=dict)
    team_requests: Dict[str, TeamRequest] = field(default_factory=dict)
    authoritative_seq: int = 0
    seen_event_ids: set = field(default_factory=set)
    seen_event_order: Deque[str] = field(default_factory=deque)
    last_snapshot: Optional[dict] = None
    last_snapshot_seq: int = 0
    accepting_bidding_events: bool = False

    def touch(self):
        self.updated_at = time.time()

    def active_participant_count(self) -> int:
        return len(self.participants)

    def get_host_participant(self) -> Optional[Participant]:
        return self.participants.get(self.host_participant_id)

    def lifecycle_status(self) -> str:
        # Expose HOST_DISCONNECTED overlay when host offline during active/ready states
        if self.status in (ROOM_READY, ROOM_IN_PROGRESS, ROOM_PAUSED) and not self.host_connected and self.host_disconnected_at:
            return ROOM_HOST_DISCONNECTED
        return self.status

    def _participant_team_request_status(self, participant_id: str) -> Optional[dict]:
        # Prefer pending request for current status, else latest resolved request
        pending = None
        latest = None
        for req in self.team_requests.values():
            if req.participant_id != participant_id:
                continue
            if req.status == "PENDING":
                pending = req
                break
            if latest is None or req.created_at > latest.created_at:
                latest = req
        chosen = pending or latest
        return chosen.to_public() if chosen else None

    def lobby_state(self, viewer: Optional[Participant] = None) -> dict:
        teams = [t.to_public() for t in self.teams.values()]
        available_teams = [t for t in teams if t.get("available")]
        team_assignments = [
            {
                "team_id": t.team_id,
                "team_name": t.name,
                "participant_id": t.owner_participant_id,
                "is_host_team": t.is_host_team,
            }
            for t in self.teams.values()
            if t.owner_participant_id
        ]
        participants = [p.to_public() for p in self.participants.values()]
        host_participant = self.get_host_participant()

        viewer_role = viewer.role if viewer else None
        viewer_id = viewer.participant_id if viewer else None

        pending_requests = []
        if viewer_role == "host":
            pending_requests = [r.to_public() for r in self.team_requests.values() if r.status == "PENDING"]

        my_request = self._participant_team_request_status(viewer_id) if viewer_id else None

        host_assigned = bool(host_participant and host_participant.team_id and host_participant.team_id in self.teams)
        config_ready = host_assigned and len(self.teams) > 0

        return {
            "room_id": self.room_id,
            "name": self.name,
            "room_name": self.name,
            "status": self.lifecycle_status(),
            "internal_status": self.status,
            "host_participant_id": self.host_participant_id,
            "host_name": host_participant.name if host_participant else None,
            "host_online": self.host_connected,
            "participants_count": self.active_participant_count(),
            "max_participants": self.max_participants,
            "participants": participants,
            "teams": teams,
            "available_teams": available_teams,
            "team_assignments": team_assignments,
            "pending_team_requests": pending_requests,
            # Room-owned configuration (rules, pool, lot order, squads). This is set by the
            # host via ROOM_CONFIG_UPDATED and is what participants read for setup screens.
            "auction_config": dict(self.room_config_metadata or {}),
            "room_configuration_metadata": dict(self.room_config_metadata or {}),
            "my_team_request": my_request,
            "my_request_status": (my_request or {}).get("status") if my_request else None,
            "current_participant_id": viewer_id,
            "current_participant_role": viewer_role,
            "configuration_ready": config_ready,
            "accepting_bidding_events": self.accepting_bidding_events and self.host_connected and self.status == ROOM_IN_PROGRESS,
        }

    def public_info(self) -> dict:
        # Backward-compatible summary; richer lobby state available via lobby_state()
        state = self.lobby_state()
        return {
            "room_id": self.room_id,
            "name": self.name,
            "status": self.lifecycle_status(),
            "internal_status": self.status,
            "participants_count": self.active_participant_count(),
            "max_participants": self.max_participants,
            "host_online": self.host_connected,
            "host_participant_id": self.host_participant_id,
            "teams": state["teams"],
            "available_teams": state["available_teams"],
            "accepting_bidding_events": state["accepting_bidding_events"],
        }


class RateLimiter:
    def __init__(self):
        self._events: Dict[str, Deque[float]] = {}
        self._lock = threading.RLock()

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        with self._lock:
            q = self._events.setdefault(key, deque())
            cutoff = now - window_seconds
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(q) > limit * 2:
                while len(q) > limit:
                    q.popleft()
            return True


class InMemoryStore:
    def __init__(self):
        self._lock = threading.RLock()
        self.rooms: Dict[str, Room] = {}
        self.tokens_index: Dict[str, Tuple[str, str]] = {}  # token -> (room_id, participant_id)
        self.connection_counts = 0
        self.rate_limiter = RateLimiter()

    def _now(self) -> float:
        return time.time()

    def cleanup_expired(self):
        now = self._now()
        with self._lock:
            to_delete = []
            for rid, room in self.rooms.items():
                if room.status == ROOM_CLOSED:
                    if not room.expires_at or now >= room.expires_at:
                        to_delete.append(rid)
                    continue

                # Waiting rooms: mark closed on expiry so APIs can return 410; remove after short tombstone window
                if room.status == ROOM_WAITING and room.expires_at and now >= room.expires_at:
                    room.status = ROOM_CLOSED
                    room.expires_at = now + 60
                    room.accepting_bidding_events = False
                    continue

                # Completed rooms: retain until expiry, then close/remove
                if room.status == ROOM_COMPLETED and room.expires_at and now >= room.expires_at:
                    room.status = ROOM_CLOSED
                    room.expires_at = now + 60
                    room.accepting_bidding_events = False
                    continue

                # Abandoned host: close after grace period
                if (not room.host_connected) and room.host_disconnected_at:
                    grace_min = getattr(settings, "ROOM_ABANDONED_HOST_GRACE_MINUTES", 10)
                    if now - room.host_disconnected_at >= grace_min * 60 and room.status not in (ROOM_COMPLETED, ROOM_CLOSED):
                        room.status = ROOM_CLOSED
                        room.expires_at = now + 30
                        room.accepting_bidding_events = False

            for rid in to_delete:
                self._delete_room_locked(rid)

    def _delete_room_locked(self, room_id: str):
        room = self.rooms.pop(room_id, None)
        if not room:
            return
        for p in room.participants.values():
            if p.token in self.tokens_index:
                del self.tokens_index[p.token]

    # Room operations
    def create_room(self, room: Room, host_participant: Participant):
        with self._lock:
            if len(self.rooms) >= getattr(settings, "MAX_ACTIVE_ROOMS", 100):
                raise ValueError("ROOM_LIMIT_REACHED")
            self.rooms[room.room_id] = room
            room.participants[host_participant.participant_id] = host_participant
            self.tokens_index[host_participant.token] = (room.room_id, host_participant.participant_id)

    def get_room(self, room_id: str) -> Optional[Room]:
        self.cleanup_expired()
        with self._lock:
            return self.rooms.get(room_id)

    def list_rooms_count(self) -> int:
        self.cleanup_expired()
        with self._lock:
            return len(self.rooms)

    def delete_room(self, room_id: str) -> bool:
        with self._lock:
            if room_id not in self.rooms:
                return False
            self._delete_room_locked(room_id)
            return True

    def add_participant(self, room_id: str, participant: Participant):
        with self._lock:
            room = self.rooms[room_id]
            room.participants[participant.participant_id] = participant
            self.tokens_index[participant.token] = (room_id, participant.participant_id)
            room.touch()

    def remove_participant(self, room_id: str, participant_id: str) -> Optional[Participant]:
        with self._lock:
            room = self.rooms.get(room_id)
            if not room:
                return None
            p = room.participants.pop(participant_id, None)
            if p and p.token in self.tokens_index:
                del self.tokens_index[p.token]
            # Explicit leave: release ownership/pending.
            if p and p.team_id and p.team_id in room.teams:
                t = room.teams[p.team_id]
                if t.owner_participant_id == participant_id:
                    t.owner_participant_id = None
            for t in room.teams.values():
                if t.pending_participant_id == participant_id:
                    t.pending_participant_id = None
            for req in room.team_requests.values():
                if req.participant_id == participant_id and req.status == "PENDING":
                    req.status = "CANCELLED"
                    req.resolved_at = self._now()
            room.touch()
            return p

    def get_by_token(self, token: str) -> Tuple[Optional[Room], Optional[Participant]]:
        self.cleanup_expired()
        with self._lock:
            ref = self.tokens_index.get(token)
            if not ref:
                return None, None
            room_id, participant_id = ref
            room = self.rooms.get(room_id)
            if not room:
                return None, None
            participant = room.participants.get(participant_id)
            if not participant:
                return None, None
            if not participant.is_token_valid():
                return None, None
            return room, participant

    def set_connection(self, participant: Participant, connected: bool, channel_name: Optional[str] = None):
        with self._lock:
            participant.connected = connected
            participant.channel_name = channel_name if connected else None
            participant.last_seen_at = self._now()

    def next_event_seq(self, room: Room) -> int:
        with self._lock:
            room.authoritative_seq += 1
            return room.authoritative_seq

    def register_event_id(self, room: Room, event_id: str) -> bool:
        """Return True if new event_id, False if duplicate. Bounded memory."""
        if not event_id:
            return True
        with self._lock:
            if event_id in room.seen_event_ids:
                return False
            if len(room.seen_event_order) >= 2000:
                old = room.seen_event_order.popleft()
                room.seen_event_ids.discard(old)
            room.seen_event_order.append(event_id)
            room.seen_event_ids.add(event_id)
            return True

    def is_bidding_allowed(self, room: Room) -> bool:
        return bool(room.accepting_bidding_events and room.host_connected and room.status == ROOM_IN_PROGRESS)

    def mark_bidding_state_from_event(self, room: Room, event_type: str):
        # Server tracks bidding acceptance only for routing gate; host remains authority.
        with self._lock:
            if event_type in ("AUCTION_STARTED", "AUCTION_RESUMED"):
                if room.status in (ROOM_READY, ROOM_PAUSED, ROOM_IN_PROGRESS):
                    room.status = ROOM_IN_PROGRESS
                room.accepting_bidding_events = True
            elif event_type == "AUCTION_PAUSED":
                if room.status == ROOM_IN_PROGRESS:
                    room.status = ROOM_PAUSED
                room.accepting_bidding_events = False
            elif event_type == "AUCTION_STOPPED":
                room.accepting_bidding_events = False
                # keep status as-is/paused/in_progress per host; host snapshot authoritative
            elif event_type == "AUCTION_COMPLETED":
                room.accepting_bidding_events = False
                room.status = ROOM_COMPLETED
                room.completed_at = self._now()
                room.expires_at = self._now() + getattr(settings, "ROOM_COMPLETED_RETENTION_MINUTES", 120) * 60
            elif event_type == "ROOM_CLOSED":
                room.accepting_bidding_events = False
                room.status = ROOM_CLOSED
            room.touch()

    def save_snapshot(self, room: Room, snapshot_payload: dict, seq: Optional[int] = None):
        with self._lock:
            room.last_snapshot = snapshot_payload
            if isinstance(seq, int):
                room.last_snapshot_seq = seq
            elif isinstance(snapshot_payload, dict) and isinstance(snapshot_payload.get("sequence_number"), int):
                room.last_snapshot_seq = snapshot_payload["sequence_number"]
            room.touch()


store = InMemoryStore()


def generate_id(prefix: str = "") -> str:
    token = secrets.token_urlsafe(16).replace("-", "").replace("_", "")
    return f"{prefix}{token}" if prefix else token


def generate_token() -> str:
    return secrets.token_urlsafe(32)
