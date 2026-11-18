"""Experimental DeviantArt paid publishing via its visible editor.

Only public UI controls are used. Any ambiguous/missing paid-access control stops
before Submit. No API-publication fallback, automatic login, or submit retries.
"""
import hashlib
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlparse

from app.publish import browser as B
from app.publish.da_access import validate_changes
from core.common import NotPublishedError, PublicationUnknown

SUBMIT = 'https://www.deviantart.com/submit/deviation'
TITLE = re.compile(r'^(Title|Deviation title|Название|Заголовок)\s*\*?$',re.I)
SEND = re.compile(r'^(Submit|Submit now|Publish|Publish now|Опубликовать|Отправить)$',re.I)
PREMIUM = 'Sell as Premium Download'
SUBSCRIBERS = 'Submit to your subscribers'


def _find(locators, required=True):
    for loc in locators:
        try:
            visible=[loc.nth(i) for i in range(min(loc.count(),40)) if loc.nth(i).is_visible()]
            if len(visible)==1:
                return visible[0]
        except Exception:
            continue
    if required:
        raise NotPublishedError('Форма DeviantArt изменилась: нужное поле не найдено однозначно')
    return None


def _title(scope, required=True):
    return _find([scope.get_by_role('textbox',name=TITLE),scope.get_by_label(TITLE),
                  scope.locator('input[name="title"], textarea[name="title"]')],required)


def _section(page, title, selector):
    label=_find([page.get_by_text(title,exact=True)])
    node=label
    for _ in range(9):
        if (selector(node) if callable(selector) else node.locator(selector).count()):
            # Never widen a sales card into the entire form.
            other=SUBSCRIBERS if title==PREMIUM else PREMIUM
            if node.get_by_text(other,exact=True).count():
                break
            return node
        node=node.locator('..')
    raise NotPublishedError(f'DeviantArt: не найден отдельный блок «{title}»')


def _state(control):
    for attr in ('aria-checked','aria-selected'):
        value=control.get_attribute(attr)
        if value in ('true','false'):
            return value=='true'
    if control.evaluate("e=>e.matches('input[type=checkbox],input[type=radio]')"):
        return control.is_checked()
    raise NotPublishedError('DeviantArt: состояние переключателя доступа не подтверждено')


def _set(control,value):
    if _state(control)!=value:
        control.click(timeout=5000)
    if _state(control)!=value:
        raise NotPublishedError('DeviantArt не сохранил переключатель доступа')


def _premium_control(page):
    direct=_find([page.get_by_role('switch',name=re.compile('^'+re.escape(PREMIUM)+'$',re.I)),
                  page.get_by_role('checkbox',name=re.compile('^'+re.escape(PREMIUM)+'$',re.I))],False)
    if direct is not None:
        return direct
    card=_section(page,PREMIUM,'input[type=checkbox],[role=switch]')
    return _find([card.get_by_role('switch'),card.get_by_role('checkbox')])


def _upload(page,scope,path):
    """Choose one file input in the bounded upload area, or its explicit chooser."""
    path=Path(path)
    previews=scope.locator('img[src^="blob:"],img[src^="data:image"],[data-testid*=preview] img,[class*=preview] img')
    before=sum(previews.nth(i).is_visible() for i in range(min(previews.count(),40)))
    inputs=scope.locator('input[type=file]')
    if inputs.count()==1:
        inputs.set_input_files(str(path),timeout=10000)
    else:
        button=_find([scope.get_by_role('button',name=re.compile(r'^(Upload File|Upload (Art|Image|Deviation)|Choose File|Загрузить файл|Загрузить изображение)$',re.I))])
        with page.expect_file_chooser(timeout=5000) as chooser:
            button.click(timeout=5000)
        chooser.value.set_files(str(path))
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        errors=scope.locator('[role=alert]').filter(visible=True)
        if errors.count() and any(re.search(r'error|failed|ошиб|не удалось',text,re.I) for text in errors.all_text_contents()):
            raise NotPublishedError('DeviantArt сообщил об ошибке загрузки файла')
        filename=scope.get_by_text(re.compile(re.escape(path.name)))
        preview=scope.locator('img[src^="blob:"],img[src^="data:image"],[data-testid*=preview] img,[class*=preview] img')
        visible_file=any(filename.nth(i).is_visible() for i in range(min(filename.count(),20)))
        visible_preview=sum(preview.nth(i).is_visible() and preview.nth(i).evaluate('e=>e.complete&&e.naturalWidth>0') for i in range(min(preview.count(),40)))>before
        busy=scope.locator('[role=progressbar],[aria-busy=true]').filter(visible=True).count()
        if not busy and (visible_file or visible_preview):
            return
        page.wait_for_timeout(250)
    raise NotPublishedError('DeviantArt: загрузка файла не подтверждена за 2 минуты')


def _assert_empty(page):
    fields=[_title(page,False),_find([page.get_by_role('textbox',name=re.compile(r'^(Description|Artist.?s? comments|Описание)$',re.I)),
                                  page.locator('textarea[name=description]')],False)]
    for field in fields:
        if field is not None:
            value=field.input_value() if field.evaluate('e=>e.matches("input,textarea")') else field.inner_text()
            if value.strip():raise NotPublishedError('В редакторе DeviantArt есть черновик: сохраните или закройте его вручную')
    preview=page.locator('[data-testid*=preview] img,[class*=preview] img')
    if any(preview.nth(i).is_visible() for i in range(min(preview.count(),40))):
        raise NotPublishedError('В редакторе DeviantArt уже есть изображение: сохраните или закройте черновик вручную')


def _discard_owned_draft(page,title):
    """Best effort, only our newly opened unsubmitted editor, never an old draft."""
    try:
        field=_title(page,False)
        if field is None or field.input_value()!=title:return False
        button=_find([page.get_by_role('button',name=re.compile(r'^(Discard draft|Delete draft|Удалить черновик)$',re.I))],False)
        if button is None:return False
        button.click(timeout=3000)
        confirm=_find([page.get_by_role('dialog').get_by_role('button',name=re.compile(r'^(Discard|Delete draft|Удалить черновик)$',re.I))],False)
        if confirm is not None:confirm.click(timeout=3000)
        field=_title(page,False)
        return field is None or not field.input_value().strip()
    except Exception:
        return False



ROBOT_ERROR=('DeviantArt отклонил проверку на робота в браузере CrossPost. '
             'Платная отправка через этот браузер сейчас недоступна. '
             'Для обычных публикаций выберите «Обычная · API»: цена и браузерный вход не нужны.')


def _robot_state(page):
    """Recognise the visible challenge; never click/solve or retry it."""
    def visible(frame,pattern):
        loc=frame.get_by_text(re.compile(pattern,re.I))
        return any(loc.nth(i).is_visible() for i in range(min(loc.count(),8)))
    state=''
    for frame in page.frames[:8]:
        try:
            denied=visible(frame,r'Access to this page has been denied because')
            challenge=denied or visible(frame,r'^Confirm\s+you.?re\s+not\s+a\s+robot\.?$') or visible(frame,r'^PRESS\s*&\s*HOLD$')
            if challenge:
                state='challenge'
                if denied and visible(frame,r'^Please try again\.?$'):return 'blocked'
        except Exception:
            continue
    return state

def _wait_form(page,timeout=30):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if _robot_state(page):raise NotPublishedError(ROBOT_ERROR)
        if '/users/login' in page.url or '/join' in page.url:
            raise NotPublishedError('Войдите в DeviantArt через «Войти в браузере» в настройках')
        if _title(page,False) is not None or page.locator('input[type=file]').count():
            return
        page.wait_for_timeout(200)
    raise NotPublishedError('Редактор DeviantArt не открылся: проверьте вход в браузере')


def login(report,timeout=600):
    with B.session(platform='deviantart',headless=False,wait=20) as (ctx,page):
        page.goto(SUBMIT,wait_until='commit',timeout=30000)
        report(progress=.2,message='Войдите в DeviantArt в открывшемся браузере и откройте создание работы')
        end=time.monotonic()+timeout
        challenge_seen=False
        while time.monotonic()<end:
            if not ctx.pages:
                raise NotPublishedError('Окно входа DeviantArt закрыто')
            robot=_robot_state(page)
            if robot=='blocked':raise NotPublishedError(ROBOT_ERROR)
            if robot and not challenge_seen:
                challenge_seen=True
                end=min(end,time.monotonic()+120)
                report(progress=.2,message='DeviantArt показал проверку на робота. Пройдите её вручную в окне браузера; ожидание до 2 минут. Для обычных публикаций этот вход не нужен.')
            if robot:
                page.wait_for_timeout(500);continue
            host=urlparse(page.url).hostname
            if host in ('www.deviantart.com','deviantart.com') and '/submit' in urlparse(page.url).path:
                # Presence of an editable submission form, not merely any DA cookie.
                if _title(page,False) is not None or page.locator('input[type=file]').count():
                    B.save_cookies(ctx)
                    report(progress=1,message='Редактор DeviantArt доступен, вход сохранён')
                    return
            page.wait_for_timeout(500)
        raise NotPublishedError(ROBOT_ERROR if challenge_seen else 'Вход в DeviantArt не завершён за 10 минут')


def _body(scope,text):
    if not text:
        return
    editor=_find([scope.get_by_role('textbox',name=re.compile(r'^(Description|Artist.?s? comments|Описание)\s*\*?$',re.I)),
                  scope.get_by_label(re.compile(r'^(Description|Artist.?s? comments|Описание)$',re.I)),
                  scope.locator('textarea[name=description],[contenteditable=true][role=textbox]')])
    editor.fill(text)
    value=editor.input_value() if editor.evaluate('e=>e.matches("input,textarea")') else editor.inner_text()
    if value!=text:
        raise NotPublishedError('DeviantArt: описание не сохранилось')


def _option(page,pattern,value,required=True):
    control=_find([page.get_by_role('checkbox',name=pattern),page.get_by_role('switch',name=pattern)],required)
    if control is not None:
        _set(control,value)


def _tags(page,tags):
    if not tags:
        return
    field=_find([page.get_by_role('textbox',name=re.compile(r'^Tags?\s*\*?$',re.I)),page.get_by_label(re.compile(r'^Tags?$',re.I))])
    # A comma cannot implicitly submit an HTML form (unlike Enter).
    for tag in tags:
        field.fill(tag)
        field.press('Comma')
    field.press('Tab')
    for tag in tags:
        chip=page.locator('[data-testid*=tag], [class*=tag], [class*=Tag]').get_by_text(tag,exact=True)
        if not any(chip.nth(i).is_visible() for i in range(min(chip.count(),40))):
            raise NotPublishedError('DeviantArt: добавление тегов не подтверждено')


def _tiers(page,names):
    card=_section(page,SUBSCRIBERS,lambda s:s.locator('select,[role=combobox],[aria-haspopup]').count() or s.get_by_text('Choose Tier',exact=True).count())
    native=card.locator('select')
    if native.count()==1:
        opts=native.locator('option').all_text_contents()
        missing=set(names)-set(opts)
        if missing:
            raise NotPublishedError('Выбранные тиры не найдены в аккаунте DeviantArt')
        native.select_option(label=names)
        return lambda: set(native.locator('option:checked').all_text_contents())==set(names)
    trigger=_find([card.get_by_role('combobox'),card.get_by_role('button',name=re.compile(r'Choose Tier|Selected|Tier|Выбрать уровень',re.I)),
                   card.locator('[aria-haspopup]')])
    choices=card.locator('input[type=checkbox],[role=checkbox],[role=option]').filter(visible=True)
    if not choices.count():
        trigger.click()
    popup=_find([page.get_by_role('listbox'),page.get_by_role('menu')],False)
    scope=popup if popup is not None else card
    choices=scope.locator('input[type=checkbox],[role=checkbox],[role=option]').filter(visible=True)
    if not choices.count():
        if not names and re.fullmatch(r'\s*Choose Tier\s*',trigger.inner_text()):
            trigger.click();return lambda:bool(re.fullmatch(r'\s*Choose Tier\s*',trigger.inner_text()))
        raise NotPublishedError('DeviantArt: список тиров не открылся')
    named={}
    for i in range(choices.count()):
        control=choices.nth(i)
        label=control.get_attribute('aria-label')
        if not label:
            label=control.evaluate("e=>e.labels?.length?[...e.labels].map(x=>x.textContent.trim()).join(' '):(e.closest('label')?.textContent||e.parentElement.textContent).trim()")
        label=' '.join((label or '').split())
        if not label or label in named:
            raise NotPublishedError('DeviantArt: названия тиров неоднозначны')
        named[label]=control
    if set(names)-set(named):
        raise NotPublishedError('Выбранные тиры не найдены в аккаунте DeviantArt')
    for name,control in named.items():
        _set(control,name in names)
    def verify():
        return {name for name,control in named.items() if _state(control)}==set(names)
    if not verify():
        raise NotPublishedError('DeviantArt: выбранные тиры не подтверждены')
    # Close a popover that would otherwise cover the final Submit button. Native
    # selects above do not need this. Re-open to read current controls before submit.
    trigger.click()
    def verify_closed():
        trigger.click()
        try:
            return verify()
        finally:
            trigger.click()
    return verify_closed



def read_tiers(page):
    """Read public tier controls without changing the user's selection or draft."""
    card=_section(page,SUBSCRIBERS,lambda s:s.locator('select,[role=combobox],[aria-haspopup]').count() or s.get_by_text('Choose Tier',exact=True).count())
    native=card.locator('select')
    if native.count()==1:
        names=native.locator('option').evaluate_all("els=>els.filter(e=>e.value&&!e.disabled).map(e=>e.textContent.trim())")
    else:
        trigger=_find([card.get_by_role('combobox'),card.get_by_role('button',name=re.compile(r'Choose Tier|Selected|Tier|Выбрать уровень',re.I)),card.locator('[aria-haspopup]')])
        opened=False
        try:
            if not card.locator('input[type=checkbox],[role=checkbox],[role=option]').filter(visible=True).count():
                trigger.click();opened=True
            popup=_find([page.get_by_role('listbox'),page.get_by_role('menu')],False)
            controls=(popup if popup is not None else card).locator('input[type=checkbox],[role=checkbox],[role=option]').filter(visible=True)
            if not controls.count():raise NotPublishedError('DeviantArt: список тиров не открылся или пуст')
            names=controls.evaluate_all("els=>els.map(e=>(e.getAttribute('aria-label')||(e.labels?.length?[...e.labels].map(l=>l.textContent).join(' '):e.closest('label')?.textContent||e.textContent)).trim().replace(/\\s+/g,' '))")
        finally:
            if opened:trigger.click()
    if not names or any(not n or len(n)>100 for n in names) or len(names)!=len(set(names)):
        raise NotPublishedError('DeviantArt: список тиров пуст или неоднозначен')
    return names


def load_tiers(cfg,report):
    if not cfg.get('browser_ok'):raise NotPublishedError('Сначала войдите в DeviantArt через браузер')
    with B.session(platform='deviantart',headless=not cfg.get('show_window',True),wait=20) as (_,page):
        report(message='Читаю доступные тиры DeviantArt без публикации')
        page.goto(SUBMIT,wait_until='domcontentloaded',timeout=30000)
        _wait_form(page)
        return read_tiers(page)

def _price(page):
    card=_section(page,PREMIUM,'input[type=number],input[inputmode=decimal],input[name*=price]')
    return _find([card.get_by_label(re.compile(r'^Price\s*\*?$',re.I)),
                  card.locator('input[type=number],input[inputmode=decimal],input[name*=price]')])


def _link(url):
    parsed=urlparse(url)
    if parsed.scheme=='https' and parsed.hostname in ('www.deviantart.com','deviantart.com') and re.fullmatch(r'/[^/]+/art/[^/]+-\d+/?',parsed.path):
        return parsed._replace(query='',fragment='').geturl()
    return ''


def _confirm(page,timeout=60):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        link=_link(page.url)
        if not link:
            links=page.locator('[data-testid=submit-success] a[href]').evaluate_all('els=>els.map(e=>e.href)')
            found={_link(url) for url in links}-{''}
            if len(found)==1:link=found.pop()
        if link:return link
        page.wait_for_timeout(300)
    raise PublicationUnknown('DeviantArt: отправка нажата, но результат не подтверждён; проверьте сайт перед повтором')


def _gallery(page,cfg):
    if not cfg.get('gallery_id'):
        return
    field=_find([page.get_by_role('combobox',name=re.compile(r'^(Gallery|Gallery folder|Папка галереи)$',re.I)),
                  page.get_by_label(re.compile(r'^(Gallery|Gallery folder|Папка галереи)$',re.I))])
    if field.evaluate('e=>e.tagName==="SELECT"'):
        options=field.locator('option').evaluate_all('els=>els.map(e=>({value:e.value,label:e.textContent.trim()}))')
        match=[o for o in options if o['value']==cfg['gallery_id'] or o['label']==cfg.get('gallery_name')]
        if len(match)!=1:raise NotPublishedError('DeviantArt: папка галереи не найдена однозначно')
        field.select_option(value=match[0]['value'])
        if field.input_value()!=match[0]['value']:raise NotPublishedError('DeviantArt: папка галереи не сохранилась')
    else:
        field.click()
        _find([page.get_by_role('option',name=cfg.get('gallery_name',''),exact=True)]).click()
        if cfg.get('gallery_name') not in field.inner_text():raise NotPublishedError('DeviantArt: папка галереи не подтверждена')


def post(page,cfg,preview,original,title,text,tags,rating,dry_run=False):
    """One work. The caller owns the isolated browser session and cookie storage."""
    from decimal import Decimal,InvalidOperation
    cfg=validate_changes(cfg)
    from app.publish.da_access import blocked_reason,requested
    reason=blocked_reason(cfg)
    if reason or not requested(cfg):
        raise NotPublishedError(reason or 'Выберите платное скачивание или тиры DeviantArt')
    submitted=False
    stage='открытие редактора'
    try:
        page.goto(SUBMIT,wait_until='domcontentloaded',timeout=30000)
        _wait_form(page)
        _assert_empty(page)
        stage='загрузка превью'
        _upload(page,page,preview)
        field=_title(page)
        existing=field.input_value().strip()
        if existing and existing not in (Path(preview).name,Path(preview).stem,'Untitled'):
            raise NotPublishedError('DeviantArt восстановил другой черновик: автоматическая замена остановлена')
        field.fill(title)
        _body(page,text)
        _tags(page,tags)
        _gallery(page,cfg)
        _option(page,re.compile(r'^Allow comments$',re.I),bool(cfg.get('allow_comments',True)),required=not cfg.get('allow_comments',True))
        stage='отметки работы'
        _option(page,re.compile(r'AI.?generated|Created (using|with) AI|Made with AI|Создано.*ИИ',re.I),bool(cfg.get('is_ai_generated',True)))
        _option(page,re.compile(r'NoAI|Do not.*(AI|training)|Запретить.*ИИ',re.I),bool(cfg.get('noai',True)))
        _option(page,re.compile(r'^Mature( content)?|Содержит.*18',re.I),rating=='erotic')
        if rating=='erotic':
            level=_find([page.get_by_role('radio',name=re.compile(r'^Moderate$',re.I))],False)
            if level is not None and not level.is_checked():level.check()
            _option(page,re.compile(r'^Nudity$',re.I),True)
        stage='настройка платного доступа'
        premium=_premium_control(page)
        _set(premium,bool(cfg.get('premium_download')))
        # A public free-download switch must not leak the original.
        _option(page,re.compile(r'^(Allow (free )?downloads?|Free download|Разрешить скачивание оригинала)$',re.I),False,required=False)
        if cfg.get('premium_download'):
            card=_section(page,PREMIUM,lambda s:s.locator('input[type=file]').count() or s.get_by_role('button',name=re.compile(r'^Upload File$',re.I)).count())
            _upload(page,card,original)
            price=_price(page)
            price.fill(cfg['premium_price_usd'])
            price.press('Tab')
        verify_tiers=_tiers(page,cfg.get('subscriber_tiers',[]) if cfg.get('subscriber_only') else [])
        # Refuse conflicting monetisation modes instead of modifying a restored sale.
        for name in ('Sell as Exclusive','Sell as T-shirt'):
            control=_find([page.get_by_role('switch',name=name),page.get_by_role('checkbox',name=name)],False)
            if control is not None and _state(control):
                raise NotPublishedError('В DeviantArt включён другой режим продажи: отключите его вручную')
        gallery=_find([page.get_by_role('combobox',name=re.compile('Choose Gallery',re.I))],False)
        if gallery is not None and (gallery.input_value() if gallery.evaluate('e=>e.tagName==="SELECT"') else gallery.inner_text().strip() not in ('','Choose Gallery')):
            raise NotPublishedError('В DeviantArt выбрана Premium Gallery: очистите её перед отправкой')
        stage='проверка перед публикацией'
        page.wait_for_timeout(400)  # Let blur/change validation settle before the final read-back.
        if _title(page).input_value()!=title or _state(_premium_control(page))!=bool(cfg.get('premium_download')) or not verify_tiers():
            raise NotPublishedError('DeviantArt: настройки доступа изменились перед отправкой')
        if cfg.get('premium_download'):
            currency_card=_section(page,PREMIUM,'input[type=number],input[inputmode=decimal],input[name*=price]')
            currency=currency_card.get_by_text(re.compile(r'^USD$',re.I))
            if not any(currency.nth(i).is_visible() for i in range(currency.count())):
                raise NotPublishedError('DeviantArt: валюта USD не подтверждена')
            try:correct=Decimal(_price(page).input_value().replace(',','.'))==Decimal(cfg['premium_price_usd'])
            except (InvalidOperation,ValueError):correct=False
            filename=_section(page,PREMIUM,lambda s:s.locator('input[type=file]').count() or s.get_by_role('button',name=re.compile(r'^Upload File$',re.I)).count()).get_by_text(re.compile(re.escape(Path(original).name)))
            if not correct or not any(filename.nth(i).is_visible() for i in range(min(filename.count(),20))):
                raise NotPublishedError('DeviantArt: цена или файл оригинала не подтверждены')
        button=_find([page.get_by_role('button',name=SEND)])
        if not button.is_enabled():
            raise NotPublishedError('DeviantArt не разрешает отправку: проверьте поля редактора')
        if dry_run:
            shot=B.dump(page,'da-paid-dry')
            cleared=_discard_owned_draft(page,title)
            note='Пробный прогон: доступ проверен, кнопка публикации не нажималась'
            if not cleared:note+='; перед следующей отправкой закройте тестовый черновик в DeviantArt'
            return {'url':'','id':'','shot':shot,'note':note}
        submitted=True
        button.click(timeout=10000)
        link=_confirm(page)
        return {'url':link,'id':'','note':'Платные параметры проверены перед отправкой'}
    except Exception as error:
        shot=''
        try:shot=B.dump(page,'da-paid-error')
        except Exception:pass
        suffix=f'; снимок экрана: {Path(shot).name}' if shot else ''
        if submitted:
            raise PublicationUnknown('DeviantArt: возможно, опубликовано; проверьте сайт перед повтором'+suffix) from error
        detail=str(error) if isinstance(error,NotPublishedError) else type(error).__name__
        raise NotPublishedError(f'DeviantArt · {stage}: {detail}'+suffix) from error


def publish(cfg,files,title,text,tags,rating,progress=None,confirmed=None,already=None,dry_run=False):
    from core.images import make_light
    from app.paths import sub
    cfg=validate_changes(cfg)
    from app.publish.da_access import blocked_reason,requested
    reason=blocked_reason(cfg)
    if reason or not requested(cfg):raise NotPublishedError(reason or 'Выберите платное скачивание или тиры DeviantArt')
    if rating not in ('sfw','erotic'):
        raise NotPublishedError('DeviantArt не принимает откровенный контент')
    saved=dict(already or {})
    results=[]
    with tempfile.TemporaryDirectory(prefix='da-paid-',dir=sub('tmp')) as folder:
        with B.session(platform='deviantart',headless=not cfg.get('show_window',True),wait=20 if dry_run else 600) as (ctx,page):
            for i,(name,data,mime) in enumerate(files):
                key=hashlib.sha256(data).hexdigest()
                if key in saved:
                    results.append(saved[key]);continue
                safe_name=Path(str(name).replace('\\','/')).name
                if safe_name in ('','.','..'):raise NotPublishedError('Неверное имя файла DeviantArt')
                work=Path(folder)/str(i);work.mkdir()
                original=work/safe_name
                original.write_bytes(data)
                preview=Path(folder)/f'preview-{i}.jpg'
                preview.write_bytes(make_light(original,{'scale':.5},max_side=1600).data)
                item_title=title[:50] if len(files)==1 else f'{title[:42]} ({i+1}/{len(files)})'
                main=original if cfg.get('subscriber_only') and not cfg.get('premium_download') else preview
                result=post(page,cfg,main,original,item_title,text,tags,rating,dry_run)
                results.append(result)
                if not dry_run:
                    saved[key]=result
                    if confirmed:confirmed(dict(saved))
                B.save_cookies(ctx)
                if progress:progress((i+1)/len(files))
                if dry_run:break
    return results
