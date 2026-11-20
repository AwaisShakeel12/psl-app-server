from __future__ import annotations

import time

import psutil
from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from app.services import rooms as room_service
from app.services import teams as team_service
from app.services.auth import AuthError, authenticate_request
from app.services.notify import broadcast_room_event
from app.services.rooms import ServiceError
from app.state.store import store


def error_response(exc, default_status=400):
    if isinstance(exc, (ServiceError, AuthError)):
        return Response(
            {"error": {"code": exc.code, "message": exc.message, "details": getattr(exc, "details", None)}},
            status=getattr(exc, "status", default_status),
        )
    return Response({"error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}}, status=500)


@method_decorator(csrf_exempt, name="dispatch")
class CreateRoomView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        try:
            ip = request.META.get("REMOTE_ADDR", "unknown")
            limit = getattr(settings, "CREATE_ROOM_RATE_LIMIT_PER_MINUTE", 20)
            if not store.rate_limiter.allow(f"create:{ip}", limit, 60):
                return Response({"error": {"code": "RATE_LIMITED", "message": "Room creation rate limit exceeded"}}, status=429)
            data = room_service.create_room(request.data or {})
            return Response(data, status=status.HTTP_201_CREATED)
        except Exception as exc:
            return error_response(exc)


@method_decorator(csrf_exempt, name="dispatch")
class JoinRoomView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, room_id: str):
        try:
            ip = request.META.get("REMOTE_ADDR", "unknown")
            limit = getattr(settings, "JOIN_RATE_LIMIT_PER_MINUTE", 30)
            if not store.rate_limiter.allow(f"join:{ip}:{room_id}", limit, 60):
                return Response({"error": {"code": "RATE_LIMITED", "message": "Join rate limit exceeded"}}, status=429)
            data = room_service.join_room(room_id, request.data or {})
            # notify room (e.g. host if connected) that participant joined via REST
            broadcast_room_event(
                room_id,
                "PARTICIPANT_JOINED",
                {
                    "participant": {
                        "participant_id": data["participant"]["participant_id"],
                        "name": data["participant"]["name"],
                        "role": data["participant"]["role"],
                    },
                    "via": "rest",
                },
            )
            return Response(data, status=status.HTTP_200_OK)
        except Exception as exc:
            return error_response(exc)


@method_decorator(csrf_exempt, name="dispatch")
class RoomDetailView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, room_id: str):
        try:
            viewer = None
            auth_header = request.headers.get("Authorization") or request.META.get("HTTP_AUTHORIZATION")
            if auth_header:
                try:
                    room, participant = authenticate_request(request, room_id=room_id)
                    viewer = participant
                except Exception:
                    # fall back to anonymous/public-safe view if token invalid for this room
                    viewer = None
            data = room_service.get_room_info(room_id, viewer=viewer)
            return Response(data, status=status.HTTP_200_OK)
        except Exception as exc:
            return error_response(exc)

    def delete(self, request, room_id: str):
        try:
            room, participant = authenticate_request(request, room_id=room_id, require_host=True)
            broadcast_room_event(room_id, "ROOM_CLOSED", {"room_id": room_id, "reason": "deleted_by_host"})
            data = room_service.delete_room(room_id, participant)
            return Response(data, status=status.HTTP_200_OK)
        except Exception as exc:
            return error_response(exc)


@method_decorator(csrf_exempt, name="dispatch")
class LeaveRoomView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, room_id: str):
        try:
            room, participant = authenticate_request(request, room_id=room_id)
            pinfo = {"participant_id": participant.participant_id, "name": participant.name, "role": participant.role, "team_id": participant.team_id}
            data = room_service.leave_room(room_id, participant)
            broadcast_room_event(room_id, "PARTICIPANT_LEFT", {"participant": pinfo, "reason": "left"})
            if pinfo.get("team_id"):
                broadcast_room_event(room_id, "TEAM_ASSIGNMENT_UPDATED", {"team_id": pinfo["team_id"], "participant_id": pinfo["participant_id"], "action": "participant_left"})
            return Response(data, status=status.HTTP_200_OK)
        except Exception as exc:
            return error_response(exc)


@method_decorator(csrf_exempt, name="dispatch")
class TeamPublishView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, room_id: str):
        try:
            room, participant = authenticate_request(request, room_id=room_id, require_host=True)
            data = team_service.publish_team_list(room, participant, request.data or {})
            broadcast_room_event(room_id, "TEAM_LIST_UPDATED", {"teams": data["teams"], "status": data.get("status")})
            if data.get("status"):
                broadcast_room_event(room_id, "AUCTION_READY", {"room_id": room_id, "status": data["status"]})
            return Response(data, status=status.HTTP_200_OK)
        except Exception as exc:
            return error_response(exc)


class TeamListView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, room_id: str):
        try:
            room, _ = authenticate_request(request, room_id=room_id)
            data = team_service.list_teams(room)
            return Response(data, status=status.HTTP_200_OK)
        except Exception as exc:
            return error_response(exc)


@method_decorator(csrf_exempt, name="dispatch")
class TeamRequestView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, room_id: str):
        try:
            room, participant = authenticate_request(request, room_id=room_id)
            data = team_service.request_team(room, participant, request.data or {})
            payload = {
                **data,
                "participant": {"participant_id": participant.participant_id, "name": participant.name, "role": participant.role},
                "team_id": data.get("request", {}).get("team_id"),
            }
            broadcast_room_event(room_id, "TEAM_ACQUISITION_REQUESTED", payload)
            broadcast_room_event(room_id, "TEAM_LIST_UPDATED", {"room_id": room_id, "teams": [t.to_public() for t in room.teams.values()]})
            # simpler: broadcast authoritative lobby-ish update
            broadcast_room_event(room_id, "TEAM_ASSIGNMENT_UPDATED", {"action": "request_created", "request": data.get("request"), "team": data.get("team")})
            return Response(data, status=status.HTTP_201_CREATED)
        except Exception as exc:
            return error_response(exc)


@method_decorator(csrf_exempt, name="dispatch")
class TeamApproveView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, room_id: str, request_id: str):
        try:
            room, participant = authenticate_request(request, room_id=room_id, require_host=True)
            data = team_service.approve_team_request(room, participant, request_id)
            req_participant = room.participants.get(data.get("participant_id"))
            payload = {
                **data,
                "participant": {"participant_id": data.get("participant_id"), "name": getattr(req_participant, "name", None)},
            }
            broadcast_room_event(room_id, "TEAM_ACQUISITION_APPROVED", payload)
            broadcast_room_event(room_id, "TEAM_ASSIGNMENT_UPDATED", {"team": data.get("team"), "participant_id": data.get("participant_id"), "action": "assigned", "request": data.get("request")})
            broadcast_room_event(room_id, "TEAM_LIST_UPDATED", {"room_id": room_id, "teams": [t.to_public() for t in room.teams.values()]})
            return Response(data, status=status.HTTP_200_OK)
        except Exception as exc:
            return error_response(exc)


@method_decorator(csrf_exempt, name="dispatch")
class TeamRejectView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, room_id: str, request_id: str):
        try:
            room, participant = authenticate_request(request, room_id=room_id, require_host=True)
            data = team_service.reject_team_request(room, participant, request_id)
            req = data.get("request") or {}
            req_participant = room.participants.get(req.get("participant_id"))
            payload = {
                **data,
                "participant": {"participant_id": req.get("participant_id"), "name": getattr(req_participant, "name", None)},
            }
            broadcast_room_event(room_id, "TEAM_ACQUISITION_REJECTED", payload)
            broadcast_room_event(room_id, "TEAM_ASSIGNMENT_UPDATED", {"team": data.get("team"), "participant_id": req.get("participant_id"), "action": "rejected", "request": data.get("request")})
            broadcast_room_event(room_id, "TEAM_LIST_UPDATED", {"room_id": room_id, "teams": [t.to_public() for t in room.teams.values()]})
            return Response(data, status=status.HTTP_200_OK)
        except Exception as exc:
            return error_response(exc)


@method_decorator(csrf_exempt, name="dispatch")
class TeamRevokeView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, room_id: str):
        try:
            room, participant = authenticate_request(request, room_id=room_id, require_host=True)
            data = team_service.revoke_team_assignment(room, participant, request.data or {})
            broadcast_room_event(room_id, "TEAM_ASSIGNMENT_UPDATED", {"team": data.get("team"), "participant_id": data.get("revoked_participant_id"), "action": "revoked"})
            return Response(data, status=status.HTTP_200_OK)
        except Exception as exc:
            return error_response(exc)


class HealthView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        store.cleanup_expired()
        mem_mb = None
        try:
            proc = psutil.Process()
            mem_mb = round(proc.memory_info().rss / (1024 * 1024), 2)
        except Exception:
            mem_mb = None
        # ws availability: channel layer configured
        ws_available = bool(getattr(settings, "CHANNEL_LAYERS", None))
        return Response(
            {
                "status": "ok",
                "api_available": True,
                "websocket_available": ws_available,
                "server_version": getattr(settings, "SERVER_VERSION", "0.1.0"),
                "active_rooms": store.list_rooms_count(),
                "active_connections": store.connection_counts,
                "memory_mb": mem_mb,
                "timestamp": time.time(),
            }
        )
