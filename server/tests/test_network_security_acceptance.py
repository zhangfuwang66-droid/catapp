"""Independent network acceptance through the real HTTP and WebSocket routes.

Only databases, model credentials and hardware are isolated.  The application
routers and security middleware are never replaced by test routes or shims.
"""

from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import asyncio
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import httpx
from starlette.websockets import WebSocketDisconnect
import uvicorn
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

from access_control import AccessSettings, SESSION_COOKIE
from database_backups import BackupSettings
from identity_store import csrf_for_session
from memory_store import MemoryStore, SummaryResult
from network_security import NetworkLimits
from support import server_app
import device_runtime


async def summarize(_messages):
    return SummaryResult(summary="simulated-network-acceptance")


class NetworkFixture(unittest.TestCase):
    mode = "local"
    origin = "http://127.0.0.1:8081"
    public_url = ""
    limits = None
    use_test_client = True

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(
            tempfile.TemporaryDirectory(prefix="network-security-")))
        self.stack.enter_context(patch.dict(os.environ, {
            "CYBERMIAO_SECRET_KEY_FILE": str(self.root / "simulated-provider.key"),
            "CYBERMIAO_DATA_DIR": str(self.root),
            "PYTHON_DOTENV_DISABLED": "1",
        }))
        for name in ("HAND_TRACKING_ENABLED", "COVER_INTERACTION_ENABLED", "ENABLE_ESP32_SCREEN_PUSH"):
            self.stack.enter_context(patch.object(device_runtime, name, False))
        self.stack.enter_context(patch.object(device_runtime, "API_KEY", ""))
        self.stack.enter_context(patch.object(device_runtime, "RECORDS_DIR", str(self.root / "records")))
        self.stack.enter_context(patch.object(server_app, "RECORDS_DIR", str(self.root / "records")))
        self.stack.enter_context(patch("knowledge_service.KnowledgeService.start", new=AsyncMock()))
        self.app = server_app.create_app(
            store=MemoryStore(self.root / "network.sqlite3"), summarizer=summarize,
            access=AccessSettings(self.mode, self.public_url), backup_settings=BackupSettings(),
            network_limits=self.limits)
        self.stack.enter_context(patch.object(
            self.app.state.providers, "key", side_effect=AssertionError("network checks must not read model keys")))
        if self.use_test_client:
            self.client = self.stack.enter_context(TestClient(
                self.app, base_url=self.origin, client=("127.0.0.1", 23456),
                raise_server_exceptions=False))

    def assert_http_denied(self, response):
        self.assertIn(response.status_code, {400, 403}, response.text)
        self.assertNotIn("set-cookie", response.headers)

    def assert_websocket_denied(self, path, headers):
        with self.assertRaises(WebSocketDisconnect) as closed:
            with self.client.websocket_connect(path, headers=headers):
                self.fail("untrusted WebSocket handshake was accepted")
        self.assertIn(closed.exception.code, {1008, 4403})

    async def invoke_http(self, path="/api/health", *, method="GET", headers=(), body=b"", chunks=None,
                          include_host=True, peer="127.0.0.1", receiver=None):
        parsed = urlsplit(self.origin)
        raw_path, separator, query = path.partition("?")
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": method, "scheme": parsed.scheme,
                 "path": raw_path, "raw_path": raw_path.encode(), "query_string": query.encode(),
                 "root_path": "", "client": (peer, 23456),
                 "server": (parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)),
                 "headers": ([(b"host", parsed.netloc.encode())] if include_host else []) + list(headers)}
        incoming = list(chunks) if chunks is not None else [{"type": "http.request", "body": body, "more_body": False}]
        sent, calls = [], 0

        async def receive():
            nonlocal calls
            calls += 1
            if receiver is not None:
                return await receiver()
            if incoming:
                return incoming.pop(0)
            await asyncio.Event().wait()

        async def send(message):
            sent.append(message)

        await asyncio.wait_for(self.app(scope, receive, send), timeout=3)
        starts = [message for message in sent if message["type"] == "http.response.start"]
        self.assertEqual(len(starts), 1, sent)
        result = httpx.Response(starts[0]["status"], headers=starts[0].get("headers", []),
                                content=b"".join(message.get("body", b"") for message in sent
                                                 if message["type"] == "http.response.body"))
        result.extensions["receive_calls"] = calls
        return result

    def raw_http(self, *args, **kwargs):
        return self.client.portal.call(lambda: self.invoke_http(*args, **kwargs))

    def assert_security_headers(self, result):
        self.assertEqual(result.headers.get("x-content-type-options"), "nosniff", result.headers)
        self.assertEqual(result.headers.get("x-frame-options"), "DENY", result.headers)
        self.assertEqual(result.headers.get("referrer-policy"), "no-referrer", result.headers)
        self.assertIn("camera=", result.headers.get("permissions-policy", ""))
        self.assertIn("frame-ancestors 'none'", result.headers.get("content-security-policy", ""))


class IndependentLocalNetworkSecurityTests(NetworkFixture):
    def test_loopback_host_remains_usable_without_browser_origin(self):
        for host in ("127.0.0.1:8081", "localhost:8081", "[::1]:8081"):
            with self.subTest(host=host):
                result = self.client.get("/api/health", headers={"Host": host})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.text, "OK")

    def test_untrusted_dns_hosts_cannot_rebind_to_local_http(self):
        for host in ("attacker.example", "localhost.attacker.example", "127.0.0.1.attacker.example"):
            with self.subTest(host=host):
                self.assert_http_denied(self.client.get("/api/health", headers={"Host": host}))

    def test_forwarded_host_does_not_override_untrusted_direct_host(self):
        self.assert_http_denied(self.client.get("/api/health", headers={
            "Host": "attacker.example", "X-Forwarded-Host": "127.0.0.1:8081",
            "X-Forwarded-For": "127.0.0.1", "Forwarded": "host=127.0.0.1:8081;for=127.0.0.1"}))

    def test_cross_origin_json_cannot_change_local_voice_state(self):
        for path in ("/voice", "/api/devices/network-local/voice"):
            with self.subTest(path=path):
                before = self.client.get(path).json()["paused"]
                result = self.client.post(path, json={"paused": not before},
                                          headers={"Origin": "https://attacker.example"})
                after = self.client.get(path).json()["paused"]
                self.assertEqual(after, before, f"cross-origin {path} changed voice state; status={result.status_code}")
                self.assert_http_denied(result)

    def test_cross_site_fetch_metadata_cannot_change_local_voice_state(self):
        before = self.client.get("/voice").json()["paused"]
        result = self.client.post("/voice", json={"paused": not before}, headers={"Sec-Fetch-Site": "cross-site"})
        after = self.client.get("/voice").json()["paused"]
        self.assertEqual(after, before, f"cross-site fetch changed voice state; status={result.status_code}")
        self.assert_http_denied(result)

    def test_cross_site_legacy_get_mutations_are_rejected(self):
        paths = ("/servo?ch=3&angle=91", "/servo_raw?ch=3&angle=91", "/servo_raw_mode?active=0",
                 "/camera_setting?param=quality&value=12", "/tft_setting?param=brightness&value=1",
                 "/screen_mode?mode=0")
        for path in paths:
            with self.subTest(path=path):
                self.assert_http_denied(self.client.get(path, headers={
                    "Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"}))

    def test_same_origin_local_write_remains_usable(self):
        before = self.client.get("/voice").json()["paused"]
        result = self.client.post("/voice", json={"paused": not before}, headers={"Origin": self.origin})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.client.get("/voice").json()["paused"], not before)

    def test_duplicate_content_encoding_cannot_hide_compressed_input(self):
        for encodings in ((b"identity", b"gzip"), (b"gzip", b"identity")):
            with self.subTest(encodings=encodings):
                before = self.client.get("/voice").json()["paused"]
                body = json.dumps({"paused": not before}).encode()
                result = self.raw_http("/voice", method="POST", body=body, headers=[
                    (b"origin", self.origin.encode()), (b"content-type", b"application/json"),
                    *[(b"content-encoding", value) for value in encodings]])
                self.assertIn(result.status_code, {400, 415}, result.text)
                self.assertEqual(self.client.get("/voice").json()["paused"], before)

    def test_duplicate_fetch_metadata_cannot_hide_cross_site_write(self):
        before = self.client.get("/voice").json()["paused"]
        result = self.raw_http("/voice", method="POST", body=json.dumps({"paused": not before}).encode(), headers=[
            (b"content-type", b"application/json"), (b"x-cybermiao-request", b"1"),
            (b"sec-fetch-site", b"same-origin"), (b"sec-fetch-site", b"cross-site")])
        self.assertIn(result.status_code, {400, 403}, result.text)
        self.assertEqual(self.client.get("/voice").json()["paused"], before)

    def test_foreign_browser_origin_cannot_open_local_websocket(self):
        self.assert_websocket_denied("/ws/viewer", {"Origin": "https://attacker.example"})

    def test_untrusted_dns_host_cannot_open_local_websocket(self):
        self.assert_websocket_denied("/ws/viewer", {"Host": "attacker.example", "Origin": "http://attacker.example"})

    def test_marker_is_explicit_and_cannot_override_foreign_browser_metadata(self):
        for headers in ({}, {"X-CyberMiao-Request": "0"},
                        {"X-CyberMiao-Request": "1", "Origin": "null"},
                        {"X-CyberMiao-Request": "1", "Sec-Fetch-Site": "cross-site"},
                        {"X-CyberMiao-Request": "1", "Sec-Fetch-Site": "same-site"}):
            with self.subTest(headers=headers):
                before = self.client.get("/voice").json()["paused"]
                result = self.client.post("/voice", json={"paused": not before}, headers=headers)
                self.assertEqual(result.status_code, 403, result.text)
                self.assertEqual(self.client.get("/voice").json()["paused"], before)
        result = self.client.post("/voice", json={"paused": True}, headers={"X-CyberMiao-Request": "1"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(self.client.get("/voice").json()["paused"])

    def test_navigation_exception_does_not_grant_private_api_access(self):
        headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}
        for path in ("/", "/knowledge", "/docs", "/redoc"):
            with self.subTest(navigation=path):
                self.assertEqual(self.client.get(path, headers=headers).status_code, 200)
        for path in ("/api/knowledge", "/voice", "/api/health"):
            with self.subTest(private_api=path):
                self.assertEqual(self.client.get(path, headers=headers).status_code, 403)

    def test_legacy_get_writes_are_removed_even_for_same_origin_clients(self):
        paths = ("/servo?ch=3&angle=91", "/servo_raw?ch=3&angle=91", "/servo_raw_mode?active=0",
                 "/camera_setting?param=quality&value=12", "/tft_setting?param=brightness&value=1",
                 "/screen_mode?mode=0")
        for path in paths:
            with self.subTest(path=path):
                result = self.client.get(path, headers={"Origin": self.origin})
                self.assertEqual(result.status_code, 405, result.text)
                self.assertIn("POST", result.headers.get("allow", ""))
                result = self.client.post(path, headers={"Origin": self.origin})
                self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.client.get("/screen_mode").json()["mode"], 0)

    def test_browser_websockets_require_origin_but_hardware_can_omit_it(self):
        for path in ("/ws_ui", "/ws/viewer", "/ws/devices/network-local/ui", "/ws/devices/network-local/viewer"):
            with self.subTest(browser=path):
                self.assert_websocket_denied(path, {})
        for path in ("/ws_audio", "/ws/camera", "/ws/devices/network-local/audio", "/ws/devices/network-local/camera"):
            with self.subTest(hardware=path):
                with self.client.websocket_connect(path):
                    pass
                self.assert_websocket_denied(path, {"Origin": "https://attacker.example"})

    def test_normal_and_rejected_responses_have_security_headers(self):
        responses = (self.client.get("/api/health"), self.client.get("/not-a-network-route"),
                     self.client.get("/servo"), self.client.get("/api/health", headers={"Host": "attacker.example"}),
                     self.client.post("/voice", json={"paused": True}),
                     self.client.post("/voice", json={"paused": {}}, headers={"Origin": self.origin}))
        self.assertEqual([result.status_code for result in responses], [200, 404, 405, 400, 403, 422])
        for result in responses:
            with self.subTest(status=result.status_code):
                self.assert_security_headers(result)


class IndependentHTTPResourceBoundaryTests(NetworkFixture):
    def test_declared_body_over_limit_is_rejected_without_reading_or_runtime(self):
        result = self.raw_http("/voice", method="POST", headers=[
            (b"origin", self.origin.encode()), (b"content-length", b"65537"),
            (b"content-type", b"application/json")])
        self.assertEqual(result.status_code, 413, result.text)
        self.assertEqual(result.extensions["receive_calls"], 0)
        self.assertEqual(self.app.state.devices.devices, {})
        self.assert_security_headers(result)

    def test_exact_body_limit_is_usable_and_chunked_overflow_has_no_side_effect(self):
        prefix, suffix = b'{"paused":true,"padding":"', b'"}'
        body = prefix + b"x" * (64 * 1024 - len(prefix) - len(suffix)) + suffix
        result = self.raw_http("/voice", method="POST", body=body, headers=[
            (b"origin", self.origin.encode()), (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode())])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(self.client.get("/voice").json()["paused"])
        result = self.raw_http("/voice", method="POST", headers=[
            (b"origin", self.origin.encode()), (b"content-type", b"application/json"),
            (b"transfer-encoding", b"chunked")], chunks=[
                {"type": "http.request", "body": b"x" * 32768, "more_body": True},
                {"type": "http.request", "body": b"x" * 32768, "more_body": True},
                {"type": "http.request", "body": b"x", "more_body": False}])
        self.assertEqual(result.status_code, 413, result.text)
        self.assertTrue(self.client.get("/voice").json()["paused"])

    def test_upload_above_generic_limit_is_preserved_without_model_access(self):
        body = b"network-security-upload\n" * 3200
        self.assertGreater(len(body), 64 * 1024)
        result = self.client.post("/api/knowledge/documents", content=body,
                                  headers={"Origin": self.origin, "X-File-Name": "allowed-network.txt"})
        self.assertEqual(result.status_code, 201, result.text)
        document = result.json()["document"]
        result = self.client.get("/api/knowledge/documents/" + document["id"] + "/download")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.content, body)

    def test_missing_duplicate_and_malformed_host_are_rejected_before_read(self):
        cases = ([], [(b"host", b"127.0.0.1:8081"), (b"host", b"attacker.example")],
                 [(b"host", b"user@127.0.0.1")], [(b"host", b"127.0.0.1:")],
                 [(b"host", b"127.0.0.1:65536")], [(b"host", b"127.0.0.1/path")])
        for headers in cases:
            with self.subTest(headers=headers):
                result = self.raw_http(headers=headers, include_host=False)
                self.assertEqual(result.status_code, 400, result.text)
                self.assertEqual(result.extensions["receive_calls"], 0)

    def test_length_ambiguity_mismatch_and_nonidentity_encoding_are_rejected(self):
        cases = (([(b"content-length", b"0"), (b"content-length", b"0")], b"", 400),
                 ([(b"content-length", b"0"), (b"transfer-encoding", b"chunked")], b"", 400),
                 ([(b"transfer-encoding", b"chunked"), (b"transfer-encoding", b"chunked")], b"", 400),
                 ([(b"content-length", b"-1")], b"", 400), ([(b"content-length", b"+1")], b"x", 400),
                 ([(b"content-length", b"1,1")], b"x", 400), ([(b"content-length", b"2")], b"x", 400),
                 ([(b"content-encoding", b"gzip")], b"", 415))
        for headers, body, status in cases:
            with self.subTest(headers=headers):
                result = self.raw_http(headers=headers, body=body)
                self.assertEqual(result.status_code, status, result.text)
                self.assert_security_headers(result)

    def test_header_count_bytes_and_url_are_bounded(self):
        result = self.raw_http(headers=[(b"x-pad", b"x" * (32 * 1024))])
        self.assertEqual(result.status_code, 431, result.text)
        result = self.raw_http(headers=[(b"x-n-" + str(index).encode(), b"x") for index in range(100)])
        self.assertEqual(result.status_code, 431, result.text)
        result = self.raw_http(headers=[(b"x-n-" + str(index).encode(), b"x") for index in range(99)])
        self.assertEqual(result.status_code, 200, result.text)
        result = self.raw_http("/api/health?" + "x" * 8192)
        self.assertEqual(result.status_code, 414, result.text)


class IndependentInjectedResourceBoundaryTests(NetworkFixture):
    limits = replace(NetworkLimits(), body_bytes=128, upload_bytes=256, body_timeout=0.25,
                     http_connections=2, websocket_connections=2)

    def test_slow_body_times_out_without_allocating_runtime_and_slot_is_released(self):
        async def slow_receive():
            await asyncio.Event().wait()

        result = self.raw_http("/voice", method="POST", receiver=slow_receive,
                               headers=[(b"origin", self.origin.encode()), (b"content-type", b"application/json")])
        self.assertEqual(result.status_code, 408, result.text)
        self.assertEqual(self.app.state.devices.devices, {})
        self.assert_security_headers(result)
        self.assertEqual(self.client.get("/api/health").status_code, 200)

    def test_chunked_upload_is_bounded_and_failed_upload_does_not_consume_capacity(self):
        headers = [(b"origin", self.origin.encode()), (b"x-file-name", b"network-small.txt"),
                   (b"transfer-encoding", b"chunked")]
        result = self.raw_http("/api/knowledge/documents", method="POST", headers=headers, chunks=[
            {"type": "http.request", "body": b"x" * 128, "more_body": True},
            {"type": "http.request", "body": b"y" * 129, "more_body": False}])
        self.assertEqual(result.status_code, 413, result.text)
        self.assertEqual(self.client.get("/api/knowledge").json()["documents"], [])
        result = self.raw_http("/api/knowledge/documents", method="POST", headers=headers, body=b"x" * 256)
        self.assertEqual(result.status_code, 201, result.text)

    def test_http_concurrency_rejects_excess_and_recovers_after_completion(self):
        async def check():
            release, entered = asyncio.Event(), asyncio.Event()
            count = 0

            async def held_receive():
                nonlocal count
                count += 1
                if count == 2:
                    entered.set()
                await release.wait()
                return {"type": "http.request", "body": b"", "more_body": False}

            holders = [asyncio.create_task(self.invoke_http(receiver=held_receive)) for _ in range(2)]
            try:
                await asyncio.wait_for(entered.wait(), timeout=1)
                result = await self.invoke_http()
                self.assertEqual(result.status_code, 503, result.text)
                self.assertEqual(result.headers.get("retry-after"), "1")
                self.assert_security_headers(result)
            finally:
                release.set()
                results = await asyncio.gather(*holders)
            self.assertEqual([result.status_code for result in results], [200, 200])
            self.assertEqual((await self.invoke_http()).status_code, 200)

        self.client.portal.call(check)

    def test_websocket_concurrency_rejects_excess_and_recovers_after_close(self):
        async def check():
            release = asyncio.Event()

            async def connection(name, accepted):
                path = "/ws/devices/" + name + "/viewer"
                scope = {"type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.4"},
                         "scheme": "ws", "path": path, "raw_path": path.encode(), "query_string": b"",
                         "root_path": "", "client": ("127.0.0.1", 23456), "server": ("127.0.0.1", 8081),
                         "headers": [(b"host", b"127.0.0.1:8081"), (b"origin", self.origin.encode())],
                         "subprotocols": []}
                sent, first = [], True

                async def receive():
                    nonlocal first
                    if first:
                        first = False
                        return {"type": "websocket.connect"}
                    await release.wait()
                    return {"type": "websocket.disconnect", "code": 1000}

                async def send(message):
                    sent.append(message)
                    if message["type"] == "websocket.accept":
                        accepted.set()

                await self.app(scope, receive, send)
                return sent

            accepted = [asyncio.Event(), asyncio.Event()]
            holders = [asyncio.create_task(connection("network-" + str(index), event))
                       for index, event in enumerate(accepted)]
            try:
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in accepted)), timeout=1)
                rejected = await asyncio.wait_for(connection("network-excess", asyncio.Event()), timeout=1)
                self.assertEqual([message["type"] for message in rejected], ["websocket.close"])
                self.assertEqual(rejected[0]["code"], 1013)
            finally:
                release.set()
                await asyncio.wait_for(asyncio.gather(*holders), timeout=2)
            recovered = await asyncio.wait_for(connection("network-recovered", asyncio.Event()), timeout=1)
            self.assertIn("websocket.accept", [message["type"] for message in recovered])

        self.client.portal.call(check)


class IndependentLoopbackWebSocketTests(NetworkFixture):
    """Real loopback transport; a larger server cap proves application limits."""

    use_test_client = False

    def setUp(self):
        super().setUp()
        listener = self.stack.enter_context(socket.socket())
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        self.port = listener.getsockname()[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(
            self.app, host="127.0.0.1", port=self.port, loop="asyncio", log_config=None,
            log_level="critical", access_log=False, proxy_headers=False, ws_max_size=2 * 1024 * 1024,
            ws_max_queue=8, timeout_graceful_shutdown=1))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [listener]}, daemon=True)

        def stop():
            self.server.should_exit = True
            self.thread.join(timeout=4)
            self.assertFalse(self.thread.is_alive(), "isolated loopback server did not stop")

        self.thread.start()
        self.stack.callback(stop)
        deadline = time.monotonic() + 5
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                self.fail("isolated loopback server did not start")
            time.sleep(0.01)

    def websocket(self, channel, *, origin=False):
        return connect(f"ws://127.0.0.1:{self.port}/ws/devices/network-wire/{channel}",
                       origin=self.origin if origin else None, proxy=None, open_timeout=2,
                       close_timeout=1, max_size=2 * 1024 * 1024)

    def drain_ui_init(self, ui):
        initial = [ui.recv(timeout=2) for _ in range(4)]
        self.assertTrue(initial[0].startswith("INIT:"), initial)
        self.assertTrue(any(message.startswith("SCREENMODE:") for message in initial), initial)

    def assert_closed_for_size(self, connection):
        for _ in range(12):
            try:
                connection.recv(timeout=2)
            except ConnectionClosed as closed:
                self.assertIsNotNone(closed.rcvd)
                self.assertEqual(closed.rcvd.code, 1009)
                self.assertNotIn(str(self.root), closed.rcvd.reason)
                return
        self.fail("oversized message did not close the WebSocket")

    def test_http_host_guard_also_operates_on_real_loopback_transport(self):
        with httpx.Client(base_url=self.origin, timeout=2, trust_env=False) as client:
            self.assertEqual(client.get("/api/health").status_code, 200)
            result = client.get("/api/health", headers={"Host": "attacker.example"})
            self.assertEqual(result.status_code, 400, result.text)
            self.assert_security_headers(result)

    def test_browser_and_hardware_handshakes_enforce_real_origin_boundary(self):
        for channel in ("ui", "viewer"):
            with self.subTest(channel=channel), self.assertRaises(InvalidStatus) as rejected:
                with self.websocket(channel):
                    self.fail("browser WebSocket accepted a missing Origin")
            self.assertEqual(rejected.exception.response.status_code, 403)
        for channel in ("audio", "camera"):
            with self.subTest(channel=channel), self.assertRaises(InvalidStatus) as rejected:
                with connect(f"ws://127.0.0.1:{self.port}/ws/devices/network-wire/{channel}",
                             origin="https://attacker.example", proxy=None, open_timeout=2, close_timeout=1):
                    self.fail("hardware WebSocket accepted a foreign Origin")
            self.assertEqual(rejected.exception.response.status_code, 403)

    def test_ui_exact_text_limit_is_forwarded_and_overflow_is_not(self):
        with self.websocket("audio") as audio, self.websocket("ui", origin=True) as ui:
            self.drain_ui_init(ui)
            allowed = "SAFE:" + "x" * (16 * 1024 - len("SAFE:"))
            ui.send(allowed)
            self.assertEqual(audio.recv(timeout=2), allowed)
            ui.send("x" * (16 * 1024 + 1))
            self.assert_closed_for_size(ui)
            audio.send("STOP")
            self.assertEqual(audio.recv(timeout=2), "OK:STOPPED")
            with self.websocket("ui", origin=True) as replacement:
                self.drain_ui_init(replacement)
                replacement.send("SAFE:replacement")
                self.assertEqual(audio.recv(timeout=2), "SAFE:replacement")

    def test_text_limit_counts_utf8_bytes(self):
        with self.websocket("audio") as audio, self.websocket("ui", origin=True) as ui:
            self.drain_ui_init(ui)
            allowed = "猫" * (16 * 1024 // 3)
            ui.send(allowed)
            self.assertEqual(audio.recv(timeout=2), allowed)
            ui.send(allowed + "猫")
            self.assert_closed_for_size(ui)
            audio.send("STOP")
            self.assertEqual(audio.recv(timeout=2), "OK:STOPPED")

    def test_audio_exact_binary_limit_and_overflow_preserve_hardware_compatibility(self):
        with self.websocket("audio") as audio:
            audio.send(b"x" * (256 * 1024))
            audio.send("STOP")
            self.assertEqual(audio.recv(timeout=2), "OK:STOPPED")
            audio.send(b"x" * (256 * 1024 + 1))
            self.assert_closed_for_size(audio)

    def test_camera_exact_binary_limit_is_forwarded_and_overflow_is_not_cached(self):
        with self.websocket("viewer", origin=True) as viewer, self.websocket("camera") as camera:
            self.assertTrue(camera.recv(timeout=2).startswith("SCRMODE:"))
            self.assertTrue(camera.recv(timeout=2).startswith("SET:FPS="))
            allowed = b"f" * (1024 * 1024)
            camera.send(allowed)
            self.assertEqual(viewer.recv(timeout=2), allowed)
            camera.send(allowed + b"x")
            self.assert_closed_for_size(camera)
            with self.assertRaises(TimeoutError):
                viewer.recv(timeout=0.2)
            runtime = self.app.state.devices.devices["network-wire"]
            self.assertTrue(all(len(frame) <= 1024 * 1024 for _timestamp, frame in runtime.last_frames))

    def test_viewer_input_text_limit_closes_with_1009(self):
        with self.websocket("viewer", origin=True) as viewer:
            viewer.send("x" * (16 * 1024 + 1))
            self.assert_closed_for_size(viewer)


class IndependentAccountsNetworkSecurityTests(NetworkFixture):
    mode = "accounts"
    origin = "https://network-security.example"
    public_url = origin
    password = "simulated-network-only-password"

    def setUp(self):
        super().setUp()
        self.user = self.app.state.identities.create_user("network-member", self.password)
        self.grant = self.app.state.identities.login(self.user.username, self.password, client_key="qa-fixture")
        self.headers = {"Cookie": f"{SESSION_COOKIE}={self.grant.token}", "Origin": self.origin,
                        "X-CSRF-Token": csrf_for_session(self.grant.token)}

    def test_configured_public_origin_does_not_allow_foreign_http_host(self):
        self.assert_http_denied(self.client.get("/api/health", headers={"Host": "attacker.example"}))

    def test_development_mode_still_rejects_malformed_dns_hostnames(self):
        with patch.object(self.app.state, "access", AccessSettings("accounts")):
            for host in ("bad%.example", "bad|host.example", "two..labels.example", "-bad.example"):
                with self.subTest(host=host):
                    result = self.client.get("/api/health", headers={"Host": host})
                    self.assertEqual(result.status_code, 400, result.text)

    def test_public_host_health_exception_requires_actual_loopback_peer_and_read_method(self):
        probe_headers = [(b"host", b"127.0.0.1:8081")]
        for path in ("/api/health", "/api/ready"):
            with self.subTest(probe=path):
                result = self.raw_http(path, headers=probe_headers, include_host=False)
                self.assertEqual(result.status_code, 200, result.text)
        for peer in ("192.168.1.20", "198.51.100.20"):
            with self.subTest(untrusted_peer=peer):
                result = self.raw_http(headers=probe_headers + [(b"x-forwarded-for", b"127.0.0.1")],
                                       include_host=False, peer=peer)
                self.assertEqual(result.status_code, 400, result.text)
        result = self.raw_http("/api/health", method="POST", headers=probe_headers, include_host=False)
        self.assertEqual(result.status_code, 400, result.text)
        result = self.raw_http("/api/auth/session", headers=probe_headers, include_host=False)
        self.assertEqual(result.status_code, 400, result.text)

    def test_accounts_mode_closes_schema_and_interactive_documentation(self):
        for path in ("/docs", "/redoc", "/openapi.json"):
            with self.subTest(path=path):
                result = self.client.get(path)
                self.assertEqual(result.status_code, 404, result.text)
                self.assert_security_headers(result)

    def test_strict_admin_login_csp_is_preserved(self):
        result = self.client.get("/admin/login")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.headers.get("content-security-policy"), server_app.ADMIN_CSP)
        self.assert_security_headers(result)

    def test_foreign_login_origin_and_cross_site_metadata_issue_no_session(self):
        for origin, fetch_site in (("https://attacker.example", "same-origin"), (self.origin, "cross-site"), ("null", "cross-site")):
            with self.subTest(origin=origin, fetch_site=fetch_site):
                result = self.client.post("/api/auth/login", json={"username": self.user.username, "password": self.password},
                                          headers={"Origin": origin, "Sec-Fetch-Site": fetch_site})
                self.assertEqual(result.status_code, 403, result.text)
                self.assertNotIn("set-cookie", result.headers)
                self.assertNotIn(self.password, result.text)

    def test_csrf_rejection_preserves_session_and_authorized_logout_revokes_it(self):
        missing = {key: value for key, value in self.headers.items() if key != "X-CSRF-Token"}
        result = self.client.post("/api/auth/logout", headers=missing)
        self.assertEqual(result.status_code, 403, result.text)
        self.assertIsNotNone(self.app.state.identities.session_user(self.grant.token))
        self.assertEqual(self.client.post("/api/auth/logout", headers=self.headers).status_code, 200)
        self.assertIsNone(self.app.state.identities.session_user(self.grant.token))
        self.assertEqual(self.client.get("/api/auth/session", headers=self.headers).status_code, 401)

    def test_validation_and_internal_failure_do_not_echo_secrets_or_database_path(self):
        sentinel = "network-secret-sentinel-" * 12
        for path in ("/api/auth/login", "/api/admin/auth/login"):
            with self.subTest(path=path):
                result = self.client.post(path, json={"username": self.user.username, "password": sentinel},
                                          headers={"Origin": self.origin})
                self.assertEqual(result.status_code, 422, result.text)
                self.assertNotIn(sentinel, result.text)
                self.assertNotIn(str(self.root), result.text)
        with patch.object(self.app.state.identities, "session_user", side_effect=RuntimeError(sentinel + str(self.root))):
            result = self.client.get("/api/auth/session", headers=self.headers)
        self.assertEqual(result.status_code, 500, result.text)
        self.assertNotIn(sentinel, result.text)
        self.assertNotIn(str(self.root), result.text)

    def test_internal_error_responses_also_have_security_headers(self):
        with patch.object(self.app.state.identities, "session_user", side_effect=RuntimeError("simulated-failure")):
            result = self.client.get("/api/auth/session", headers=self.headers)
        self.assertEqual(result.status_code, 500, result.text)
        self.assert_security_headers(result)

    def test_static_traversal_and_windows_aliases_cannot_expose_protected_files(self):
        paths = ("/web/%2e%2e/app.py", "/web/%2e%2e/access_control.py", "/web/Admin/index.html",
                 "/web/admin%5cindex.html", "/web/admin./index.html", "/web/admin%20/index.html",
                 "/web/admin%2findex.html", "/web/admin/admin.js")
        for path in paths:
            with self.subTest(path=path):
                result = self.client.get(path, follow_redirects=False)
                self.assertIn(result.status_code, {401, 403, 404}, result.text[:300])
                self.assertNotIn("cybermiao_session =", result.text)
                self.assertNotIn(str(self.root), result.text)


if __name__ == "__main__":
    unittest.main()
