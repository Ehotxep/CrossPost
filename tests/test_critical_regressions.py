"""Run: python -m unittest discover -s tests -p 'test_*.py' -v"""
import os
from contextlib import closing
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class CriticalRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, CROSSPOST_DATA=self.tmp.name)
        self.env.start()
        from app import db
        self.db = db
        db.reset_for_tests()

    def tearDown(self):
        self.db.reset_for_tests()
        self.env.stop()
        self.tmp.cleanup()

    def test_sqlite_lock_preserves_healthy_database_and_can_retry(self):
        path = Path(self.tmp.name) / 'posts.db'
        # sqlite3.Connection context commits/rolls back but does not close the file.
        with closing(sqlite3.connect(path)) as seed:
            seed.executescript(self.db.SCHEMA)
            seed.execute('CREATE TABLE marker (value TEXT)')
            seed.execute("INSERT INTO marker VALUES ('keep me')")
            seed.commit()
        # Old valid schema needs a migration, which competes with a second writer.
        holder = sqlite3.connect(path)
        holder.execute('PRAGMA journal_mode=WAL')
        holder.execute('BEGIN IMMEDIATE')
        connect = sqlite3.connect

        def short_connect(*args, **kwargs):
            kwargs['timeout'] = 0.05
            return connect(*args, **kwargs)

        try:
            with patch('app.db.sqlite3.connect', side_effect=short_connect):
                with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                    self.db.conn()
            self.assertIsNone(self.db._conn)
            self.assertFalse(list(Path(self.tmp.name).glob('posts.db*.corrupt-*')))
        finally:
            holder.rollback()
            holder.close()
        self.assertEqual(self.db.query('SELECT value FROM marker'), [{'value': 'keep me'}])
        self.assertEqual(self.db.conn().execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_pipeline_pause_blocks_near_and_pinned_posts_but_not_manual_schedule(self):
        from app.publish import pipeline, schedule, runner
        # Import schedule inside the isolated data directory: it creates its table on import.
        now = schedule._now()
        with self.db.tx() as c:
            for title, minutes, queue, pinned in (
                ('near', 5, 1, 0), ('far', 60, 1, 0),
                ('pinned', 5, 1, 1), ('manual', 5, 0, 0),
            ):
                c.execute("INSERT INTO scheduled(run_at,created,draft,state,queue,pinned,title) VALUES (?,?,?,'pending',?,?,?)",
                          ((now + timedelta(minutes=minutes)).isoformat(), now.isoformat(), '{}', queue, pinned, title))
        with patch.object(pipeline, '_last', {'t': 0, 'sig': None}):
            pipeline.set_cfg({'enabled': False})
            states = {r['title']: r['state'] for r in self.db.query('SELECT title,state FROM scheduled')}
            self.assertEqual(states, {'near': 'queued', 'far': 'queued', 'pinned': 'pending', 'manual': 'pending'})
            jobs = Mock()
            jobs.get.return_value = {'state': 'done'}
            api = SimpleNamespace(jobs=jobs, _tg_ctx=Mock())
            with patch.object(schedule, '_now', return_value=now + timedelta(minutes=6)), \
                 patch.object(runner, 'plan', return_value={'errors': []}), \
                 patch.object(runner, 'state', return_value={'dests': [{'state': 'done'}]}), \
                 patch.object(schedule.time, 'sleep'):
                schedule.tick(api)
                # Only the independently scheduled manual post may reach the sender.
                self.assertEqual(jobs.start.call_count, 1)
                rows = {r['title']: r for r in self.db.query('SELECT title,state,run_at FROM scheduled')}
                self.assertEqual(rows['manual']['state'], 'done')
                self.assertEqual(rows['pinned']['state'], 'pending')
                self.assertEqual(rows['pinned']['run_at'], (now + timedelta(minutes=5)).isoformat())
                # Resume preserves pinned time and allows it to run.
                from app.publish import settings
                settings.update('pipeline', {'enabled': True})
                schedule.tick(api)
                self.assertEqual(jobs.start.call_count, 2)
                self.assertEqual(self.db.query("SELECT state FROM scheduled WHERE title='pinned'")[0]['state'], 'done')


if __name__ == '__main__':
    unittest.main()
