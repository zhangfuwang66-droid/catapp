"""Renderer attack cases plus optional real Nginx/TLS/proxy checks, all local."""

import importlib.util
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from http.client import HTTPConnection, HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


spec = importlib.util.spec_from_file_location("render_public_edge", Path(__file__).resolve().parents[1] / "deploy/render_public_edge.py")
edge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edge)


class PublicEdgeRendererTests(unittest.TestCase):
    def test_ipv4_default_port_and_domain_custom_port_are_literal(self):
        for url, canonical in (("https://198.51.100.27:443/", "198.51.100.27"),
                               ("https://CAT.example:8443", "cat.example:8443")):
            files = edge.render(url, "/etc/ssl/cat/fullchain.pem", "/etc/ssl/cat/privkey.pem")
            self.assertIn(f'proxy_set_header Host "{canonical}";', files["nginx-public.conf"])
            self.assertIn(f"Environment=CYBERMIAO_PUBLIC_URL=https://{canonical}", files["cybermiao-public.conf"])
            self.assertNotIn("@@", "".join(files.values()))

    def test_address_and_path_injection_fail_before_writing(self):
        for url in ("http://cat.example", "https://cat.example:0", "https://cat.example:65536",
                    "https://cat.example:", "https://cat.example:0443", "https://u:p@cat.example",
                    "https://cat.example/a", "https://cat.example?x=1", "https://cat.example#x",
                    "https://[::1]", "https://127.000.0.1", "https://cat.example\n",
                    "https://cat.example;include", "https://cat.example\\x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                edge.render(url, "/cert.pem", "/key.pem")
        for path in ("relative.pem", "/a/../key.pem", "/a//key.pem", "/a b.pem", "/a;include",
                     "/a\nerror_log", "/$variable", "/a\"", "/a\\b", "/a/./b"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                edge.render("https://cat.example", path, "/key.pem")

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="edge-render-review-") as directory:
            output = Path(directory) / "new"
            files = edge.render("https://cat.example", "/cert.pem", "/key.pem")
            edge.write_bundle(output, files)
            sentinel = output / "nginx-public.conf"
            sentinel.write_text("keep-existing-config", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                edge.write_bundle(output, files)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep-existing-config")

    def test_optional_renewal_webroot_is_literal_and_no_certificate_is_read(self):
        files = edge.render("https://198.51.100.27:8443", "/absent/cert.pem", "/absent/key.pem", "/var/lib/acme")
        self.assertIn("root /var/lib/acme;", files["nginx-public.conf"])
        self.assertIn('"198.51.100.27:80" 1;', files["nginx-public.conf"])
        with self.assertRaises(ValueError):
            edge.render("https://cat.example", "/cert.pem", "/key.pem", "/a;return")

    def test_default_shared_nat_budget_preserves_global_and_upload_limits(self):
        source = edge.render("https://cat.example", "/cert.pem", "/key.pem")["nginx-public.conf"]
        # Twenty devices need forty audio/camera sockets before browser connections/polls.
        import re
        self.assertEqual(re.findall(r"limit_conn cybermiao_peer (\d+);", source), ["96"] * 4)
        self.assertIn("limit_conn cybermiao_ws 128;", source)
        self.assertEqual(source.count("limit_conn cybermiao_http 64;"), 3)
        self.assertIn("limit_conn cybermiao_upload 2;", source)


class _Backend(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path.startswith("/ws/devices/"):
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "upgrade")
            self.end_headers()
            return
        self.reply()

    def do_POST(self):
        self.reply()

    def reply(self):
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length) if length else b""
        body = json.dumps({"headers": dict(self.headers), "size": len(data)}).encode()
        self.send_response(500 if self.path == "/fault" else 200)
        if self.path == "/strict":
            self.send_header("Content-Security-Policy", "default-src 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@unittest.skipUnless(os.name == "posix" and os.environ.get("CYBERMIAO_REVIEW_NGINX"), "optional Linux Nginx binary not supplied")
class PublicEdgeNginxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="cybermiao-edge-nginx-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.binary = os.environ["CYBERMIAO_REVIEW_NGINX"]
        cls.backend = ThreadingHTTPServer(("127.0.0.1", 8081), _Backend)
        cls.backend.daemon_threads = True
        cls.addClassCleanup(cls.backend.server_close)
        cls.thread = threading.Thread(target=cls.backend.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.backend.shutdown)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            cls.https_port = listener.getsockname()[1]
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            cls.http_port = listener.getsockname()[1]
        cls.cert, cls.key = str(cls.root / "cert.pem"), str(cls.root / "key.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
                        "-keyout", cls.key, "-out", cls.cert], check=True, capture_output=True)
        challenge = cls.root / "acme/.well-known/acme-challenge"
        challenge.mkdir(parents=True)
        (challenge / "review-token").write_text("local-renewal-challenge", encoding="ascii")
        # Test sandbox is private; Nginx workers need traversal/read access only to this dummy challenge.
        cls.root.chmod(0o755)
        files = edge.render(f"https://127.0.0.1:{cls.https_port}", cls.cert, cls.key, str(cls.root / "acme"))
        source = files["nginx-public.conf"].replace("listen 80 ", f"listen 127.0.0.1:{cls.http_port} ")
        source = source.replace("    listen [::]:80 default_server;", "")
        source = source.replace(f"listen {cls.https_port} ", f"listen 127.0.0.1:{cls.https_port} ")
        source = source.replace(f"    listen [::]:{cls.https_port} ssl default_server;", "")
        source = source.replace("/var/log/nginx/cybermiao-access.log", str(cls.root / "access.log"))
        cls.config = cls.root / "nginx.conf"
        cls.config.write_text(f"pid {cls.root}/nginx.pid;\nerror_log /dev/null crit;\nevents {{ worker_connections 256; }}\nhttp {{\n"
                              f"client_body_temp_path {cls.root}/body;\nproxy_temp_path {cls.root}/proxy;\n"
                              f"fastcgi_temp_path {cls.root}/fastcgi;\nuwsgi_temp_path {cls.root}/uwsgi;\n"
                              f"scgi_temp_path {cls.root}/scgi;\n" + source + "\n}\n", encoding="utf-8")
        cls.command = [cls.binary, "-p", str(cls.root), "-c", str(cls.config)]
        result = subprocess.run(cls.command + ["-t"], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)
        subprocess.run(cls.command, check=True, capture_output=True)
        cls.addClassCleanup(lambda: subprocess.run(cls.command + ["-s", "quit"], capture_output=True))
        cls.context = ssl.create_default_context(cafile=cls.cert)

    def request(self, path, *, method="GET", headers=None, body=None, plain=False):
        conn = (HTTPConnection("127.0.0.1", self.http_port, timeout=3) if plain else
                HTTPSConnection("127.0.0.1", self.https_port, context=self.context, timeout=3))
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_tls_ipv4_and_spoofed_headers_are_replaced_or_removed(self):
        status, _headers, data = self.request("/echo", headers={"X-Forwarded-For": "198.51.100.9", "X-Real-IP": "198.51.100.9",
            "Forwarded": "for=198.51.100.9", "X-Forwarded-Proto": "http", "X-Forwarded-Host": "attacker.example",
            "X-Forwarded-Port": "80", "X-Forwarded-Prefix": "/outside"})
        self.assertEqual(status, 200)
        echoed = json.loads(data)["headers"]
        self.assertEqual(echoed["Host"], f"127.0.0.1:{self.https_port}")
        self.assertEqual(echoed["X-Forwarded-For"], "127.0.0.1")
        self.assertEqual(echoed["X-Real-IP"], "127.0.0.1")
        self.assertEqual(echoed["X-Forwarded-Proto"], "https")
        for name in ("Forwarded", "X-Forwarded-Host", "X-Forwarded-Port", "X-Forwarded-Prefix"):
            self.assertNotIn(name, echoed)
        self.assertEqual(self.request("/", headers={"Host": "attacker.example"})[0], 421)

    def test_errors_have_fallback_headers_and_admin_csp_is_preserved(self):
        for path, expected in (("/fault", 500), ("/openapi.json", 404), ("/provider.key", 404)):
            status, headers, _body = self.request(path)
            self.assertEqual(status, expected)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(headers["Strict-Transport-Security"], "max-age=86400")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("/strict")[1]["Content-Security-Policy"], "default-src 'none'")

    def test_body_classes_and_websocket_upgrade(self):
        body = b"x" * (64 * 1024 + 1)
        self.assertEqual(self.request("/api/check", method="POST", body=body)[0], 413)
        status, _headers, data = self.request("/api/knowledge/documents", method="POST", body=body)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["size"], len(body))
        self.assertEqual(self.request("/api/knowledge/documents", method="POST", headers={"Content-Length": str(20 * 1024 * 1024 + 1)})[0], 413)
        self.assertEqual(self.request("/api/knowledge/documents", method="GET")[0], 405)
        self.assertEqual(self.request("/ws/devices/cat-review/audio", headers={"Connection": "Upgrade", "Upgrade": "websocket"})[0], 101)

    def test_http_renewal_challenge_and_canonical_redirect(self):
        headers = {"Host": "127.0.0.1"}
        status, _response_headers, body = self.request("/.well-known/acme-challenge/review-token", headers=headers, plain=True)
        self.assertEqual((status, body), (200, b"local-renewal-challenge"))
        status, response_headers, _body = self.request("/hello?q=1", headers=headers, plain=True)
        self.assertEqual(status, 308)
        self.assertEqual(response_headers["Location"], f"https://127.0.0.1:{self.https_port}/hello?q=1")
        self.assertEqual(self.request("/", headers={"Host": "attacker.example"}, plain=True)[0], 421)

    def test_metadata_log_never_records_sentinel_query_or_credentials(self):
        self.request("/echo?SENTINEL_QUERY_9e21", headers={"Authorization": "Bearer SENTINEL_AUTH_9e21",
                     "Cookie": "test=SENTINEL_COOKIE_9e21", "Referer": "https://example.test/SENTINEL_REFERER_9e21"})
        log = (self.root / "access.log").read_text(encoding="utf-8")
        self.assertTrue(log)
        self.assertNotIn("SENTINEL_", log)
        for line in log.splitlines():
            self.assertEqual(set(json.loads(line)), {"time", "peer", "route", "status", "bytes", "seconds"})


if __name__ == "__main__":
    unittest.main()
