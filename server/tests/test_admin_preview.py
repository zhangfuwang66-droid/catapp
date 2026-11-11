"""在新进程验证预览不读取真实配置、没有模型/设备通道并清理临时数据。"""

import json
from http.cookiejar import CookieJar
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import textwrap
import threading
from types import SimpleNamespace
import unittest
from urllib import request


class AdminPreviewTests(unittest.TestCase):
    def test_feature_detection_checks_openapi_methods_not_route_container_shape(self):
        from tools.preview_admin import preview_features
        missing = SimpleNamespace(openapi=lambda: {'paths': {
            '/api/admin/dashboard': {'post': {}}, '/api/admin/users': {'get': {}}}})
        self.assertEqual(preview_features(missing), {
            'dashboard': False, 'device_crud': False, 'account_crud': False})
        included = SimpleNamespace(openapi=lambda: {'paths': {
            '/api/admin/dashboard': {'get': {}}, '/api/admin/users': {'get': {}, 'post': {}},
            '/api/admin/devices/{device_id}/rotate-credential': {'post': {}}}})
        self.assertEqual(preview_features(included), {
            'dashboard': True, 'device_crud': True, 'account_crud': True})

    def test_preview_is_isolated_authenticated_and_removes_its_data(self):
        server_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="preview-isolation-sentinel-") as temporary:
            sentinel = Path(temporary)
            (sentinel / ".env").write_text("HAND_TRACKING_INTERVAL_SEC=invalid-real-config-sentinel", encoding="utf-8")
            (sentinel / "keep.txt").write_text("outside preview data", encoding="utf-8")
            environment = dict(os.environ, PYTHONPATH=str(server_root), PYTHONDONTWRITEBYTECODE="1",
                               PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                               CYBERMIAO_DATA_DIR=str(sentinel), CYBERMIAO_BACKUP_DIR=str(sentinel),
                               CYBERMIAO_MEMORY_DB_PATH=str(sentinel / "must-not-exist.sqlite3"),
                               CYBERMIAO_SECRET_KEY_FILE=str(sentinel / "must-not-exist.key"),
                               HAND_TRACKING_INTERVAL_SEC="not-a-number")
            code = textwrap.dedent('''
                import json
                from pathlib import Path
                from tools.preview_admin import preview_application, PASSWORD
                with preview_application() as (application, directory):
                    from fastapi.testclient import TestClient
                    from starlette.websockets import WebSocketDisconnect
                    import runtime_paths
                    assert runtime_paths.DATA_ROOT == directory
                    assert application.state.memory_manager.store.path.parent == directory
                    assert application.state.provider_settings.key_file.parent == directory
                    assert application.state.database_backups.settings.directory.parent == directory
                    with TestClient(application, base_url='http://127.0.0.1:8077') as client:
                        assert client.get('/admin', follow_redirects=False).status_code == 303
                        response = client.post('/api/auth/login', headers={'Origin': 'http://127.0.0.1:8077'},
                                               json={'username': 'preview-admin', 'password': PASSWORD})
                        assert response.status_code == 200, response.text
                        assert client.get('/admin').status_code == 200
                        devices = client.get('/api/admin/devices').json()
                        assert devices['total'] == 4, devices
                        assert any(row['loan_active'] for row in devices['items'])
                        assert any(row['revoked'] for row in devices['items'])
                        assert client.get('/api/admin/overview').json()['model_billing'] is None
                        assert client.put('/api/account/model', json={'api_key': 'simulated-preview-input'}).status_code == 403
                        try:
                            with client.websocket_connect('/ws/devices/cat-preview/ui'):
                                raise AssertionError('preview accepted device WebSocket')
                        except WebSocketDisconnect as exc:
                            assert exc.code == 4403
                        assert application.state.devices.devices == {}
                        assert not application.state.provider_settings.status(
                            application.state.identities.list_users()[0]['id'])['configured']
                    result = {'directory': str(directory), 'temporary_data_exists': directory.exists()}
                result['removed_after_exit'] = not Path(result['directory']).exists()
                print(json.dumps(result))
            ''')
            result = subprocess.run([sys.executable, "-X", "utf8", "-c", code], env=environment,
                                    cwd=server_root.parent, capture_output=True, text=True,
                                    encoding="utf-8", timeout=35)
            self.assertEqual(result.returncode, 0, result.stderr)
            outcome = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertTrue(outcome["temporary_data_exists"])
            self.assertTrue(outcome["removed_after_exit"])
            self.assertEqual(sorted(path.name for path in sentinel.iterdir()), [".env", "keep.txt"])
            self.assertEqual((sentinel / "keep.txt").read_text(encoding="utf-8"), "outside preview data")

    def test_cli_serves_loopback_and_cleans_up_after_interrupt(self):
        script = Path(__file__).resolve().parents[1] / "tools" / "preview_admin.py"
        bootstrap = textwrap.dedent('''
            import _thread
            from pathlib import Path
            import sys
            import threading
            sys.path.insert(0, str(Path(sys.argv[1]).parents[1]))
            from tools import preview_admin
            def interrupt_on_input():
                if sys.stdin.readline():
                    _thread.interrupt_main()
            original_serve = preview_admin.serve
            async def serve(*args):
                # Windows 的 NumPy 扩展加载完成后再启动阻塞控制线程。
                threading.Thread(target=interrupt_on_input, daemon=True).start()
                return await original_serve(*args)
            preview_admin.serve = serve
            raise SystemExit(preview_admin.main(['--port', '0']))
        ''')
        child = subprocess.Popen([sys.executable, "-X", "utf8", "-c", bootstrap, str(script)],
                                 cwd=script.parents[2], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                 env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        first_line = queue.Queue()
        threading.Thread(target=lambda: first_line.put(child.stdout.readline()), daemon=True).start()
        try:
            try:
                line = first_line.get(timeout=20)
            except queue.Empty:
                child.kill()
                stdout, stderr = child.communicate(timeout=5)
                self.fail('预览未报告就绪: ' + stderr + stdout)
            self.assertTrue(line, "预览启动前退出")
            info = json.loads(line)
            self.assertTrue(info["url"].startswith("http://127.0.0.1:"), info)
            directory = Path(info["temporary_data_dir"])
            self.assertTrue(directory.exists())
            with request.urlopen(info["url"].replace("/admin", "/api/health"), timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"OK")
            base_url = info['url'].removesuffix('/admin')
            opener = request.build_opener(request.ProxyHandler({}), request.HTTPCookieProcessor(CookieJar()))
            headers = {'Origin': base_url, 'Content-Type': 'application/json'}

            def call(path, method='GET', body=None):
                data = json.dumps(body).encode('utf-8') if body is not None else None
                with opener.open(request.Request(base_url + path, data=data, headers=headers, method=method), timeout=8) as response:
                    return response.status, json.load(response)

            login_status, session = call('/api/auth/login', 'POST', {
                'username': 'preview-admin', 'password': 'preview-only-password'})
            self.assertEqual(login_status, 200)
            headers['X-CSRF-Token'] = session['csrf_token']
            dashboard_status, _ = call('/api/admin/dashboard')
            rotation_status, _ = call('/api/admin/devices/deer-preview/rotate-credential', 'POST', {
                'confirm_device_id': 'deer-preview'})
            creation_status, _ = call('/api/admin/users', 'POST', {
                'username': 'cli-check-user', 'password': 'simulated-cli-password', 'confirm_username': 'cli-check-user'})
            self.assertEqual(info['features'], {'dashboard': dashboard_status == 200,
                                               'device_crud': rotation_status == 200,
                                               'account_crud': creation_status == 201})
            self.assertTrue(all(info['features'].values()))
            stdout, stderr = child.communicate(input="stop\n", timeout=20)
            self.assertEqual(child.returncode, 0, stderr)
            self.assertIn("临时数据已清理", stdout)
            self.assertFalse(directory.exists())
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
