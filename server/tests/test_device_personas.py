"""人格锁档不随升级/账号/显示名改变，未知设备不借用猫的人格。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from device_personas import CAT_PROMPT, DEER_PROMPT, PRESETS, PersonaStore, prompt_sha256


class PersonaStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='persona-lock-')
        self.directory = Path(self.temp.name) / 'personas'
        self.store = PersonaStore(self.directory, clock=lambda: 1794456000)

    def tearDown(self):
        self.temp.cleanup()

    def test_cat_and_original_deer_are_distinct_and_metadata_is_readonly(self):
        self.assertEqual(self.store.metadata('cat-first')['status'], 'not_initialized')
        self.assertFalse(self.directory.exists())
        cat = self.store.get_or_create('cat-first')
        deer = self.store.get_or_create('deer-first')
        self.assertTrue(cat.ready)
        self.assertTrue(deer.ready)
        self.assertEqual(cat.prompt, CAT_PROMPT)
        self.assertEqual(deer.prompt, DEER_PROMPT)
        self.assertEqual(deer.sha256, 'b8b45093e3950c7b5b93f89a5ef9376e54db52cf7ba27625bbfddb223c1281fd')
        self.assertNotEqual(cat.sha256, deer.sha256)
        metadata = self.store.metadata('deer-first')
        self.assertEqual(metadata['status'], 'locked')
        self.assertNotIn('prompt', metadata)
        self.assertNotIn(deer.prompt, repr(deer))
        self.assertEqual(self.store.get_or_create('test-cat-001').kind, 'cat')

    def test_upgraded_templates_do_not_overwrite_original_locked_snapshot(self):
        first = self.store.get_or_create('deer-stable')
        path = self.directory / 'deer-stable.json'
        original = path.read_bytes()
        with patch.dict(PRESETS, {'deer': {'name': '新版名字', 'version': 'changed', 'prompt': '新版提示词'}}):
            reloaded = PersonaStore(self.directory).get_or_create('deer-stable')
        self.assertEqual(reloaded, first)
        self.assertEqual(path.read_bytes(), original)

    def test_unknown_or_corrupt_archive_requires_review_without_replacement(self):
        self.assertFalse(self.store.get_or_create('unverified-one').ready)
        self.assertEqual(self.store.metadata('unverified-one')['status'], 'needs_review')
        self.assertFalse(self.directory.exists())
        self.store.get_or_create('cat-corrupt')
        path = self.directory / 'cat-corrupt.json'
        document = json.loads(path.read_text(encoding='utf-8'))
        document['prompt'] = '损坏内容'
        path.write_text(json.dumps(document), encoding='utf-8')
        damaged = path.read_bytes()
        self.assertFalse(self.store.get_or_create('cat-corrupt').ready)
        self.assertEqual(path.read_bytes(), damaged)
        with self.assertRaises(ValueError):
            self.store.export_private('cat-corrupt', Path(self.temp.name) / 'refused.json')

    def test_concurrent_initialization_publishes_one_complete_archive(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            personas = list(pool.map(lambda _: PersonaStore(self.directory).get_or_create('deer-concurrent'), range(4)))
        self.assertTrue(all(persona.ready for persona in personas))
        self.assertTrue(all(persona == personas[0] for persona in personas))
        self.assertEqual([path.name for path in self.directory.iterdir()], ['deer-concurrent.json'])

    def test_private_export_is_complete_exclusive_and_does_not_print_prompt(self):
        persona = self.store.get_or_create('deer-export')
        target = Path(self.temp.name) / 'deer-export.secret.json'
        output = io.StringIO()
        with redirect_stdout(output):
            metadata = self.store.export_private('deer-export', target)
        self.assertEqual(output.getvalue(), '')
        document = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(document['prompt'], persona.prompt)
        self.assertEqual(document['sha256'], prompt_sha256(document['prompt']))
        self.assertEqual(metadata, persona.metadata())
        with self.assertRaises(FileExistsError):
            self.store.export_private('deer-export', target)
        self.assertEqual(json.loads(target.read_text(encoding='utf-8')), document)


if __name__ == '__main__':
    unittest.main()
