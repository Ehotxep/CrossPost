"""Independent destinations and migration from the former single-site selector."""
import copy
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.publish import settings as S, booru


class BooruSplit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, CROSSPOST_DATA=self.tmp.name)
        env.start()
        self.addCleanup(env.stop)
        from app import db
        db.reset_for_tests()
        self.addCleanup(db.reset_for_tests)

    def test_existing_selected_site_and_all_keys_are_preserved_without_enabling_other_sites(self):
        old = {'e6ai': {'site':'floofgens', 'enabled':True, 'username':'blep-user', 'api_key':'retired-key',
                        'blep_api_key':'blep-key', 'preselect':.7, 'with_description':False,
                        'accounts':{'floofgens':{'username':'floof-user','api_key':'floof-key','constant_tags':['fluffy']},
                                    'e621':{'username':'e621-user','api_key':'e621-key','constant_tags':['drawn']}}},
               'modules':{'e6ai':True,'translation':True}, 'text_lang':{'e6ai':'both'}, 'versions':{'e6ai':['en']}}
        S._path().write_text(json.dumps(old), encoding='utf-8')
        cfg = S.load()
        self.assertTrue(cfg['floofgens']['enabled'])
        self.assertFalse(cfg['e6ai']['enabled'])
        self.assertFalse(cfg['e621']['enabled'])
        for site, secret in [('e6ai','blep-key'),('e621','e621-key'),('floofgens','floof-key')]:
            self.assertEqual(cfg[site]['api_key'], secret)
            self.assertNotIn(secret, json.dumps(S.public(cfg)))
        self.assertEqual(cfg['floofgens']['constant_tags'], ['fluffy'])
        self.assertEqual(cfg['text_lang']['floofgens'], 'both')
        self.assertEqual(cfg['versions']['floofgens'], ['en'])
        S.update('floofgens', {'constant_tags':['changed'], 'api_key':'new-floof-key'})
        self.assertEqual(S.load()['e621']['constant_tags'], ['drawn'])
        self.assertEqual(S.load()['e6ai']['api_key'], 'blep-key')
        self.assertEqual(S.load()['floofgens']['api_key'], 'new-floof-key')
        legacy = {'e6ai_tags':{'file':['legacy']}, 'e6ai_meta':{'file':{'nochar':True}}, 'exclude':['e6ai','vk']}
        self.assertEqual(booru.fields(cfg, legacy, 'floofgens')['tags']['file'], ['legacy'])
        self.assertEqual(booru.fields(cfg, legacy, 'e6ai')['tags'], {})
        self.assertEqual(booru.excludes(cfg, legacy), {'floofgens','vk'})

    def test_old_history_and_scheduled_profiles_keep_their_actual_destinations(self):
        from app.publish import templates
        rows = booru.destinations(json.dumps([
            {'key':'e6ai','platform':'e6ai','name':'User','link':'https://floofgens.net/posts/42','state':'done'},
            {'key':'e6ai','platform':'e6ai','name':'e621 · User','state':'error'},
            {'key':'e6ai','platform':'e6ai','name':'blep.art · e621Fan','state':'error'}]))
        self.assertEqual([r['key'] for r in rows], ['floofgens','e621','e6ai'])
        cfg = copy.deepcopy(S.DEFAULTS)
        cfg['legacy_booru_site'] = 'floofgens'
        cfg['floofgens']['enabled'] = True
        draft = {'profile':{'platforms':['e6ai'], 'e6ai':{'constant_tags':['legacy_tag']}, 'text_lang':{'e6ai':'en'}}}
        effective = templates.effective(cfg, draft)
        self.assertTrue(effective['floofgens']['enabled'])
        self.assertFalse(effective['e6ai']['enabled'])
        self.assertEqual(effective['floofgens']['constant_tags'], ['legacy_tag'])
        self.assertEqual(effective['text_lang']['floofgens'], 'en')

    def test_drafts_retain_independent_tag_sets_and_reject_unknown_targets(self):
        from app.publish import drafts
        raw = {'files':[], 'booru_posts':{'e621':{'tags':{'file':['drawn']},'meta':{}},
                                         'floofgens':{'tags':{'file':['fluffy']},'meta':{}}}}
        saved = drafts.content(raw)
        raw['booru_posts']['e621']['tags']['file'].append('changed')
        self.assertEqual(saved['booru_posts']['e621']['tags']['file'], ['drawn'])
        with self.assertRaises(ValueError):
            drafts.content({'booru_posts':{'unknown':{}}})

    def test_tag_databases_and_categories_do_not_leak_between_concurrent_sites(self):
        from app import e6tags
        import sqlite3
        from concurrent.futures import ThreadPoolExecutor
        for site, category in [('e6ai',1),('e621',5),('floofgens',4)]:
            path = Path(self.tmp.name) / f'{e6tags._prefix(site)}.db'
            with sqlite3.connect(path) as conn:
                conn.executescript('CREATE TABLE tags(name TEXT PRIMARY KEY,cat INTEGER,n INTEGER); CREATE TABLE aliases(a TEXT PRIMARY KEY,c TEXT);')
                conn.execute('INSERT INTO tags VALUES (?,?,1)', ('shared_tag',category))
        def read(site):
            try:
                with e6tags.for_site(site):
                    return [e6tags.info(['shared_tag'])['shared_tag']['cat'] for _ in range(30)]
            finally:
                conn = getattr(e6tags._local, 'c', None)
                if conn:
                    conn.close(); e6tags._local.c = None
        with ThreadPoolExecutor(max_workers=3) as pool:
            values = list(pool.map(read, booru.PLATFORMS))
        self.assertEqual(values, [['artist']*30,['species']*30,['character']*30])

    def test_moderation_learning_is_scoped_and_disabled_site_is_never_polled(self):
        from app import db, e6tags
        from app.insights import e6learn
        from datetime import datetime, timedelta, timezone
        importlib.reload(e6learn)
        S.update('modules', {'e6ai':False, 'floofgens':True})
        e6learn.record(1, 42, ['smile'], 'floofgens')
        with db.tx() as conn:
            conn.execute('UPDATE e6_sent SET at=?', ((datetime.now(timezone.utc)-timedelta(hours=100)).isoformat(),))
        with patch.object(e6learn, '_fetch', return_value={'general':['fluffy']}) as fetch, patch.object(e6tags,'ready',return_value=False):
            self.assertEqual(e6learn.check_due(), 1)
            fetch.assert_called_once_with(42, 'floofgens')
        with e6tags.for_site('floofgens'):
            self.assertEqual(e6learn.learned()['smile']['removed'], 1)
            self.assertEqual(e6learn.learned()['fluffy']['added'], 1)
        with e6tags.for_site('e621'):
            self.assertEqual(e6learn.learned(), {})
            e6learn.forget()
        with e6tags.for_site('floofgens'):
            self.assertIn('fluffy', e6learn.learned())
        S.update('modules', {'floofgens':False})
        with patch.object(e6learn, '_fetch') as fetch:
            self.assertEqual(e6learn.check_due(), 0)
            fetch.assert_not_called()

    def test_all_three_sites_can_publish_same_post_independently(self):
        from app import db
        from app.publish import runner
        importlib.reload(runner)
        cfg = copy.deepcopy(S.DEFAULTS)
        file = {'id':'0123456789abcdef','name':'drawing.png','kind':'image',
                'path':Path(self.tmp.name)/'drawing.png','size':8}
        file['path'].write_bytes(b'original')
        draft = {'files':[file['id']], 'title':'Drawing','text':'Description','rating':'sfw',
                 'hashtags':[], 'signature':False, 'exclude':[], 'booru_posts':{}}
        for site in booru.PLATFORMS:
            cfg['modules'][site] = True
            cfg[site].update(enabled=True, username=site+'-user', api_key=site+'-key', base=S.BOORU[site],
                             site_name=site, constant_tags=['constant_'+site], with_sources=False)
            draft['booru_posts'][site] = {'tags':{file['id']:['solo','female','anthro']},'meta':{}}
        with patch.object(runner.S,'load',return_value=cfg), patch.object(runner,'_files',return_value=[file]), \
             patch('app.e6tags.ready',return_value=False), \
             patch('app.publish.e6check.check',return_value={'total':4}), patch('app.publish.e6check.blockers',return_value=[]):
            plan = runner.plan(draft)
            dests = [d for d in plan['dests'] if d['platform'] in booru.PLATFORMS]
            self.assertEqual({d['key'] for d in dests if d['will']}, set(booru.PLATFORMS))
            draft['exclude'] = ['e621']
            toggled = runner.plan(draft)['dests']
            self.assertEqual({d['key'] for d in toggled if d['will']}, {'e6ai','floofgens'})
            draft['exclude'] = []
            runner._state = {'pub_id':1,'dests':[dict(d,state='queued') for d in dests], 'draft':draft}
            sent = []
            def upload(account, path, tags, *args):
                site = account['site_name']; sent.append((site,account['api_key'],tags))
                return {'post_id':42,'url':S.BOORU[site]+'/posts/42','duplicate':False}
            from app.insights import e6learn
            with patch.object(runner,'_save_pub'), patch.object(runner.E6,'upload',side_effect=upload), patch.object(e6learn,'record') as record:
                runner._run(draft, Mock(), only=None)
            self.assertEqual({d['state'] for d in runner.state()['dests']}, {'done'})
            self.assertEqual(len(sent), 3)
            for dest in runner.state()['dests']:
                self.assertEqual(len(dest['post_refs']),1)
                self.assertTrue(dest['post_refs'][0]['url'].startswith(S.BOORU[dest['key']]))
            for site, key, tags in sent:
                self.assertEqual(key, site+'-key')
                self.assertEqual([t for t in tags if t.startswith('constant_')], ['constant_'+site])
            self.assertEqual({c.args[-1] for c in record.call_args_list}, {'blep','e621','floofgens'})


if __name__ == '__main__':
    unittest.main()
