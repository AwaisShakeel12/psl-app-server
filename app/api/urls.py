from django.urls import path

from .views import (
    CreateRoomView,
    HealthView,
    JoinRoomView,
    LeaveRoomView,
    RoomDetailView,
    TeamApproveView,
    TeamListView,
    TeamPublishView,
    TeamRejectView,
    TeamRequestView,
    TeamRevokeView,
)

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("rooms/", CreateRoomView.as_view(), name="room-create"),
    path("rooms/<str:room_id>/", RoomDetailView.as_view(), name="room-detail"),
    path("rooms/<str:room_id>/join/", JoinRoomView.as_view(), name="room-join"),
    path("rooms/<str:room_id>/leave/", LeaveRoomView.as_view(), name="room-leave"),
    path("rooms/<str:room_id>/teams/", TeamListView.as_view(), name="team-list"),
    path("rooms/<str:room_id>/teams/publish/", TeamPublishView.as_view(), name="team-publish"),
    path("rooms/<str:room_id>/teams/request/", TeamRequestView.as_view(), name="team-request"),
    path("rooms/<str:room_id>/teams/requests/<str:request_id>/approve/", TeamApproveView.as_view(), name="team-approve"),
    path("rooms/<str:room_id>/teams/requests/<str:request_id>/reject/", TeamRejectView.as_view(), name="team-reject"),
    path("rooms/<str:room_id>/teams/revoke/", TeamRevokeView.as_view(), name="team-revoke"),
]

