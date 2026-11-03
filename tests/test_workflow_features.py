import copy
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from PIL import Image


class WorkflowFeatures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, CROSSPOST_DATA=self.tmp.name)
        self.env.start()
        from app import db, backups
        from app.publish import runner, schedule, settings, templates, review, journal
        self.db, self.backups, self.settings = db, backups, settings
        self.runner, self.templates, self.review, self.journal = runner, templates, review, journal
        db.reset_for_tests()
        importlib.reload(schedule)
        importlib.reload(runner)
        self.state_patch = patch.object(runner, '_state', {})
        self.state_patch.start()

    def tearDown(self):
        self.state_patch.stop()
        self.db.reset_for_tests()
        self.env.stop()
        self.tmp.cleanup()

    def image(self, uid='0123456789abcdef'):
        p = self.root / 'uploads' / uid / 'art.png'
        p.parent.mkdir(parents=True)
        Image.new('RGB', (100, 80), 'blue').save(p)
        return uid

    def discord(self):
        self.settings.update('discord', {'enabled': True, 'webhooks': [{'name': 'Test', 'url': 'https://example.invalid/webhook'}]})
        return {'files': [self.image()], 'title': 'Title', 'text': 'Body', 'rating': 'sfw', 'hashtags': []}

    def test_journal_survives_restart_masks_credentials_and_ignores_progress_noise(self):
        self.runner._state.update(pub_id=7, dests=[{'key': 'discord:0', 'state': 'queued'}])
        self.runner._set('discord:0', state='running', message='Uploading')
        self.runner._set('discord:0', progress=0.4)
        self.runner._set('discord:0', message='Uploading')
        self.runner._set('discord:0', state='error', message='api_key=abcdefghijklmnop')
        self.db.reset_for_tests()
        events = self.journal.read(7)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['message'], 'Uploading')
        self.assertNotIn('abcdefghijklmnop', json.dumps(events))

    def test_templates_preserve_content_and_presentation_but_not_credentials_or_files(self):
        draft = self.discord()
        self.settings.update('telegram', {'bot_token': 'private-bot-token'})
        self.settings.update('light', {'quality': 73})
        item = self.templates.save('Artwork', draft)
        saved = self.templates.get(item['id'])
        self.assertNotIn('files', saved['draft'])
        self.assertNotIn('private-bot-token', (self.root / 'templates.json').read_text())
        self.assertNotIn('webhook', json.dumps(saved))
        self.settings.update('light', {'quality': 95})
        current = self.settings.load()
        effective = self.templates.effective(current, saved['draft'])
        self.assertEqual(effective['light']['quality'], 73)
        self.assertEqual(current['light']['quality'], 95)
        self.assertEqual(effective['telegram']['bot_token'], 'private-bot-token')
        self.assertFalse(effective['telegram']['enabled'])
        self.templates.remove(item['id'])
        self.assertEqual(self.templates.items(), [])

    def test_composition_uses_sender_text_signature_and_selected_files(self):
        draft = self.discord()
        draft['multi'] = True
        draft['marks'] = {draft['files'][0]: 'ru'}
        other = self.image('fedcba9876543210')
        draft['files'].append(other)
        draft['marks'][other] = 'en'
        self.settings.update('modules', {'targeting': True})
        self.settings.update('versions', {'discord': ['ru']})
        self.settings.update('signatures', {'platforms': {'discord': {'sfw': 'Signature'}}})
        comp = self.review.composition(draft)['platforms'][0]
        self.assertEqual(comp['text'], '**Title**\nBody\n\nSignature')
        self.assertEqual([f['id'] for f in comp['files']], ['0123456789abcdef'])
        self.assertIn('JPEG', comp['files'][0]['transform'])

    def test_template_channel_exclusions_follow_channel_ids_after_reordering(self):
        channels = [{'id': '@first', 'rating': 'sfw'}, {'id': '@second', 'rating': 'sfw'}]
        self.settings.update('telegram', {'channels': channels, 'enabled': True})
        item = self.templates.save('Channel choice', {'exclude': ['telegram:0']})
        self.settings.update('telegram', {'channels': list(reversed(channels))})
        self.assertEqual(self.templates.get(item['id'])['draft']['exclude'], ['telegram:1'])

    def test_readiness_never_sends_and_distinguishes_unverified_access(self):
        draft = self.discord()
        local = self.review.readiness(draft)
        self.assertTrue(local['ready'])
        self.assertFalse(local['access_verified'])
        with patch.object(self.review.requests, 'get', return_value=Mock(status_code=200)) as get, \
             patch.object(self.runner.D, 'post') as send:
            remote = self.review.readiness(draft, remote=True)
        self.assertTrue(remote['access_verified'])
        get.assert_called_once()
        send.assert_not_called()
        draft['files'] = ['missing']
        self.assertFalse(self.review.readiness(draft)['ready'])

    def seed_backup(self):
        draft = self.discord()
        self.settings.update('telegram', {'bot_token': 'private-bot-token'})
        self.settings.update('translate', {'api_key': 'private-translation-key'})
        self.settings.update('pipeline', {'enabled': True})
        (self.root / 'tokens.json').write_text('{"secret":"private-session"}')
        (self.root / 'telegram.session').write_bytes(b'private-login')
        with self.db.tx() as c:
            c.execute('CREATE TABLE marker (value TEXT)')
            c.execute("INSERT INTO marker VALUES ('before')")
            c.execute("INSERT INTO scheduled(run_at,created,draft,state) VALUES ('2099-01-01','2026-01-01',?,'pending')", (json.dumps(draft),))
        return draft

    def test_backup_restore_roundtrip_preserves_current_credentials_and_pauses_queue(self):
        draft = self.seed_backup()
        bid = self.backups.create(include_originals=True)
        path = self.backups.archive_path(bid)
        with zipfile.ZipFile(path) as z:
            self.assertNotIn('tokens.json', z.namelist())
            self.assertNotIn('telegram.session', z.namelist())
            config = z.read('publish.json').decode()
            self.assertNotIn('private-bot-token', config)
            self.assertNotIn('private-translation-key', config)
            self.assertNotIn('example.invalid/webhook', config)
            self.assertIn('uploads/' + draft['files'][0] + '/art.png', z.namelist())
        with self.db.tx() as c:
            c.execute("UPDATE marker SET value='after'")
        self.settings.update('telegram', {'bot_token': 'current-token'})
        with patch('app.worker.status', return_value={'running': False}):
            result = self.backups.stage_restore(bid)
        self.assertTrue(result['restart'])
        self.assertTrue(self.backups.archive_path(result['rollback_id']).exists())
        self.db.reset_for_tests()
        self.assertTrue(self.backups.apply_pending())
        self.assertEqual(self.db.query('SELECT value FROM marker'), [{'value': 'before'}])
        self.assertEqual(self.db.query('SELECT state FROM scheduled')[0]['state'], 'missed')
        cfg = self.settings.load(raw=True)
        self.assertEqual(cfg['telegram']['bot_token'], 'current-token')
        self.assertFalse(cfg['pipeline']['enabled'])
        self.assertFalse(self.backups.pending())
        self.assertTrue((self.root / 'tokens.json').exists())

    def test_backup_without_originals_and_tampered_archive(self):
        self.seed_backup()
        bid = self.backups.create()
        with zipfile.ZipFile(self.backups.archive_path(bid)) as original:
            self.assertFalse(any(n.startswith('uploads/') for n in original.namelist()))
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as bad:
                for name in original.namelist():
                    bad.writestr(name, b'changed' if name == 'posts.db' else original.read(name))
        data.seek(0)
        with self.assertRaisesRegex(ValueError, 'сумма'):
            self.backups.receive(data, len(data.getvalue()))
        self.assertEqual(len(self.backups.items()), 1)

    def test_restore_rejects_path_traversal_and_rolls_back_failed_file_replacement(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as bad:
            bad.writestr('posts.db', b'db')
            bad.writestr('../escaped', b'bad')
            bad.writestr('manifest.json', json.dumps({'version': 1, 'files': {
                '../escaped': hashlib.sha256(b'bad').hexdigest(),
                'posts.db': hashlib.sha256(b'db').hexdigest()}}))
        data.seek(0)
        with self.assertRaisesRegex(ValueError, 'путь'):
            self.backups.receive(data, len(data.getvalue()))
        self.assertFalse((self.root.parent / 'escaped').exists())
        self.seed_backup()
        bid = self.backups.create()
        with self.db.tx() as c:
            c.execute("UPDATE marker SET value='current'")
        with patch('app.worker.status', return_value={'running': False}):
            self.backups.stage_restore(bid)
        self.db.reset_for_tests()
        replace = Path.replace
        failed = []
        def fail_once(path, target):
            if path.name == 'ui.json' and path.parent.name == 'new' and not failed:
                failed.append(True)
                raise OSError('simulated disk failure')
            return replace(path, target)
        with patch.object(Path, 'replace', new=fail_once):
            self.assertFalse(self.backups.apply_pending())
        self.assertIn('disk failure', self.backups.restore_error())
        self.assertEqual(self.db.query('SELECT value FROM marker'), [{'value': 'current'}])
        self.assertTrue(self.backups.pending())
        self.backups.cancel_restore()
        with patch('app.worker.status', return_value={'running': False}):
            self.backups.stage_restore(bid)
        self.db.reset_for_tests()
        self.assertTrue(self.backups.apply_pending())
        self.assertEqual(self.db.query('SELECT value FROM marker'), [{'value': 'before'}])

    def test_import_rejects_invalid_metadata_without_polluting_backup_list(self):
        self.seed_backup()
        bid = self.backups.create()
        payload = io.BytesIO()
        with zipfile.ZipFile(self.backups.archive_path(bid)) as original, zipfile.ZipFile(payload, 'w') as changed:
            for name in original.namelist():
                content = original.read(name)
                if name == 'manifest.json':
                    manifest = json.loads(content)
                    manifest['created'] = 42
                    content = json.dumps(manifest).encode()
                changed.writestr(name, content)
        payload.seek(0)
        with self.assertRaises(ValueError):
            self.backups.receive(payload, len(payload.getvalue()))
        self.assertEqual(len(self.backups.items()), 1)
