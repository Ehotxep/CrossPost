"""Exact URL to UUID reconciliation, using mocked documented API responses."""
import importlib
import json
import os
import tempfile
import unittest
from unittest.mock import Mock,patch
from platforms import deviantart as DA

UUID='12345678-1234-4234-8234-123456789abc'
LINK='https://www.deviantart.com/fixture/art/Artwork-123'

class DeviantArtBrowserStats(unittest.TestCase):
    def response(self,**data):
        r=Mock(status_code=200);r.json.return_value=data;return r

    def test_resolution_requires_exact_art_url_and_valid_uuid(self):
        response=self.response(results=[{'url':LINK+'4','deviationid':UUID},
            {'url':'https://example.org/fixture/art/Artwork-123','deviationid':UUID},
            {'url':LINK,'deviationid':'123'}, {'url':LINK,'deviationid':UUID}],has_more=False)
        with patch.object(DA,'_token',return_value='fixture'),patch.object(DA.requests,'get',return_value=response) as get:
            self.assertEqual(DA.resolve_ids({},[LINK]),{LINK:UUID})
        self.assertEqual(get.call_count,1)
        self.assertEqual(get.call_args.args[0],DA.API+'/gallery/all')
        self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_resolution_is_bounded_and_rejects_external_urls_before_auth(self):
        with patch.object(DA,'_token',return_value='fixture') as token,patch.object(DA.requests,'get',return_value=self.response(results=[],has_more=True,next_offset=24)) as get:
            self.assertEqual(DA.resolve_ids({},['https://example.org/fixture/art/Artwork-123']),{})
            token.assert_not_called();get.assert_not_called()
            self.assertEqual(DA.resolve_ids({},[LINK]),{})
            self.assertEqual(get.call_count,2)

    def test_browser_statistics_require_oauth_and_do_not_report_partial_totals(self):
        from app.insights import post_stats
        from app.publish import runner
        dest={'key':'deviantart','link':LINK,'paid_confirmed':{'a':{'url':LINK},'b':{'url':LINK+'4'}}}
        with patch.object(runner.core_common,'load_token',return_value={}):
            self.assertIn('OAuth',post_stats._da_nums(dest)['error'])
        with patch.object(runner.core_common,'load_token',return_value={'access_token':'fixture'}),patch.object(DA,'resolve_ids',return_value={LINK:UUID}),patch.object(DA,'stats') as stats:
            self.assertIn('Не все',post_stats._da_nums(dest)['error']);stats.assert_not_called()

    def test_resolved_ids_persist_without_overwriting_other_destinations(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,CROSSPOST_DATA=tmp):
            from app import db
            from app.publish import runner
            from app.insights import post_stats
            db.reset_for_tests()
            try:
                importlib.reload(runner)
                dest={'key':'deviantart','platform':'deviantart','link':LINK}
                other={'key':'discord:0','state':'done','link':'preserved'}
                with db.tx() as conn:
                    pid=conn.execute('INSERT INTO publications(created,dests) VALUES (?,?)',('2026-11-18',json.dumps([dest,other]))).lastrowid
                with patch.object(runner.core_common,'load_token',return_value={'access_token':'fixture'}),patch.object(DA,'resolve_ids',return_value={LINK:UUID}),patch.object(DA,'stats',return_value=dict(views=3,favourites=1,comments=0,downloads=0)):
                    result=post_stats._da_nums(dest,pid)
                self.assertEqual(result['cells'][0],['Просмотры',3])
                saved=json.loads(db.query('SELECT dests FROM publications WHERE id=?',(pid,))[0]['dests'])
                self.assertEqual(saved[0]['deviation_ids'],[UUID]);self.assertEqual(saved[1],other)
            finally:db.reset_for_tests()
