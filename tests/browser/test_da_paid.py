"""Paid DA submission against local DOM fixtures. No live accounts or requests."""
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from PIL import Image
from playwright.sync_api import sync_playwright
from diagnostics import capture_failure,watch_page
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.publish import da_web as W
from core.common import NotPublishedError,PublicationUnknown

FORM='''<!doctype html><html><body>
<button id=upload onclick="document.getElementById('art').click()">Upload Art</button><input type=file id=art accept="image/*" hidden>
<div id=preview></div><label>Title<input name=title></label>
<label>Description<textarea name=description></textarea></label>
<label>Tags<input id=tags></label><div class=tags id=chips></div>
<label><input type=checkbox checked id=ai>Created using AI tools</label>
<label><input type=checkbox checked id=noai>NoAI</label>
<label><input type=checkbox id=mature>Mature content</label>
<label><input type=checkbox id=nudity>Nudity</label>
<label><input type=checkbox checked>Allow comments</label>
<label><input type=checkbox checked id=free>Allow free download</label>
<section id=premium><label><input type=checkbox id=paid>Sell as Premium Download</label>
<div id=paidfields hidden><input type=file accept="image/*" id=original hidden><button onclick="document.getElementById('original').click()">Upload File</button>
<div id=filename></div><label>Price<input name=price inputmode=decimal></label><span>USD</span></div></section>
<section id=subscribers><h3>Submit to your subscribers</h3><button aria-haspopup=listbox id=choose onclick="document.getElementById('tiers').hidden=!document.getElementById('tiers').hidden">Choose Tier</button>
<div id=tiers role=listbox hidden><label><input type=checkbox>Tip Jar</label><label><input type=checkbox>Big Tip Jar</label><label><input type=checkbox>Other Tier</label></div></section>
<label><input type=checkbox id=exclusive>Sell as Exclusive</label><label><input type=checkbox>Sell as T-shirt</label>
<select aria-label="Choose Gallery"><option value="">Choose Gallery</option></select>
<button id=submit onclick="window.submits++;location.href='https://www.deviantart.com/fixture/art/Published-123456'">Submit Now</button>
<script>
window.submits=window.uploads=0;
paid.onchange=()=>{paidfields.hidden=!paid.checked;};
art.onchange=e=>{window.uploads++;const img=new Image();img.src=URL.createObjectURL(e.target.files[0]);preview.append(img);};
original.onchange=e=>{filename.textContent=e.target.files[0].name;};
tags.onkeydown=e=>{if(e.key===','){e.preventDefault();const tag=document.createElement('span');tag.textContent=tags.value;chips.append(tag);tags.value='';}};
</script></body></html>'''


class DeviantArtPaidBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(headless=True,executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None)
        cls.tmp=tempfile.TemporaryDirectory()
        cls.original=Path(cls.tmp.name)/'original.png'
        cls.preview=Path(cls.tmp.name)/'preview.jpg'
        Image.new('RGB',(80,60),'blue').save(cls.original)
        Image.new('RGB',(40,30),'blue').save(cls.preview)
    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop();cls.tmp.cleanup()

    def config(self,**kwargs):
        return dict(browser_ok=True,premium_download=True,premium_price_usd='5.50',subscriber_only=True,
                    subscriber_tiers=['Tip Jar','Big Tip Jar'],is_ai_generated=True,noai=True,**kwargs)

    @contextmanager
    def form(self,source=FORM):
        ctx=self.browser.new_context()
        page=watch_page(ctx.new_page())
        ctx.route('**/*',lambda route:route.fulfill(content_type='text/html',body=source if '/submit' in route.request.url else 'Published'))
        try:yield ctx,page
        except BaseException:
            capture_failure(page,self.id());raise
        finally:ctx.close()

    def post(self,page,cfg=None,dry=True):
        with patch.object(W.B,'dump',return_value='da-paid-test.png'):
            return W.post(page,cfg or self.config(),self.preview,self.original,'Paid artwork','Description',['wolf','forest'],'sfw',dry)

    def test_read_tiers_preserves_selected_access_and_existing_draft(self):
        source=FORM.replace('<input type=checkbox>Other Tier','<input type=checkbox checked>Other Tier')
        with self.form(source) as (_,page):
            page.goto(W.SUBMIT)
            page.locator('input[name=title]').fill('User draft')
            names=W.read_tiers(page)
            self.assertEqual(names,['Tip Jar','Big Tip Jar','Other Tier'])
            self.assertEqual(page.locator('#tiers input:checked').count(),1)
            self.assertEqual(page.locator('input[name=title]').input_value(),'User draft')
            self.assertEqual(page.evaluate('window.submits'),0)
            self.assertFalse(page.locator('#tiers').is_visible())

    def test_dry_fills_original_price_and_exact_tiers_without_submitting(self):
        with self.form() as (_,page):
            result=self.post(page)
            self.assertEqual(page.locator('input[name=price]').input_value(),'5.50')
            self.assertEqual(page.locator('#original').evaluate('e=>e.files[0].size'),self.original.stat().st_size)
            self.assertEqual(page.locator('#filename').inner_text(),'original.png')
            self.assertEqual(page.locator('#tiers input:checked').count(),2)
            self.assertFalse(page.locator('#free').is_checked())
            self.assertEqual(page.evaluate('window.submits'),0)
            self.assertEqual(result['shot'],'da-paid-test.png')

    def test_premium_only_clears_previously_selected_tiers(self):
        cfg=self.config();cfg.update(subscriber_only=False,subscriber_tiers=[])
        source=FORM.replace('<input type=checkbox>Other Tier','<input type=checkbox checked>Other Tier')
        with self.form(source) as (_,page):
            self.post(page,cfg)
            self.assertEqual(page.locator('#tiers input:checked').count(),0)
            self.assertEqual(page.evaluate('window.submits'),0)

    def test_subscriber_only_checks_tiers_without_enabling_premium_download(self):
        cfg=self.config();cfg.update(premium_download=False,premium_price_usd='')
        with self.form() as (_,page):
            self.post(page,cfg)
            self.assertFalse(page.locator('#paid').is_checked())
            self.assertEqual(page.locator('#tiers input:checked').count(),2)
            self.assertEqual(page.evaluate('window.submits'),0)

    def test_missing_tier_wrong_currency_and_late_price_reset_stop_before_submit(self):
        cases=[(FORM,self.config(), 'missing tier'),(FORM.replace('<span>USD</span>','<span>EUR</span>'),self.config(),'currency'),
               (FORM.replace('</script>','document.querySelector("input[name=price]").onblur=e=>{e.target.value="1"};</script>'),self.config(),'reset')]
        cases[0][1]['subscriber_tiers']=['Missing Tier']
        for source,cfg,why in cases:
            with self.subTest(why=why),self.form(source) as (_,page):
                with self.assertRaises(NotPublishedError):self.post(page,cfg)
                self.assertEqual(page.evaluate('window.submits'),0)

    def test_disabled_submit_and_conflicting_exclusive_sale_stop(self):
        for source in (FORM.replace('id=submit onclick','id=submit disabled onclick'),FORM.replace('id=exclusive>','id=exclusive checked>')):
            with self.form(source) as (_,page):
                with self.assertRaises(NotPublishedError):self.post(page)
                self.assertEqual(page.evaluate('window.submits'),0)

    def test_empty_price_or_tiers_stop_before_opening_editor(self):
        for changes in ({'premium_price_usd':''},{'subscriber_tiers':[]}):
            cfg=self.config();cfg.update(changes)
            with self.form() as (_,page):
                with patch.object(page,'goto',side_effect=AssertionError('must not open')):
                    with self.assertRaises(NotPublishedError):self.post(page,cfg)

    def test_submit_once_and_require_own_confirmed_art_link(self):
        with self.form() as (_,page):
            result=self.post(page,dry=False)
            self.assertEqual(result['url'],'https://www.deviantart.com/fixture/art/Published-123456')
        with self.form() as (_,page),patch.object(W,'_confirm',side_effect=PublicationUnknown('unconfirmed')):
            # The submit handler does not navigate, allowing a reliable count.
            source=FORM.replace("window.submits++;location.href='https://www.deviantart.com/fixture/art/Published-123456'",'window.submits++')
            page.context.unroute('**/*')
            page.context.route('**/*',lambda route:route.fulfill(content_type='text/html',body=source))
            with self.assertRaises(PublicationUnknown):self.post(page,dry=False)
            self.assertEqual(page.evaluate('window.submits'),1)

    def test_restored_draft_is_not_overwritten(self):
        with self.form(FORM.replace('name=title>','name=title value="Existing draft">')) as (_,page):
            with self.assertRaises(NotPublishedError):self.post(page)
            self.assertEqual(page.locator('input[name=title]').input_value(),'Existing draft')
            self.assertEqual(page.evaluate('window.uploads'),0)

if __name__=='__main__':
    from tools.run_tests import report_result
    program=unittest.main(verbosity=2,exit=False)
    sys.exit(report_result(program.result))
