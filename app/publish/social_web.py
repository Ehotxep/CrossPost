"""Experimental Reddit / JoyReactor UI adapters, using the same profile as VK.

Selectors must be checked against the live sites before declaring support stable.
No private HTTP API, CAPTCHA solving, automatic login or submission retries.
"""
import re
import time
from urllib.parse import urlparse, urljoin

from app.publish import browser as B
from app.publish.browser_targets import valid_community
from core.common import NotPublishedError, PublicationUnknown

SITES = {'reddit': 'https://www.reddit.com', 'joyreactor': 'https://joyreactor.cc', 'x': 'https://x.com'}


def _site_host_ok(platform, url):
    host = urlparse(url).hostname
    return host in ({'reddit.com', 'www.reddit.com'} if platform == 'reddit' else {urlparse(SITES[platform]).hostname})


def _url(platform, community='FurAI'):
    if platform not in SITES:
        raise NotPublishedError('Неизвестная браузерная площадка')
    if platform == 'reddit' and not valid_community(community):
        raise NotPublishedError('Некорректное имя сообщества Reddit')
    return SITES[platform] + (f'/r/{community}/submit/' if platform == 'reddit' else '/compose/post' if platform == 'x' else '/post/add')


def _find(candidates, timeout=10):
    """Only a unique visible match is actionable; never click an arbitrary first item."""
    deadline = time.monotonic() + timeout
    while True:
        for locator in candidates:
            visible = locator.filter(visible=True)
            if visible.count() == 1:
                return visible
        if time.monotonic() >= deadline:
            raise NotPublishedError('Форма сайта изменилась или требует входа: нужное поле не найдено однозначно')
        time.sleep(.2)


def _title(page):
    return _find([page.locator('textarea[name="title"], input[name="title"]'),
                  page.get_by_role('textbox', name=re.compile(r'^(Title|Заголовок)\*?$', re.I))], timeout=2)


def _joy_form(page):
    return _find([page.locator('form').filter(has=page.locator('input[name="tags"], textarea[name="tags"], #post_tags'))], timeout=2)


def _x_editor(page):
    return _find([page.locator('[data-testid="tweetTextarea_0"][contenteditable="true"]')], timeout=2)


def _x_scope(page):
    """Prefer the open composer dialog over the background timeline composer."""
    selector = '[data-testid="tweetTextarea_0"][contenteditable="true"]'
    dialogs = page.get_by_role('dialog').filter(has=page.locator(selector)).filter(visible=True)
    if dialogs.count() > 1:
        raise NotPublishedError('Открыто несколько окон редактора X')
    return dialogs if dialogs.count() == 1 else page


def login(platform, report, timeout=600):
    with B.session(headless=False) as (ctx, page):
        page.goto(_url(platform), wait_until='domcontentloaded')
        report(progress=.1, message='Войдите на сайт в окне браузера; откройте форму нового поста')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if not _site_host_ok(platform, page.url):
                    time.sleep(.5)
                    continue
                if platform == 'reddit':
                    _title(page)
                elif platform == 'x':
                    _x_editor(_x_scope(page))
                else:
                    _joy_form(page)
                B.save_cookies(ctx)
                report(progress=1, message='Форма публикации доступна, вход сохранён')
                return
            except NotPublishedError:
                if not ctx.pages:
                    break
            time.sleep(.5)
        raise NotPublishedError('Вход не завершён или форма публикации недоступна')


def _fill_body(page, scope, text, required=False):
    fields = [scope.locator('textarea[name="body"], textarea[name="text"], #post_text'),
              scope.locator('[contenteditable="true"][role="textbox"], [contenteditable="true"].ProseMirror')]
    if scope.locator('iframe.tox-edit-area__iframe').count() == 1:
        fields.append(scope.frame_locator('iframe.tox-edit-area__iframe').locator('body[contenteditable="true"]'))
    if not text and not required and not any(field.count() for field in fields):
        return
    editor = _find(fields)
    editor.fill(text)  # Playwright fills contenteditable without executing user HTML.


def _reddit_labels(page, flair, nsfw):
    # Both controls belong to the same dialog. A missing required control stops the post.
    if not flair and not nsfw:
        return
    _find([page.get_by_role('button', name=re.compile(r'^(Add flair(?: and tags)?|Добавить фл[еэ]йр.*)\*?$', re.I))]).click()
    dialog = _find([page.get_by_role('dialog')])
    label = flair or 'No flair'
    _find([dialog.get_by_role('radio', name=label, exact=True),
           dialog.get_by_text(label, exact=True)]).click()
    if nsfw:
        checkbox = _find([dialog.get_by_role('checkbox', name=re.compile(r'NSFW|Mature|18\+', re.I))])
        checkbox.set_checked(True)
    _find([dialog.get_by_role('button', name=re.compile(r'^(Apply|Save|Применить|Сохранить)$', re.I))]).click()
    dialog.wait_for(state='hidden')


def _reddit_inline_attach(page, files):
    """Insert images through the text composer's image picker, one at a time."""
    previews = page.locator('[contenteditable="true"][role="textbox"] img, [contenteditable="true"].ProseMirror img')
    if previews.count():
        raise NotPublishedError('В редакторе уже есть изображения — сохраните или очистите черновик')
    button = _find([page.get_by_role('button', name=re.compile(r'^(?:add|insert|upload) (?:an? )?(?:image|photo)s?$|^(?:image|photo)$|^(?:добавить|вставить|загрузить) изображение$', re.I))])
    for i, path in enumerate(files):
        with page.expect_file_chooser(timeout=10000) as pending:
            button.click()
        pending.value.set_files(path)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if previews.count() == i + 1 and previews.evaluate_all('imgs=>imgs.every(i=>i.complete && i.naturalWidth>0)'):
                break
            page.wait_for_timeout(250)
        else:
            raise NotPublishedError('Не удалось подтвердить загрузку изображения в текст поста')


def _attach(page, scope, files, platform):
    preview = (scope.locator('[data-testid="media-preview"] img, [data-testid="image-preview"], shreddit-media-editor img')
               if platform == 'reddit' else scope.locator('.uploaded_image img, .post_content img, [contenteditable="true"] img'))
    if preview.count():
        raise NotPublishedError('В форме уже есть вложения. Очистите черновик сайта перед пробным прогоном')
    picker = scope.locator('input[type="file"][accept*="image"], input[type="file"]:not([accept])')
    if picker.count() != 1:
        raise NotPublishedError('Не найдено единственное поле загрузки изображений')
    picker.set_input_files(files)
    # A file input alone is not evidence that an asynchronous upload completed.
    if platform == 'joyreactor':
        preview = scope.locator('.uploaded_image img, .post_content img, [contenteditable="true"] img')
        if scope.locator('iframe.tox-edit-area__iframe').count() == 1:
            preview = scope.frame_locator('iframe.tox-edit-area__iframe').locator('body img')
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if preview.count() == len(files) and preview.evaluate_all('imgs=>imgs.every(i=>i.complete && i.naturalWidth>0)'):
            return
        page.wait_for_timeout(250)
    raise NotPublishedError('Не удалось подтвердить завершение загрузки изображений')


def _post_link(url, platform, community):
    parsed = urlparse(url)
    if not _site_host_ok(platform, url):
        return ''
    pattern = (rf'/r/{re.escape(community)}/comments/[a-z0-9]+(?:/|$)' if platform == 'reddit' else r'^/post/\d+/?$')
    if re.search(pattern, parsed.path, re.I):
        return parsed._replace(query='', fragment='').geturl()
    return ''


def x_dry(files, headless=False, progress=lambda n: None, log=lambda s: None):
    """Fill the X composer with generated test data; never submit a post."""
    if len(files) != 1:
        raise NotPublishedError('Для пробы X нужно одно тестовое изображение')
    stage = 'открытие редактора X'
    with B.session(headless=headless) as (ctx, page):
        try:
            log('открываю редактор X')
            page.goto(_url('x'), wait_until='domcontentloaded')
            if urlparse(page.url).hostname != 'x.com':
                raise NotPublishedError('X перенаправил на другой адрес — проверьте вход')
            stage = 'заполнение текста'
            scope = _x_scope(page)
            editor = _x_editor(scope)
            existing = scope.locator('[data-testid="attachments"] img, [data-testid="tweetPhoto"] img')
            if editor.inner_text().strip() or existing.count():
                raise NotPublishedError('В редакторе уже есть черновик. Сохраните или очистите его перед проверкой')
            editor.fill('Crosspost test — not published')
            progress(.25)
            stage = 'загрузка изображения'
            picker = scope.locator('input[data-testid="fileInput"][type="file"]')
            if picker.count() != 1:
                raise NotPublishedError('Не найдено единственное поле загрузки изображений')
            picker.set_input_files(files)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if existing.count() == 1 and existing.evaluate_all('imgs=>imgs.every(i=>i.complete && i.naturalWidth>0)'):
                    break
                page.wait_for_timeout(250)
            else:
                raise NotPublishedError('Не удалось подтвердить загрузку изображения')
            progress(.8)
            stage = 'проверка кнопки публикации'
            button = _find([scope.locator('[data-testid="tweetButton"]'), scope.locator('[data-testid="tweetButtonInline"]')])
            if not button.is_enabled():
                raise NotPublishedError('X не разрешает отправку: проверьте форму')
            shot = B.dump(page, 'x-dry')
            B.save_cookies(ctx)
            progress(1)
            return {'shot': shot, 'note': 'Пробный прогон: кнопка публикации не нажималась'}
        except Exception as error:
            try:
                shot = B.dump(page, 'x-error')
            except Exception:
                shot = ''
            detail = str(error) if isinstance(error, NotPublishedError) else type(error).__name__
            raise NotPublishedError(f'{stage}: {detail}' + (f' (снимок: {shot})' if shot else '')) from error


def post(platform, files, title, text, *, community='FurAI', flair='', nsfw=False,
         tags=None, dry_run=False, headless=False, progress=lambda n: None, log=lambda s: None):
    if platform == 'x':
        raise NotPublishedError('Публикация в X ещё не подключена')
    target = _url(platform, community)
    if not files or len(files) > (10 if platform == 'reddit' else 1):
        raise NotPublishedError('Reddit: 1–10 изображений; JoyReactor: пока одно изображение')
    if platform == 'reddit' and (not title.strip() or len(title) > 300):
        raise NotPublishedError('Заголовок Reddit: от 1 до 300 символов')
    if platform == 'joyreactor' and not tags:
        raise NotPublishedError('Для JoyReactor нужны теги')
    submitted = False
    stage = 'открытие формы'
    with B.session(headless=headless) as (ctx, page):
        try:
            log('открываю форму публикации')
            page.goto(target, wait_until='domcontentloaded')
            if not _site_host_ok(platform, page.url):
                raise NotPublishedError('Сайт перенаправил на другой адрес — проверьте вход')
            if platform == 'reddit':
                stage = 'проверка сообщества'
                if not re.match(rf'^/r/{re.escape(community)}/submit/?$', urlparse(page.url).path, re.I):
                    raise NotPublishedError(f'Не удалось подтвердить выбранное сообщество r/{community}')
                stage = 'выбор режима изображений'
                image_tab = re.compile(r'^(Images(?:\s*(?:&|and)\s*Video)?|Изображения(?:\s*и\s*видео)?)$', re.I)
                try:
                    _find([page.get_by_role('tab', name=image_tab), page.get_by_role('button', name=image_tab),
                           page.get_by_role('link', name=image_tab)], timeout=2).click()
                    inline = False
                except NotPublishedError:
                    inline = True
                stage = 'заполнение заголовка'
                _title(page).fill(title)
                scope = page
                stage = 'заполнение текста'
                _fill_body(page, scope, text)
                stage = 'выбор flair и отметки NSFW'
                _reddit_labels(page, flair, nsfw)
            else:
                stage = 'заполнение формы JoyReactor'
                scope = _joy_form(page)
                _fill_body(page, scope, '\n\n'.join(part for part in (title, text) if part), required=True)
                _find([scope.locator('input[name="tags"], textarea[name="tags"], #post_tags')]).fill(', '.join(tags))
            progress(.2)
            log('загружаю изображения')
            stage = 'загрузка изображений'
            if platform == 'reddit' and inline:
                _reddit_inline_attach(page, files)
            else:
                _attach(page, scope, files, platform)
            progress(.75)
            stage = 'поиск кнопки публикации'
            button = _find([scope.get_by_role('button', name=re.compile(r'^(Post|Publish|Опубликовать|Отправить)$', re.I))])
            if not button.is_enabled():
                raise NotPublishedError('Сайт не разрешает отправку: проверьте поля, flair и теги')
            if dry_run:
                shot = B.dump(page, platform + '-dry')
                B.save_cookies(ctx)
                return {'link': '', 'shot': shot, 'note': 'Пробный прогон: кнопка публикации не нажималась'}
            before = set(page.locator('a[href]').evaluate_all('els=>els.map(e=>e.href)'))
            log('публикую')
            # Mark before click: a timeout inside click can still follow a delivered submit.
            submitted = True
            button.click()
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                link = _post_link(page.url, platform, community)
                if link:
                    B.save_cookies(ctx)
                    progress(1)
                    return {'link': link, 'note': ''}
                # Do not identify a different person's newly loaded feed item as our post.
                confirmation = page.locator('[data-testid="post-success"] a[href], .post_success a[href]')
                links = {_post_link(urljoin(page.url, h), platform, community)
                         for h in confirmation.evaluate_all('els=>els.map(e=>e.href)') if h not in before}
                links.discard('')
                if len(links) == 1:
                    B.save_cookies(ctx)
                    return {'link': links.pop(), 'note': ''}
                page.wait_for_timeout(500)
            raise PublicationUnknown('Кнопка нажата, но ссылка не подтверждена. Проверьте сайт перед повтором')
        except Exception as error:
            shot = ''
            try:
                shot = B.dump(page, platform + '-error')
            except Exception:
                pass
            if submitted:
                raise PublicationUnknown('Возможно, опубликовано. Проверьте сайт перед повтором') from error
            if isinstance(error, NotPublishedError):
                detail = str(error)
            else:
                detail = f'{type(error).__name__}'
            raise NotPublishedError(f'{stage}: {detail}' + (f' (снимок: {shot})' if shot else '')) from error
