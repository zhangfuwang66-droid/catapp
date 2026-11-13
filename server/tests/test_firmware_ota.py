"""真实 SQLite 与应用请求验证签名、离线领取、归属变更与升级回执，不访问硬件/模型。"""
import base64
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from support import server_app
from memory_store import MemoryStore, SummaryResult
from access_control import AccessSettings, SESSION_COOKIE
from firmware_store import FirmwareStore
from firmware_manifest import signing_bytes, verify_manifest, read_package, HARDWARE, LAYOUT
from starlette.requests import Request
from firmware_routes import download


async def summarize(messages):
    return SummaryResult(summary='模拟摘要')


class FirmwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = server_app.create_app(store=MemoryStore(self.root / 'memory.sqlite3'), summarizer=summarize, access=AccessSettings('accounts'))
        self.identities = self.app.state.identities
        self.alice = self.identities.create_user('alice', 'ota-test-password-a')
        self.bob = self.identities.create_user('bob', 'ota-test-password-b')
        self.token = self.identities.register_device('deer-ABCDEF012345', '模拟鹿').token
        grant = self.identities.request_pairing('deer-ABCDEF012345', self.token)
        self.identities.claim_pairing(grant.code, self.alice.id, client_key='test')
        self.now = 1000
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.public = self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        public = self.root / 'public.pem'; public.write_bytes(self.public)
        releases = self.root / 'releases'; releases.mkdir()
        self.store = FirmwareStore(self.root / 'memory.sqlite3', releases, public, clock=lambda: self.now)
        self.app.state.firmware = self.store
        self.client = TestClient(self.app)
        self.client.__enter__()
        result = self.client.post('/api/auth/login', json={'username':'alice','password':'ota-test-password-a'}, headers={'Origin':'http://testserver'})
        self.owner_headers = {'Origin':'http://testserver','X-CSRF-Token':result.json()['csrf_token']}
        self.device_headers = {'Authorization':'Bearer ' + self.token}
        self.prefix = '/api/devices/deer-ABCDEF012345/firmware'
        self.check_url = '/api/firmware/devices/deer-ABCDEF012345/check'
        self.report = dict(version='0.3.0-ota.1',sequence=1,ota_capable=True,slot='app0',previous_version='',rollback_available=False,state='idle',progress=0,command_id='',error_code='',busy=False,idle_seconds=0)
        self.add_release()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.temp.cleanup()

    def device(self):
        return self.identities.credential_device('deer-ABCDEF012345', self.token)

    def add_release(self):
        image = bytearray(1024); image[0] = 0xE9; image[12] = 9
        marker = b'CYBERMIAO_OTA:deer:0.3.0-ota.2:2'; image[64:64+len(marker)] = marker
        manifest = dict(schema=1,id='deer-0.3.0-ota.2',profile='deer',version='0.3.0-ota.2',sequence=2,hardware=HARDWARE,layout=LAYOUT,size=len(image),sha256=hashlib.sha256(image).hexdigest(),notes='完整语音与远程更新')
        manifest['notes_sha256'] = hashlib.sha256(manifest['notes'].encode()).hexdigest()
        manifest['signature'] = base64.b64encode(self.key.sign(signing_bytes(manifest),ec.ECDSA(hashes.SHA256()))).decode()
        target = self.store.release_root / manifest['id']; target.mkdir()
        (target/'application.bin').write_bytes(image)
        (target/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        self.manifest = manifest

    def test_signature_corruption_wrong_profile_and_wrong_image_are_not_published(self):
        self.assertEqual(len(self.store.releases()),1)
        tampered = {**self.manifest,'sequence':3}
        with self.assertRaises(ValueError): verify_manifest(tampered,self.public)
        with self.assertRaises(ValueError): verify_manifest({**self.manifest,'notes':'伪造更新说明'},self.public)
        with self.assertRaises(ValueError): verify_manifest({**self.manifest,'profile':'cat'},self.public)
        image = self.store.release_root/self.manifest['id']/'application.bin'
        image.write_bytes(b'broken')
        self.assertEqual(self.store.releases(),[])

    def test_offline_command_survives_process_restart_and_late_device_receives_it(self):
        self.store.check(self.device(), self.report)
        self.now += 60
        self.assertFalse(self.store.view(self.device())['online'])
        payload={'action':'install','release_id':self.manifest['id'],'confirm_device_id':'deer-ABCDEF012345'}
        command = self.client.post(self.prefix+'/update',json=payload,headers=self.owner_headers)
        self.assertEqual(command.status_code,200,command.text)
        repeated = self.client.post(self.prefix+'/update',json=payload,headers=self.owner_headers).json()
        self.assertEqual(command.json()['id'],repeated['id'])
        restarted=FirmwareStore(self.store.path,self.store.release_root,self.store.public_key_path,clock=lambda:self.now)
        self.app.state.firmware=restarted
        received=self.client.post(self.check_url,json=self.report,headers=self.device_headers)
        self.assertEqual(received.status_code,200,received.text)
        self.assertEqual(received.json()['command']['manifest']['version'],'0.3.0-ota.2')
        download=self.client.get('/api/firmware/devices/deer-ABCDEF012345/download/'+command.json()['id'],headers=self.device_headers)
        self.assertEqual(download.status_code,200)
        progress={**self.report,'command_id':command.json()['id'],'state':'downloading','progress':52}
        self.client.post(self.check_url,json=progress,headers=self.device_headers)
        self.assertEqual(restarted.view(self.device())['command']['progress'],52)

    def test_manual_default_idle_policy_and_no_infinite_failed_release_loop(self):
        self.assertIsNone(self.store.check(self.device(),{**self.report,'idle_seconds':600})['command'])
        result=self.client.put(self.prefix+'/policy',json={'auto_update':True},headers=self.owner_headers)
        self.assertEqual(result.status_code,200,result.text)
        self.assertIsNone(self.store.check(self.device(),{**self.report,'idle_seconds':299})['command'])
        self.assertIsNone(self.store.check(self.device(),{**self.report,'idle_seconds':600,'busy':True})['command'])
        command=self.store.check(self.device(),{**self.report,'idle_seconds':300})['command']
        self.assertIsNotNone(command)
        self.assertIsNone(self.store.check(self.device(),{**self.report,'idle_seconds':600,'command_id':command['id'],'state':'rolled_back'})['command'])
        self.assertTrue(self.store.view(self.device())['automatic_blocked'])
        self.assertIsNotNone(self.store.request(self.device(), 'install'))  # 手动可重试，自动不循环。

    def test_auth_csrf_ownership_revoke_and_report_validation(self):
        self.assertEqual(self.client.post(self.check_url,json=self.report).status_code,401)
        self.assertEqual(self.client.put(self.prefix+'/policy',json={'auto_update':True}).status_code,403)
        self.assertEqual(self.client.post(self.check_url,json={**self.report,'progress':101},headers=self.device_headers).status_code,422)
        self.store.check(self.device(), self.report)
        command=self.store.request(self.device(),'install')
        self.identities.unbind_device('deer-ABCDEF012345',self.alice.id)
        self.assertEqual(self.client.get(self.prefix).status_code,404)
        self.assertIsNone(self.store.check(self.device(),self.report)['command'])
        self.assertEqual(self.client.get('/api/firmware/devices/deer-ABCDEF012345/download/'+command['id'],headers=self.device_headers).status_code,404)
        self.identities.revoke_device('deer-ABCDEF012345')
        self.assertEqual(self.client.post(self.check_url,json=self.report,headers=self.device_headers).status_code,401)

    def test_rollback_requires_verified_previous_image(self):
        self.store.check(self.device(), self.report)
        with self.assertRaises(ValueError): self.store.request(self.device(),'rollback')
        self.store.check(self.device(),{**self.report,'rollback_available':True,'previous_version':'0.2.9'})
        command=self.store.request(self.device(),'rollback')
        self.assertEqual(self.store.check(self.device(),self.report)['command'],{'id':command['id'],'action':'rollback'})

    def test_completed_version_must_match_signed_target_and_idle_does_not_cancel_job(self):
        self.store.check(self.device(),self.report)
        command=self.store.request(self.device(),'install')
        report={**self.report,'command_id':command['id'],'state':'updated','progress':100}
        with self.assertRaises(ValueError): self.store.check(self.device(),report)
        self.store.check(self.device(),{**report,'state':'idle','progress':0})
        self.assertEqual(self.store.view(self.device())['command']['state'],'queued')
        self.store.check(self.device(),{**report,'version':'0.3.0-ota.2','sequence':2})
        self.assertEqual(self.store.view(self.device())['command']['state'],'updated')

    def test_manual_rollback_excludes_new_version_only_from_automatic_reinstall(self):
        self.store.check(self.device(),{**self.report,'version':'0.3.0-ota.2','sequence':2,'rollback_available':True,'previous_version':'0.3.0-ota.1'})
        self.store.set_policy(self.device(),True)
        command=self.store.request(self.device(),'rollback')
        result=self.store.check(self.device(),{**self.report,'command_id':command['id'],'state':'rolled_back','progress':100,'idle_seconds':600})
        self.assertIsNone(result['command'])
        self.assertTrue(self.store.view(self.device())['automatic_blocked'])
        self.assertIsNotNone(self.store.request(self.device(),'install'))

    def test_download_stops_when_credential_is_revoked_between_chunks(self):
        self.store.check(self.device(),self.report)
        command=self.store.request(self.device(),'install')
        request=Request({'type':'http','method':'GET','path':'/','headers':[(b'authorization',('Bearer '+self.token).encode())],
                         'path_params':{'device_id':'deer-ABCDEF012345'},'app':self.app})
        async def exercise():
            response=await download(command['id'],request,self.device())
            iterator=response.body_iterator
            self.assertTrue(await anext(iterator))
            self.identities.revoke_device('deer-ABCDEF012345')
            with self.assertRaises(StopAsyncIteration): await anext(iterator)
        asyncio.run(exercise())

    def test_publish_is_atomic_idempotent_and_never_copies_extra_files(self):
        from tools.firmware_publish import publish
        source=self.store.release_root/self.manifest['id']
        (source/'private.txt').write_text('should-not-be-copied')
        target=self.root/'published'
        self.assertFalse(publish(source,target,self.store.public_key_path)['already_published'])
        self.assertEqual({p.name for p in (target/self.manifest['id']).iterdir()},{'manifest.json','application.bin'})
        self.assertTrue(publish(source,target,self.store.public_key_path)['already_published'])


if __name__ == '__main__':
    unittest.main()
