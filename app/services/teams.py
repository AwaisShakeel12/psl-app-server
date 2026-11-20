from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from app.services.rooms import ServiceError
from app.state.store import Participant, Room, Team, TeamRequest, ROOM_READY, ROOM_WAITING, generate_id, store


def _ensure_host(room: Room, participant: Participant):
    if participant.role != "host" or participant.participant_id != room.host_participant_id:
        raise ServiceError("HOST_REQUIRED", "Host privileges required", 403)


def publish_team_list(room: Room, host: Participant, payload: Dict[str, Any]) -> Dict[str, Any]:
    _ensure_host(room, host)
    teams_in = payload.get("teams") or []
    if not isinstance(teams_in, list) or len(teams_in) == 0:
        raise ServiceError("VALIDATION_ERROR", "teams must be a non-empty list", 400)

    host_team_id = str(payload.get("host_team_id") or payload.get("selected_team_id") or "").strip()

    with store._lock:
        # reset teams and requests when host republishes (pre-auction setup)
        room.teams = {}
        room.team_requests = {}
        # clear participant team bindings except host will be reassigned if present
        for p in room.participants.values():
            p.team_id = None

        seen_ids = set()
        resolved_host_team = None
        for item in teams_in:
            if not isinstance(item, dict):
                raise ServiceError("VALIDATION_ERROR", "Each team must be an object", 400)
            tid = str(item.get("team_id") or item.get("id") or "").strip()
            name = str(item.get("name") or item.get("team_name") or "").strip()
            is_host_team = bool(item.get("is_host_team", False)) or (host_team_id and tid == host_team_id)
            if not tid or not name:
                raise ServiceError("VALIDATION_ERROR", "team_id and name are required for each team", 400)
            if tid in seen_ids:
                raise ServiceError("VALIDATION_ERROR", f"Duplicate team_id: {tid}", 400)
            seen_ids.add(tid)
            team = Team(team_id=tid, name=name, is_host_team=is_host_team)
            if is_host_team:
                team.owner_participant_id = room.host_participant_id
                host.team_id = tid
                resolved_host_team = team
            room.teams[tid] = team

        # If host team not explicitly identified, preserve existing host team if possible or require? best-effort: if exactly one marked none, fail.
        if resolved_host_team is None:
            # fallback: if only host exists and a team named? no. require explicit host team.
            raise ServiceError("VALIDATION_ERROR", "Host team must be specified (host_team_id or is_host_team)", 400)

        if room.status == ROOM_WAITING:
            room.status = ROOM_READY
        room.touch()
        return {
            "room_id": room.room_id,
            "teams": [t.to_public() for t in room.teams.values()],
            "available_teams": [t.to_public() for t in room.teams.values() if t.to_public().get("available")],
            "host_team_id": host.team_id,
            "status": room.status,
        }


def list_teams(room: Room) -> Dict[str, Any]:
    return {"room_id": room.room_id, "teams": [t.to_public() for t in room.teams.values()]}


def _find_participant_owned_team(room: Room, participant_id: str) -> Optional[Team]:
    for t in room.teams.values():
        if t.owner_participant_id == participant_id:
            return t
    return None


def request_team(room: Room, participant: Participant, payload: Dict[str, Any]) -> Dict[str, Any]:
    team_id = str(payload.get("team_id") or "").strip()
    if not team_id:
        raise ServiceError("VALIDATION_ERROR", "team_id is required", 400)
    with store._lock:
        if participant.participant_id not in room.participants:
            raise ServiceError("FORBIDDEN", "Participant is not a member of this room", 403)
        if participant.role != "participant":
            raise ServiceError("FORBIDDEN", "Only participants can request teams", 403)
        team = room.teams.get(team_id)
        if not team:
            raise ServiceError("TEAM_NOT_FOUND", "Team not found", 404)
        if not team.is_participating:
            raise ServiceError("TEAM_DISABLED", "This franchise is not participating in this auction", 409)
        if team.is_host_team or team.owner_participant_id == room.host_participant_id:
            raise ServiceError("TEAM_RESERVED", "Host team is reserved", 409)
        if team.owner_participant_id:
            raise ServiceError("TEAM_TAKEN", "Team already assigned", 409)
        if team.pending_participant_id:
            raise ServiceError("TEAM_PENDING", "Team already has a pending request", 409)
        if participant.team_id or _find_participant_owned_team(room, participant.participant_id):
            raise ServiceError("ALREADY_OWNS_TEAM", "Participant already owns a team", 409)
        # prevent multiple pending requests by same participant
        for req in room.team_requests.values():
            if req.participant_id == participant.participant_id and req.status == "PENDING":
                raise ServiceError("REQUEST_PENDING", "Participant already has a pending team request", 409)

        req_id = generate_id("treq_")
        req = TeamRequest(request_id=req_id, room_id=room.room_id, participant_id=participant.participant_id, team_id=team_id)
        room.team_requests[req_id] = req
        team.pending_participant_id = participant.participant_id
        room.touch()
        return {"request": _req_public(req), "team": team.to_public()}


def _req_public(req: TeamRequest) -> dict:
    return {
        "request_id": req.request_id,
        "room_id": req.room_id,
        "participant_id": req.participant_id,
        "team_id": req.team_id,
        "status": req.status,
        "created_at": req.created_at,
        "resolved_at": req.resolved_at,
    }


def _get_request(room: Room, request_id: str) -> TeamRequest:
    req = room.team_requests.get(request_id)
    if not req:
        raise ServiceError("REQUEST_NOT_FOUND", "Team request not found", 404)
    return req


def approve_team_request(room: Room, host: Participant, request_id: str) -> Dict[str, Any]:
    _ensure_host(room, host)
    with store._lock:
        req = _get_request(room, request_id)
        if req.status != "PENDING":
            raise ServiceError("REQUEST_NOT_PENDING", "Request is not pending", 409)
        team = room.teams.get(req.team_id)
        if not team:
            raise ServiceError("TEAM_NOT_FOUND", "Team not found", 404)
        participant = room.participants.get(req.participant_id)
        if not participant:
            raise ServiceError("PARTICIPANT_NOT_FOUND", "Participant not found", 404)
        if team.owner_participant_id:
            raise ServiceError("TEAM_TAKEN", "Team already assigned", 409)
        if participant.team_id or _find_participant_owned_team(room, participant.participant_id):
            raise ServiceError("ALREADY_OWNS_TEAM", "Participant already owns a team", 409)

        team.owner_participant_id = participant.participant_id
        team.pending_participant_id = None
        participant.team_id = team.team_id
        req.status = "APPROVED"
        req.resolved_at = time.time()
        room.touch()
        return {"request": _req_public(req), "team": team.to_public(), "participant_id": participant.participant_id}


def reject_team_request(room: Room, host: Participant, request_id: str) -> Dict[str, Any]:
    _ensure_host(room, host)
    with store._lock:
        req = _get_request(room, request_id)
        if req.status != "PENDING":
            raise ServiceError("REQUEST_NOT_PENDING", "Request is not pending", 409)
        team = room.teams.get(req.team_id)
        if team and team.pending_participant_id == req.participant_id:
            team.pending_participant_id = None
        req.status = "REJECTED"
        req.resolved_at = time.time()
        room.touch()
        return {"request": _req_public(req), "team": team.to_public() if team else None}


def revoke_team_assignment(room: Room, host: Participant, payload: Dict[str, Any]) -> Dict[str, Any]:
    _ensure_host(room, host)
    with store._lock:
        if room.status not in (ROOM_WAITING, ROOM_READY):
            raise ServiceError("INVALID_STATE", "Team assignment can be revoked only before auction starts", 409)
        team_id = str(payload.get("team_id") or "").strip()
        participant_id = str(payload.get("participant_id") or "").strip()
        if not team_id:
            raise ServiceError("VALIDATION_ERROR", "team_id is required", 400)
        team = room.teams.get(team_id)
        if not team:
            raise ServiceError("TEAM_NOT_FOUND", "Team not found", 404)
        if team.is_host_team or team.team_id == host.team_id:
            raise ServiceError("TEAM_RESERVED", "Cannot revoke host team", 409)
        if participant_id and team.owner_participant_id != participant_id:
            raise ServiceError("OWNERSHIP_MISMATCH", "Team not owned by specified participant", 409)
        owner_id = team.owner_participant_id
        if owner_id and owner_id in room.participants:
            room.participants[owner_id].team_id = None
        team.owner_participant_id = None
        team.pending_participant_id = None
        # mark related approved request as revoked
        for req in room.team_requests.values():
            if req.team_id == team_id and req.participant_id == owner_id and req.status == "APPROVED":
                req.status = "REVOKED"
                req.resolved_at = time.time()
        room.touch()
        return {"team": team.to_public(), "revoked_participant_id": owner_id}
