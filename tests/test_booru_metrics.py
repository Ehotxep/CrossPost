import json
import os
from datetime import datetime,timedelta,timezone
import tempfile
import unittest
from unittest.mock import Mock,patch

from app import db
from app.insights import booru_metrics as BM, post_stats
from app.publish import settings as PS


class BooruMeasurements(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,CROSSPOST_DATA=self.tmp.name);env.start();self.addCleanup(env.stop)
        db.reset_for_tests();self.addCleanup(db.reset_for_tests)
        self.now=datetime(2026,11,14,12,tzinfo=timezone.utc)
        clock=patch.object(BM,'now_utc',return_value=self.now);clock.start();self.addCleanup(clock.stop)
        tick=patch.object(BM,'_last_tick',0.0);tick.start();self.addCleanup(tick.stop)
        post_stats._e6_cache.clear();self.addCleanup(post_stats._e6_cache.clear)
        BM.ensure_schema()
        PS.update('modules',{'collect':True,'floofgens':True,'e621':True,'e6ai':True})
        with db.tx() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS publications(id INTEGER PRIMARY KEY,created TEXT,title TEXT,text TEXT,rating TEXT,files TEXT,dests TEXT,draft TEXT)''')

    def item(self,pid,published,*,pub_id=None,site='floofgens',owner='id:1',rating='s',kind='image',favs=10,age=24.5):
        link=PS.BOORU[site]+f'/posts/{pid}'
        pub_id=pid if pub_id is None else pub_id
        BM.register({'id':pub_id,'created':published.isoformat()},{'state':'done','link':link})
        values={'created_at':published.isoformat(),'observed_at':(published+timedelta(hours=age)).isoformat(),
                'owner':owner,'rating':rating,'kind':kind,'favs':favs,'score':-2,'comments':0}
        self.assertTrue(BM.observe(link,values))
        return link,values

    def test_checkpoints_are_observed_once_and_cached_early_counts_are_not_backdated(self):
        published=self.now-timedelta(hours=25)
        link,values=self.item(1,published,favs=12,age=23.9)
        self.assertEqual(db.query('SELECT * FROM booru_checkpoints'),[])
        values.update(observed_at=(published+timedelta(hours=24.1)).isoformat())
        BM.observe(link,values)
        values.update(favs=999,observed_at=(published+timedelta(hours=25.5)).isoformat())
        BM.observe(link,values)
        row=db.query('SELECT * FROM booru_checkpoints')[0]
        self.assertEqual(row['favs'],12)
        self.assertAlmostEqual(row['age_h'],24.1)
        for hours in (72,168):
            values.update(observed_at=(published+timedelta(hours=hours+.1)).isoformat())
            BM.observe(link,values)
        self.assertEqual({r['checkpoint'] for r in db.query('SELECT * FROM booru_checkpoints')},{24,72,168})

    def test_late_start_does_not_reconstruct_missed_day(self):
        published=self.now-timedelta(hours=48)
        self.item(2,published,age=48)
        report=BM.report(2,'floofgens',now=self.now)['items'][0]
        self.assertEqual([p['state'] for p in report['points']],['missed','waiting','waiting'])
        self.assertEqual(db.query('SELECT * FROM booru_checkpoints'),[])

    def test_missing_or_invalid_counts_never_become_zero_observations(self):
        published=self.now-timedelta(hours=24.5)
        link,values=self.item(3,published,favs=0)
        self.assertEqual(db.query('SELECT favs FROM booru_checkpoints')[0]['favs'],0)
        for pid,changes in [(4,{'favs':None}),(5,{'favs':-1}),(6,{'owner':None}),(7,{'created_at':'bad-date'}),(8,{'deleted':True})]:
            url=PS.BOORU['floofgens']+f'/posts/{pid}'
            BM.register({'id':pid,'created':published.isoformat()},{'state':'done','link':url})
            self.assertFalse(BM.observe(url,dict(values,**changes)))
        self.assertEqual(len(db.query('SELECT * FROM booru_checkpoints')),1)

    def test_cohort_uses_same_author_site_rating_format_and_prior_measurements(self):
        target=self.now-timedelta(hours=24.5)
        for i in range(10):self.item(100+i,target-timedelta(days=i+2),favs=10)
        # These must not inflate the sample or baseline.
        self.item(300,target-timedelta(days=2),owner='id:2',favs=10000)
        self.item(301,target-timedelta(days=2),site='e621',favs=10000)
        self.item(302,target-timedelta(days=2),rating='e',favs=10000)
        self.item(303,target-timedelta(days=2),kind='gif',favs=10000)
        self.item(304,target-timedelta(days=200),favs=10000)
        self.item(305,target+timedelta(hours=1),favs=10000)
        self.item(306,target-timedelta(hours=.2),favs=10000,age=25.9) # measured after current checkpoint
        link,values=self.item(400,target,favs=20)
        # Reposting the same remote ID does not create a new independent observation.
        BM.register({'id':1000,'created':target.isoformat()},{'state':'done','link':PS.BOORU['floofgens']+'/posts/100'})
        c=BM.report(400,'floofgens',now=self.now)['items'][0]['points'][0]['comparison']
        self.assertTrue(c['ready']);self.assertEqual(c['n'],10)
        self.assertEqual(c['median'],10);self.assertEqual(c['index'],2)
        self.assertEqual((c['median_low'],c['median_high']),(10,10))
        self.assertEqual(c['lower'],10)

    def test_album_weights_current_album_and_zero_baseline_are_explicit(self):
        target=self.now-timedelta(hours=24.5)
        for i in range(10):
            for offset in range(3):self.item(100+i*3+offset,target-timedelta(days=i+1),pub_id=i+1,favs=0)
        self.item(900,target,pub_id=90,favs=0)
        self.item(901,target-timedelta(minutes=2),pub_id=90,favs=500)
        c=BM.report(90,'floofgens',now=self.now)['items'][0]['points'][0]['comparison']
        self.assertEqual(c['n'],10);self.assertEqual(c['equal'],10)
        self.assertIsNone(c['index']);self.assertEqual(c['lower'],0)

    def test_worker_collects_known_booru_posts_without_a_telegram_login_and_respects_switches(self):
        published=self.now-timedelta(hours=24.5)
        refs=[{'url':'https://floofgens.net/posts/42','sent_at':published.isoformat()},
              {'url':'https://floofgens.net/posts/43','sent_at':published.isoformat()}]
        dests=[{'key':'floofgens','platform':'floofgens','state':'done','link':refs[0]['url'],'post_refs':refs},
               {'key':'e621','platform':'e621','state':'done','link':'https://e621.net/posts/42'},
               {'key':'reddit:X','platform':'reddit','state':'done','link':'https://www.reddit.com/r/X/comments/abc/test/'}]
        with db.tx() as c:c.execute('INSERT INTO publications VALUES (?,?,?,?,?,?,?,?)',(1,published.isoformat(),'Art','','sfw','[]',json.dumps(dests),'{}'))
        PS.update('modules',{'e621':False,'e6ai':False})
        values={'created_at':published.isoformat(),'observed_at':self.now.isoformat(),'owner':'id:1','rating':'s','kind':'image','favs':5}
        with patch.object(post_stats,'e6ai_stats',return_value=values) as fetch:
            result=BM.tick(now=self.now,min_gap=0)
            self.assertEqual(result['updated'],2)
            self.assertEqual({c.args[0] for c in fetch.call_args_list},{r['url'] for r in refs})
            self.assertTrue(all(c.kwargs['force'] for c in fetch.call_args_list))
        PS.update('modules',{'collect':False})
        with patch.object(post_stats,'e6ai_stats') as fetch:
            self.assertEqual(BM.tick(now=self.now+timedelta(minutes=10),min_gap=0)['skipped'],'disabled')
            fetch.assert_not_called()

    def test_first_observation_verifies_server_date_and_no_api_failures_create_snapshots(self):
        published=self.now-timedelta(hours=1)
        dest={'state':'done','link':'https://floofgens.net/posts/1'}
        with db.tx() as c:c.execute('INSERT INTO publications VALUES (?,?,?,?,?,?,?,?)',(1,published.isoformat(),'Art','','sfw','[]',json.dumps([dest]),'{}'))
        with patch.object(post_stats,'e6ai_stats',return_value={'error':'HTTP 403'}) as fetch:
            BM.tick(now=self.now,min_gap=0)
            fetch.assert_called_once()
            self.assertEqual(db.query('SELECT * FROM booru_checkpoints'),[])
        with patch.object(post_stats,'e6ai_stats') as fetch:
            BM.tick(now=self.now+timedelta(minutes=1),min_gap=0)
            fetch.assert_not_called()

    def test_saved_checkpoints_remain_readable_with_collection_disabled(self):
        published=self.now-timedelta(hours=24.5)
        link,_=self.item(1,published,favs=12)
        dest={'key':'floofgens','platform':'floofgens','state':'done','link':link,'name':'floofgens.net'}
        with db.tx() as c:c.execute('INSERT INTO publications VALUES (?,?,?,?,?,?,?,?)',(1,published.isoformat(),'Art','','sfw','[]',json.dumps([dest]),'{}'))
        PS.update('modules',{'collect':False})
        with patch('requests.get') as fetch,patch.object(post_stats.M,'growth_curve',return_value={'points':[]}):
            report=post_stats._for_single_publication(1)
            self.assertIn('выключен',report['dests'][0]['why'])
            point=report['dests'][0]['booru_metrics']['items'][0]['points'][0]
            self.assertEqual(point['favs'],12)
            fetch.assert_not_called()

    def test_fetch_exposes_real_metadata_and_force_bypasses_cache(self):
        response=Mock(status_code=200,json=lambda:{'post':{'created_at':self.now.isoformat(),'uploader_id':7,'rating':'s','file':{'ext':'png'},'fav_count':0,'score':{'total':-2},'comment_count':0}})
        with patch('requests.get',return_value=response) as fetch:
            a=post_stats.e6ai_stats('https://floofgens.net/posts/42')
            post_stats.e6ai_stats('https://floofgens.net/posts/42')
            self.assertEqual(fetch.call_count,1)
            post_stats.e6ai_stats('https://floofgens.net/posts/42',force=True)
            self.assertEqual(fetch.call_count,2)
        self.assertEqual((a['owner'],a['kind'],a['favs']),('id:7','image',0))
        self.assertIsNotNone(BM.dt(a['observed_at']))


if __name__=='__main__':unittest.main()
