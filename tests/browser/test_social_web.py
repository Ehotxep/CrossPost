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
from diagnostics import capture_failure, watch_page

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.publish import social_web as W
from app.publish import vk_web as V
from core.common import NotPublishedError, PublicationUnknown, PlatformError


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
        page = watch_page(context.new_page())
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
        except BaseException:
            capture_failure(page, self.id() + "-" + platform)
            raise
        finally:
            context.close()

    @contextmanager
    def vk_fixture(self, trigger='<button id="create">Создать</button>', menu=True, ignore_first=False):
        page=watch_page(self.browser.new_page())
        source='''<section data-testid="comment"><button onclick="window.wrong++">Создать</button>
          <div contenteditable="true" role="textbox" id="comment">Comment draft</div></section>
          <article><span>Пост</span></article>''' + trigger + '''
          <div role="menu" id="menu" hidden><button role="menuitem" id="post">Пост</button><button>Клип</button></div>
          <div role="dialog" id="composer" hidden><div contenteditable="true" role="textbox" id="text"></div>
            <input type="file" accept="image/*" multiple><div id="photos"></div>
            <button onclick="window.submits++">Опубликовать</button></div>
          <script>
          window.submits=window.wrong=window.clicks=0;
          const composer=document.getElementById('composer'),menu=document.getElementById('menu');
          document.getElementById('post').onclick=()=>{menu.hidden=true;composer.hidden=false;};
          document.querySelector('input[type=file]').onchange=e=>{
            for(const file of e.target.files){const img=new Image();img.src=URL.createObjectURL(file);document.getElementById('photos').append(img);}
          };
          </script>'''
        try:
            page.set_content(source)
            page.evaluate("""({menu,ignore})=>{document.getElementById('create').onclick=()=>{
                window.clicks++;if(ignore&&window.clicks===1)return;
                if(menu)document.getElementById('menu').hidden=false;
                else document.getElementById('composer').hidden=false;
            }}""",{'menu':menu,'ignore':ignore_first})
            yield page
        except BaseException:
            capture_failure(page,self.id()+'-vk')
            raise
        finally:
            page.close()

    def test_vk_create_without_old_testid_opens_fills_and_attaches_in_editor(self):
        with self.vk_fixture() as page, patch.object(page,'wait_for_load_state',side_effect=AssertionError('no networkidle wait')):
            editor,scope=V._open_composer(page,timeout_ms=5000)
            editor.fill('New VK post')
            V._attach(page,[self.image])
            page.locator('#photos img').wait_for()
            self.assertEqual(scope.get_attribute('id'),'composer')
            self.assertEqual(page.locator('#text').inner_text(),'New VK post')
            self.assertEqual(page.locator('#comment').inner_text(),'Comment draft')
            self.assertEqual(page.evaluate('window.wrong+window.submits'),0)

    def test_vk_direct_accessible_create_button_and_existing_editor(self):
        with self.vk_fixture('<div role="button" aria-label="Create" tabindex="0" id="create">+</div>',menu=False) as page:
            V._open_composer(page,timeout_ms=3000)
            V._open_composer(page,timeout_ms=3000)
            self.assertEqual(page.evaluate('window.clicks'),1)
            self.assertEqual(page.evaluate('window.submits'),0)

    def test_vk_retries_create_before_page_becomes_interactive(self):
        with self.vk_fixture('<button data-testid="group_publish_create_button" id="create">Создать</button>',ignore_first=True) as page:
            V._open_composer(page,timeout_ms=6000)
            self.assertEqual(page.evaluate('window.clicks'),2)
            self.assertEqual(page.evaluate('window.submits'),0)

    def test_vk_ambiguous_create_buttons_and_comment_dialog_are_not_used(self):
        with self.vk_fixture() as page:
            page.evaluate("""()=>{
                document.getElementById('create').insertAdjacentHTML('afterend','<button onclick="window.wrong++">Создать</button>');
                document.body.insertAdjacentHTML('beforeend','<div role="dialog"><div class="comment" contenteditable="true">Reply</div><button>Отправить</button></div>');
            }""")
            with self.assertRaisesRegex(PlatformError,'однозначная кнопка'):
                V._open_composer(page,timeout_ms=500)
            self.assertEqual(page.evaluate('window.clicks+window.wrong+window.submits'),0)

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
    sys.path.insert(0, str(ROOT))
    from tools.run_tests import report_result
    program = unittest.main(verbosity=2, exit=False)
    sys.exit(report_result(program.result))
