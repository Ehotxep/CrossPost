import importlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image


class EditorExtensions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, CROSSPOST_DATA=self.tmp.name)
        self.env.start()
        from app import db
        from app.publish import runner, schedule, drafts, duplicates, settings, pipeline
        self.db, self.runner, self.schedule = db, runner, schedule
        self.drafts, self.duplicates, self.settings, self.pipeline = drafts, duplicates, settings, pipeline
        db.reset_for_tests()
        importlib.reload(schedule)
        importlib.reload(runner)
        self.state = patch.object(runner, '_state', {})
        self.state.start()
        self.settings.update('discord', {'enabled': True, 'webhooks': [{'url': 'https://example.invalid/hook', 'name': 'Test'}]})

    def tearDown(self):
        self.state.stop()
        self.db.reset_for_tests()
        self.env.stop()
        self.tmp.cleanup()

    def image(self, uid='0123456789abcdef'):
        folder = self.root / 'uploads' / uid
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'work.png'
        image = Image.new('RGB', (90, 80))
        image.putdata([(x * 2, y * 3, (x + y) % 256) for y in range(80) for x in range(90)])
        image.save(path)
        return uid

    def draft(self):
        return dict(files=[self.image()], title='Закат', text='Описание', rating='sfw', signature=False, hashtags=[])

    def test_named_drafts_keep_files_and_detect_concurrent_saves(self):
        draft = self.draft()
        meta = self.drafts.save('Работа', draft)
        self.db.reset_for_tests()
        self.assertEqual(self.drafts.items('работ')[0]['id'], meta['id'])
        saved = self.drafts.get(meta['id'])
        self.assertEqual(saved['draft']['files'][0]['id'], draft['files'][0])
        next_meta = self.drafts.save('Готовая работа', dict(draft, text='Новое описание'), 'ready', meta['id'], meta['revision'])
        with self.assertRaisesRegex(ValueError, 'другом окне'):
            self.drafts.save('Старое окно', draft, 'working', meta['id'], meta['revision'])
        self.assertEqual(self.drafts.get(meta['id'])['draft']['text'], 'Новое описание')
        self.assertIn(draft['files'][0], self.drafts.protected_ids())
        self.drafts.remove(meta['id'], next_meta['revision'])
        self.assertEqual(self.drafts.items(), [])

    def test_cleanup_preserves_named_and_current_ui_drafts(self):
        from app import ui_state
        from app.publish import storage
        draft = self.draft()
        self.drafts.save('Keep', draft)
        second = self.image('fedcba9876543210')
        ui_state.update({'draft': {'files': [{'id': second}]}})
        for uid in (draft['files'][0], second):
            old = time.time() - 40 * 86400
            os.utime(self.root / 'uploads' / uid, (old, old))
        storage.cleanup(everything_published=True)
        self.assertTrue((self.root / 'uploads' / draft['files'][0]).exists())
        self.assertTrue((self.root / 'uploads' / second).exists())
        self.assertTrue({draft['files'][0], second} <= storage.used_ids())

    def test_missing_file_reference_is_visible_until_user_removes_it(self):
        meta = self.drafts.save('Missing', dict(files=['0' * 16], text='Keep this text'))
        loaded = self.drafts.get(meta['id'])
        self.assertEqual(loaded['missing'], ['0' * 16])
        self.assertTrue(loaded['draft']['files'][0]['missing'])
        self.assertEqual(loaded['draft']['text'], 'Keep this text')

    def test_scheduled_edit_pauses_sender_preserves_id_position_and_uses_revision(self):
        draft = self.draft()
        sid = self.schedule.add(draft, (self.schedule._now() + timedelta(hours=1)).isoformat())['id']
        with self.db.tx() as c:
            c.execute('UPDATE scheduled SET queue=1,qpos=7 WHERE id=?', (sid,))
        edit = self.schedule.open_edit(sid)
        self.assertEqual(self.db.query('SELECT state FROM scheduled')[0]['state'], 'editing')
        api = SimpleNamespace(jobs=Mock(), _tg_ctx=Mock())
        api.jobs.get.return_value = None
        with patch.object(self.pipeline, 'maintain'), patch.object(self.schedule, '_now', return_value=self.schedule._now() + timedelta(hours=2)):
            self.schedule.tick(api)
        api.jobs.start.assert_not_called()
        with self.assertRaises(ValueError):
            self.schedule.publish_now(sid)
        with self.assertRaises(ValueError):
            self.pipeline.pin(sid, None)
        self.schedule.save_edit(sid, edit['token'], edit['revision'], dict(draft, text='Edited'))
        row = self.db.query('SELECT * FROM scheduled')[0]
        self.assertEqual((row['id'], row['qpos'], row['queue']), (sid, 7, 1))
        self.assertEqual(json.loads(row['draft'])['text'], 'Edited')
        self.assertEqual(row['state'], 'queued')
        with self.assertRaises(ValueError):
            self.schedule.save_edit(sid, edit['token'], edit['revision'], draft)

    def test_cancel_edit_does_not_immediately_send_an_overdue_post(self):
        sid = self.schedule.add(self.draft(), (self.schedule._now() + timedelta(hours=1)).isoformat())['id']
        edit = self.schedule.open_edit(sid)
        with patch.object(self.schedule, '_now', return_value=self.schedule._now() + timedelta(hours=2)):
            self.schedule.cancel_edit(sid, edit['token'], edit['revision'])
        self.assertEqual(self.db.query('SELECT state FROM scheduled')[0]['state'], 'missed')

    def test_planner_cannot_reactivate_an_edit_claimed_after_its_snapshot(self):
        draft = self.draft()
        sid = self.schedule.add(draft, (self.schedule._now() + timedelta(hours=2)).isoformat())['id']
        with self.db.tx() as c:
            c.execute('UPDATE scheduled SET queue=1,qpos=1 WHERE id=?', (sid,))
        original = self.pipeline.plan_slots
        def edit_during_planning(*args, **kwargs):
            self.schedule.open_edit(sid)
            return original(*args, **kwargs)
        cfg = dict(enabled=True, per_day=2, min_gap_h=4, explore=.2, horizon_days=14)
        with patch.object(self.pipeline, 'cfg', return_value=cfg), patch.object(self.pipeline, 'grid', return_value={}), patch.object(self.pipeline, 'plan_slots', side_effect=edit_during_planning):
            self.pipeline.maintain(force=True)
        self.assertEqual(self.db.query('SELECT state FROM scheduled')[0]['state'], 'editing')

    def test_platform_text_is_independent_and_used_in_composition(self):
        from app.publish import review
        self.settings.update('platform_texts_enabled', {'discord': True})
        self.settings.update('modules', {'translation': True})
        draft = self.draft()
        draft['variants'] = {'en': {'title': 'English', 'text': 'Shared English'}}
        draft['platform_texts'] = {'discord': {'enabled': True, 'title': '', 'text': 'Discord only'}}
        cfg = self.settings.load()
        cfg['text_lang']['discord'] = 'en'
        cfg['text_lang']['bluesky'] = 'en'
        self.assertEqual(self.runner.variant(cfg, draft, 'discord')[:2], ('', 'Discord only'))
        self.assertEqual(self.runner.variant(cfg, draft, 'bluesky')[:2], ('English', 'Shared English'))
        comp = review.composition(draft)['platforms'][0]
        self.assertEqual(comp['text'], 'Discord only')

    def test_platform_text_setting_defaults_off_and_preserves_saved_override(self):
        from app.publish import review
        draft = self.draft()
        draft['platform_texts'] = {'discord': {'enabled': True, 'title': 'Custom', 'text': 'Custom body'}}
        meta = self.drafts.save('Custom draft', draft)
        self.assertFalse(self.settings.load()['platform_texts_enabled']['discord'])
        self.assertEqual(self.runner.variant(self.settings.load(), draft, 'discord')[:2], ('Закат', 'Описание'))
        self.settings.update('platform_texts_enabled', {'discord': True})
        self.assertEqual(review.composition(draft)['platforms'][0]['text'], '**Custom**\nCustom body')
        self.settings.update('platform_texts_enabled', {'discord': False})
        self.assertEqual(review.composition(draft)['platforms'][0]['text'], '**Закат**\nОписание')
        self.assertEqual(self.drafts.get(meta['id'])['draft']['platform_texts'], draft['platform_texts'])
        cfg = self.settings.load()
        cfg['modules']['translation'] = True
        cfg['text_lang']['discord'] = 'en'
        draft['variants'] = {'en': {'title': 'English', 'text': 'English body'}}
        self.assertEqual(self.runner.variant(cfg, draft, 'discord')[:2], ('English', 'English body'))
        self.settings.update('platform_texts_enabled', {'discord': True})
        self.assertEqual(self.runner.variant(self.settings.load(), draft, 'discord')[:2], ('Custom', 'Custom body'))

    def test_bluesky_uses_distinct_alt_descriptions_in_image_order(self):
        from platforms import bluesky
        response = Mock(status_code=200)
        response.json.return_value = {'uri': 'at://did:plc:test/app.bsky.feed.post/abc'}
        image = SimpleNamespace(data=b'image', mime='image/png', width=100, height=80)
        with patch.object(bluesky, 'session', return_value={'accessJwt': 'test', 'did': 'did:plc:test', 'handle': 'test'}), \
             patch.object(bluesky, 'upload_blob', return_value={'ref': 'test'}), \
             patch('core.retry.post', return_value=response) as post:
            bluesky.post({}, 'body', 'sfw', images=[image, image], alt='fallback', alts=['First picture', 'Second picture'])
        images = post.call_args.kwargs['json']['record']['embed']['images']
        self.assertEqual([i['alt'] for i in images], ['First picture', 'Second picture'])

    def test_search_covers_old_posts_unicode_filters_and_pagination(self):
        with self.db.tx() as c:
            for i in range(65):
                c.execute('INSERT INTO publications(created,title,text,files,dests,draft) VALUES (?,?,?,?,?,?)',
                          ('2026-06-01T12:00:00+00:00', 'ЗАКАТ' if i == 0 else f'Post {i}', '', '[]',
                           json.dumps([{'key': 'discord:0', 'platform': 'discord', 'state': 'error' if i == 0 else 'done', 'outcome': 'unknown'}]),
                           json.dumps({'hashtags': ['fox']})))
        self.assertEqual(self.runner.search_history()['total'], 65)
        self.assertEqual(len(self.runner.search_history()['items']), 30)
        self.assertEqual(len(self.runner.search_history(offset=60)['items']), 5)
        self.assertEqual(self.runner.search_history(q='закат', platform='discord', state='unknown')['total'], 1)
        self.assertEqual(self.runner.search_history(q='#fox', date_from='2026-06-01', date_to='2026-06-01')['total'], 65)
        self.assertEqual(self.runner.search_history(date_to='2026-05-31')['total'], 0)

    def test_duplicate_warning_uses_history_and_requires_current_confirmation(self):
        draft = self.draft()
        uid = draft['files'][0]
        from app.publish import uploads
        path = uploads.path_of(uid)
        with self.db.tx() as c:
            pid = c.execute('INSERT INTO publications(created,title,files,dests,draft) VALUES (?,?,?,?,?)',
                            ('2026-01-01T00:00:00+00:00', 'Old work', json.dumps([{'id': uid, 'kind': 'image'}]),
                             json.dumps([{'key': 'discord:0', 'name': 'Discord', 'state': 'done', 'link': 'https://example.invalid/post'}]), '{}')).lastrowid
        result = self.duplicates.find(draft)
        self.assertGreater(result['total'], 0)
        self.assertEqual(result['matches'][0]['pub_id'], pid)
        with self.assertRaises(ValueError):
            self.duplicates.require_confirmation(draft, '')
        self.duplicates.require_confirmation(draft, result['token'])
        with self.assertRaises(ValueError):
            self.duplicates.require_confirmation(dict(draft, text='Changed'), result['token'])
        self.assertEqual(self.duplicates.find(dict(draft, extend=pid))['total'], 0)

    def test_backup_preserves_saved_drafts_and_clears_restored_edit_sessions(self):
        from app import backups, ui_state
        draft = self.draft()
        draft.update(platform_texts={'discord': {'enabled': True, 'text': 'Saved custom text'}},
                     alts={draft['files'][0]: 'An image description'})
        meta = self.drafts.save('Portable draft', draft)
        sid = self.schedule.add(draft, (self.schedule._now() + timedelta(hours=1)).isoformat())['id']
        edit = self.schedule.open_edit(sid)
        ui_state.update({'draft': dict(edit['draft'], editing_schedule=edit)})
        bid = backups.create(include_originals=True)
        with patch('app.worker.status', return_value={'running': False}):
            backups.stage_restore(bid)
        self.db.reset_for_tests()
        self.assertTrue(backups.apply_pending())
        row = self.db.query('SELECT state,edit_token FROM scheduled WHERE id=?', (sid,))[0]
        self.assertEqual(row, {'state': 'missed', 'edit_token': None})
        self.assertNotIn('editing_schedule', ui_state.load()['draft'])
        saved = self.drafts.get(meta['id'])['draft']
        self.assertEqual(saved['platform_texts']['discord']['text'], 'Saved custom text')
        self.assertEqual(saved['alts'][draft['files'][0]], 'An image description')

    def test_disabled_features_keep_document_but_are_not_applied(self):
        from app.publish import templates
        draft = self.draft()
        draft.update(variants={'en': {'title': 'English', 'text': 'Saved English'}},
                     multi=True, marks={draft['files'][0]: 'en'}, profile={'text_lang': {'discord': 'en'}})
        self.settings.update('modules', {'translation': False, 'targeting': False})
        meta = self.drafts.save('Complete document', draft)
        saved = self.drafts.get(meta['id'])['draft']
        self.assertEqual(saved['variants'], draft['variants'])
        self.assertTrue(saved['multi'])
        self.assertEqual(saved['marks'], draft['marks'])
        cfg = templates.effective(self.settings.load(), saved)
        self.assertEqual(self.runner.variant(cfg, saved, 'discord')[:2], ('Закат', 'Описание'))
        self.settings.update('modules', {'translation': True})
        cfg = templates.effective(self.settings.load(), saved)
        self.assertEqual(self.runner.variant(cfg, saved, 'discord')[:2], ('English', 'Saved English'))

    def test_basic_first_run_defaults_do_not_reset_existing_preferences(self):
        import copy
        self.settings._path().unlink()
        cfg = self.settings.load()
        self.assertTrue(cfg['modules']['crosspost'])
        self.assertFalse(cfg['modules']['stats'])
        self.assertFalse(cfg['modules']['tagger'])
        legacy = copy.deepcopy(self.settings.DEFAULTS)
        self.settings.save(legacy)
        self.assertTrue(self.settings.load()['modules']['stats'])
        self.assertTrue(self.settings.load()['modules']['tagger'])

    def test_paused_queue_addition_reports_that_it_will_not_send_yet(self):
        row = self.pipeline.add(self.draft())
        self.assertTrue(row['paused'])
        self.assertIsNone(row['run_at'])
        self.assertIn('приостановлена', row['notice'])
        self.assertEqual(self.db.query('SELECT state FROM scheduled WHERE id=?', (row['id'],))[0]['state'], 'queued')

    def test_scheduling_wakes_sender_without_waiting_for_interface_poll(self):
        from app.api import Api
        from app import worker
        api = Api.__new__(Api)
        with patch.object(api, '_tg_ctx'), \
             patch.dict(os.environ, {'CROSSPOST_NO_SCHEDULER': ''}), \
             patch.object(worker, 'ensure_running', return_value=True) as wake, \
             patch.object(worker, 'describe', return_value={'running': True, 'off': False, 'work': 'scheduled'}):
            result = api.post_schedule(self.draft(), (self.schedule._now()+timedelta(days=2)).isoformat())
        wake.assert_called_once()
        self.assertTrue(result['worker']['running'])
        self.assertEqual(self.db.query('SELECT state FROM scheduled WHERE id=?', (result['id'],))[0]['state'], 'pending')

    def test_disabled_scheduler_does_not_spawn_a_real_sender(self):
        from app.api import Api
        from app import worker
        api = Api.__new__(Api)
        with patch.dict(os.environ, {'CROSSPOST_NO_SCHEDULER': '1'}), \
             patch.object(worker, 'ensure_running') as wake, \
             patch.object(worker, 'describe', return_value={'running': False}):
            api.worker_status()
        wake.assert_not_called()
