from __future__ import annotations

from asgiref.sync import async_to_sync

try:
    from channels.layers import get_channel_layer
except Exception:  # channels not installed
    get_channel_layer = None


def room_group_name(room_id: str) -> str:
    return f"room_{room_id}"


def broadcast_room_event(
    room_id: str,
    event_type: str,
    payload: dict,
    event_id: str | None = None,
    sequence_number: int | None = None,
    sender: dict | None = None,
):
    if get_channel_layer is None:
        return
    try:
        channel_layer = get_channel_layer()
    except Exception:
        return
    if not channel_layer:
        return
    message = {
        "type": "room.event",
        "event": {
            "room_id": room_id,
            "event_id": event_id,
            "event_type": event_type,
            "sequence_number": sequence_number,
            "sender": sender or {"role": "server"},
            "payload": payload,
        },
    }
    try:
        async_to_sync(channel_layer.group_send)(room_group_name(room_id), message)
    except Exception:
        # best-effort in Phase 1; avoid breaking REST flows if WS layer misconfigured
        return

