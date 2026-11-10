"""USB 配发工具使用模拟串口，验证分片、编号核对和私密输出；不打开设备。"""

from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from provision_device import PREFIX, ProvisioningClient, ProvisioningError, main, validate_server_url


class FakeSerial:
    def __init__(self, *, device_id="cat-112233445566", reject=False, diagnostics=None):
        self.device_id = device_id
        self.reject = reject
        self.writes = []
        self.pending = b""
        self.closed = False
        self.diagnostics = diagnostics

    def write(self, payload):
        request = json.loads(payload[len(PREFIX):])
        self.writes.append(request)
        result = {"protocol":1, "request_id":request["request_id"], "device_id":self.device_id,
                  "ok":not self.reject, "credential_configured":True, "has_wifi":True,
                  "server_url":"https://cat.example:443", "mode":"accounts"}
        result["device_type"] = "deer" if self.device_id.startswith("deer-") else "cat"
        result["product_name"] = "赛博小鹿" if result["device_type"] == "deer" else "赛博小喵"
        if request["op"] in {"provision", "configure"}:
            result.update(pending_reboot=True, identity_saved=request["op"] == "provision")
        result["device_token"] = "unexpected-private-field"
        if request["op"] == "diagnostics" and self.diagnostics is not None:
            result["diagnostics"] = self.diagnostics
        # 普通日志、错误请求编号和分片都不应被当作本次操作结果。
        noise = PREFIX + b'{"request_id":"old"}\n'
        self.pending += b"[BOOT] simulated\n" + noise + PREFIX + json.dumps(result).encode() + b"\n"
        return len(payload)

    def read(self, size):
        data, self.pending = self.pending[:7], self.pending[7:]
        return data

    def close(self):
        self.closed = True


class ProvisionDeviceTests(unittest.TestCase):
    def test_deer_profile_and_credential_are_isolated_from_cat(self):
        stream = FakeSerial(device_id="deer-C8381AAD4F7C")
        response = ProvisioningClient(stream).exchange("status")
        self.assertEqual(response["device_type"], "deer")
        self.assertEqual(response["product_name"], "赛博小鹿")
        with self.assertRaises(ProvisioningError):
            ProvisioningClient(stream).provision({"device_id":"cat-C8381AAD4F7C", "device_token":"T"*43})
        self.assertNotIn("provision", [request["op"] for request in stream.writes])
        response = ProvisioningClient(stream).provision({"device_id":"deer-C8381AAD4F7C", "device_token":"T"*43})
        self.assertTrue(response["identity_saved"])

    def test_fragmented_status_and_noise_do_not_expose_unexpected_fields(self):
        response = ProvisioningClient(FakeSerial()).exchange("status")
        self.assertEqual(response["device_id"], "cat-112233445566")
        self.assertNotIn("device_token", response)
        self.assertNotIn("request_id", response)

    def test_device_mismatch_is_checked_before_sending_credential(self):
        stream = FakeSerial()
        with self.assertRaises(ProvisioningError):
            ProvisioningClient(stream).provision({"device_id":"cat-other", "device_token":"T"*43})
        self.assertEqual([request["op"] for request in stream.writes], ["status"])

    def test_cli_reads_private_file_but_never_prints_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device.secret.json"
            path.write_text(json.dumps({"version":1,"device_id":"cat-112233445566","device_token":"T"*43}), encoding="utf-8")
            stream, output = FakeSerial(), io.StringIO()
            with redirect_stdout(output):
                code = main(["--port","SIMULATED","provision","--credential-file",str(path)], serial_factory=lambda port: stream)
            self.assertEqual(code,0)
            self.assertTrue(stream.closed)
            self.assertTrue(json.loads(output.getvalue())["identity_saved"])
            self.assertNotIn("T"*43, output.getvalue())
            self.assertNotIn("unexpected-private-field", output.getvalue())
            self.assertEqual([request["op"] for request in stream.writes], ["status","provision"])

    def test_configure_uses_hidden_wifi_prompt_and_normalizes_server(self):
        stream, output = FakeSerial(), io.StringIO()
        with patch("builtins.input",return_value="simulated-wifi"), patch("getpass.getpass",return_value="simulated-wifi-password"), redirect_stdout(output):
            code = main(["--port","SIMULATED","configure","--server-url","https://cat.example","--mode","accounts","--wifi"], serial_factory=lambda port: stream)
        self.assertEqual(code,0)
        request = stream.writes[-1]
        self.assertEqual(request["server_url"],"https://cat.example:443")
        self.assertEqual(request["wifi_password"],"simulated-wifi-password")
        self.assertNotIn("simulated-wifi",output.getvalue())

    def test_invalid_urls_and_device_rejection_are_reported_without_raw_output(self):
        for url in ("ws://cat.example", "https://user:pass@cat.example", "https://cat.example/path", "https://cat.example?", "https://cat.example:0", "http://999.2.3.4", "https://-cat.example", "https://[::1]"):
            with self.subTest(url=url), self.assertRaises(ProvisioningError):
                validate_server_url(url)
        output = io.StringIO()
        with redirect_stderr(output):
            code = main(["--port","SIMULATED","status"],serial_factory=lambda port: FakeSerial(reject=True))
        self.assertEqual(code,1)
        self.assertNotIn("unexpected-private-field",output.getvalue())

    def test_oversize_request_is_refused_before_usb_write(self):
        stream = FakeSerial()
        with self.assertRaises(ProvisioningError):
            ProvisioningClient(stream).exchange("configure",wifi_password="x"*2000)
        self.assertEqual(stream.writes,[])

    def test_diagnostics_cli_is_read_only_and_filters_nested_private_fields(self):
        stream = FakeSerial(diagnostics={
            "filesystem_ready": True, "animation_sets": 4, "animation_frames": 120, "psram_bytes": 8388608,
            "wifi_connected": True, "camera_init_error": 0, "link_state": "bound",
            "socket_phase": "audio_connect", "free_heap_bytes": 32000, "tls_psram_preferred": True,
            "speaker_volume_percent": 78, "playback_pcm_bytes": 48000, "playback_body_errors": 0,
            "playback_buffered_bytes": 12000, "playback_buffer_capacity_bytes": 65536,
            "playback_buffer_allocation_failures": 0, "playback_rebuffers": 1,
            "playback_max_network_read_ms": 3, "playback_max_feed_gap_ms": 24,
            "device_token": "diagnostic-secret", "raw_log": "diagnostic-secret",
            "camera_transport": {"last_http_status": 101, "bytes_sent": 4096,
                                 "authorization": "diagnostic-secret"},
            "audio_transport": {"connect_pending": False, "last_tls_error": -32512},
        })
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--port", "SIMULATED", "diagnostics"], serial_factory=lambda port: stream)
        self.assertEqual(code, 0)
        self.assertTrue(stream.closed)
        self.assertEqual([request["op"] for request in stream.writes], ["diagnostics"])
        self.assertNotIn("diagnostic-secret", output.getvalue())
        self.assertNotIn("unexpected-private-field", output.getvalue())
        diagnostics = json.loads(output.getvalue())["diagnostics"]
        self.assertIs(diagnostics["filesystem_ready"], True)
        self.assertEqual(diagnostics["animation_sets"], 4)
        self.assertEqual(diagnostics["animation_frames"], 120)
        self.assertEqual(diagnostics["psram_bytes"], 8388608)
        self.assertIs(diagnostics["tls_psram_preferred"], True)
        self.assertEqual(diagnostics["playback_pcm_bytes"], 48000)
        self.assertEqual(diagnostics["playback_body_errors"], 0)
        self.assertEqual(diagnostics["playback_buffered_bytes"], 12000)
        self.assertEqual(diagnostics["playback_buffer_capacity_bytes"], 65536)
        self.assertEqual(diagnostics["playback_buffer_allocation_failures"], 0)
        self.assertEqual(diagnostics["playback_rebuffers"], 1)
        self.assertEqual(diagnostics["playback_max_network_read_ms"], 3)
        self.assertEqual(diagnostics["playback_max_feed_gap_ms"], 24)
        self.assertEqual(diagnostics["camera_transport"], {"last_http_status": 101, "bytes_sent": 4096})
        self.assertEqual(diagnostics["audio_transport"]["last_tls_error"], -32512)

    def test_diagnostics_rejects_invalid_types_and_unrestricted_text(self):
        stream = FakeSerial(diagnostics={
            "wifi_connected": "private-text", "rssi_dbm": False, "free_heap_bytes": 2**64,
            "tls_psram_preferred": "private-text",
            "link_state": "private-text", "socket_phase": ["private-text"],
            "audio_transport": {"last_tls_error": "private-text", "connect_pending": 1},
        })
        result = ProvisioningClient(stream).exchange("diagnostics")
        self.assertEqual(result["diagnostics"], {"audio_transport": {}})
        with self.assertRaises(ProvisioningError):
            ProvisioningClient(FakeSerial()).exchange("diagnostics")


if __name__ == "__main__":
    unittest.main()
