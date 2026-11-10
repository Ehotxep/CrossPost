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

    def test_error_motion_sorting_and_line_chart_respect_reduced_motion(self):
        page=self.browser.new_page(reduced_motion='no-preference')
        try:
            page.goto(self.base,wait_until='networkidle')
            page.evaluate("""()=>{
                CP.S.view='post';CP.S.postMode='progress';
                CP.S.pubJob={state:'running',progress:.4};
                CP.S.pubState={pub_id:999,title:'Test',dests:[
                    {key:'vk',platform:'vk',name:'VK',state:'running',details:[]},
                    {key:'discord',platform:'discord',name:'Discord',state:'running',details:[]}]};
                CP.render();
                CP.S.pubState.dests[0].state='error';CP.S.pubState.dests[0].outcome='not_sent';
                CP.S.pubState.dests[1].state='done';CP.render();
            }""")
            self.assertEqual(page.locator('[data-send-key]').first.get_attribute('data-send-key'),'discord')
            failed=page.locator('.just-failed .pb')
            self.assertEqual(failed.evaluate('el=>getComputedStyle(el).animationName'),'send-error-shake')
            page.emulate_media(reduced_motion='reduce')
            self.assertEqual(failed.evaluate('el=>getComputedStyle(el).animationName'),'none')
            page.emulate_media(reduced_motion='no-preference')
            page.evaluate("""()=>{
                window.testChart=document.body.appendChild(document.createElement('div'));
                testChart.style.width='640px';
                window.testSeries=[{name:'Test',color:'#8888ff',points:[{m:'01.26',v:100},{m:'02.26',v:200}]}];
                Charts.lineChart(testChart,testSeries);
            }""")
            self.assertGreater(page.evaluate("testChart.querySelector('path').getAnimations().length"),0)
            page.evaluate('Charts.lineChart(testChart,testSeries)')
            self.assertEqual(page.evaluate("testChart.querySelector('path').getAnimations().length"),0)
            page.emulate_media(reduced_motion='reduce')
            page.evaluate('testSeries[0].points[1].v=300;Charts.lineChart(testChart,testSeries)')
            self.assertEqual(page.evaluate("testChart.querySelector('path').getAnimations().length"),0)
        finally:
            page.close()

    def test_list_merge_and_split_are_visible_and_persistent(self):
        page = self.browser.new_page()
        page.on("dialog", lambda dialog: dialog.accept())
        atoms = []
        try:
            page.goto(self.base, wait_until="networkidle")
            page.evaluate("async()=>{CP.S.view='history';CP.S.histMode='published';CP.S.historyFilters={};CP.S.historyOffset=0;await Pub.load('history');CP.render()}")
            initial = self.api("post_history_search")["total"]
            page.get_by_role("button", name="Объединить посты", exact=True).click()
            boxes = page.locator("[data-history-select]")
            atoms = page.evaluate("()=>CP.S.hist.slice(0,2).flatMap(r=>r.atoms)")
            boxes.nth(0).check()
            boxes.nth(1).check()
            page.get_by_role("button", name="Принудительно объединить", exact=True).click()
            page.wait_for_function("count=>CP.S.historyPage.total===count", arg=initial-1)
            self.assertEqual(self.api("post_history_search")["total"], initial-1)
            page.get_by_role("button", name="Объединить посты", exact=True).click()
            page.locator(".hitem").filter(has_text="Объединено: 2").locator("input[type=checkbox]").check()
            page.get_by_role("button", name="Разъединить", exact=True).click()
            page.wait_for_function("count=>CP.S.historyPage.total===count", arg=initial)
        finally:
            if atoms:
                self.api("cal_split", atoms=atoms)
            page.close()

    def test_telegram_editor_sends_only_selected_destination(self):
        page = self.browser.new_page()
        saved = []
        def reply(route, value):
            route.fulfill(content_type="application/json", body=json.dumps({"ok":True,"result":value}))
        page.route("**/api/pub_stats", lambda r: reply(r, {"id":1,"dests":[{"key":"telegram:test","platform":"telegram","name":"Test channel","editable":True,"source_pub_id":1}]}))
        page.route("**/api/post_edit_prepare", lambda r: reply(r, {"pub_id":1,"key":"telegram:test","kind":"caption","text":"Old caption","exact":True,"revision":"revision"}))
        def save(route):
            saved.append(route.request.post_data_json)
            reply(route, {})
        page.route("**/api/post_edit_apply", save)
        try:
            page.goto(self.base, wait_until="networkidle")
            page.evaluate("async()=>{CP.S.view='history';await Pub.load('history');await Pub.openDrawer('pub:1')}")
            page.get_by_role("button", name="Редактировать в Telegram", exact=True).click()
            page.locator("#tg-edit-text").fill("Updated caption")
            page.get_by_role("button", name="Сохранить в Telegram", exact=True).click()
            page.wait_for_function("()=>CP.S.tgEdit===null")
            self.assertEqual(saved, [{"pub_id":1,"key":"telegram:test","text":"Updated caption","revision":"revision"}])
        finally:
            page.close()

    def test_missing_publication_dependency_shows_original_error_without_error_loop(self):
        page = self.browser.new_page()
        try:
            page.route("**/static/post-state.js", lambda route: route.abort())
            page.goto(self.base)
            panel = page.get_by_role("alert")
            panel.wait_for()
            self.assertIn("PostState", panel.inner_text())
            self.assertIn("/static/post-state.js", panel.inner_text())
            self.assertIn("Pub", panel.inner_text())
            self.assertEqual(page.locator("#acts").inner_text(), "")
            self.assertEqual(page.get_by_role("button", name="Перезагрузить интерфейс").count(), 1)
        finally:
            page.close()

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

    def test_update_channel_selector_changes_channel_without_installing(self):
        page = self.browser.new_page()
        calls = []
        try:
            page.goto(self.base, wait_until='domcontentloaded')
            page.wait_for_function('window.CP?.S.info && CP.S.pub')
            page.evaluate("async()=>{CP.S.view='settings';CP.S.platSec='app';await Pub.load('settings');CP.render()}")
            state = page.evaluate('CP.S.info.update')
            state.update(channel='experimental', current_channel='stable', latest=None, newer=False, show=False)
            def respond(route):
                calls.append(route.request.post_data_json)
                route.fulfill(status=200, content_type='application/json', body=json.dumps({'ok':True,'result':state}))
            page.route('**/api/update_channel', respond)
            page.locator('#update-channel').select_option('experimental')
            page.wait_for_function("CP.S.info.update.channel === 'experimental'")
            self.assertEqual(calls, [{'channel':'experimental'}])
            self.assertEqual(page.locator('#update-channel').input_value(), 'experimental')
            self.assertIn('резервную копию', page.locator('#update-channel').locator('..').inner_text())
        finally:
            page.close()

    def test_add_and_disable_reddit_community_from_settings(self):
        page = self.browser.new_page()
        try:
            page.goto(self.base, wait_until='domcontentloaded')
            page.wait_for_function('window.CP?.S.info && CP.S.pub')
            page.evaluate("async()=>{CP.S.view='settings';CP.S.platSec='reddit';await Pub.load('settings');CP.render()}")
            page.locator('#reddit-community-add').fill('https://www.reddit.com/r/ArtCommunity/submit/')
            page.locator('[data-social-action="add-community"]').click()
            page.wait_for_function("CP.S.pub.reddit.communities.ArtCommunity === true")
            page.locator('#bt-reddit-ArtCommunity').uncheck()
            page.wait_for_function("CP.S.pub.reddit.communities.ArtCommunity === false")
            self.assertFalse(self.api('pub_settings')['reddit']['communities']['ArtCommunity'])
        finally:
            page.close()

    def test_translation_columns_stay_aligned_with_many_platforms(self):
        page = self.browser.new_page(viewport={'width':1100, 'height':900})
        try:
            page.goto(self.base, wait_until='domcontentloaded')
            page.wait_for_function('window.CP?.S.info && CP.S.pub')
            page.evaluate("""() => {
                CP.S.view = 'post'; CP.S.postMode = 'edit';
                CP.S.pub.modules.translation = true;
                for (const key of Object.keys(CP.S.pub.text_lang)) CP.S.pub.text_lang[key] = 'en';
                CP.render();
            }""")
            page.wait_for_function("""() => {
                const ids = ['ptitle', 'ptitle_en', 'ptext', 'ptext_en'];
                const fields = ids.map(id => document.getElementById(id));
                if (fields.some(el => !el || !el.getClientRects().length)) return false;
                const y = fields.map(el => el.getBoundingClientRect().top);
                return Math.abs(y[0] - y[1]) <= 1 && Math.abs(y[2] - y[3]) <= 1;
            }""")
            self.assertGreater(page.locator('[data-pa="tlang"]').count(), 4)
            page.set_viewport_size({'width':390, 'height':844})
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
        finally:
            page.close()

    def test_view_transition_runs_only_on_navigation_and_respects_reduced_motion(self):
        page = self.browser.new_page(reduced_motion='no-preference')
        try:
            page.goto(self.base, wait_until='domcontentloaded')
            page.wait_for_function('window.CP?.S.info && CP.S.pub')
            page.evaluate("""() => {
                CP.render();
                window.viewAnimations = 0;
                const main = document.querySelector('#main');
                const original = main.animate.bind(main);
                main.animate = (...args) => { window.viewAnimations++; return original(...args); };
                CP.S.view = 'settings'; CP.S.platSec = 'app'; CP.render();
            }""")
            self.assertEqual(page.evaluate('window.viewAnimations'), 1)
            page.evaluate('CP.render()')
            self.assertEqual(page.evaluate('window.viewAnimations'), 1)
            page.emulate_media(reduced_motion='reduce')
            page.evaluate("CP.S.view='post'; CP.render()")
            self.assertEqual(page.evaluate('window.viewAnimations'), 1)
            self.assertTrue(page.locator('#ptitle').is_visible())
        finally:
            page.close()

    def test_interface_motion_respects_reduced_motion_preference(self):
        page = self.browser.new_page(reduced_motion='no-preference')
        try:
            page.goto(self.base, wait_until='domcontentloaded')
            page.wait_for_function('window.CP?.S.info && CP.S.pub')
            page.evaluate("CP.toast('Motion test', 'info')")
            toast = page.locator('.toast').last
            self.assertEqual(toast.evaluate("el => getComputedStyle(el).animationName"), 'toast-enter')
            page.emulate_media(reduced_motion='reduce')
            self.assertEqual(toast.evaluate("el => getComputedStyle(el).animationName"), 'none')
            self.assertEqual(page.locator('#nav button').first.evaluate("el => getComputedStyle(el).transitionDuration"), '0s')
            page.evaluate("async()=>{CP.S.view='settings';CP.S.platSec='app';await Pub.load('settings');CP.render()}")
            toggle = page.locator('.tgl input[type=checkbox]').first
            self.assertEqual(toggle.evaluate("el => getComputedStyle(el, '::after').transitionDuration"), '0s')
        finally:
            page.close()

    def test_publication_animation_tracks_real_progress_and_reduced_motion(self):
        page = self.browser.new_page(reduced_motion='no-preference')
        try:
            page.goto(self.base, wait_until='networkidle')
            page.wait_for_function('window.CP?.S.info && CP.S.pub')
            page.evaluate("""() => {
                CP.S.view = 'post'; CP.S.postMode = 'progress';
                CP.S.pubState = {title:'Test',rating:'sfw',dests:[
                    {key:'discord:0',name:'Discord',state:'running',progress:.42,details:[]},
                    {key:'vk',name:'VK',state:'done',details:[]},
                    {key:'reddit:foo',platform:'reddit',name:'Reddit · foo',state:'error',outcome:'unknown',details:[]},
                    {key:'reddit:bar',platform:'reddit',name:'Reddit · bar',state:'error',outcome:'not_sent',details:[]},
                    {key:'telegram:1',platform:'telegram',name:'Telegram · one',state:'queued',details:[]},
                    {key:'telegram:2',platform:'telegram',name:'Telegram · two',state:'done',note:'скорее всего, вышла, но ссылку найти не удалось',details:[]}
                ]};
                CP.S.pubJob = {state:'running',progress:.38}; CP.render();
            }""")
            overall = page.locator('[role=progressbar][aria-label="Публикация"]')
            row = page.locator('[role=progressbar][aria-label="Discord"]')
            self.assertEqual(page.locator('.send-dest').count(), 6)
            self.assertEqual(page.locator('.send-dest.done').count(), 1)
            self.assertEqual(page.locator('.send-dest.uncertain').count(), 2)
            self.assertEqual(page.locator('.send-dest.error').count(), 1)
            self.assertIn('VK: опубликовано', page.locator('.send-dest.done').get_attribute('aria-label'))
            self.assertEqual(overall.get_attribute('aria-valuenow'), '38')
            self.assertEqual(row.get_attribute('aria-valuenow'), '42')
            self.assertEqual(overall.locator('div').evaluate("el=>el.style.width"), '38%')
            self.assertEqual(row.locator('div').evaluate("el=>getComputedStyle(el,'::after').animationName"), 'progress-glint')
            self.assertEqual(page.locator('.prow.done .pic').first.evaluate("el=>getComputedStyle(el).animationName"), 'none')
            page.emulate_media(reduced_motion='reduce')
            self.assertEqual(row.locator('div').evaluate("el=>getComputedStyle(el,'::after').animationName"), 'none')
            self.assertEqual(page.locator('.prow.running .pic').evaluate("el=>getComputedStyle(el).animationName"), 'none')
            page.evaluate("CP.S.pubJob={state:'done'};CP.render()")
            self.assertEqual(page.locator('.send-dest.done').count(), 1)
            self.assertEqual(page.locator('.send-dest.uncertain').count(), 2)
        finally:
            page.close()

    def test_sending_celebrates_only_new_confirmed_success(self):
        page = self.browser.new_page(reduced_motion='no-preference')
        try:
            page.goto(self.base, wait_until='networkidle')
            page.evaluate("""() => {
                CP.S.view='post'; CP.S.postMode='progress';
                CP.S.pubState={pub_id:987,title:'Test',rating:'sfw',dests:[
                    {key:'discord:0',platform:'discord',name:'Discord',state:'running',details:[]},
                    {key:'vk',platform:'vk',name:'VK',state:'done',details:[]},
                    {key:'reddit:r',platform:'reddit',name:'Reddit',state:'running',details:[]}
                ]}; CP.S.pubJob={state:'running',progress:.42}; CP.render();
            }""")
            self.assertEqual(page.locator('.send-flight').get_attribute('style'), 'left:42%')
            self.assertEqual(page.locator('.send-dest.just-sent').count(), 0)
            page.evaluate("""() => {
                CP.S.pubState.dests[0].state='done';
                CP.S.pubState.dests[2].state='error';CP.S.pubState.dests[2].outcome='unknown';
                CP.render();
            }""")
            self.assertEqual(page.locator('.send-dest.just-sent').count(), 1)
            self.assertIn('Discord', page.locator('.send-dest.just-sent').get_attribute('aria-label'))
            self.assertEqual(page.locator('.send-dest.uncertain.just-sent').count(), 0)
            self.assertEqual(page.locator('.send-dest.just-sent').evaluate("el=>getComputedStyle(el,'::before').animationName"), 'sent-ring')
            page.emulate_media(reduced_motion='reduce')
            self.assertEqual(page.locator('.send-dest.just-sent').evaluate("el=>getComputedStyle(el,'::before').animationName"), 'none')
            self.assertEqual(page.locator('.send-flight svg').evaluate("el=>getComputedStyle(el).animationName"), 'none')
            page.evaluate('CP.render()')
            self.assertEqual(page.locator('.send-dest.just-sent').count(), 0)
        finally:
            page.close()

    def test_history_hover_motion_respects_reduced_motion(self):
        page = self.browser.new_page(reduced_motion='no-preference')
        try:
            page.set_content('<div class="hitem" style="width:400px;height:60px"><span>Post</span><span class="chev">›</span></div>')
            page.add_style_tag(path=str(ROOT/'app/web/style.css'))
            page.locator('.hitem').hover()
            page.wait_for_function("getComputedStyle(document.querySelector('.hitem .chev')).transform !== 'none'")
            page.emulate_media(reduced_motion='reduce')
            self.assertEqual(page.locator('.hitem .chev').evaluate("el=>getComputedStyle(el).transform"), 'none')
            self.assertEqual(page.locator('.hitem .chev').evaluate("el=>getComputedStyle(el).transitionDuration"), '0s')
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

    def test_settings_autosave_preserves_focus_hidden_fields_and_recovers_errors(self):
        self.api('ui_set', changes={'lang':'ru','settingsPending':None})
        self.api('pub_save',section='signatures',changes={'platforms':{'telegram':{'en':{'sfw':'Keep hidden English'}}}})
        page=self.browser.new_page(viewport={'width':1360,'height':900})
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('dialog',lambda dialog:dialog.accept())
        try:
            page.goto(self.base,wait_until='networkidle')
            page.evaluate("async()=>{CP.S.view='settings';CP.S.platSec='signatures';await Pub.load('settings');CP.render()}")
            signature=page.locator('#sig-telegram-sfw')
            signature.fill('Autosaved signature')
            page.wait_for_function("CP.S.pub.signatures.platforms.telegram.sfw==='Autosaved signature'")
            self.assertEqual(page.evaluate('document.activeElement.id'),'sig-telegram-sfw')
            saved=self.api('pub_settings')['signatures']['platforms']['telegram']
            self.assertEqual(saved['sfw'],'Autosaved signature')
            self.assertEqual(saved['en']['sfw'],'Keep hidden English')
            self.assertFalse(page.locator('[data-pa="sig-save"]').is_visible())
            signature.fill('Saved when leaving immediately')
            page.locator('[data-psec="hashtags"]').click()
            page.locator('#htC').wait_for()
            self.assertEqual(self.api('pub_settings')['signatures']['platforms']['telegram']['sfw'],'Saved when leaving immediately')
            page.locator('#htOn').uncheck()
            page.locator('#htC').fill('autosave crosspost')
            page.wait_for_function("CP.S.pub.hashtags.constant.includes('crosspost') && !CP.S.pub.hashtags.enabled")
            page.locator('[data-psec="light"]').click()
            quality=page.locator('#lq')
            quality.wait_for()
            previous=self.api('pub_settings')['light']['quality']
            quality.fill('')
            self.assertTrue(page.locator('#settings-save-error').is_visible())
            self.assertEqual(self.api('pub_settings')['light']['quality'],previous)
            quality.fill('83')
            page.wait_for_function('CP.S.pub.light.quality===83')
            self.assertFalse(page.locator('#settings-save-error').count())
            page.locator('[data-psec="signatures"]').click()
            signature.wait_for()
            page.route('**/api/pub_save',lambda route:route.fulfill(status=200,content_type='application/json',body='{"ok":false,"error":"Save unavailable"}'))
            signature.fill('Recover this edit')
            page.locator('#settings-save-error [data-settings-save-retry]').wait_for()
            page.evaluate('CP.render()')
            self.assertEqual(signature.input_value(),'Recover this edit')
            self.assertTrue(page.locator('#settings-save-error').is_visible())
            page.unroute('**/api/pub_save')
            page.locator('[data-settings-save-retry]').click()
            page.wait_for_function("CP.S.pub.signatures.platforms.telegram.sfw==='Recover this edit'")
            self.assertFalse(page.locator('#settings-save-error').count())
            page.route('**/api/pub_save',lambda route:route.fulfill(status=200,content_type='application/json',body='{"ok":false,"error":"Save unavailable"}'))
            signature.fill('Recovered after reload')
            page.locator('#settings-save-error').wait_for()
            page.unroute('**/api/pub_save')
            page.reload(wait_until='domcontentloaded')
            page.wait_for_function("CP.S.pub?.signatures.platforms.telegram.sfw==='Recovered after reload'")
            self.assertEqual(self.api('pub_settings')['signatures']['platforms']['telegram']['sfw'],'Recovered after reload')
            self.api('modules_set',key='translation',on=True)
            page.evaluate("async()=>{await CP.loadInfo();CP.S.view='settings';CP.S.platSec='translate';await Pub.load('settings');CP.render()}")
            calls=[]
            page.on('request',lambda request:calls.append(request.post_data_json) if request.url.endswith('/api/pub_save') else None)
            page.locator('#trbase').fill('https://example.invalid/v1')
            page.locator('#trmodel').fill('example-model')
            page.locator('#trkey').fill('not-saved-automatically')
            page.wait_for_function("CP.S.pub.translate.model==='example-model'")
            self.assertFalse(any('api_key' in call.get('changes',{}) for call in calls))
            self.assertFalse(self.api('pub_settings')['translate']['has_key'])
            self.assertEqual(page.locator('#trkey').input_value(),'not-saved-automatically')
            self.assertFalse(errors,errors)
        except BaseException:
            page.screenshot(path=str(ROOT/'test-artifacts'/'settings-autosave-failure.png'),full_page=True)
            raise
        finally:
            page.close()
            self.api('modules_set',key='translation',on=False)

    def test_setup_guide_resumes_uses_real_settings_and_does_not_publish(self):
        self.api('ui_set', changes={'lang': 'ru', 'setupGuide': None})
        page = self.browser.new_page(viewport={'width':1360,'height':1000})
        errors, calls = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: calls.append(request.url.split('/api/')[-1]) if '/api/' in request.url else None)
        try:
            page.goto(self.base, wait_until='domcontentloaded')
            page.wait_for_function('window.CP?.S.info && CP.S.pub')
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
            page.reload(wait_until='domcontentloaded')
            page.wait_for_function('window.CP?.S.info && CP.S.pub')
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
