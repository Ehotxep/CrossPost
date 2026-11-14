import csv
import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from trend_fixture import write_fixture
from app.trends import aggregate, dumps, metrics


class TrendTopics(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,CROSSPOST_DATA=self.tmp.name)
        env.start();self.addCleanup(env.stop)
        metrics.forget();self.addCleanup(metrics.forget)
        write_fixture(self.tmp.name)
        self.model=metrics.get_model('floofgens','all')

    def test_generic_tags_hidden_everywhere_but_kept_in_data_and_restorable(self):
        hidden=metrics.overview(self.model)
        tags={row['tag'] for row in hidden['popular']+hidden['rising']+hidden['niches']['points']+hidden['seasonal']}
        self.assertTrue({'solo','smile','ubiquitous_feature'}.isdisjoint(tags))
        self.assertEqual(hidden['filters']['hidden_common'],3)
        self.assertIn('armor',tags)
        restored=metrics.overview(self.model,show_common=True)
        self.assertIn('solo',{r['tag'] for r in restored['popular']})
        self.assertIn('solo',self.model.ti)
        manual=metrics.overview(self.model,show_common=True,hidden=['solo'])
        self.assertNotIn('solo',{r['tag'] for r in manual['popular']})
        self.assertIsNotNone(metrics.forecast(self.model,'solo'))

    def test_categories_use_tag_topics_and_manual_style_remains_explicit(self):
        clothes=metrics.overview(self.model,category='clothing')
        self.assertEqual({r['tag'] for r in clothes['popular']},{'armor','maid_uniform'})
        setting=metrics.overview(self.model,category='setting')
        self.assertEqual([r['tag'] for r in setting['popular']],['desert'])
        style=metrics.overview(self.model,style=['wolf','solo'],mine_only=True)
        self.assertEqual([r['tag'] for r in style['popular']],['wolf'])
        self.assertTrue(style['popular'][0]['mine'])

    def test_short_and_long_growth_use_equal_nonoverlapping_windows(self):
        short=metrics.overview(self.model,period='month')
        long=metrics.overview(self.model,period='quarter')
        a=next(r for r in short['rising'] if r['tag']=='armor')
        b=next(r for r in long['rising'] if r['tag']=='armor')
        self.assertEqual((a['sample'],a['previous_sample']),(160,48))
        self.assertEqual((b['sample'],b['previous_sample']),(268,156))
        self.assertIn('reason',a);self.assertIn('confidence',a)
        with self.assertRaises(ValueError):metrics.overview(self.model,period='invalid')

    def test_api_compares_floofgens_against_e621_and_uses_correct_label(self):
        from app.api import Api
        write_fixture(self.tmp.name,'e621')
        api=Api.__new__(Api)
        result=api.trends_overview('floofgens',show_common=False)
        self.assertEqual(result['source'],'floofgens')
        self.assertEqual(result['leads_target'],'floofgens')
        self.assertEqual(result['leads_label'],'floofgens.net')
        self.assertIsNotNone(result['leads'])
        other=api.trends_overview('e621')
        self.assertEqual(other['leads_target'],'floofgens')
        # Stale snapshots cannot support a claim that a trend has not reached the other site.
        path=Path(self.tmp.name)/'trends/e621/meta.json'
        meta=json.loads(path.read_text());meta['dump_date']='2026-10-01';path.write_text(json.dumps(meta))
        metrics.forget()
        stale=api.trends_overview('floofgens')
        self.assertIsNone(stale['leads'])
        self.assertIn('две недели',stale['comparison_note'])

    def test_leading_signals_respect_categories_hidden_tags_and_common_filter(self):
        write_fixture(self.tmp.name,'e621')
        other=metrics.get_model('e621','all')
        # Deliberately make generic tags grow faster than the useful topic.
        other.N[other.ti['solo'],-13:]*=2
        other.N[other.ti['armor'],-13:]*=3
        result=metrics.leads(other,self.model,category='clothing')
        tags={r['tag'] for r in result['new']+result['growing']}
        self.assertNotIn('solo',tags)
        hidden=metrics.leads(other,self.model,category='clothing',hidden=['armor'])
        self.assertNotIn('armor',{r['tag'] for r in hidden['new']+hidden['growing']})

    def test_floofgens_dump_discovery_accepts_e621ng_manifest_and_dated_directory(self):
        response=Mock(ok=True,json=lambda:[{'name':name,'url':f'https://floofgens.net/db_export/{name}.csv.gz','updated_at':'2026-11-14T00:00:00Z'} for name in ('posts','tags')])
        with patch('requests.get',return_value=response) as get:
            result=dumps.find_latest('floofgens','Crosspost/test')
            self.assertEqual(get.call_args.args[0],'https://floofgens.net/db_exports.json')
            self.assertEqual(result['day'],'2026-11-14')
            self.assertEqual(result['posts']['url'],'https://floofgens.net/db_export/posts.csv.gz')
        directory=Mock(text='<a href="posts-2026-11-14.csv.gz">posts</a><a href="tags-2026-11-14.csv.gz">tags</a>')
        with patch('requests.get',side_effect=[Mock(ok=False),directory]):
            self.assertEqual(dumps.find_latest('floofgens','test')['tags']['url'],'https://floofgens.net/db_export/tags-2026-11-14.csv.gz')

    def test_manual_floofgens_import_runs_the_real_aggregation_pipeline(self):
        from datetime import datetime,timedelta,timezone
        incoming=Path(self.tmp.name)/'incoming';incoming.mkdir()
        posts=incoming/'posts-2026-11-14.csv.gz';tags=incoming/'tags-2026-11-14.csv.gz'
        with gzip.open(tags,'wt',newline='',encoding='utf-8') as file:
            writer=csv.writer(file);writer.writerow(['name','category']);writer.writerows([['wolf',5],['armor',0],['solo',0]])
        with gzip.open(posts,'wt',newline='',encoding='utf-8') as file:
            writer=csv.writer(file);writer.writerow(['created_at','rating','tag_string','fav_count','is_deleted'])
            for index in range(60):
                when=datetime(2026,11,14,tzinfo=timezone.utc)-timedelta(days=20+index%15)
                writer.writerow([when.isoformat(),'s','wolf armor solo',10,'f'])
        day,p,t=dumps.import_files('floofgens',str(posts),str(tags))
        self.assertIn('/dumps/floofgens/',Path(p).as_posix())
        report=aggregate.build('floofgens',day,Path(p),Path(t),Mock())
        self.assertEqual(report['posts'],60)
        metrics.forget()
        model=metrics.get_model('floofgens','all')
        self.assertIsNotNone(model)
        self.assertEqual(model.source,'floofgens')
        self.assertIn('wolf',model.ti)


if __name__=='__main__':unittest.main()
