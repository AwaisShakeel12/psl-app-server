import asyncio
import json
import time
from unittest import skipUnless

from asgiref.sync import async_to_sync, sync_to_async
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from app.state.store import ROOM_COMPLETED, ROOM_IN_PROGRESS, ROOM_READY, ROOM_WAITING, store

try:
    from channels.testing import WebsocketCommunicator
    from project.asgi import application as asgi_application
    CHANNELS_AVAILABLE = True
except Exception:
    CHANNELS_AVAILABLE = False


DEFAULT_TEAMS = [
    {"team_id": "team_host", "name": "Host Team"},
    {"team_id": "team_1", "name": "Team 1"},
    {"team_id": "team_2", "name": "Team 2"},
    {"team_id": "team_3", "name": "Team 3"},
]


class APITestBase(TestCase):
    def setUp(self):
        store.rooms.clear()
        store.tokens_index.clear()
        store.connection_counts = 0
        store.rate_limiter._events.clear()
        self.client = APIClient()

    def auth(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def create_room(self, **overrides):
        payload = {
            "room_name": "Test Room",
            "room_password": "secret123",
            "host_participant_name": "Host",
            "max_participants": 6,
            "room_configuration_metadata": {"mode": "test"},
            "teams": [dict(t) for t in DEFAULT_TEAMS],
            "host_team_id": "team_host",
        }
        payload.update(overrides)
        return self.client.post("/api/v1/rooms/", payload, format="json")

    def join(self, room_id, name="P1", password="secret123"):
        return self.client.post(
            f"/api/v1/rooms/{room_id}/join/",
            {"participant_name": name, "room_password": password},
            format="json",
        )


class RoomRestTests(APITestBase):
    def test_create_room_returns_ids_tokens_ws_and_host_assignment(self):
        res = self.create_room()
        self.assertEqual(res.status_code, 201)
        self.assertIn("room", res.data)
        self.assertIn("host", res.data)
        self.assertIn("websocket", res.data)
        self.assertTrue(res.data["room"]["room_id"].startswith("room_"))
        self.assertTrue(res.data["host"]["token"])
        self.assertNotIn("room_password", json.dumps(res.data))
        # host auth compatibility/contract fields
        self.assertEqual(res.data["room_id"], res.data["room"]["room_id"])
        self.assertEqual(res.data["host_participant_id"], res.data["host"]["participant_id"])
        self.assertEqual(res.data["host_token"], res.data["host"]["token"])
        self.assertEqual(res.data["token"], res.data["host"]["token"])
        self.assertEqual(res.data["host"]["role"], "host")
        self.assertEqual(res.data["authentication"]["token"], res.data["host"]["token"])
        self.assertEqual(res.data["credentials"]["host_token"], res.data["host"]["token"])
        self.assertEqual(res.data["websocket"]["query_param"], "token")
        # host team assigned/reserved at creation
        self.assertEqual(res.data["host_team_id"], "team_host")
        self.assertEqual(res.data["host"]["team_id"], "team_host")
        self.assertEqual(res.data["assigned_team"]["status"], "ASSIGNED")
        teams = res.data["lobby_state"]["teams"]
        host_team = next(t for t in teams if t["team_id"] == "team_host")
        self.assertEqual(host_team["owner_participant_id"], res.data["host_participant_id"])
        self.assertFalse(host_team["available"])

    def test_creator_receives_role_host_and_host_team_not_available(self):
        res = self.create_room()
        self.assertEqual(res.status_code, 201)
        room_id = res.data["room_id"]
        self.assertEqual(res.data["host"]["role"], "host")
        lobby = res.data["lobby_state"]
        self.assertTrue(all(t["team_id"] != "team_host" for t in lobby["available_teams"]))
        # verify via room state endpoint as anonymous
        info = self.client.get(f"/api/v1/rooms/{room_id}/")
        self.assertEqual(info.status_code, 200)
        available_ids = [t["team_id"] for t in info.data["room"]["available_teams"]]
        self.assertNotIn("team_host", available_ids)

    def test_host_selected_team_invalid_rejected(self):
        res = self.create_room(host_team_id="nope")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error"]["code"], "VALIDATION_ERROR")

    def test_join_room_with_correct_password(self):
        room = self.create_room().data
        room_id = room["room"]["room_id"]
        res = self.join(room_id, "P1")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["participant"]["role"], "participant")
        self.assertIn("token", res.data["participant"])
        self.assertNotIn(room["host"]["token"], json.dumps(res.data))

    def test_join_room_rejects_incorrect_password(self):
        room = self.create_room().data
        room_id = room["room"]["room_id"]
        res = self.join(room_id, "P1", password="wrong")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data["error"]["code"], "INVALID_PASSWORD")

    def test_join_room_rejects_expired_room(self):
        room = self.create_room().data
        room_id = room["room"]["room_id"]
        r = store.get_room(room_id)
        r.expires_at = time.time() - 10
        res = self.join(room_id, "P1")
        self.assertEqual(res.status_code, 410)

    def test_join_room_rejects_full_room(self):
        room = self.create_room(max_participants=2).data  # host + 1 participant
        room_id = room["room"]["room_id"]
        res1 = self.join(room_id, "P1")
        self.assertEqual(res1.status_code, 200)
        res2 = self.join(room_id, "P2")
        self.assertEqual(res2.status_code, 409)
        self.assertEqual(res2.data["error"]["code"], "ROOM_FULL")

    def test_reject_duplicate_participant_name(self):
        room = self.create_room().data
        room_id = room["room"]["room_id"]
        res1 = self.join(room_id, "P1")
        self.assertEqual(res1.status_code, 200)
        res2 = self.join(room_id, "P1")
        self.assertEqual(res2.status_code, 409)

    def test_get_safe_room_info_and_viewer_context(self):
        room = self.create_room().data
        room_id = room["room"]["room_id"]
        join = self.join(room_id, "P1").data
        # anonymous
        res = self.client.get(f"/api/v1/rooms/{room_id}/")
        self.assertEqual(res.status_code, 200)
        body = json.dumps(res.data)
        self.assertIn(room_id, body)
        self.assertNotIn("password", body.lower())
        self.assertNotIn(room["host"]["token"], body)
        self.assertEqual(res.data["room"]["host_participant_id"], room["host_participant_id"])
        self.assertEqual(res.data["room"]["current_participant_id"], None)
        self.assertEqual(res.data["room"]["pending_team_requests"], [])
        # host view sees pending requests
        self.auth(join["participant"]["token"])
        self.client.post(f"/api/v1/rooms/{room_id}/teams/request/", {"team_id": "team_1"}, format="json")
        self.auth(room["host"]["token"])
        host_view = self.client.get(f"/api/v1/rooms/{room_id}/")
        self.assertEqual(host_view.status_code, 200)
        self.assertEqual(host_view.data["room"]["current_participant_role"], "host")
        self.assertEqual(len(host_view.data["room"]["pending_team_requests"]), 1)
        # participant view sees own request status
        self.auth(join["participant"]["token"])
        p_view = self.client.get(f"/api/v1/rooms/{room_id}/")
        self.assertEqual(p_view.data["room"]["current_participant_role"], "participant")
        self.assertEqual(p_view.data["room"]["my_request_status"], "PENDING")

    def test_delete_room_as_host(self):
        room = self.create_room().data
        room_id = room["room"]["room_id"]
        self.auth(room["host"]["token"])
        res = self.client.delete(f"/api/v1/rooms/{room_id}/")
        self.assertEqual(res.status_code, 200)
        res2 = self.client.get(f"/api/v1/rooms/{room_id}/")
        self.assertEqual(res2.status_code, 404)

    def test_delete_room_rejected_for_participant(self):
        room = self.create_room().data
        room_id = room["room"]["room_id"]
        join = self.join(room_id, "P1").data
        self.auth(join["participant"]["token"])
        res = self.client.delete(f"/api/v1/rooms/{room_id}/")
        self.assertEqual(res.status_code, 403)

    def test_leave_room(self):
        room = self.create_room().data
        room_id = room["room"]["room_id"]
        join = self.join(room_id, "P1").data
        self.auth(join["participant"]["token"])
        res = self.client.post(f"/api/v1/rooms/{room_id}/leave/")
        self.assertEqual(res.status_code, 200)


class TeamWorkflowTests(APITestBase):
    def _setup_room_with_participant(self):
        room = self.create_room().data
        room_id = room["room"]["room_id"]
        join = self.join(room_id, "P1").data
        return room, join

    def test_host_cannot_request_team_via_participant_flow(self):
        room = self.create_room().data
        room_id = room["room"]["room_id"]
        self.auth(room["host"]["token"])
        res = self.client.post(f"/api/v1/rooms/{room_id}/teams/request/", {"team_id": "team_1"}, format="json")
        self.assertEqual(res.status_code, 403)

    def test_participant_can_request_available_team_and_host_receives_pending(self):
        room, join = self._setup_room_with_participant()
        room_id = room["room"]["room_id"]
        self.auth(join["participant"]["token"])
        req = self.client.post(f"/api/v1/rooms/{room_id}/teams/request/", {"team_id": "team_1"}, format="json")
        self.assertEqual(req.status_code, 201)
        self.assertEqual(req.data["request"]["status"], "PENDING")
        # host sees pending request
        self.auth(room["host"]["token"])
        info = self.client.get(f"/api/v1/rooms/{room_id}/")
        self.assertEqual(info.status_code, 200)
        pending = info.data["room"]["pending_team_requests"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["participant_id"], join["participant"]["participant_id"])
        self.assertEqual(pending[0]["team_id"], "team_1")

    def test_host_can_approve_request_and_participant_receives_team(self):
        room, join = self._setup_room_with_participant()
        room_id = room["room"]["room_id"]
        self.auth(join["participant"]["token"])
        req = self.client.post(f"/api/v1/rooms/{room_id}/teams/request/", {"team_id": "team_1"}, format="json")
        request_id = req.data["request"]["request_id"]
        self.auth(room["host"]["token"])
        appr = self.client.post(f"/api/v1/rooms/{room_id}/teams/requests/{request_id}/approve/")
        self.assertEqual(appr.status_code, 200)
        self.assertEqual(appr.data["request"]["status"], "APPROVED")
        self.assertEqual(appr.data["team"]["owner_participant_id"], join["participant"]["participant_id"])
        # participant sees assigned team and approved status
        self.auth(join["participant"]["token"])
        info = self.client.get(f"/api/v1/rooms/{room_id}/")
        self.assertEqual(info.data["room"]["my_request_status"], "APPROVED")
        me = next(p for p in info.data["room"]["participants"] if p["participant_id"] == join["participant"]["participant_id"])
        self.assertEqual(me["team_id"], "team_1")
        # team removed from available
        self.assertTrue(all(t["team_id"] != "team_1" for t in info.data["room"]["available_teams"]))

    def test_host_can_reject_request_team_remains_available(self):
        room, join = self._setup_room_with_participant()
        room_id = room["room"]["room_id"]
        self.auth(join["participant"]["token"])
        req = self.client.post(f"/api/v1/rooms/{room_id}/teams/request/", {"team_id": "team_1"}, format="json")
        request_id = req.data["request"]["request_id"]
        self.auth(room["host"]["token"])
        rej = self.client.post(f"/api/v1/rooms/{room_id}/teams/requests/{request_id}/reject/")
        self.assertEqual(rej.status_code, 200)
        self.assertEqual(rej.data["request"]["status"], "REJECTED")
        self.assertIsNone(rej.data["team"]["pending_participant_id"])
        self.assertTrue(rej.data["team"]["available"])
        # participant sees rejected status and team available
        self.auth(join["participant"]["token"])
        info = self.client.get(f"/api/v1/rooms/{room_id}/")
        self.assertEqual(info.data["room"]["my_request_status"], "REJECTED")
        self.assertTrue(any(t["team_id"] == "team_1" for t in info.data["room"]["available_teams"]))

    def test_prevent_duplicate_team_ownership_and_pending_conflicts(self):
        room = self.create_room().data
        room_id = room["room"]["room_id"]
        j1 = self.join(room_id, "P1").data
        j2 = self.join(room_id, "P2").data
        self.auth(j1["participant"]["token"])
        req1 = self.client.post(f"/api/v1/rooms/{room_id}/teams/request/", {"team_id": "team_1"}, format="json")
        self.assertEqual(req1.status_code, 201)
        self.auth(j2["participant"]["token"])
        req2 = self.client.post(f"/api/v1/rooms/{room_id}/teams/request/", {"team_id": "team_1"}, format="json")
        self.assertEqual(req2.status_code, 409)  # pending/conflict

    def test_prevent_participant_acquiring_multiple_teams(self):
        room, join = self._setup_room_with_participant()
        room_id = room["room"]["room_id"]
        self.auth(join["participant"]["token"])
        req1 = self.client.post(f"/api/v1/rooms/{room_id}/teams/request/", {"team_id": "team_1"}, format="json")
        self.auth(room["host"]["token"])
        self.client.post(f"/api/v1/rooms/{room_id}/teams/requests/{req1.data['request']['request_id']}/approve/")
        self.auth(join["participant"]["token"])
        req2 = self.client.post(f"/api/v1/rooms/{room_id}/teams/request/", {"team_id": "team_2"}, format="json")
        self.assertEqual(req2.status_code, 409)

    def test_non_host_cannot_approve_or_reject_requests(self):
        room = self.create_room().data
        room_id = room["room"]["room_id"]
        j1 = self.join(room_id, "P1").data
        j2 = self.join(room_id, "P2").data
        self.auth(j1["participant"]["token"])
        req = self.client.post(f"/api/v1/rooms/{room_id}/teams/request/", {"team_id": "team_1"}, format="json")
        request_id = req.data["request"]["request_id"]
        # other participant cannot approve/reject
        self.auth(j2["participant"]["token"])
        appr = self.client.post(f"/api/v1/rooms/{room_id}/teams/requests/{request_id}/approve/")
        rej = self.client.post(f"/api/v1/rooms/{room_id}/teams/requests/{request_id}/reject/")
        self.assertEqual(appr.status_code, 403)
        self.assertEqual(rej.status_code, 403)

    def test_host_can_revoke_assignment(self):
        room, join = self._setup_room_with_participant()
        room_id = room["room"]["room_id"]
        self.auth(join["participant"]["token"])
        req = self.client.post(f"/api/v1/rooms/{room_id}/teams/request/", {"team_id": "team_1"}, format="json")
        self.auth(room["host"]["token"])
        self.client.post(f"/api/v1/rooms/{room_id}/teams/requests/{req.data['request']['request_id']}/approve/")
        rev = self.client.post(f"/api/v1/rooms/{room_id}/teams/revoke/", {"team_id": "team_1"}, format="json")
        self.assertEqual(rev.status_code, 200)
        self.assertIsNone(rev.data["team"]["owner_participant_id"])


class HealthAndRootTests(APITestBase):
    def test_health_endpoint(self):
        res = self.client.get("/api/v1/health/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "ok")
        self.assertIn("active_rooms", res.data)
        self.assertIn("active_connections", res.data)

    def test_root_health_endpoint(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")


@skipUnless(CHANNELS_AVAILABLE, "channels not installed")
class WebSocketPhase2Tests(TestCase):
    def setUp(self):
        store.rooms.clear()
        store.tokens_index.clear()
        store.connection_counts = 0
        store.rate_limiter._events.clear()
        self.client = APIClient()

    def _create_room(self):
        res = self.client.post("/api/v1/rooms/", {
            "room_name": "R",
            "room_password": "secret123",
            "host_participant_name": "Host",
            "max_participants": 6,
            "room_configuration_metadata": {},
            "teams": [dict(t) for t in DEFAULT_TEAMS],
            "host_team_id": "team_host",
        }, format="json")
        self.assertEqual(res.status_code, 201)
        return res.data

    def _join(self, room_id, name):
        res = self.client.post(f"/api/v1/rooms/{room_id}/join/", {"participant_name": name, "room_password": "secret123"}, format="json")
        self.assertEqual(res.status_code, 200)
        return res.data

    async def _recv_until(self, comm, event_type, timeout=2):
        end = time.time() + timeout
        while time.time() < end:
            try:
                msg = await asyncio.wait_for(comm.receive_json_from(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if msg.get("event_type") == event_type:
                return msg
        raise AssertionError(f"Did not receive event {event_type}")

    async def _drain(self, comm, duration=0.15):
        end = time.time() + duration
        msgs = []
        while time.time() < end:
            try:
                msgs.append(await asyncio.wait_for(comm.receive_json_from(), timeout=0.05))
            except Exception:
                break
        return msgs

    def test_ws_auth_and_basic_room_broadcasts(self):
        room = self._create_room()
        room_id = room["room"]["room_id"]
        host_token = room["host"]["token"]

        async def scenario():
            bad = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token=bad")
            connected, _ = await bad.connect()
            assert connected is False
            await bad.disconnect()

            host = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={host_token}")
            ok, _ = await host.connect()
            assert ok
            sync = await host.receive_json_from()
            assert sync["event_type"] == "ROOM_STATE_SYNC"
            assert sync["payload"]["room"]["host_participant_id"] == room["host_participant_id"]
            await self._drain(host)

            p = self._join(room_id, "P1")
            p_comm = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={p['participant']['token']}")
            ok2, _ = await p_comm.connect()
            assert ok2
            psync = await p_comm.receive_json_from()
            assert psync["event_type"] == "ROOM_STATE_SYNC"

            seen = await self._drain(host, 0.5)
            assert any(m.get("event_type") == "PARTICIPANT_JOINED" for m in seen)

            await p_comm.send_json_to({"event_type": "PING"})
            pong = await self._recv_until(p_comm, "PONG")
            assert pong["event_type"] == "PONG"

            await p_comm.disconnect()
            await host.disconnect()

        async_to_sync(scenario)()

    def test_bid_request_routed_to_host_and_host_result_broadcast(self):
        room = self._create_room()
        room_id = room["room"]["room_id"]
        host_token = room["host"]["token"]
        p = self._join(room_id, "P1")

        # request and approve team for participant
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {p['participant']['token']}")
        req = self.client.post(f"/api/v1/rooms/{room_id}/teams/request/", {"team_id": "team_1"}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {host_token}")
        self.client.post(f"/api/v1/rooms/{room_id}/teams/requests/{req.data['request']['request_id']}/approve/")

        async def scenario():
            host = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={host_token}")
            await host.connect(); await host.receive_json_from()
            part = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={p['participant']['token']}")
            await part.connect(); await part.receive_json_from()
            await self._drain(host); await self._drain(part)

            await host.send_json_to({"event_type": "AUCTION_STARTED", "event_id": "evt_start_1", "payload": {"auction": "started"}})
            started_host = await self._recv_until(host, "AUCTION_STARTED")
            started_part = await self._recv_until(part, "AUCTION_STARTED")
            assert started_host["sequence_number"] == 1
            assert started_part["sequence_number"] == 1

            await part.send_json_to({"event_type": "BID_SUBMITTED", "event_id": "bidreq1", "payload": {"team_id": "team_1", "amount": 100}})
            routed = await self._recv_until(host, "BID_SUBMITTED")
            assert routed["sender"]["participant_id"] == p["participant"]["participant_id"]
            assert routed["payload"]["team_id"] == "team_1"
            try:
                msg = await asyncio.wait_for(part.receive_json_from(), timeout=0.2)
                assert msg.get("event_type") != "BID_SUBMITTED"
            except asyncio.TimeoutError:
                pass

            await host.send_json_to({"event_type": "BID_ACCEPTED", "event_id": "bidacc1", "payload": {"team_id": "team_1", "amount": 100}})
            acc_h = await self._recv_until(host, "BID_ACCEPTED")
            acc_p = await self._recv_until(part, "BID_ACCEPTED")
            assert acc_h["sequence_number"] == 2
            assert acc_p["payload"]["amount"] == 100

            await host.disconnect(); await part.disconnect()

        async_to_sync(scenario)()

    def test_participant_cannot_send_authoritative_events_and_duplicate_protection(self):
        room = self._create_room()
        room_id = room["room"]["room_id"]
        host_token = room["host"]["token"]
        p = self._join(room_id, "P1")

        async def scenario():
            host = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={host_token}")
            await host.connect(); await host.receive_json_from()
            part = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={p['participant']['token']}")
            await part.connect(); await part.receive_json_from()
            await self._drain(host); await self._drain(part)

            await part.send_json_to({"event_type": "BID_ACCEPTED", "event_id": "x1", "payload": {"amount": 999}})
            err = await self._recv_until(part, "ERROR")
            assert err["payload"]["code"] == "FORBIDDEN"

            await host.send_json_to({"event_type": "AUCTION_PAUSED", "event_id": "dup1", "payload": {}})
            m1 = await self._recv_until(part, "AUCTION_PAUSED")
            assert m1["event_id"] == "dup1"
            await host.send_json_to({"event_type": "AUCTION_PAUSED", "event_id": "dup1", "payload": {}})
            try:
                m2 = await asyncio.wait_for(part.receive_json_from(), timeout=0.3)
                assert m2.get("event_id") != "dup1"
            except asyncio.TimeoutError:
                pass

            await host.disconnect(); await part.disconnect()

        async_to_sync(scenario)()

    def test_host_disconnect_reconnect_and_room_delete_disconnects(self):
        room = self._create_room()
        room_id = room["room"]["room_id"]
        host_token = room["host"]["token"]
        p = self._join(room_id, "P1")

        async def scenario():
            host = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={host_token}")
            await host.connect(); await host.receive_json_from()
            part = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={p['participant']['token']}")
            await part.connect(); await part.receive_json_from()
            await self._drain(host); await self._drain(part)

            await host.disconnect()
            hd = await self._recv_until(part, "HOST_DISCONNECTED")
            assert hd["event_type"] == "HOST_DISCONNECTED"

            host2 = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={host_token}")
            await host2.connect(); await host2.receive_json_from()
            hc = await self._recv_until(part, "HOST_CONNECTED")
            assert hc["event_type"] == "HOST_CONNECTED"

            self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {host_token}")
            res = self.client.delete(f"/api/v1/rooms/{room_id}/")
            assert res.status_code == 200
            try:
                while True:
                    closed = await asyncio.wait_for(part.receive_json_from(), timeout=1.0)
                    if closed.get("event_type") in ("ROOM_CLOSED", "ERROR"):
                        break
            except Exception:
                pass
            for c in (part, host2):
                try:
                    await c.disconnect()
                except BaseException:
                    pass

        async_to_sync(scenario)()

    def test_snapshot_routing_and_auction_events_relay(self):
        room = self._create_room()
        room_id = room["room"]["room_id"]
        host_token = room["host"]["token"]
        p = self._join(room_id, "P1")

        async def scenario():
            host = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={host_token}")
            await host.connect(); await host.receive_json_from()
            part = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={p['participant']['token']}")
            await part.connect(); await part.receive_json_from()
            await self._drain(host); await self._drain(part)

            events = [
                ("CURRENT_PLAYER_UPDATED", {"player_id": "pl1"}),
                ("TIMER_UPDATED", {"remaining_ms": 5000, "timer_mode": "countdown"}),
                ("PLAYER_SOLD", {"player_id": "pl1", "team_id": "team_1"}),
                ("PLAYER_UNSOLD", {"player_id": "pl2"}),
                ("ROUND_CHANGED", {"round": 2}),
                ("AUCTION_COMPLETED", {"summary": "done"}),
            ]
            seq = 0
            for et, payload in events:
                seq += 1
                await host.send_json_to({"event_type": et, "event_id": f"e_{et}", "payload": payload})
                mh = await self._recv_until(host, et)
                mp = await self._recv_until(part, et)
                assert mh["sequence_number"] == seq
                assert mp["sequence_number"] == seq
                assert mp["payload"] == payload

            await part.send_json_to({"event_type": "REQUEST_STATE_SNAPSHOT", "event_id": "snapreq1", "payload": {"reason": "resync"}})
            req = await self._recv_until(host, "REQUEST_STATE_SNAPSHOT")
            assert req["payload"]["target_participant_id"] == p["participant"]["participant_id"]
            await host.send_json_to({"event_type": "AUCTION_STATE_SNAPSHOT", "event_id": "snap1", "payload": {"target_participant_id": p["participant"]["participant_id"], "state": {"ok": True}}})
            snap = await self._recv_until(part, "AUCTION_STATE_SNAPSHOT")
            assert snap["payload"]["state"]["ok"] is True

            await host.disconnect(); await part.disconnect()

        async_to_sync(scenario)()

    def test_participant_reconnect_receives_current_state_and_snapshot(self):
        room = self._create_room()
        room_id = room["room"]["room_id"]
        host_token = room["host"]["token"]
        p = self._join(room_id, "P1")
        # create pending request while participant disconnected from WS? not necessary; just ensure state is in store
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {p['participant']['token']}")
        self.client.post(f"/api/v1/rooms/{room_id}/teams/request/", {"team_id": "team_1"}, format="json")

        async def scenario():
            host = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={host_token}")
            await host.connect(); await host.receive_json_from()
            part = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={p['participant']['token']}")
            await part.connect(); await part.receive_json_from()
            await self._drain(host)
            await host.send_json_to({"event_type": "AUCTION_STATE_SNAPSHOT", "event_id": "snapcache", "payload": {"state": {"phase": "mid"}}})
            await asyncio.sleep(0.2)
            await part.disconnect()
            # reconnect same participant token
            part2 = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={p['participant']['token']}")
            await part2.connect()
            sync = await part2.receive_json_from()
            assert sync["event_type"] == "ROOM_STATE_SYNC"
            lobby = sync["payload"]["room"]
            assert lobby["current_participant_id"] == p["participant"]["participant_id"]
            assert lobby["my_request_status"] == "PENDING"
            assert lobby["host_participant_id"] == room["host_participant_id"]
            snap = await self._recv_until(part2, "AUCTION_STATE_SNAPSHOT", timeout=2)
            assert snap["payload"]["state"]["phase"] == "mid"
            await part2.disconnect(); await host.disconnect()

        async_to_sync(scenario)()

    def test_team_request_events_received_by_host_and_participant(self):
        room = self._create_room()
        room_id = room["room"]["room_id"]
        host_token = room["host"]["token"]
        p = self._join(room_id, "P1")

        async def scenario():
            host = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={host_token}")
            await host.connect(); await host.receive_json_from()
            part = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={p['participant']['token']}")
            await part.connect(); await part.receive_json_from()
            await self._drain(host); await self._drain(part)

            # participant requests team via REST; host should receive event
            def _do_request():
                self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {p['participant']['token']}")
                return self.client.post(f"/api/v1/rooms/{room_id}/teams/request/", {"team_id": "team_1"}, format="json")
            req = await sync_to_async(_do_request, thread_sensitive=True)()
            self.assertEqual(req.status_code, 201)
            got_host = await self._recv_until(host, "TEAM_ACQUISITION_REQUESTED", timeout=2)
            assert got_host["payload"]["request"]["participant_id"] == p["participant"]["participant_id"]
            got_part = await self._recv_until(part, "TEAM_ACQUISITION_REQUESTED", timeout=2)
            assert got_part["payload"]["request"]["team_id"] == "team_1"

            request_id = req.data["request"]["request_id"]
            # host approves; participant should receive result
            def _do_approve():
                self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {host_token}")
                return self.client.post(f"/api/v1/rooms/{room_id}/teams/requests/{request_id}/approve/")
            appr = await sync_to_async(_do_approve, thread_sensitive=True)()
            self.assertEqual(appr.status_code, 200)
            approved = await self._recv_until(part, "TEAM_ACQUISITION_APPROVED", timeout=2)
            assert approved["payload"]["request"]["status"] == "APPROVED"
            assert approved["payload"]["participant_id"] == p["participant"]["participant_id"]
            await self._recv_until(host, "TEAM_ACQUISITION_APPROVED", timeout=2)

            await host.disconnect(); await part.disconnect()

        async_to_sync(scenario)()

    def test_create_room_host_credentials_work_for_websocket_auth(self):
        res = self.client.post("/api/v1/rooms/", {
            "room_name": "Cred Room",
            "room_password": "secret123",
            "host_participant_name": "Host",
            "max_participants": 4,
            "room_configuration_metadata": {"k": "v"},
            "teams": [dict(t) for t in DEFAULT_TEAMS],
            "host_team_id": "team_host",
        }, format="json")
        self.assertEqual(res.status_code, 201)
        body = json.dumps(res.data)
        self.assertNotIn("room_password", body)
        host_token = res.data["host_token"]
        self.assertEqual(host_token, res.data["host"]["token"])
        self.assertEqual(host_token, res.data["authentication"]["access_token"])
        self.assertEqual(res.data["host_participant_id"], res.data["host"]["participant_id"])
        self.assertEqual(res.data["host"]["team_id"], "team_host")
        room_id = res.data["room_id"]

        async def scenario():
            comm = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={host_token}")
            connected, _ = await comm.connect()
            assert connected is True
            sync = await comm.receive_json_from()
            assert sync["event_type"] == "ROOM_STATE_SYNC"
            assert sync["payload"]["participant"]["role"] == "host"
            assert sync["payload"]["participant"]["participant_id"] == res.data["host_participant_id"]
            assert sync["payload"]["room"]["host_participant_id"] == res.data["host_participant_id"]
            await comm.send_json_to({"event_type": "AUCTION_STARTED", "event_id": "cred_evt_1", "payload": {"ok": True}})
            started = await comm.receive_json_from()
            if started.get("event_type") != "AUCTION_STARTED":
                started = await asyncio.wait_for(comm.receive_json_from(), timeout=2)
            assert started["event_type"] == "AUCTION_STARTED"
            assert started["sequence_number"] == 1
            await comm.disconnect()

        async_to_sync(scenario)()

    def test_participant_can_connect_after_join_and_multiple_participants_connect(self):
        room = self._create_room()
        room_id = room["room"]["room_id"]
        host_token = room["host"]["token"]
        p1 = self._join(room_id, "P1")
        p2 = self._join(room_id, "P2")

        async def scenario():
            host = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={host_token}")
            c1 = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={p1['participant']['token']}")
            c2 = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={p2['participant']['token']}")
            assert (await host.connect())[0] is True
            assert (await c1.connect())[0] is True
            assert (await c2.connect())[0] is True
            for c in (host, c1, c2):
                sync = await c.receive_json_from()
                assert sync["event_type"] == "ROOM_STATE_SYNC"
            for c in (host, c1, c2):
                try:
                    await c.disconnect()
                except BaseException:
                    pass

        async_to_sync(scenario)()

    @override_settings(WS_ALLOWED_ORIGINS=["http://localhost:3000"], DEBUG=False)
    def test_native_client_without_origin_not_rejected_when_token_valid(self):
        room = self._create_room()
        room_id = room["room"]["room_id"]
        host_token = room["host"]["token"]

        async def scenario():
            comm = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={host_token}")
            connected, _ = await comm.connect()
            assert connected is True
            sync = await comm.receive_json_from()
            assert sync["event_type"] == "ROOM_STATE_SYNC"
            await comm.disconnect()

        async_to_sync(scenario)()

    def test_ws_rejects_missing_invalid_wrong_room_and_closed_room_tokens(self):
        room = self._create_room()
        room_id = room["room"]["room_id"]
        host_token = room["host"]["token"]
        p = self._join(room_id, "P1")

        async def scenario():
            c1 = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/")
            assert (await c1.connect())[0] is False
            await c1.disconnect()
            c2 = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token=invalidtoken")
            assert (await c2.connect())[0] is False
            await c2.disconnect()
            other = self._create_room()
            c3 = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={other['host']['token']}")
            assert (await c3.connect())[0] is False
            await c3.disconnect()
            self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {host_token}")
            res = self.client.delete(f"/api/v1/rooms/{room_id}/")
            assert res.status_code == 200
            c4 = WebsocketCommunicator(asgi_application, f"/ws/v1/rooms/{room_id}/?token={p['participant']['token']}")
            assert (await c4.connect())[0] is False
            await c4.disconnect()

        async_to_sync(scenario)()


class LifecycleAndCleanupTests(APITestBase):
    def test_room_expiry_cleanup_removes_waiting_room_after_tombstone(self):
        room = self.create_room().data
        room_id = room["room"]["room_id"]
        r = store.get_room(room_id)
        r.status = ROOM_WAITING
        r.expires_at = time.time() - 10
        store.cleanup_expired()
        self.assertIsNotNone(store.rooms.get(room_id))
        store.rooms[room_id].expires_at = time.time() - 1
        store.cleanup_expired()
        self.assertIsNone(store.rooms.get(room_id))

    def test_join_rejected_when_not_accepting(self):
        room = self.create_room().data
        room_id = room["room"]["room_id"]
        r = store.get_room(room_id)
        r.status = ROOM_IN_PROGRESS
        res = self.join(room_id, "P1")
        self.assertEqual(res.status_code, 409)
