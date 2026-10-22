"""Публикация через браузер: для площадок, где API закрыт (ВК без бизнес-профиля, платные тиры DeviantArt).

Приложение открывает Microsoft Edge (есть в каждой Windows 11) или Chrome с ОТДЕЛЬНЫМ профилем
в папке данных. В нём вы один раз входите на сайт — дальше вход сохраняется, как в обычном браузере.
Ваш основной браузер и его пароли не трогаются.

Если шаг не удался, сохраняются снимок экрана и страница (папка browser_debug) — по ним видно, что
поменялось на сайте.
"""
import os
import re
import threading
import time
from contextlib import contextmanager

from app.paths import sub
from core.common import PlatformError

_lock = threading.Lock()
CHANNELS = ("msedge", "chrome", None)   # None — Chromium из Playwright, если он установлен


def available() -> tuple[bool, str]:
    try:
        import playwright  # noqa: F401
        return True, ""
    except ImportError:
        return False, "Не установлена библиотека playwright — запустите Install.bat ещё раз"


def _launch(pw, headless: bool, downloads: bool = False):
    exe = os.environ.get("CROSSPOST_BROWSER_EXE")   # для проверок без Edge
    if os.environ.get("CROSSPOST_BROWSER_HEADLESS"):
        headless = True
    base = dict(user_data_dir=str(sub("browser")), headless=headless, viewport={"width": 1280, "height": 900},
                locale="ru-RU", accept_downloads=downloads)
    if exe:
        return pw.chromium.launch_persistent_context(executable_path=exe, **base)
    last = None
    for ch in CHANNELS:
        try:
            return pw.chromium.launch_persistent_context(**(dict(base, channel=ch) if ch else base))
        except Exception as e:  # noqa: BLE001
            last = e
    raise PlatformError(f"Не получилось открыть браузер (Edge или Chrome): {str(last).splitlines()[0] if last else ''}")


@contextmanager
def session(headless: bool = False, downloads: bool = False, wait: float = 600):
    """Один браузер за раз: профиль нельзя открыть дважды — ни в одном процессе, ни в двух (окно и фоновая отправка)."""
    ok, why = available()
    if not ok:
        raise PlatformError(why)
    from playwright.sync_api import sync_playwright
    from app.paths import data_dir
    from app.telegram.backend import SessionLock
    if not _lock.acquire(timeout=wait):
        raise PlatformError("Браузер занят другой задачей")
    plock = SessionLock(data_dir() / "browser.lock")
    deadline = time.time() + wait
    while not plock.try_acquire():   # профилем пользуется другая часть Crosspost (окно или фоновая отправка)
        if time.time() >= deadline:
            _lock.release()
            raise PlatformError("Браузер занят другой задачей (публикацией или сбором статистики) — попробуйте через минуту")
        time.sleep(1)
    try:
        with sync_playwright() as pw:
            ctx = _launch(pw, headless, downloads)
            _restore_cookies(ctx)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.set_default_timeout(20000)
                yield ctx, page
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    finally:
        try:   # процессы Edge с профилем Crosspost, которые не закрылись вместе с браузером
            from app import perf
            perf.kill_stray_browsers()
        except Exception:  # noqa: BLE001
            pass
        plock.release()
        _lock.release()


def _cookie_file():
    return sub("browser") / "crosspost_cookies.json"


def save_cookies(ctx):
    """Сохраняем куки отдельно: «сеансовые» куки браузер при закрытии стирает, а вход на сайте держится на них."""
    import json
    try:
        _cookie_file().write_text(json.dumps(ctx.cookies()), encoding="utf-8")
    except Exception:
        pass


def _restore_cookies(ctx):
    import json
    f = _cookie_file()
    if not f.exists():
        return
    try:
        have = {(c["name"], c["domain"]) for c in ctx.cookies()}
        saved = [c for c in json.loads(f.read_text(encoding="utf-8")) if (c["name"], c["domain"]) not in have]
        now = time.time()
        saved = [c for c in saved if c.get("expires", -1) in (-1, None) or c["expires"] > now]
        if saved:
            ctx.add_cookies(saved)
    except Exception:
        pass


def dump(page, tag: str) -> str:
    """Снимок экрана и HTML страницы для разбора. Возвращает имя PNG в папке browser_debug."""
    folder = sub("browser_debug")
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{re.sub(r'[^a-z0-9_-]', '', tag.lower())}"
    try:
        page.screenshot(path=str(folder / f"{name}.png"), full_page=False)
        (folder / f"{name}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        return ""
    for old in sorted(folder.glob("*.png"))[:-20]:   # храним последние 20
        old.unlink(missing_ok=True)
        old.with_suffix(".html").unlink(missing_ok=True)
    return f"{name}.png"


def first_visible(page, candidates, timeout_ms: int = 8000):
    """Первый видимый элемент из списка вариантов (сайты меняют вёрстку — проверяем несколько)."""
    deadline = time.time() + timeout_ms / 1000
    while True:
        for c in candidates:
            try:
                loc = c(page) if callable(c) else page.locator(c)
                n = loc.count()
                for i in range(min(n, 5)):
                    el = loc.nth(i)
                    if el.is_visible():
                        return el
            except Exception:
                continue
        if time.time() > deadline:
            return None
        page.wait_for_timeout(300)
