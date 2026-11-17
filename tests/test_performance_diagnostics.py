import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import diagnostics as D


class PerformanceDiagnostics(unittest.TestCase):
    def test_browser_report_drops_payload_urls_errors_and_unknown_identifiers(self):
        sample = {'kind':'api','name':'post_plan','view':'post','ms':12.5,'ok':False,
                  'id':'1234abcd-1','args':{'token':'SECRET'},'error':'PRIVATE TITLE',
                  'url':'https://example.invalid/private'}
        result = D.sanitize([sample,dict(sample,name='SECRET'),dict(sample,view=['secret']),
                             {'kind':[],'name':'post_plan','ms':1},dict(sample,ms=float('nan')),
                             dict(sample,ms=10**1000),dict(sample,ms=-1)])
        self.assertEqual(len(result), 2)
        text = json.dumps(result)
        for secret in ('SECRET','PRIVATE TITLE','example.invalid','token'):
            self.assertNotIn(secret,text)
        self.assertEqual(result[0]['id'],'1234abcd-1')
        self.assertNotIn('view',result[1])

    def test_ring_is_bounded_and_percentiles_include_failures_but_not_pending_errors(self):
        with patch.object(D, '_samples', D.deque(maxlen=D.LIMIT)):
            for i in range(D.LIMIT+10):
                D.record('server_api','post_plan',i,ok=i%2==0)
            data = D.snapshot([{'kind':'api','name':'post_plan','ms':10,'ok':False,'pending':True}])
        self.assertEqual(len(data['recent_server']),D.LIMIT)
        group=data['server'][0]
        self.assertEqual(group['n'],D.LIMIT)
        self.assertEqual(group['max_ms'],D.LIMIT+9)
        self.assertEqual(group['p95_ms'],1909)
        self.assertEqual(data['client'][0]['errors'],0)
        self.assertEqual(data['client'][0]['pending'],1)

    def test_export_includes_sanitized_timings_and_removes_old_exception_messages(self):
        from app import perf, db
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,CROSSPOST_DATA=folder):
            db.reset_for_tests()
            try:
                with patch.object(perf,'gpu_info',return_value={}), \
                        patch('app.tagger.jtp.status',return_value={}), \
                        patch('app.tagger.jtp.torch_info',return_value=None), \
                        patch.object(perf,'snapshot',return_value={}), \
                        patch.object(perf,'summary',return_value={}), \
                        patch.object(perf,'journal',return_value=[{'kind':'task','name':'test','err':'Error: PRIVATE TITLE SECRET'}]):
                    path = perf.report(client=[{'kind':'api','name':'post_plan','ms':200,'args':{'token':'SECRET'}}])
                text=Path(path).read_text()
                self.assertNotIn('SECRET',text)
                self.assertNotIn('PRIVATE TITLE',text)
                report=json.loads(text)
                self.assertTrue(report['timings']['client'])
                self.assertIs(report['journal'][0]['err'],True)
            finally:
                db.reset_for_tests()

    def test_http_records_compute_and_serialization_without_request_content(self):
        import requests
        from app.server import serve_in_background
        class Stub:
            def call(self,name,args):
                if name=='post_plan':return {'text':args['text']}
                raise ValueError('PRIVATE ERROR')
            def post_plan(self):pass
            def info(self):pass
        server, base = serve_in_background(Stub())
        try:
            with patch.object(D, '_samples', D.deque(maxlen=D.LIMIT)), requests.Session() as session:
                session.trust_env=False
                response=session.post(base+'api/post_plan',headers={'X-Token':server.cp_token,'X-Perf-ID':'1234abcd-1'},
                                      json={'text':'PRIVATE TITLE','token':'SECRET'},timeout=10)
                self.assertTrue(response.json()['ok'])
                failed=session.post(base+'api/info',headers={'X-Token':server.cp_token,'X-Perf-ID':'1234abcd-1'},json={},timeout=10)
                self.assertFalse(failed.json()['ok'])
                rows=D.snapshot()['recent_server']
                self.assertTrue(any(not r['ok'] for r in rows))
                self.assertEqual({r['kind'] for r in rows},{'server_api','serialize'})
                self.assertTrue(all(r['id']=='1234abcd-1' for r in rows))
                self.assertNotIn('PRIVATE',json.dumps(rows))
                self.assertNotIn('SECRET',json.dumps(rows))
        finally:
            server.shutdown()
            server.server_close()
