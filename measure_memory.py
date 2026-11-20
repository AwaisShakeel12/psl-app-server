import asyncio
import os
import time

import django
import psutil

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")
django.setup()

from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import Client
from project.asgi import application
from app.state.store import store


def rss_mb():
    return psutil.Process().memory_info().rss / (1024 * 1024)


async def main():
    store.rooms.clear()
    store.tokens_index.clear()
    store.connection_counts = 0
    store.rate_limiter._events.clear()
    client = Client()
    print(f"BASE_RSS_MB={rss_mb():.2f}")
    comms = []
    room_ids = []
    for i in range(10):
        res = client.post('/api/v1/rooms/', data={
            'room_name': f'Room {i}',
            'room_password': 'secret123',
            'host_participant_name': f'Host{i}',
            'max_participants': 6,
            'room_configuration_metadata': {'i': i},
        }, content_type='application/json')
        data = res.json()
        room_id = data['room']['room_id']
        host_token = data['host']['token']
        room_ids.append(room_id)
        hc = WebsocketCommunicator(application, f"/ws/v1/rooms/{room_id}/?token={host_token}")
        ok, _ = await hc.connect()
        assert ok
        await hc.receive_json_from()
        comms.append(hc)
        for j in range(4):
            jr = client.post(f'/api/v1/rooms/{room_id}/join/', data={'participant_name': f'P{i}_{j}', 'room_password': 'secret123'}, content_type='application/json')
            pt = jr.json()['participant']['token']
            pc = WebsocketCommunicator(application, f"/ws/v1/rooms/{room_id}/?token={pt}")
            ok, _ = await pc.connect()
            assert ok
            await pc.receive_json_from()
            comms.append(pc)
    await asyncio.sleep(0.5)
    print(f"AFTER_SETUP_ROOMS=10_CONNECTIONS={len(comms)}_RSS_MB={rss_mb():.2f}")
    print(f"ACTIVE_ROOMS={store.list_rooms_count()} ACTIVE_CONNECTIONS={store.connection_counts}")

    # simulate some events
    host0 = comms[0]
    await host0.send_json_to({'event_type': 'AUCTION_STARTED', 'event_id': 'sim1', 'payload': {'x': 1}})
    await asyncio.sleep(0.2)
    print(f"AFTER_EVENTS_RSS_MB={rss_mb():.2f}")

    for c in comms:
        try:
            await c.disconnect()
        except BaseException:
            pass
    await asyncio.sleep(0.2)
    print(f"AFTER_DISCONNECT_RSS_MB={rss_mb():.2f}")
    print(f"ACTIVE_ROOMS_END={store.list_rooms_count()} ACTIVE_CONNECTIONS_END={store.connection_counts}")

asyncio.run(main())
