"""ВКонтакте через браузер: запись на стене группы от вашего имени как админа (без API и бизнес-профиля).

Шаги как у человека: открыть группу → поле «Напишите что-нибудь» → текст → прикрепить фото →
дождаться загрузки → «Опубликовать» → взять ссылку на новую запись.
ВК иногда меняет вёрстку, поэтому на каждый шаг есть несколько вариантов поиска элемента,
а при неудаче сохраняется снимок экрана (папка browser_debug).
"""
import os
import re
import time

from app.publish import browser as B
from core.common import PlatformError

BASE = os.environ.get("CROSSPOST_VK_BASE", "https://vk.com")
LOGIN_COOKIES = ("remixsid", "remixnsid")


def _logged(ctx) -> bool:
    try:
        return any(c["name"] in LOGIN_COOKIES and c["value"] and c["value"] != "DELETED" for c in ctx.cookies())
    except Exception:
        return False


def login(report, timeout: int = 600) -> bool:
    """Открывает окно браузера и ждёт, пока вы войдёте во ВК (до 10 минут)."""
    with B.session(headless=False) as (ctx, page):
        page.goto(BASE + "/", wait_until="domcontentloaded")
        if _logged(ctx):
            B.save_cookies(ctx)
            report(progress=1.0, message="Вход уже выполнен")
            return True
        report(progress=0.3, message="Войдите во ВКонтакте в открывшемся окне браузера — после входа окно закроется само")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if _logged(ctx):
                    time.sleep(2)   # дать сайту дописать куки
                    B.save_cookies(ctx)
                    report(progress=1.0, message="Вход выполнен")
                    return True
                if not ctx.pages:
                    raise PlatformError("Окно браузера закрыто до входа")
            except PlatformError:
                raise
            except Exception:
                raise PlatformError("Окно браузера закрыто до входа")
            time.sleep(1)
        raise PlatformError("Вход не завершён за 10 минут")


def check() -> bool:
    """Есть ли сохранённый вход (без открытия окна)."""
    with B.session(headless=True) as (ctx, page):
        return _logged(ctx)


def _group_url(group_id: str) -> str:
    g = str(group_id).strip()
    m = re.search(r"vk\.com/([\w.]+)", g)
    if m:
        g = m.group(1)
    if g.lstrip("-").isdigit():
        return f"{BASE}/club{g.lstrip('-')}"
    return f"{BASE}/{g}"


FIELD = [
    "#post_field",
    lambda p: p.get_by_role("textbox", name=re.compile(r"Напишите что-нибудь|Что у вас нового|Write something|What's new", re.I)),
    lambda p: p.get_by_text(re.compile(r"^\s*(Напишите что-нибудь|Что у вас нового\??|Write something|What's new\??)", re.I)),
    lambda p: p.get_by_role("button", name=re.compile(r"(Создать|Новая|Написать) (запись|пост)|Create post|New post", re.I)),
    lambda p: p.get_by_text(re.compile(r"^\s*(Создать запись|Создать пост|Новая запись|Написать пост)\s*$", re.I)),
    "[data-testid*='posting'] [contenteditable='true']",
    "[data-testid*='posting'], [data-testid*='create_post'], [data-testid*='post_create']",
    "[placeholder*='Напишите'], [placeholder*='нового']",
    "#page_block_submit_post, .submit_post_field, .post_field_wrap",
]
EDITOR = ["#post_field[contenteditable='true']", "[data-testid*='posting'] [contenteditable='true']",
          "[contenteditable='true'][role='textbox']", "[contenteditable='true']"]
PHOTO_BTN = [
    lambda p: p.get_by_role("button", name=re.compile(r"^\s*(Фото|Фотография|Photo|Фото/Видео|Фото и видео)\s*$", re.I)),
    "[aria-label='Фото'], [aria-label='Фотография'], [aria-label='Photo']",
    "[data-testid*='attach_photo'], [data-testid*='photo']",
    ".ms_item_photo",
    lambda p: p.get_by_text(re.compile(r"^\s*(Фото|Фотография)\s*$")),
]
UPLOAD_ITEM = [lambda p: p.get_by_text(re.compile(r"Загрузить (фото|с компьютера)|Upload (photo|from computer)", re.I))]
PUBLISH = [
    lambda p: p.get_by_role("button", name=re.compile(r"^\s*(Опубликовать( сейчас)?|Publish( now)?|Далее|Next)\s*$", re.I)),
    "#send_post",
    lambda p: p.get_by_text(re.compile(r"^\s*Опубликовать\s*$")),
]


VIDEO_BTN = [
    lambda p: p.get_by_role("button", name=re.compile(r"^\s*(Видео|Видеозапись|Video|Фото/Видео|Фото и видео)\s*$", re.I)),
    "[aria-label='Видео'], [aria-label='Видеозапись'], [aria-label='Video']",
    "[data-testid*='attach_video'], [data-testid*='video']",
    ".ms_item_video",
]
DOC_BTN = [
    lambda p: p.get_by_role("button", name=re.compile(r"^\s*(Документ|Файл|Document|File)\s*$", re.I)),
    "[aria-label='Документ'], [aria-label='Файл'], [aria-label='Document']",
    "[data-testid*='attach_doc'], [data-testid*='doc']",
    ".ms_item_doc",
]
MORE_BTN = [
    lambda p: p.get_by_role("button", name=re.compile(r"^\s*(Ещё|Еще|More)\s*$", re.I)),
    "[aria-label='Ещё'], [aria-label='Еще'], [aria-label='More']",
]
UPLOAD_ANY = [lambda p: p.get_by_text(re.compile(r"Загрузить (видео|файл|новый файл|документ|с компьютера)|Выбрать файл|"
                                                 r"Upload (video|file|new file|from computer)|Choose file", re.I))]
DIALOG = "[role='dialog'], [aria-modal='true']"
MENU_POST = [
    lambda p: p.get_by_role("menuitem", name=re.compile(r"^\s*(Пост|Запись|Новый пост|Post)(\s|$)", re.I)),
    lambda p: p.get_by_role("option", name=re.compile(r"^\s*(Пост|Запись|Post)(\s|$)", re.I)),
    lambda p: p.get_by_text(re.compile(r"^\s*(Пост|Запись|Новый пост|Новая запись|Post)\s*$", re.I)),
]


class _NoDialog:
    """Пустая «область»: окна записи нет — значит, ничего не найдено (никаких поисков по всей странице,
    иначе можно попасть в поле комментария)."""
    def locator(self, *a, **k):
        raise LookupError("окно записи не открыто")

    get_by_role = get_by_text = locator


NO_DIALOG = _NoDialog()


def _scope(page):
    """Окно новой записи. Ищем ТОЛЬКО внутри него: на стене есть поля и кнопки комментариев,
    с которыми его нельзя путать."""
    d = page.locator(DIALOG)
    try:
        n = d.count()
    except Exception:
        return NO_DIALOG
    for i in range(n - 1, -1, -1):
        el = d.nth(i)
        try:
            if el.is_visible() and el.locator("[contenteditable='true']").count():
                return el
        except Exception:
            continue
    for i in range(n - 1, -1, -1):   # второй шаг окна («Далее» → настройки) — уже без поля текста
        el = d.nth(i)
        try:
            if el.is_visible() and el.get_by_role("button", name=re.compile(r"Опубликовать|Publish|Назад|Back|Редактировать|Запланировать", re.I)).count():
                return el
        except Exception:
            continue
    return NO_DIALOG


def _find_in(page, cands, timeout_ms: int = 8000):
    """Как first_visible, но сначала внутри окна редактора."""
    deadline = time.time() + timeout_ms / 1000
    while True:
        scope = _scope(page)
        for c in (cands if scope is not NO_DIALOG else []):
            try:
                loc = c(scope) if callable(c) else scope.locator(c)
                for i in range(min(loc.count(), 5)):
                    el = loc.nth(i)
                    if el.is_visible() and not el.evaluate("e => !!e.closest('[data-testid*=comment]')"):
                        return el
            except Exception:
                continue
        if time.time() > deadline:
            return None
        page.wait_for_timeout(300)


REMOVE_ATTACH = ("[aria-label*='Удалить' i], [aria-label*='Убрать' i], [aria-label*='Remove' i], [aria-label*='Delete' i], "
                 "[data-testid*='remove' i], [data-testid*='delete' i], "
                 "[class*='remove' i][role='button'], button[class*='remove' i], button[class*='delete' i], button[class*='dismiss' i]")


def _clear_draft(page, editor):
    """ВК хранит черновик: при открытии окна там может остаться текст и фото прошлой попытки.
    Стираем текст и убираем прикреплённые фото, прежде чем вставлять своё."""
    try:
        editor.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
    except Exception:
        pass
    for _ in range(20):
        scope = _scope(page)
        if scope is NO_DIALOG:
            break
        btns = scope.locator(REMOVE_ATTACH)
        target = None
        for i in range(btns.count()):
            b = btns.nth(i)
            try:
                label = (b.get_attribute("aria-label") or "").lower()
                if b.is_visible() and "закрыть" not in label and "close" not in label:
                    target = b
                    break
            except Exception:
                continue
        if target is None:
            break
        try:
            target.click()
            page.wait_for_timeout(400)
        except Exception:
            break


def _close_draft(page, editor):
    """После пробного прогона: стереть текст и закрыть окно, не публикуя."""
    try:
        back = _find_in(page, [lambda p: p.get_by_role("button", name=re.compile(r"^\s*(Назад|Back|Редактировать|Edit)\s*$", re.I))], 1000)
        if back:
            back.click()
            page.wait_for_timeout(600)
        ed = _find_in(page, ["[contenteditable='true']"], 1500) or editor
        _clear_draft(page, ed)
    except Exception:
        pass
    for _ in range(3):
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            ok = B.first_visible(page, [lambda p: p.get_by_role("button", name=re.compile(
                r"^\s*(Не сохранять|Выйти|Закрыть|Удалить черновик|Удалить|Discard)\s*$", re.I))], 800)
            if ok:
                ok.click()
                page.wait_for_timeout(400)
        except Exception:
            break


def _dialog_editor(page):
    scope = _scope(page)
    if scope is NO_DIALOG:
        return None, None
    loc = scope.locator("[contenteditable='true']")
    for i in range(min(loc.count(), 5)):
        el = loc.nth(i)
        if el.is_visible():
            return el, scope
    return None, None


def _open_composer(page):
    """Новый ВК: «+ Создать» (data-testid=group_publish_create_button) → «Пост» → окно «Новый пост».
    Пробуем до трёх раз: первый клик иногда приходится на ещё не «оживший» интерфейс страницы."""
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    menu_seen = False
    for attempt in range(3):
        editor, scope = _dialog_editor(page)
        if editor is not None:
            return editor, scope
        btn = B.first_visible(page, ["[data-testid='group_publish_create_button']"] + FIELD, 15000)
        if not btn:
            raise PlatformError("не нашла кнопку «Создать» — проверьте, что вы админ группы")
        btn.click()
        deadline = time.time() + 6
        while time.time() < deadline:
            editor, scope = _dialog_editor(page)
            if editor is not None:
                return editor, scope
            item = B.first_visible(page, MENU_POST, 300)   # «Создать» открывает меню: пост, клип, история…
            if item:
                menu_seen = True
                item.click()
                end = time.time() + 10
                while time.time() < end:
                    editor, scope = _dialog_editor(page)
                    if editor is not None:
                        return editor, scope
                    page.wait_for_timeout(300)
                break
            page.wait_for_timeout(300)
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        page.wait_for_timeout(1000)
    raise PlatformError("окно «Новый пост» не открылось" + ("" if menu_seen else " (меню «Создать» не появилось или в нём нет пункта «Пост»)"))


def _attach(page, files):
    """Прикрепить фото: сначала через скрытое поле файла, если оно есть, иначе через кнопку «Фото»."""
    scope = _scope(page)
    if scope is NO_DIALOG:
        raise PlatformError("окно записи закрылось до прикрепления фото")
    inp = scope.locator("input[type='file']")
    for i in range(inp.count()):
        acc = (inp.nth(i).get_attribute("accept") or "").lower()
        if "image" in acc or ".jpg" in acc or "jpeg" in acc:
            inp.nth(i).set_input_files(files)
            return
    btn = None
    for c in PHOTO_BTN:   # кнопку ищем внутри окна редактора — на странице есть ещё «фото» у комментариев
        try:
            loc = c(scope) if callable(c) else scope.locator(c)
            for i in range(min(loc.count(), 5)):
                el = loc.nth(i)
                if el.is_visible() and not el.evaluate("e => !!e.closest('[data-testid*=comment]')"):
                    btn = el
                    break
        except Exception:
            continue
        if btn is not None:
            break
    if not btn:
        raise PlatformError("не нашла кнопку «Фото» в окне новой записи")
    try:
        with page.expect_file_chooser(timeout=5000) as fc:
            btn.click()
        fc.value.set_files(files)
        return
    except Exception:
        pass
    item = B.first_visible(page, UPLOAD_ITEM, 5000)   # кнопка открыла меню — выбираем «Загрузить с компьютера»
    if not item:
        raise PlatformError("после кнопки «Фото» не появилось окно выбора файла")
    with page.expect_file_chooser(timeout=8000) as fc:
        item.click()
    fc.value.set_files(files)


def _find_btn(scope, cands):
    for c in cands:
        try:
            loc = c(scope) if callable(c) else scope.locator(c)
            for i in range(min(loc.count(), 5)):
                el = loc.nth(i)
                if el.is_visible() and not el.evaluate("e => !!e.closest('[data-testid*=comment]')"):
                    return el
        except Exception:
            continue
    return None


def _choose_file(page, btn, path: str):
    """Нажать кнопку и отдать файл: сразу окно выбора файла или меню/окно с «Загрузить…»."""
    try:
        with page.expect_file_chooser(timeout=5000) as fc:
            btn.click()
        fc.value.set_files([path])
        return
    except Exception:
        pass
    for _ in range(2):   # бывает окно со списком загруженного раньше и кнопкой «Загрузить новый»
        inp = page.locator(f"{DIALOG} input[type='file']")
        if inp.count():
            inp.last.set_input_files([path])
            return
        item = B.first_visible(page, UPLOAD_ANY, 5000)
        if not item:
            break
        try:
            with page.expect_file_chooser(timeout=6000) as fc:
                item.click()
            fc.value.set_files([path])
            return
        except Exception:
            page.wait_for_timeout(800)
    raise PlatformError("не появилось окно выбора файла")


def _attach_media(page, path: str, kind: str):
    """GIF — документом (ВК проигрывает его в ленте), видео — видеозаписью. Только кнопки окна новой записи."""
    scope = _scope(page)
    if scope is NO_DIALOG:
        raise PlatformError("окно записи закрылось до прикрепления файла")
    inp = scope.locator("input[type='file']")
    for i in range(inp.count()):   # поле, которое само принимает видео, — как у фото
        acc = (inp.nth(i).get_attribute("accept") or "").lower()
        if kind == "video" and ("video" in acc or ".mp4" in acc):
            inp.nth(i).set_input_files([path])
            return
        if kind == "gif" and ".gif" in acc and "image/*" not in acc:
            inp.nth(i).set_input_files([path])
            return
    btn = _find_btn(scope, VIDEO_BTN if kind == "video" else DOC_BTN)
    if not btn:   # «Документ»/«Видео» может прятаться в меню «Ещё»
        more = _find_btn(scope, MORE_BTN)
        if more:
            more.click()
            page.wait_for_timeout(600)
            btn = _find_btn(page, VIDEO_BTN if kind == "video" else DOC_BTN)
    if not btn:
        raise PlatformError(f"не нашла кнопку «{'Видео' if kind == 'video' else 'Документ'}» в окне новой записи")
    _choose_file(page, btn, path)


def _wait_media(page, kind: str, timeout: int):
    """Ждём, пока ВК загрузит видео/GIF: появится вложение и пропадут индикаторы загрузки."""
    deadline = time.time() + timeout
    seen = False
    while time.time() < deadline:
        scope = _scope(page)
        if scope is NO_DIALOG:
            raise PlatformError("окно записи закрылось во время загрузки")
        try:
            busy = scope.locator("[class*='progress' i]:visible, [class*='Spinner' i]:visible, [class*='loading' i]:visible, "
                                 "[class*='upload' i][class*='percent' i]:visible").count()
            att = scope.locator("video, [class*='video' i] img, [class*='doc' i], [class*='Attach' i] img, [class*='attach' i] img").count()
            seen = seen or busy > 0 or att > 0
            if att > 0 and busy == 0:
                page.wait_for_timeout(1200)
                return
        except Exception:
            pass
        page.wait_for_timeout(1000)
    raise PlatformError(("видео" if kind == "video" else "GIF") + f" не загрузил{'ось' if kind == 'video' else 'ся'} за {timeout // 60} мин")


def _wait_uploaded(page, n: int, timeout: int = 180, before: int = 0):
    """Ждём, пока фото загрузятся: в окне записи прибавится n картинок и пропадут индикаторы загрузки."""
    deadline = time.time() + timeout
    scope = _scope(page)
    if scope is not NO_DIALOG:
        while time.time() < deadline:
            try:
                imgs = scope.locator("img").count()
                busy = scope.locator("[class*='progress' i]:visible, [class*='Spinner' i]:visible, [class*='loading' i]:visible").count()
                if imgs >= before + n and busy == 0:
                    page.wait_for_timeout(800)
                    return
            except Exception:
                pass
            page.wait_for_timeout(500)
        raise PlatformError("фото не загрузились за 3 минуты")
    raise PlatformError("окно записи закрылось во время загрузки фото")





def post(group_id: str, files: list[str], text: str, dry_run: bool = False, headless: bool = False,
         progress=lambda f: None, log=lambda m: None, kind: str = "images") -> dict:
    with B.session(headless=headless) as (ctx, page):
        try:
            log("открываю группу")
            page.goto(_group_url(group_id), wait_until="domcontentloaded")
            if not _logged(ctx) or "/login" in page.url:
                raise PlatformError("в браузере не выполнен вход во ВК — «Настройки» → ВКонтакте → «Войти в браузере»")
            title = re.sub(r"\s*\|\s*(ВКонтакте|VK)\s*$", "", page.title() or "").strip()
            progress(0.1)
            editor, scope = _open_composer(page)
            _clear_draft(page, editor)   # остатки прошлого черновика (ВК их сохраняет)
            editor.click()
            log("пишу текст")
            for i, line in enumerate((text or "").split("\n")):
                if i:
                    page.keyboard.press("Shift+Enter")
                if line:
                    page.keyboard.insert_text(line)
            progress(0.2)
            if files and kind in ("gif", "video"):
                log("загружаю " + ("видео" if kind == "video" else "GIF"))
                _attach_media(page, files[0], kind)
                _wait_media(page, kind, 1200 if kind == "video" else 300)
            elif files:
                log("загружаю фото")
                imgs_before = _scope(page).locator("img").count() if _scope(page) is not NO_DIALOG else 0
                _attach(page, files)
                _wait_uploaded(page, len(files), before=imgs_before)
            progress(0.75)
            if dry_run:
                shot = B.dump(page, "vk-dry")
                shot2 = ""
                nxt = _find_in(page, [lambda p: p.get_by_role("button", name=re.compile(r"^\s*(Далее|Next)\s*$", re.I))], 2000)
                if nxt:   # заглянем на второй шаг (настройки публикации), но «Опубликовать» не нажимаем
                    nxt.click()
                    page.wait_for_timeout(1500)
                    shot2 = B.dump(page, "vk-dry-step2")
                _close_draft(page, editor)
                return {"shot": shot, "shot2": shot2, "title": title}
            before = set(page.locator(f"a[href*='wall-{str(group_id).lstrip('-')}_']").evaluate_all("els => els.map(e => e.getAttribute('href'))")) \
                if str(group_id).lstrip("-").isdigit() else set()
            btn = _find_in(page, PUBLISH)
            if not btn:
                raise PlatformError("не нашла кнопку «Опубликовать»")
            log("публикую")
            first_label = (btn.inner_text() or "").strip().lower()
            btn.click()
            page.wait_for_timeout(800)
            if first_label.startswith("далее"):   # бывает два шага: «Далее» → «Опубликовать»
                nxt = _find_in(page, PUBLISH[:1], 5000)
                if nxt:
                    nxt.click()
            progress(0.85)
            link = ""
            deadline = time.time() + 60   # если ВК спросит капчу — в окне браузера её можно ввести
            gid = str(group_id).lstrip("-")
            while time.time() < deadline and gid.isdigit():
                hrefs = page.locator(f"a[href*='wall-{gid}_']").evaluate_all("els => els.map(e => e.getAttribute('href'))")
                new = [h for h in hrefs if h not in before]
                if new:
                    m = re.search(r"wall-\d+_\d+", new[0])
                    link = f"{BASE}/{m.group(0)}" if m else ""
                    break
                page.wait_for_timeout(700)
            if not link:
                shot = B.dump(page, "vk-after")
                return {"link": "", "title": title, "note": f"запись, скорее всего, вышла, но ссылку найти не удалось (снимок {shot})"}
            B.save_cookies(ctx)
            return {"link": link, "title": title, "note": ""}
        except PlatformError as e:
            shot = B.dump(page, "vk-error")
            raise PlatformError(f"ВК в браузере: {e}" + (f" (снимок экрана: {shot})" if shot else ""))
        except Exception as e:  # noqa: BLE001
            shot = B.dump(page, "vk-error")
            raise PlatformError(f"ВК в браузере: {type(e).__name__}: {str(e).splitlines()[0]}" + (f" (снимок экрана: {shot})" if shot else ""))
