"""Experimental Reddit / JoyReactor UI adapters, using isolated site profiles.

Selectors must be checked against the live sites before declaring support stable.
No private HTTP API, CAPTCHA solving, automatic login or submission retries.
"""
import re
import time
from urllib.parse import urlparse, urljoin

from app.publish import browser as B
from app.publish.browser_targets import SUBREDDITS
from core.common import NotPublishedError, PublicationUnknown

SITES = {'reddit': 'https://www.reddit.com', 'joyreactor': 'https://joyreactor.cc'}


def _url(platform, community='FurAI'):
    if platform not in SITES:
        raise NotPublishedError('Неизвестная браузерная площадка')
    if platform == 'reddit' and community not in SUBREDDITS:
        raise NotPublishedError('Поддерживаются только r/FurAI и r/aiyiff')
    return SITES[platform] + (f'/r/{community}/submit' if platform == 'reddit' else '/post/add')


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


def login(platform, report, timeout=600):
    with B.session(headless=False, platform=platform) as (ctx, page):
        page.goto(_url(platform), wait_until='domcontentloaded')
        report(progress=.1, message='Войдите на сайт в окне браузера; откройте форму нового поста')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if urlparse(page.url).hostname != urlparse(SITES[platform]).hostname:
                    time.sleep(.5)
                    continue
                _title(page) if platform == 'reddit' else _joy_form(page)
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


def _assert_empty_composer(scope, platform):
    """Do not mutate a restored site draft, even during a dry run."""
    fields = scope.locator('textarea[name="title"], input[name="title"], textarea[name="body"], textarea[name="text"], #post_text, input[name="tags"], textarea[name="tags"], #post_tags, [contenteditable="true"][role="textbox"], [contenteditable="true"].ProseMirror').filter(visible=True)
    for field in fields.all():
        if field.evaluate('el => String(el.value ?? el.innerText ?? "").trim()'):
            raise NotPublishedError('В редакторе уже есть черновик. Сохраните или очистите его перед проверкой')
    images = scope.locator('[data-testid="media-preview"] img, [data-testid="image-preview"], shreddit-media-editor img, .uploaded_image img, .post_content img, [contenteditable="true"] img')
    if images.count():
        raise NotPublishedError('В редакторе уже есть вложения. Сохраните или очистите черновик')
    for picker in scope.locator('input[type="file"]').all():
        if picker.evaluate('el => !!el.files?.length'):
            raise NotPublishedError('В редакторе уже выбраны файлы. Сохраните или очистите черновик')
    if scope.locator('iframe.tox-edit-area__iframe').count() == 1:
        body = scope.frame_locator('iframe.tox-edit-area__iframe').locator('body[contenteditable="true"]')
        body.wait_for(state='visible')
        if body.inner_text().strip() or body.locator('img').count():
            raise NotPublishedError('В редакторе уже есть черновик. Сохраните или очистите его перед проверкой')


def _reddit_labels(page, flair, nsfw):
    # Both controls belong to the same dialog. A missing required control stops the post.
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
    if parsed.hostname != urlparse(SITES[platform]).hostname:
        return ''
    pattern = (rf'/r/{re.escape(community)}/comments/[a-z0-9]+(?:/|$)' if platform == 'reddit' else r'^/post/\d+/?$')
    if re.search(pattern, parsed.path, re.I):
        return parsed._replace(query='', fragment='').geturl()
    return ''


def post(platform, files, title, text, *, community='FurAI', flair='', nsfw=False,
         tags=None, dry_run=False, headless=False, progress=lambda n: None, log=lambda s: None):
    target = _url(platform, community)
    if not files or len(files) > (10 if platform == 'reddit' else 1):
        raise NotPublishedError('Reddit: 1–10 изображений; JoyReactor: пока одно изображение')
    if platform == 'reddit' and (not title.strip() or len(title) > 300):
        raise NotPublishedError('Заголовок Reddit: от 1 до 300 символов')
    if platform == 'joyreactor' and not tags:
        raise NotPublishedError('Для JoyReactor нужны теги')
    submitted = False
    with B.session(headless=headless, platform=platform) as (ctx, page):
        try:
            log('открываю форму публикации')
            page.goto(target, wait_until='domcontentloaded')
            if urlparse(page.url).hostname != urlparse(target).hostname:
                raise NotPublishedError('Сайт перенаправил на другой адрес — проверьте вход')
            if platform == 'reddit':
                _title(page)
                _assert_empty_composer(page, platform)
                _find([page.get_by_role('tab', name=re.compile(r'^(Images(?: & Video)?|Изображения.*)$', re.I))]).click()
                _title(page).fill(title)
                scope = page
                _fill_body(page, scope, text)
                _reddit_labels(page, flair, nsfw)
            else:
                scope = _joy_form(page)
                _assert_empty_composer(scope, platform)
                _fill_body(page, scope, '\n\n'.join(part for part in (title, text) if part), required=True)
                _find([scope.locator('input[name="tags"], textarea[name="tags"], #post_tags')]).fill(', '.join(tags))
            progress(.2)
            log('загружаю изображения')
            _attach(page, scope, files, platform)
            progress(.75)
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
            try:
                B.dump(page, platform + '-error')
            except Exception:
                pass
            if submitted:
                raise PublicationUnknown('Возможно, опубликовано. Проверьте сайт перед повтором') from error
            if isinstance(error, NotPublishedError):
                raise
            raise NotPublishedError(f'Браузерная публикация не началась: {type(error).__name__}') from error
