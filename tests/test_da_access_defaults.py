import copy
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.publish import settings as S, templates
from app.publish.da_access import blocked_reason
from platforms import deviantart as DA
from core.common import NotPublishedError


class DeviantArtAccessDefaults(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,CROSSPOST_DATA=self.tmp.name)
        env.start();self.addCleanup(env.stop)
        from app import db
        db.reset_for_tests();self.addCleanup(db.reset_for_tests)

    def test_defaults_persist_normalize_price_and_keep_multiple_tiers(self):
        saved=S.update('deviantart',{'premium_download':True,'premium_price_usd':'5,5',
            'subscriber_only':True,'subscriber_tiers':[' Tip Jar ','Big Tip Jar','Tip Jar']})['deviantart']
        self.assertEqual(saved['premium_price_usd'],'5.50')
        self.assertEqual(saved['subscriber_tiers'],['Tip Jar','Big Tip Jar'])
        loaded=S.load()['deviantart']
        self.assertEqual(loaded['premium_price_usd'],'5.50')
        self.assertTrue(loaded['premium_download'])

    def test_invalid_price_or_tiers_do_not_overwrite_saved_settings(self):
        S.update('deviantart',{'premium_price_usd':'5'})
        for changes in ({'premium_price_usd':'-1'},{'premium_price_usd':'1.234'},
                        {'premium_price_usd':5},{'subscriber_tiers':'Tip Jar'},{'subscriber_only':'false'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):S.update('deviantart',changes)
            self.assertEqual(S.load()['deviantart']['premium_price_usd'],'5.00')

    def test_template_freezes_access_defaults_without_credentials(self):
        cfg=copy.deepcopy(S.DEFAULTS)
        cfg['deviantart'].update(premium_download=True,premium_price_usd='10.00',subscriber_tiers=['Tip Jar'],client_secret='SECRET')
        profile=templates.clean_profile(cfg)
        cfg['deviantart']['premium_price_usd']='20.00'
        effective=templates.effective(cfg,{'profile':profile,'booru_posts':{}})
        self.assertEqual(effective['deviantart']['premium_price_usd'],'10.00')
        self.assertNotIn('client_secret',profile['deviantart'])

    def test_paid_plan_requires_oauth_but_not_browser_price_or_tiers(self):
        from app.publish import runner
        importlib.reload(runner)
        cfg = copy.deepcopy(S.DEFAULTS)
        cfg["deviantart"].update(enabled=True, premium_download=True, subscriber_only=True)
        f = {"id":"0123456789abcdef", "name":"art.png", "kind":"image",
             "path":Path(self.tmp.name)/"art.png", "size":8}
        f["path"].write_bytes(b"original")
        draft = {"files":[f["id"]], "title":"Art", "text":"", "rating":"sfw", "signature":False}
        with patch.object(runner.S,"load",return_value=cfg), patch.object(runner,"_files",return_value=[f]), \
                patch.object(runner.core_common,"load_token",return_value={"access_token":"fixture"}):
            dest = next(d for d in runner.plan(draft)["dests"] if d["platform"]=="deviantart")
        self.assertTrue(dest["will"])
        self.assertTrue(dest["stash_options"]["premium_download"])
        self.assertIn("Sta.sh", " ".join(dest["details"]))

    def test_paid_batch_keeps_original_bytes_separate_and_resumes_confirmed_files(self):
        import io,hashlib
        from contextlib import contextmanager
        from unittest.mock import Mock
        from PIL import Image
        from app.publish import da_web
        cfg={'browser_ok':True,'premium_download':True,'premium_price_usd':'5.00'}
        images=[]
        for color in ('blue','red'):
            image=io.BytesIO();Image.new('RGB',(80,60),color).save(image,format='PNG');images.append(image.getvalue())
        files=[('preview-0.jpg',images[0],'image/png'),('second.png',images[1],'image/png')]
        saved={};seen=[]
        @contextmanager
        def session(**kwargs):yield Mock(),Mock()
        def prepare(page,cfg,preview,original,*args):
            data=original.read_bytes();seen.append(data)
            self.assertEqual(Image.open(preview).size,(40,30))
            if data==images[1] and len(seen)==2:raise NotPublishedError('Second upload failed')
            return {'url':'https://www.deviantart.com/fixture/art/Work-'+str(len(seen)),'id':''}
        with patch.object(da_web.B,'session',session),patch.object(da_web.B,'save_cookies'),patch.object(da_web,'post',side_effect=prepare):
            with self.assertRaises(NotPublishedError):
                da_web.publish(cfg,files,'Title','',[],'sfw',confirmed=lambda rows:saved.update(rows))
            self.assertIn(hashlib.sha256(images[0]).hexdigest(),saved)
            da_web.publish(cfg,files,'Title','',[],'sfw',already=saved)
        self.assertEqual(seen,[images[0],images[1],images[1]])

    def test_subscriber_only_uploads_original_as_main_image(self):
        import io
        from contextlib import contextmanager
        from unittest.mock import Mock
        from PIL import Image
        from app.publish import da_web
        data=io.BytesIO();Image.new('RGB',(80,60),'blue').save(data,format='PNG')
        cfg={'browser_ok':True,'subscriber_only':True,'subscriber_tiers':['Tip Jar']}
        @contextmanager
        def session(**kwargs):yield Mock(),Mock()
        def prepare(page,cfg,main,original,*args):
            self.assertEqual(main.read_bytes(),data.getvalue())
            self.assertEqual(original.read_bytes(),data.getvalue())
            return {'url':'https://www.deviantart.com/fixture/art/Work-1','id':''}
        with patch.object(da_web.B,'session',session),patch.object(da_web.B,'save_cookies'),patch.object(da_web,'post',side_effect=prepare):
            da_web.publish(cfg,[('work.png',data.getvalue(),'image/png')],'Title','',[],'sfw')

    def test_paid_checkpoint_persists_before_next_file(self):
        import json
        from app import db
        from app.publish import runner
        importlib.reload(runner)
        with patch.object(runner, '_state', {'created':1,'title':'Art','text':'','rating':'sfw',
                'files':[],'dests':[{'key':'deviantart','state':'sending'}]}):
            rows={'hash':{'url':'https://www.deviantart.com/fixture/art/Work-123','id':''}}
            runner._da_paid_checkpoint('deviantart',rows)
            with db.tx() as conn:
                stored=json.loads(conn.execute('SELECT dests FROM publications WHERE id=?',
                    (runner.state()['pub_id'],)).fetchone()[0])
            self.assertEqual(stored[0]['paid_confirmed'],rows)
            self.assertEqual(stored[0]['link'],rows['hash']['url'])

    def test_ordinary_publish_uses_api_without_price_tiers_or_browser(self):
        from app.publish import da_web
        from core import retry
        from unittest.mock import Mock
        cfg=copy.deepcopy(S.DEFAULTS['deviantart'])
        cfg.update(premium_download=False,subscriber_only=False,browser_ok=False,premium_price_usd='',subscriber_tiers=[])
        responses=[Mock(status_code=200),Mock(status_code=200)]
        responses[0].json.return_value={'itemid':123}
        responses[1].json.return_value={'url':'https://www.deviantart.com/fixture/art/Art-123','deviationid':'fixture-id'}
        with patch.object(DA,'_token',return_value='fixture'),patch.object(retry,'post',side_effect=responses) as post,patch.object(da_web,'publish') as browser:
            result=DA.publish(cfg,[('art.png',b'original','image/png')],'Title','Body',[],'sfw')
        self.assertEqual(result[0]['id'],'fixture-id')
        self.assertEqual(post.call_count,2);browser.assert_not_called()
        self.assertNotIn('price',str(post.call_args_list))

    def test_paid_preparation_submits_only_and_resumes_from_saved_draft(self):
        from core import retry
        from unittest.mock import Mock
        cfg = dict(premium_download=True, subscriber_only=True, browser_ok=False)
        files = [("one.jpg", b"first", "image/jpeg"), ("two.jpg", b"second", "image/jpeg")]
        response = Mock(status_code=200)
        response.json.return_value = {"itemid": 37}
        saved = {}
        with patch.object(DA, "_token", return_value="fixture"), \
                patch.object(retry, "post", return_value=response) as post:
            rows = DA.publish(cfg, files, "Title", "Description", ["wolf"], "sfw",
                              already={"0": {"name":"one.jpg","id":"","url":"https://sta.sh/01"}},
                              confirmed=saved.update)
        post.assert_called_once()
        self.assertTrue(post.call_args.args[0].endswith("/stash/submit"))
        self.assertFalse(post.call_args.kwargs["idempotent"])
        self.assertEqual(post.call_args.kwargs["data"]["artist_comments"], "Description")
        self.assertEqual(post.call_args.kwargs["data"]["tags[]"], ["wolf"])
        self.assertEqual(rows[1]["url"], "https://sta.sh/011")
        self.assertEqual(saved["1"], rows[1])
