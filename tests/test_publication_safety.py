import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch


class SettingsConcurrency(unittest.TestCase):
    def test_publish_settings_survive_parallel_process_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = '''
import sys, time
from app.publish import settings
original = settings.load
# Slow the read to expose overlapping read/modify/write operations.
def slow_load(*args, **kwargs):
    result = original(*args, **kwargs)
    time.sleep(0.01)
    return result
settings.load = slow_load
for i in range(12):
    settings.update('discord', {sys.argv[1] + str(i): True})
'''
            env = dict(os.environ, CROSSPOST_DATA=tmp)
            children = [subprocess.Popen([sys.executable, '-c', script, prefix], env=env,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                        for prefix in ('a', 'b', 'c')]
            try:
                for child in children:
                    out, err = child.communicate(timeout=30)
                    self.assertEqual(child.returncode, 0, out + err)
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                        child.communicate()
            cfg = json.loads((Path(tmp) / 'publish.json').read_text(encoding='utf-8'))
            for prefix in ('a', 'b', 'c'):
                for i in range(12):
                    self.assertTrue(cfg['discord'][prefix + str(i)])

    def test_general_settings_survive_parallel_thread_updates(self):
        from app import settings
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, CROSSPOST_DATA=tmp):
            original = settings.load
            errors = []

            def slow_load():
                result = original()
                time.sleep(0.005)
                return result

            def write(prefix):
                try:
                    for i in range(12):
                        settings.save({prefix + str(i): True})
                except Exception as e:
                    errors.append(e)

            with patch.object(settings, 'load', side_effect=slow_load):
                threads = [threading.Thread(target=write, args=(p,)) for p in ('a', 'b')]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=15)
                    self.assertFalse(t.is_alive())
            self.assertFalse(errors)
            cfg = settings.load()
            for prefix in ('a', 'b'):
                for i in range(12):
                    self.assertTrue(cfg[prefix + str(i)])


class PublicationSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, CROSSPOST_DATA=cls.tmp.name)
        cls.env.start()
        from app import db
        from app.publish import runner, schedule, pipeline
        cls.db, cls.runner, cls.schedule, cls.pipeline = db, runner, schedule, pipeline
        db.reset_for_tests()
        # Modules create tables at import. Recreate them in this class's isolated database.
        import importlib
        importlib.reload(schedule)
        importlib.reload(runner)

    @classmethod
    def tearDownClass(cls):
        cls.db.reset_for_tests()
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        with self.db.tx() as c:
            c.execute('DELETE FROM scheduled')
            c.execute('DELETE FROM publications')
        self.state_patch = patch.object(self.runner, '_state', {})
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)

    def test_ambiguous_creation_request_is_never_repeated_automatically(self):
        import requests
        from core import retry
        from core.common import PublicationUnknown
        for failure in (requests.ReadTimeout('response lost'), requests.ConnectionError('reset')):
            with self.subTest(failure=failure), patch.object(retry.requests, 'request', side_effect=failure) as request:
                with self.assertRaises(PublicationUnknown):
                    retry.post('https://example.invalid/create', idempotent=False)
                self.assertEqual(request.call_count, 1)
        response = Mock(status_code=503)
        with patch.object(retry.requests, 'request', return_value=response) as request:
            with self.assertRaises(PublicationUnknown):
                retry.post('https://example.invalid/create', idempotent=False)
            self.assertEqual(request.call_count, 1)

    def test_manual_retry_requires_confirmation_for_unknown_and_legacy_errors(self):
        dests = [dict(key=key, state='error', **extra) for key, extra in (
            ('discord:0', {'outcome': 'not_sent'}),
            ('discord:1', {'outcome': 'unknown'}),
            ('discord:2', {}),
        )]
        self.runner._state.update(dests=dests, draft={'rating': 'sfw'})
        with patch.object(self.runner, '_run') as run:
            self.runner.retry(Mock())
            self.assertEqual(run.call_args.kwargs['only'], {'discord:0'})
            self.runner.retry(Mock(), confirm_unknown=True)
            self.assertEqual(run.call_args.kwargs['only'], {'discord:1', 'discord:2'})

    def test_sender_persists_refusal_and_unknown_outcomes(self):
        import copy
        from core.common import NotPublishedError, PublicationUnknown
        cfg = copy.deepcopy(self.runner.S.DEFAULTS)
        cfg['discord']['webhooks'] = [{'url': 'https://example.invalid/webhook'}]
        draft = {'rating': 'sfw', 'text': 'test', 'signature': False}
        file = {'id': '0123456789abcdef', 'path': Path(self.tmp.name) / 'image.png',
                'name': 'image.png', 'kind': 'image'}
        for error, expected in ((NotPublishedError('HTTP 403'), 'not_sent'),
                                (PublicationUnknown('response lost'), 'unknown')):
            with self.subTest(expected=expected):
                self.runner._state.clear()
                self.runner._state.update(dests=[{'key': 'discord:0', 'platform': 'discord',
                                                 'name': 'Discord', 'state': 'queued'}], draft=draft)
                with patch.object(self.runner.S, 'load', return_value=cfg), \
                     patch.object(self.runner, '_files', return_value=[file]), \
                     patch.object(self.runner.prep, 'get_light', return_value=Mock()), \
                     patch.object(self.runner.D, 'post', side_effect=error), \
                     patch.object(self.runner, '_save_pub') as save:
                    self.runner._run(draft, Mock(), only=None)
                dest = self.runner.state()['dests'][0]
                self.assertEqual(dest['state'], 'error')
                self.assertEqual(dest['outcome'], expected)
                self.assertEqual(self.runner.needs_verification(dest), expected == 'unknown')
                self.assertTrue(save.called)

    def test_telegram_media_rejection_after_text_is_not_a_total_refusal(self):
        from core.common import NotPublishedError, PublicationUnknown
        bot = self.runner.tg_bot
        media = {'kind': 'images', 'photos': []}
        with patch.object(bot, 'call') as send_text, \
             patch.object(bot.T, '_send_photos', side_effect=NotPublishedError('photo rejected')):
            with self.assertRaises(PublicationUnknown):
                bot.send_to_channel('unused', {}, {'id': '@mychannel'}, media,
                                    'x' * 1100, '', False, Mock())
            self.assertEqual(send_text.call_count, 1)
            with self.assertRaises(NotPublishedError):
                bot.send_to_channel('unused', {}, {'id': '@mychannel'}, media,
                                    'short', '', False, Mock())
            self.assertEqual(send_text.call_count, 1)

    def test_auto_retry_requires_confirmed_total_refusal_and_reuses_publication(self):
        now = self.schedule._now().isoformat()
        variants = {
            'refused': [{'state': 'error', 'outcome': 'not_sent'}],
            'unknown': [{'state': 'error', 'outcome': 'unknown'}],
            'legacy': [{'state': 'error'}],
            'partial': [{'state': 'done'}, {'state': 'error', 'outcome': 'not_sent'}],
            'empty': [],
        }
        with self.db.tx() as c:
            for title, dests in variants.items():
                pub = c.execute('INSERT INTO publications(created,dests,draft) VALUES (?,?,?)',
                                (now, json.dumps(dests), '{}')).lastrowid
                c.execute("INSERT INTO scheduled(run_at,created,title,draft,state,queue,pub_id,done_at) VALUES (?,?,?,?,'failed',1,?,?)",
                          (now, now, title, '{}', pub, now))
            for title, safe in (('crashed_without_id', 0), ('preflight', 1)):
                c.execute("INSERT INTO scheduled(run_at,created,title,draft,state,queue,done_at,retry_safe) VALUES (?,?,?,?,'failed',1,?,?)",
                          (now, now, title, '{}', now, safe))
        cfg = {'enabled': True, 'per_day': 2, 'min_gap_h': 4, 'explore': 0.2, 'horizon_days': 14}
        with patch.object(self.pipeline, 'cfg', return_value=cfg):
            self.pipeline.maintain(force=True)
        rows = {r['title']: r for r in self.db.query('SELECT * FROM scheduled')}
        self.assertEqual(rows['refused']['state'], 'pending')
        self.assertEqual(rows['refused']['tries'], 1)
        self.assertEqual(json.loads(rows['refused']['draft'])['extend'], rows['refused']['pub_id'])
        self.assertEqual(rows['preflight']['state'], 'pending')
        for title in ('unknown', 'legacy', 'partial', 'empty', 'crashed_without_id'):
            self.assertEqual(rows[title]['state'], 'failed')
            self.assertEqual(rows[title]['tries'], 0)

    def test_publication_is_linked_to_schedule_before_sending(self):
        now = self.schedule._now().isoformat()
        with self.db.tx() as c:
            sid = c.execute("INSERT INTO scheduled(run_at,created,draft,state,queue) VALUES (?,?,?,'running',1)",
                            (now, now, '{}')).lastrowid
        draft = {'rating': 'sfw', '_schedule_id': sid}
        plan = {'errors': [], 'kind': 'images', 'dests': [{'key': 'discord:0', 'will': True}]}
        with patch.object(self.runner, 'plan', return_value=plan), \
             patch.object(self.runner, '_files', return_value=[]), \
             patch('app.publish.storage.make_pub_thumb'), \
             patch('app.insights.forecast.for_draft', return_value={'items': []}), \
             patch('app.insights.post_stats.save_forecasts'), \
             patch.object(self.runner, '_run', side_effect=RuntimeError('process interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.runner.start(draft, Mock())
        row = self.db.query('SELECT pub_id FROM scheduled WHERE id=?', (sid,))[0]
        self.assertIsNotNone(row['pub_id'])
        self.assertEqual(row['pub_id'], self.runner.state()['pub_id'])

    def test_historical_unknown_result_can_be_retried_only_after_confirmation(self):
        draft = {'rating': 'sfw', 'files': []}
        with self.db.tx() as c:
            pub_id = c.execute('INSERT INTO publications(created,files,dests,draft) VALUES (?,?,?,?)',
                               (self.schedule._now().isoformat(), '[]',
                                json.dumps([{'key': 'discord:0', 'state': 'error', 'outcome': 'unknown'}]),
                                json.dumps(draft))).lastrowid
        with patch.object(self.runner, '_run') as run:
            from core.common import PlatformError
            with self.assertRaises(PlatformError):
                self.runner.retry(Mock(), pub_id=pub_id)
            run.assert_not_called()
            self.runner.retry(Mock(), pub_id=pub_id, confirm_unknown=True)
            self.assertEqual(run.call_args.kwargs['only'], {'discord:0'})
            self.assertEqual(self.runner.state()['pub_id'], pub_id)
