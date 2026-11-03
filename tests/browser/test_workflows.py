"""Isolated UI smoke tests: temporary data, mocked sender, no real accounts/posts."""
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from PIL import Image
import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


class InterfaceWorkflows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='crosspost-interface-')
        code = '''
import sys,json
from app import ui_state,updates,db
from app.publish import settings,runner
ui_state.update({'lang':'ru','onboardingDone':True})
settings.update('modules',{'collect':False,'news':False,'tagger':False,'forecast':False,'stats':True})
settings.update('discord',{'enabled':True,'webhooks':[{'name':'Новый пост','url':'https://example.invalid/hook'}]})
updates.check_async=lambda:None
runner.D.post=lambda *args,**kwargs:[]
with db.tx() as c:
 for i in range(65):
  c.execute('INSERT INTO publications(created,title,text,files,dests,draft) VALUES (?,?,?,?,?,?)',('2026-01-01T12:00:00+00:00','Новый пост' if i==0 else 'Post '+str(i),'','[]',json.dumps([{'key':'discord:0','platform':'discord','name':'Discord','state':'done'}]),'{}'))
from app.main import main
sys.argv=['app','--no-open','--no-worker','--port','0']
main()
'''
        cls.process = subprocess.Popen([sys.executable, '-u', '-c', code], cwd=ROOT,
                                       env=dict(os.environ, CROSSPOST_DATA=cls.tmp.name, CROSSPOST_NO_SCHEDULER='1'),
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        output = []
        while True:
            line = cls.process.stdout.readline()
            output.append(line)
            match = re.search(r'Crosspost [^:]+: (http://127.0.0.1:\d+)/', line)
            if match:
                cls.base = match[1]
                break
            if cls.process.poll() is not None:
                raise RuntimeError(''.join(output))
        cls.session = requests.Session()
        cls.session.trust_env = False
        token = re.search(r'window.API_TOKEN = "([^"]+)"', cls.session.get(cls.base).text)[1]
        cls.headers = {'X-Token': token}
        cls.playwright = sync_playwright().start()
        executable = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        cls.browser = cls.playwright.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.session.close()
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.process.stdout.close()
        cls.tmp.cleanup()

    def api(self, name, **args):
        result = self.session.post(self.base+'/api/'+name, headers=self.headers, json=args, timeout=30).json()
        self.assertTrue(result['ok'], result)
        return result['result']

    def test_editor_history_queue_settings_and_preservation(self):
        buffer = io.BytesIO()
        Image.new('RGB', (80, 60), '#356ca1').save(buffer, format='PNG')
        upload = self.session.post(self.base+'/upload?name=art.png', headers=self.headers, data=buffer.getvalue()).json()['result']
        draft = dict(files=[upload], title='Новый пост', text='Main text', variants={'en': {'text': 'Keep English'}},
                     multi=True, marks={upload['id']: 'en'}, rating='sfw', signature=False, hashtags=[])
        self.api('ui_set', changes={'draft': draft})
        page = self.browser.new_page(viewport={'width': 1360, 'height': 900})
        errors = []
        (ROOT/'test-artifacts').mkdir(exist_ok=True)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('dialog', lambda dialog: dialog.accept())
        try:
            page.goto(self.base, wait_until='networkidle')
            self.assertTrue(page.locator('#post-tools').is_hidden())
            self.assertEqual(page.locator('.postmain > .card h2').first.inner_text(), 'Файлы')
            page.locator('[data-panel="drafts"]').click()
            page.locator('#new-draft-name').fill('Новый пост')
            page.locator('[data-pa="draft-save"]').click()
            page.locator('#draft-name').wait_for()
            did = self.api('post_drafts')[0]['id']
            saved = self.api('post_draft_get', did=did)['draft']
            self.assertEqual(saved['variants']['en']['text'], 'Keep English')
            self.assertTrue(saved['multi'])
            self.assertEqual(saved['marks'][upload['id']], 'en')
            page.locator('[data-pa="post-panel-close"]').click()
            page.locator('#ptext').fill('Autosaved with panel closed')
            page.wait_for_function('CP.S.draft._saved.revision>1 && !CP.S.draftSaveError')
            self.assertEqual(self.api('post_draft_get', did=did)['draft']['text'], 'Autosaved with panel closed')
            page.locator('[data-panel="templates"]').click()
            page.locator('#template-name').fill('Template')
            page.locator('[data-pa="template-save"]').click()
            page.locator('[data-pa="template-apply"]').wait_for()
            page.locator('[data-pa="template-apply"]').click()
            page.wait_for_function('CP.S.postPanel===null')
            page.locator('[data-pa="post-check"]').click()
            page.wait_for_function('CP.S.composition && CP.S.readiness && !CP.S.pubBusy')
            self.assertTrue(page.locator('#post-tools').is_visible())
            page.locator('[data-pa="post-panel-close"]').click()
            wire = dict(draft, files=[upload['id']])
            row = self.api('post_schedule', draft=wire, run_at=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat())
            page.evaluate("async()=>{CP.S.view='history';await Pub.load('history');CP.render()}")
            self.assertEqual(page.locator('[role="tab"]').count(), 2)
            self.assertEqual(page.locator('[data-tab="calendar"]').get_attribute('aria-selected'), 'true')
            self.assertTrue(page.locator('#history-queue').is_hidden())
            page.wait_for_function('sid=>Object.values(CP.S.cal||{}).some(c=>(c.sched||[]).some(r=>r.sid===sid))', arg=row['id'])
            page.screenshot(path=str(ROOT/'test-artifacts'/'calendar-main.png'), full_page=True)
            page.locator('[data-pa="history-queue"]').first.click()
            self.assertEqual(page.locator('[data-tab="calendar"]').get_attribute('aria-selected'), 'true')
            page.screenshot(path=str(ROOT/'test-artifacts'/'queue.png'), full_page=True)
            page.locator('[data-pa="schedule-edit"][data-id="'+str(row['id'])+'"]').click()
            page.locator('[data-pa="schedule-save"]').first.wait_for()
            self.assertEqual(self.api('post_scheduled')[0]['state'], 'editing')
            page.locator('[data-pa="schedule-discard"]').click()
            page.wait_for_function('CP.S.view==="history"')
            page.locator('[data-tab="published"]').click()
            page.locator('[data-pa="history-next"]').click()
            page.wait_for_function('CP.S.historyOffset===30 && CP.S.historyPage.offset===30 && !CP.S.pubBusy')
            page.locator('[data-pa="history-next"]').click()
            page.wait_for_function('CP.S.historyPage.offset===60 && !CP.S.pubBusy')
            self.assertEqual(page.locator('.hitem').count(), 5)
            page.locator('.hitem').last.click()
            self.assertTrue(page.locator('.drawer .journal-body').is_hidden())
            page.locator('[data-pa="journal-toggle"]').click()
            page.wait_for_function('CP.S.publicationLog && !CP.S.pubBusy')
            self.assertTrue(page.locator('.journal-body').is_visible())
            page.locator('[data-pa="journal-toggle"]').click()
            self.assertTrue(page.locator('.journal-body').is_hidden())
            page.locator('.drawer [data-pa="pstat-close"]').click()
            page.evaluate("async()=>{CP.S.view='settings';CP.S.platSec='app';await Pub.load('settings');CP.render()}")
            page.locator('#settings-search').fill('вебхук')
            self.assertTrue(page.locator('#settings-results [data-tab="discord"]').is_visible())
            page.locator('#settings-results [data-tab="discord"]').click()
            setting=page.locator('[data-platform-texts-setting="discord"]')
            setting.wait_for()
            setting.check()
            page.wait_for_function('CP.S.pub.platform_texts_enabled.discord===true')
            # Failed checking stays in the panel, with diagnostics available and the draft intact.
            page.evaluate("async()=>{CP.S.view='post';await Pub.load('post');CP.render()}")
            extra=page.locator('details[data-detail-key="platform-texts"]')
            extra.locator('summary').click()
            page.locator('[data-platform-enable="discord"]').check()
            page.locator('[data-platform-title="discord"]').wait_for()
            self.assertTrue(extra.evaluate('e=>e.open'))
            extra.locator('summary').click()
            page.route('**/api/post_readiness', lambda route: route.fulfill(status=200, content_type='application/json', body='{"ok":false,"error":"ConnectionPool: timed out"}'))
            page.locator('[data-pa="post-check"]').click()
            page.locator('#post-tools .problem').wait_for()
            self.assertTrue(page.locator('#post-tools [data-tab="diag"]').is_visible())
            self.assertEqual(page.locator('#ptext').input_value(), 'Autosaved with panel closed')
            page.unroute('**/api/post_readiness')
            page.locator('[data-pa="post-panel-close"]').click()
            self.api('ui_set', changes={'lang': 'en'})
            page.reload(wait_until='networkidle')
            page.locator('[data-panel="drafts"]').click()
            self.assertEqual(page.locator('#draft-name').input_value(), 'Новый пост')
            self.assertTrue(page.locator('b[translate="no"]').filter(has_text='Новый пост').count())
            page.locator('[data-pa="post-panel-close"]').click()
            page.set_viewport_size({'width': 390, 'height': 844})
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
            page.screenshot(path=str(ROOT/'test-artifacts'/'editor-mobile.png'), full_page=True)
            self.assertFalse(errors, errors)
        except BaseException:
            (ROOT/'test-artifacts').mkdir(exist_ok=True)
            page.screenshot(path=str(ROOT/'test-artifacts'/'workflow.png'), full_page=True)
            raise
        finally:
            page.close()

    def test_first_post_guide_can_be_dismissed(self):
        self.api('ui_set', changes={'lang': 'ru'})
        page = self.browser.new_page()
        try:
            page.goto(self.base, wait_until='networkidle')
            page.evaluate("()=>{CP.store.set('onboardingDone',false);CP.S.draft={files:[],title:'',text:'',rating:null,exclude:[]};CP.S.plan={...CP.S.plan,configured:[]};CP.render()}")
            self.assertTrue(page.locator('.onboarding').is_visible())
            self.assertEqual(page.locator('.onboarding li').count(), 3)
            self.assertTrue(page.locator('.onboarding [data-tab="telegram"]').is_visible())
            page.locator('[data-pa="onboarding-dismiss"]').click()
            self.assertFalse(page.locator('.onboarding').count())
            self.assertTrue(page.evaluate("CP.store.get('onboardingDone',false)"))
        finally:
            page.close()

    def test_setup_guide_resumes_uses_real_settings_and_does_not_publish(self):
        self.api('ui_set', changes={'lang': 'ru', 'setupGuide': None})
        page = self.browser.new_page(viewport={'width':1360,'height':1000})
        errors, calls = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: calls.append(request.url.split('/api/')[-1]) if '/api/' in request.url else None)
        try:
            page.goto(self.base, wait_until='networkidle')
            page.evaluate("async()=>{CP.S.view='settings';await Pub.load('settings');CP.render()}")
            page.locator('[data-guide-action="open"]').click()
            guide=page.locator('.setup-guide')
            self.assertTrue(guide.is_visible())
            self.assertTrue(page.locator('[data-guide-platform="discord"]').is_checked())
            page.locator('[data-guide-platform="telegram"]').check()
            guide.screenshot(path=str(ROOT/'test-artifacts'/'setup-guide-platforms.png'))
            guide.locator('[data-guide-open="settings"][data-tab="discord"]').click()
            page.locator('[data-guide-action="expand"]').wait_for()
            self.assertTrue(page.locator('[data-platform-texts-setting="discord"]').is_visible())
            page.locator('[data-guide-action="expand"]').click()
            with page.expect_response(lambda response: '/api/ui_set' in response.url and response.request.post_data_json.get('changes',{}).get('setupGuide',{}).get('step')==1):
                page.locator('[data-guide-action="next"]').click()
            page.reload(wait_until='networkidle')
            page.evaluate("async()=>{CP.S.view='settings';await Pub.load('settings');CP.render()}")
            page.locator('[data-guide-action="open"]').click()
            self.assertEqual(page.locator('[data-guide-step="1"]').get_attribute('aria-current'),'step')
            page.locator('[data-guide-action="skip"]').click()
            self.assertEqual(page.locator('[data-guide-step="2"]').get_attribute('aria-current'),'step')
            guide.locator('[data-guide-open="queue"]').click()
            page.locator('#history-queue').wait_for()
            page.locator('#nav [data-view="settings"]').click()
            page.locator('[data-guide-action="expand"]').wait_for()
            page.locator('[data-guide-action="expand"]').click()
            page.locator('[data-guide-action="next"]').click()
            page.locator('.setup-guide [data-feat="trends"]').check()
            page.wait_for_function('CP.S.pub.modules.trends===true')
            self.assertTrue(self.api('modules')['trends'])
            page.locator('[data-guide-action="check"]').click()
            page.wait_for_function('CP.S.guideResult && !CP.S.guideChecking')
            self.assertFalse(page.evaluate('CP.S.guideResult.ready'))
            self.assertEqual(page.locator('.guide-result li').count(),2)
            page.locator('[data-guide-step="0"]').click()
            page.locator('[data-guide-platform="telegram"]').uncheck()
            page.locator('[data-guide-step="3"]').click()
            page.locator('[data-guide-action="check"]').click()
            page.wait_for_function('CP.S.guideResult && !CP.S.guideChecking')
            self.assertTrue(page.evaluate('CP.S.guideResult.ready'))
            guide.screenshot(path=str(ROOT/'test-artifacts'/'setup-guide-check.png'))
            page.locator('[data-guide-action="finish"]').click()
            self.assertFalse(guide.count())
            self.assertTrue(page.evaluate("CP.store.get('setupGuide',{}).done"))
            page.locator('[data-guide-action="open"]').click()
            self.assertEqual(page.locator('[data-guide-step="3"]').get_attribute('aria-current'),'step')
            page.set_viewport_size({'width':390,'height':844})
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
            self.assertFalse(set(calls)&{'post_publish','post_schedule','pipeline_add','pipeline_set'})
            self.assertFalse(errors,errors)
        except BaseException:
            page.screenshot(path=str(ROOT/'test-artifacts'/'setup-guide-failure.png'),full_page=True)
            raise
        finally:
            page.close()

    def test_translation_preserves_user_content_on_initial_render_and_mutation(self):
        page = self.browser.new_page()
        try:
            page.set_content('<button id="ui">Новый пост</button><b translate="no" id="title">Новый пост</b><textarea>Новый пост</textarea>')
            page.evaluate("window.UI_STATE={lang:'en'}")
            page.add_script_tag(path=str(ROOT/'app/web/lang_en.js'))
            page.add_script_tag(path=str(ROOT/'app/web/i18n.js'))
            self.assertEqual(page.locator('#ui').inner_text(), 'New post')
            self.assertEqual(page.locator('#title').inner_text(), 'Новый пост')
            page.locator('#title').evaluate("e=>e.textContent='Сохранить'")
            page.wait_for_timeout(50)
            self.assertEqual(page.locator('#title').inner_text(), 'Сохранить')
            messages=[]
            page.on('dialog', lambda dialog: (messages.append(dialog.message), dialog.dismiss()))
            page.evaluate("I18N.confirm('Новый пост','\\nНовый пост')")
            self.assertEqual(messages[-1], 'New post\nНовый пост')
        finally:
            page.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
