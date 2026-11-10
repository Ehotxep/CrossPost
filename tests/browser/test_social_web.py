"""Browser adapters against isolated DOM fixtures, never live social accounts."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.publish import social_web as W
from core.common import NotPublishedError, PublicationUnknown


UPLOAD = '''<script>
window.submits=0;
document.querySelector('input[type=file]').onchange=e=>{
 const preview=document.querySelector('#previews');preview.replaceChildren();
 for(const file of e.target.files){const img=new Image();img.src=URL.createObjectURL(file);preview.append(img);}
};
</script>'''
REDDIT = '''<button role="tab" onclick="this.dataset.selected=1">Images</button>
<textarea name="title"></textarea><textarea name="body"></textarea>
<button onclick="document.querySelector('dialog').showModal()">Add flair and tags</button>
<dialog><label><input type=radio name=flair value="none">No flair</label>
<label><input type=radio name=flair value="Gay">Gay</label>
<label><input type=radio name=flair value="SFW">SFW</label>
<label><input type=checkbox id=nsfw>NSFW</label>
<button onclick="document.querySelector('dialog').close()">Apply</button></dialog>
<input type=file accept="image/*" multiple><div data-testid=media-preview id=previews></div>
<button id=send onclick="window.submits++;location.href='/r/FurAI/comments/abc123/art/'">Post</button>'''+UPLOAD
JOY = '''<form onsubmit="return false"><textarea name=text></textarea><input name=tags>
<input type=file accept="image/*"><div class=post_content id=previews></div>
<button id=send type=button onclick="window.submits++;location.href='/post/12345'">Опубликовать</button></form>'''+UPLOAD
X_FORM = '''<div data-testid="tweetTextarea_0" contenteditable="true" role="textbox"></div>
<input type="file" data-testid="fileInput" accept="image/*"><div data-testid="attachments" id="previews"></div>
<button data-testid="tweetButton" onclick="window.submits++">Post</button>'''+UPLOAD


class SocialBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.image = str(Path(cls.tmp.name) / 'art.png')
        Image.new('RGB', (20, 20), 'blue').save(cls.image)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.tmp.cleanup()

    @contextmanager
    def fixture(self, platform, html=None):
        context = self.browser.new_context()
        page = context.new_page()
        source = html or (REDDIT if platform == 'reddit' else X_FORM if platform == 'x' else JOY)
        # All requests are fulfilled locally, including the pretend confirmation page.
        context.route('**/*', lambda route: route.fulfill(content_type='text/html; charset=utf-8', body=source if route.request.url.rstrip('/').endswith(('submit', 'add', '/compose/post')) else '<p>Published fixture</p>'))
        @contextmanager
        def session(**kwargs):
            self.assertEqual(kwargs.get("platform"), platform)
            yield context, page
        try:
            with patch.object(W.B, 'session', session), patch.object(W.B, 'save_cookies'), patch.object(W.B, 'dump', return_value='fixture.png'):
                yield page
        finally:
            context.close()

    def test_dry_run_fills_rating_flair_tags_and_files_without_submitting(self):
        for platform in ('reddit', 'joyreactor'):
            with self.subTest(platform=platform), self.fixture(platform) as page:
                result = W.post(platform, [self.image], 'Title', 'Body', flair='Gay', nsfw=True,
                                tags=['AI Art', 'тег с пробелом'], dry_run=True)
                self.assertEqual(page.evaluate('window.submits'), 0)
                self.assertEqual(result['shot'], 'fixture.png')
                self.assertEqual(page.locator('#previews img').count(), 1)
                if platform == 'reddit':
                    self.assertEqual(page.locator('[name=title]').input_value(), 'Title')
                    self.assertTrue(page.locator('#nsfw').is_checked())
                    self.assertTrue(page.locator('[value=Gay]').is_checked())
                else:
                    self.assertEqual(page.locator('[name=tags]').input_value(), 'AI Art, тег с пробелом')
                    self.assertEqual(page.locator('[name=text]').input_value(), 'Title\n\nBody')

    def test_confirmed_navigation_produces_platform_link(self):
        for platform in ('reddit', 'joyreactor'):
            with self.subTest(platform=platform), self.fixture(platform):
                result = W.post(platform, [self.image], 'Title', 'Body', flair='SFW', tags=['AI Art'])
                self.assertIn('/comments/abc123/' if platform == 'reddit' else '/post/12345', result['link'])

    def test_click_exception_is_unknown_and_never_retried(self):
        for platform in ('reddit', 'joyreactor'):
            source = (REDDIT if platform == 'reddit' else JOY).replace("location.href='/r/FurAI/comments/abc123/art/'", "window.sent=true").replace("location.href='/post/12345'", "window.sent=true")
            with self.subTest(platform=platform), self.fixture(platform, source) as page:
                find = W._find
                class AmbiguousClick:
                    def __init__(self, locator): self.locator = locator
                    def is_enabled(self): return True
                    def click(self):
                        self.locator.click()
                        raise RuntimeError('Browser disconnected after click')
                def find_then_disconnect(*args, **kwargs):
                    locator = find(*args, **kwargs)
                    return AmbiguousClick(locator) if locator.get_attribute('id') == 'send' else locator
                with patch.object(W, '_find', side_effect=find_then_disconnect):
                    with self.assertRaises(PublicationUnknown):
                        W.post(platform, [self.image], 'Title', 'Body', flair='SFW', tags=['AI Art'])
                self.assertEqual(page.evaluate('window.submits'), 1)

    def test_a_feed_link_or_other_subreddit_is_not_a_confirmation(self):
        self.assertFalse(W._post_link('https://evil.invalid/post/123', 'joyreactor', 'FurAI'))
        self.assertFalse(W._post_link('https://www.reddit.com/r/aiyiff/comments/abc/', 'reddit', 'FurAI'))

    def test_pre_submit_error_identifies_stage_and_screenshot(self):
        with self.fixture('reddit'), patch.object(W, '_find', side_effect=NotPublishedError('Элемент не найден')):
            with self.assertRaisesRegex(NotPublishedError, 'проверка существующего черновика.*fixture.png'):
                W.post('reddit', [self.image], 'Title', 'Body', dry_run=True)

    def test_site_dry_run_preserves_existing_title_body_and_tags(self):
        for platform,source in [
            ('reddit',REDDIT.replace('<textarea name="title"></textarea>','<textarea name="title">Saved title</textarea>').replace('<textarea name="body"></textarea>','<textarea name="body">Saved body</textarea>')),
            ('joyreactor',JOY.replace('<textarea name=text></textarea>','<textarea name=text>Saved body</textarea>').replace('<input name=tags>','<input name=tags value="Saved tags">'))
        ]:
            with self.subTest(platform=platform),self.fixture(platform,source) as page:
                with self.assertRaisesRegex(NotPublishedError,'уже есть черновик'):
                    W.post(platform,[self.image],'New title','New body',tags=['New tags'],dry_run=True)
                body=page.locator('[name=body]' if platform=='reddit' else '[name=text]')
                self.assertEqual(body.input_value(),'Saved body')
                if platform=='reddit':
                    self.assertEqual(page.locator('[name=title]').input_value(),'Saved title')
                else:
                    self.assertEqual(page.locator('[name=tags]').input_value(),'Saved tags')
                self.assertEqual(page.evaluate('window.submits'),0)

    def test_existing_attachment_is_detected_before_text_is_filled(self):
        source=REDDIT.replace('id=previews></div>','id=previews><img alt="existing attachment"></div>')
        with self.fixture('reddit',source) as page:
            with self.assertRaisesRegex(NotPublishedError,'уже есть вложения'):
                W.post('reddit',[self.image],'New title','New body',dry_run=True)
            self.assertEqual(page.locator('[name=title]').input_value(),'')
            self.assertEqual(page.locator('[name=body]').input_value(),'')
            self.assertEqual(page.evaluate('window.submits'),0)

    def test_reddit_text_editor_inserts_image_without_submitting(self):
        source = '''<textarea name="title"></textarea>
          <div class="ProseMirror" contenteditable="true" role="textbox"></div>
          <button aria-label="Insert image" onclick="document.getElementById('inline-file').click()">Image</button>
          <input id="inline-file" type="file" accept="image/*" hidden>
          <button id="send" onclick="window.submits++">Post</button>
          <script>window.submits=0; document.getElementById('inline-file').onchange=e=>{
            const img=new Image();img.src=URL.createObjectURL(e.target.files[0]);
            document.querySelector('.ProseMirror').append(img);
          };</script>'''
        with self.fixture('reddit', source) as page:
            result = W.post('reddit', [self.image], 'Title', 'Body', community='aiyiff', dry_run=True)
            self.assertEqual(page.locator('[name=title]').input_value(), 'Title')
            self.assertEqual(page.locator('.ProseMirror img').count(), 1)
            self.assertEqual(page.evaluate('window.submits'), 0)
            self.assertEqual(result['shot'], 'fixture.png')

    def test_reddit_accepts_canonical_host_and_rejects_other_community(self):
        self.assertEqual(W._url('reddit', 'aiyiff'), 'https://www.reddit.com/r/aiyiff/submit/')
        self.assertTrue(W._site_host_ok('reddit', 'https://reddit.com/r/aiyiff/submit/'))
        self.assertFalse(W._site_host_ok('reddit', 'https://reddit.com.example.invalid/r/aiyiff/submit/'))

    def test_x_dry_run_attaches_test_image_and_never_submits(self):
        with self.fixture('x') as page:
            result = W.x_dry([self.image])
            self.assertEqual(page.evaluate('window.submits'), 0)
            self.assertEqual(page.locator('[data-testid="tweetTextarea_0"]').inner_text(), 'Crosspost test — not published')
            self.assertEqual(page.locator('#previews img').count(), 1)
            self.assertEqual(result['shot'], 'fixture.png')

    def test_x_test_preserves_existing_draft(self):
        source = X_FORM.replace('role="textbox"></div>', 'role="textbox">Existing draft</div>')
        with self.fixture('x', source) as page:
            with self.assertRaisesRegex(NotPublishedError, 'уже есть черновик'):
                W.x_dry([self.image])
            self.assertEqual(page.locator('[data-testid="tweetTextarea_0"]').inner_text(), 'Existing draft')
            self.assertEqual(page.evaluate('window.submits'), 0)

    def test_unconfirmed_upload_stops_each_dry_run_without_posting(self):
        for platform, source in [('reddit', REDDIT), ('joyreactor', JOY), ('x', X_FORM)]:
            source = source.replace('preview.append(img);', 'void img;')
            messages = []
            with self.subTest(platform=platform), self.fixture(platform, source) as page, \
                    patch.object(W, 'DRY_UPLOAD_WAIT_SECONDS', 1):
                with self.assertRaisesRegex(NotPublishedError, 'загрузк.*подтвердить'):
                    if platform == 'x':
                        W.x_dry([self.image], log=messages.append)
                    else:
                        W.post(platform, [self.image], 'Title', 'Body', flair='SFW',
                               tags=['AI Art'], dry_run=True, log=messages.append)
                self.assertEqual(page.evaluate('window.submits'), 0)
                self.assertTrue(any('ожидаю подтверждение загрузки' in m for m in messages))


    def test_open_form_waits_for_document_commit_with_bounded_timeout(self):
        from unittest.mock import Mock
        page = Mock()
        W._open_form(page, W._url('reddit'))
        page.goto.assert_called_once_with(W._url('reddit'), wait_until='commit', timeout=20000)

    def test_x_editor_can_appear_after_document_navigation(self):
        source = '<div id="mount"></div><script>setTimeout(()=>{document.getElementById("mount").innerHTML=' + __import__('json').dumps(X_FORM.replace(UPLOAD, '')) + ';' + UPLOAD.replace('<script>', '').replace('</script>', '') + '}, 2500)</script>'
        with self.fixture('x', source) as page:
            W.x_dry([self.image])
            self.assertEqual(page.evaluate('window.submits'), 0)

    def test_reddit_existing_nsfw_label_does_not_require_checkbox_in_flair_dialog(self):
        source = '<span>NSFW</span>' + REDDIT.replace('<label><input type=checkbox id=nsfw>NSFW</label>', '')
        with self.fixture('reddit', source) as page:
            W.post('reddit', [self.image], 'Title', '', flair='Gay', nsfw=True, dry_run=True)
            self.assertTrue(page.locator('[value=Gay]').is_checked())
            self.assertEqual(page.evaluate('window.submits'), 0)

    def test_x_modal_ignores_background_composer(self):
        background = '''<div id="background"><div data-testid="tweetTextarea_0" contenteditable="true">Background draft</div>
          <input type="file" data-testid="fileInput"><button data-testid="tweetButtonInline">Post</button></div>'''
        source = background + '<div role="dialog">' + X_FORM.replace(UPLOAD, '') + '</div>' + UPLOAD.replace("document.querySelector('input[type=file]')", "document.querySelector('[role=dialog] input[type=file]')")
        with self.fixture('x', source) as page:
            W.x_dry([self.image])
            self.assertEqual(page.locator('#background [contenteditable]').inner_text(), 'Background draft')
            self.assertEqual(page.locator('[role=dialog] [contenteditable]').inner_text(), 'Crosspost test — not published')
            self.assertEqual(page.evaluate('window.submits'), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
