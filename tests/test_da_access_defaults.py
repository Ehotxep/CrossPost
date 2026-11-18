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

    def test_paid_modes_stop_before_upload_or_auth_while_inactive_defaults_do_not_block(self):
        self.assertEqual(blocked_reason({'premium_price_usd':'5.00','subscriber_tiers':['Tip Jar']}),'')
        for mode in ('premium_download','subscriber_only'):
            with self.subTest(mode=mode),patch.object(DA,'_token',side_effect=AssertionError('must not authenticate')):
                with self.assertRaises(NotPublishedError):
                    DA.publish({mode:True},[('image.png',b'original','image/png')],'Title','',[],'sfw')

    def test_paid_destination_is_blocked_in_plan_and_can_be_excluded(self):
        from app.publish import runner
        importlib.reload(runner)
        cfg=copy.deepcopy(S.DEFAULTS)
        cfg['deviantart'].update(enabled=True,premium_download=True,premium_price_usd='5.00')
        f={'id':'0123456789abcdef','name':'art.png','kind':'image','path':Path(self.tmp.name)/'art.png','size':8}
        f['path'].write_bytes(b'original')
        draft={'files':[f['id']],'title':'Art','text':'','rating':'sfw','signature':False,'exclude':[],'booru_posts':{}}
        with patch.object(runner.S,'load',return_value=cfg),patch.object(runner,'_files',return_value=[f]), \
                patch.object(runner.core_common,'load_token',return_value={'access_token':'fixture-token'}):
            result=runner.plan(draft)
            dest=next(d for d in result['dests'] if d['platform']=='deviantart')
            self.assertFalse(dest['will'])
            self.assertTrue(any('браузер' in e for e in result['errors']))
            draft['exclude']=['deviantart']
            self.assertFalse(any('браузер' in e for e in runner.plan(draft)['errors']))

    def test_browser_paid_plan_can_use_saved_browser_login_without_oauth(self):
        from app.publish import runner
        importlib.reload(runner)
        cfg=copy.deepcopy(S.DEFAULTS)
        cfg['deviantart'].update(enabled=True,browser_ok=True,premium_download=True,premium_price_usd='5.00')
        file={'id':'0123456789abcdef','name':'art.png','kind':'image','path':Path(self.tmp.name)/'art.png','size':8}
        file['path'].write_bytes(b'original')
        draft={'files':[file['id']],'title':'Art','text':'','rating':'sfw','signature':False,'exclude':[],'booru_posts':{}}
        with patch.object(runner.S,'load',return_value=cfg),patch.object(runner,'_files',return_value=[file]),patch.object(runner.core_common,'load_token',return_value={}):
            result=runner.plan(draft)
        dest=next(d for d in result['dests'] if d['platform']=='deviantart')
        self.assertTrue(dest['will'])
        self.assertIn('$5.00 USD',' '.join(dest['details']))

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
