"""Разведка статистики ВК: как страница «Статистика → Посты → Контент» получает свои цифры.

Открывает страницу в браузере Crosspost (с вашим входом), записывает запросы, которые она делает
к ВК, и пробует нажать «Скачать» (значок ⬇ у выбора дат). Ничего не публикует и не пишет:
кнопки «Отправить», «Опубликовать» и поля комментариев не трогаются.

Результат — один архив vk_recon-<время>.zip в папке browser_debug:
  report.json — список запросов: адрес, названия параметров, ответ (ключи и куки вырезаны);
  1_*.png, 2_*.png … — снимки каждого шага, after_click.png — после «Скачать»; header.html — шапка статистики; download/* — скачанный файл, если он был;
  page_text.txt — текст страницы (цифры, как их видно на экране).
Перед отправкой архив стоит просмотреть: снимки экрана показывают страницу как есть.
"""
import json
import re
import time
import zipfile
from datetime import datetime
from urllib.parse import parse_qsl, urlsplit

from app.paths import sub
from core.common import PlatformError

HOSTS = ("vk.ru", "vk.com", "vk.me", "vkontakte.ru")
HIDDEN = "«скрыто»"
# названия полей, значения которых никогда не сохраняем
SENSITIVE = re.compile(r"(token|hash|sid|secret|key|auth|cookie|sign|csrf|session|pass|captcha|device|uuid|fingerprint|"
                       r"remix|phone|email)", re.I)
# похожее на ключи в любых строках
PATTERNS = [re.compile(r"vk1\.a\.[\w\-]+"), re.compile(r"\beyJ[\w\-]+\.[\w\-]+\.[\w\-]*"),
            re.compile(r"\b[a-f0-9]{24,}\b", re.I), re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")]
# кнопки, которые нажимать нельзя ни при каких условиях
FORBIDDEN = re.compile(r"(отправ|опублик|коммент|удал|написать|send|publish|post\b|delete)", re.I)
DOWNLOAD_HINT = re.compile(r"(скач|выгруз|экспорт|download|export)", re.I)
FORMAT_HINT = re.compile(r"(csv|xls|excel|скач|выгруз|download)", re.I)
MAX_BODY = 400_000


def dashboard_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        raise PlatformError("Вставьте ссылку на страницу статистики группы: «Статистика» → «Посты» → «Контент»")
    if not u.startswith("http"):
        u = "https://" + u
    sp = urlsplit(u)
    if not any(sp.hostname == h or (sp.hostname or "").endswith("." + h) for h in HOSTS):
        raise PlatformError("Это не ссылка ВКонтакте")
    if "/dashboard" not in sp.path and "/stats" not in sp.path:
        raise PlatformError("Нужна ссылка на страницу статистики группы (в адресе есть /groups/dashboard/)")
    return u


class Scrubber:
    """Вырезает ключи: по названиям полей, по виду значения и все значения ваших кук."""

    def __init__(self, cookie_values):
        # куки только похожие на ключи: с буквами или длинные (короткие числа совпали бы с цифрами статистики)
        self.cookies = sorted({v for v in cookie_values if v and len(v) >= 6 and (re.search(r"[A-Za-z]", v) or len(v) >= 16)},
                              key=len, reverse=True)

    def text(self, s: str) -> str:
        if not s:
            return s
        for v in self.cookies:
            if v in s:
                s = s.replace(v, HIDDEN)
        for p in PATTERNS:
            s = p.sub(HIDDEN, s)
        return s

    def obj(self, o, key: str = ""):
        if key and SENSITIVE.search(key):
            return HIDDEN if o not in (None, "", 0, False) else o
        if isinstance(o, dict):
            return {k: self.obj(v, str(k)) for k, v in o.items()}
        if isinstance(o, list):
            return [self.obj(v) for v in o[:500]] + ([f"… ещё {len(o) - 500}"] if len(o) > 500 else [])
        if isinstance(o, str):
            return self.text(o)
        return o

    def params(self, pairs) -> dict:
        out = {}
        for k, v in pairs:
            out[k] = HIDDEN if SENSITIVE.search(k) else self.text(v)
        return out


def _is_vk(url: str) -> bool:
    h = urlsplit(url).hostname or ""
    return any(h == x or h.endswith("." + x) for x in HOSTS)


def _parse_body(text: str):
    """Тело запроса/ответа: JSON, форма или текст. ВК иногда добавляет перед JSON служебный префикс."""
    if text is None:
        return None, "empty"
    t = text.strip()
    for cut in ("", "<!--", "for(;;);", "while(1);"):
        u = t[len(cut):].lstrip() if cut and t.startswith(cut) else t
        if u[:1] in "{[":
            try:
                return json.loads(u), "json"
            except ValueError:
                pass
    if "=" in t and "\n" not in t[:2000] and " " not in t[:200]:
        return parse_qsl(t, keep_blank_values=True), "form"
    return t, "text"


def run(report, url: str, headless: bool = False) -> dict:
    from app.publish import browser as B
    from app.publish import vk_web
    url = dashboard_url(url)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    work = sub("tmp") / f"vk_recon-{stamp}"
    (work / "download").mkdir(parents=True, exist_ok=True)
    calls, downloads, notes = [], [], []
    t0 = time.time()

    report(progress=0.05, message="Открываю браузер…")
    with B.session(headless=headless, downloads=True) as (ctx, page):
        if not vk_web._logged(ctx):
            raise PlatformError("Во ВК не выполнен вход в браузере Crosspost — «Настройки» → ВКонтакте → «Войти во ВКонтакте»")
        scrub = Scrubber([c.get("value", "") for c in ctx.cookies()])

        def on_response(resp):
            try:
                req = resp.request
                if req.resource_type not in ("xhr", "fetch", "document") or not _is_vk(req.url):
                    return
                sp = urlsplit(req.url)
                item = {"t": round(time.time() - t0, 2), "type": req.resource_type, "method": req.method,
                        "url": f"{sp.scheme}://{sp.hostname}{sp.path}",
                        "query": scrub.params(parse_qsl(sp.query, keep_blank_values=True)),
                        "status": resp.status, "content_type": (resp.headers or {}).get("content-type", "")}
                loc = (resp.headers or {}).get("location")
                if loc:   # перенаправление: куда отправил ВК
                    lsp = urlsplit(loc)
                    item["redirect_to"] = f"{lsp.hostname or ''}{lsp.path}"
                body = req.post_data
                if body:
                    parsed, kind = _parse_body(body[:MAX_BODY])
                    item["body_kind"] = kind
                    item["body"] = scrub.params(parsed) if kind == "form" else scrub.obj(parsed) if kind == "json" else scrub.text(str(parsed)[:5000])
                if req.resource_type != "document":
                    ct = item["content_type"]
                    if any(x in ct for x in ("json", "javascript", "text", "html")) or not ct:
                        try:
                            txt = resp.text()
                        except Exception:
                            txt = None
                        if txt is not None:
                            item["size"] = len(txt)
                            parsed, kind = _parse_body(txt[:MAX_BODY])
                            item["response_kind"] = kind
                            item["response"] = scrub.obj(parsed) if kind == "json" else scrub.text(str(parsed)[:20000])
                            if len(txt) > MAX_BODY:
                                item["response_cut"] = True
                    else:
                        item["response_kind"] = "binary"
                        dispo = (resp.headers or {}).get("content-disposition", "")
                        if dispo:
                            item["content_disposition"] = scrub.text(dispo)
                calls.append(item)
            except Exception as e:  # noqa: BLE001 — запись не должна ломать разведку
                notes.append(f"не записан ответ: {e}")

        def on_download(dl):
            try:
                name = re.sub(r"[^\w.\-]+", "_", dl.suggested_filename or "download")[:80]
                dst = work / "download" / name
                dl.save_as(str(dst))
                src = urlsplit(dl.url)
                downloads.append({"file": f"download/{name}", "size": dst.stat().st_size,
                                  "from": f"{src.scheme}://{src.hostname}{src.path}" if src.scheme.startswith("http") else src.scheme,
                                  "query": scrub.params(parse_qsl(src.query, keep_blank_values=True)) if src.scheme.startswith("http") else {}})
            except Exception as e:  # noqa: BLE001
                notes.append(f"скачивание не сохранилось: {e}")

        visited = []

        def on_nav(frame):
            try:
                if frame == page.main_frame:
                    v = urlsplit(frame.url)
                    visited.append({"t": round(time.time() - t0, 2), "page": f"{v.hostname}{v.path}"})
            except Exception:
                pass

        ctx.on("response", on_response)
        page.on("download", on_download)
        page.on("framenavigated", on_nav)
        steps = []

        def go(u, label):
            report(message=f"{label}…")
            try:
                page.goto(u, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:  # noqa: BLE001
                notes.append(f"{label}: не открылась ({str(e).splitlines()[0][:120]})")
            _settle(page)
            return snap(label)

        def snap(label):
            ok = _on_stats(page)
            n = len(steps) + 1
            page.screenshot(path=str(work / f"{n}_{'stats' if ok else 'page'}.png"), full_page=False)
            steps.append({"step": label, "landed": f"{urlsplit(page.url).hostname}{urlsplit(page.url).path}", "stats": ok})
            return ok

        report(progress=0.15)
        on = go(url, "Открываю статистику по ссылке")
        if not on:   # тот же адрес на другом домене (вход мог быть выполнен на vk.com, а ссылка — на vk.ru)
            sp = urlsplit(url)
            other = {"vk.ru": "vk.com", "vk.com": "vk.ru"}.get((sp.hostname or "").removeprefix("www."))
            if other:
                on = go(f"{sp.scheme}://{other}{sp.path}" + (f"?{sp.query}" if sp.query else ""), f"Пробую то же на {other}")
        if not on:   # как человек: страница группы → «Статистика» → «Посты» → «Контент»
            on = _via_group(page, url, go, snap, notes, steps, report)
        if on:
            if _open_posts_content(page, notes):
                snap("Вкладки «Посты» → «Контент»")
        else:
            notes.append("статистика не открылась: ВК показывает другую страницу — см. снимки и steps")
        final = page.url
        if re.search(r"(login|/join|id\.vk\.)", final):
            notes.append("ВК перенаправил на вход: " + (urlsplit(final).hostname or "") + urlsplit(final).path)

        report(progress=0.45, message="Прокручиваю таблицу постов…")
        for _ in range(4):   # подгрузка следующих строк, если таблица листается
            page.mouse.wheel(0, 1600)
            page.wait_for_timeout(1500)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(1000)

        report(progress=0.6, message="Ищу кнопку «Скачать»…")
        layout = _layout(page) if on else {}
        clicked = _click_download(page, notes) if on else ""
        if clicked:
            page.wait_for_timeout(2000)
            page.screenshot(path=str(work / "after_click.png"), full_page=False)
            item = _pick_format(page, notes)
            if item:
                notes.append(f"в меню выбран пункт: {item}")
            deadline = time.time() + 20
            while time.time() < deadline and not downloads:
                page.wait_for_timeout(500)
            if not downloads:
                notes.append("после нажатия файл не скачался за 20 с")
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            page.screenshot(path=str(work / "after_download.png"), full_page=False)
        try:
            page_text = page.evaluate("(document.querySelector('main') || document.body).innerText") or ""
        except Exception:
            page_text = ""
        page.wait_for_timeout(1000)   # дать дописаться последним ответам
        # куки могли появиться по ходу (вход, обновление сессии) — финальная чистка по всем, что есть сейчас
        final_scrub = Scrubber([c.get("value", "") for c in ctx.cookies()] + scrub.cookies)

    report(progress=0.9, message="Упаковываю…")
    summary = {"made": stamp, "page": scrub_url(url), "final_page": scrub_url(final), "stats_opened": on,
               "steps": steps, "visited": visited, "layout": layout, "requests": len(calls),
               "download_button": clicked, "downloads": downloads, "notes": notes,
               "how_to_read": "Значения ключей, кук и похожих на них строк заменены на «скрыто». "
                              "Снимки экрана — как есть: проверьте их перед отправкой."}
    (work / "report.json").write_text(final_scrub.text(json.dumps({"summary": summary, "requests": calls}, ensure_ascii=False, indent=1)),
                                      encoding="utf-8")
    (work / "page_text.txt").write_text(final_scrub.text(page_text)[:60000], encoding="utf-8")
    if layout.get("header_html"):
        (work / "header.html").write_text(final_scrub.text(layout["header_html"]), encoding="utf-8")
        layout["header_html"] = "см. header.html"
    out = sub("browser_debug") / f"vk_recon-{stamp}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(work.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(work).as_posix())
    import shutil
    shutil.rmtree(work, ignore_errors=True)
    data_calls = [c for c in calls if c.get("response_kind") == "json"]
    msg = ("Статистика не открылась — ВК показал другую страницу (см. снимки в архиве). " if not on else "") + (
           f"Готово: записано запросов — {len(calls)}, из них с данными — {len(data_calls)}; "
           + (f"файл выгрузки скачан ({downloads[0]['file'].split('/')[-1]})" if downloads else
              "кнопка «Скачать» нажата, но файла нет" if clicked else "кнопку «Скачать» не нашла" if on else "до кнопки «Скачать» не дошла")
           + f". Архив: {out.name}")
    report(progress=1.0, message=msg)
    return {"file": out.name, "requests": len(calls), "data": len(data_calls), "downloaded": bool(downloads),
            "clicked": bool(clicked), "message": msg}


def scrub_url(u: str) -> str:
    sp = urlsplit(u or "")
    q = "&".join(f"{k}={HIDDEN if SENSITIVE.search(k) else v}" for k, v in parse_qsl(sp.query, keep_blank_values=True))
    return f"{sp.scheme}://{sp.hostname}{sp.path}" + (f"?{q}" if q else "")


def _label(el) -> str:
    try:
        return " ".join(filter(None, [el.get_attribute("aria-label"), el.get_attribute("title"),
                                      el.get_attribute("data-testid"), (el.inner_text() or "")[:60]]))
    except Exception:
        return ""


def _click_download(page, notes) -> str:
    """Найти и нажать значок «Скачать» в шапке статистики. Возвращает, что нажато ('' — не нашла)."""
    sels = ['[aria-label*="Скач" i]', '[aria-label*="Выгруз" i]', '[aria-label*="Экспорт" i]', '[aria-label*="download" i]',
            '[aria-label*="export" i]', '[title*="Скач" i]', '[title*="Выгруз" i]', '[title*="Экспорт" i]',
            '[data-testid*="download" i]', '[data-testid*="export" i]', 'button:has(svg[class*="download" i])',
            'button:has(use[href*="download" i])', 'button:has(use[*|href*="download" i])', '[class*="download" i]']
    for sel in sels:
        try:
            for el in page.locator(sel).all()[:6]:
                if not el.is_visible():
                    continue
                lab = _label(el)
                if FORBIDDEN.search(lab):
                    continue
                el.click(timeout=5000)
                notes.append(f"нажата кнопка: {sel} ({lab[:60]})")
                return sel
        except Exception:
            continue
    # значок без подписи рядом с выбором дат (у ВК: «?» и «⬇» слева от «10.10 – 16.10») — берём ближайший к датам
    try:
        n = page.evaluate("""(re) => {
          const rx = new RegExp(re);
          const els = [...document.querySelectorAll('body *')].filter(e => e.children.length <= 3 && rx.test(e.innerText || '') && (e.innerText || '').length < 40);
          const date = els[els.length - 1]; if (!date) return 0;
          let box = date; for (let i = 0; box && i < 4; i++) box = box.parentElement; if (!box) return 0;
          const all = [...box.querySelectorAll('button, [role=button], a')].filter(b => b.offsetParent && b.querySelector('svg') && !(b.innerText || '').trim() && !b.contains(date));
          const before = all.filter(b => b.compareDocumentPosition(date) & Node.DOCUMENT_POSITION_FOLLOWING);
          const pick = before.length ? before[before.length - 1] : null;
          if (!pick) return 0;
          pick.setAttribute('data-cp-dl', '1'); return all.length;
        }""", DATE_RANGE)
        if n:
            el = page.locator('[data-cp-dl="1"]').first
            lab = _label(el)
            if not FORBIDDEN.search(lab):
                el.click(timeout=5000)
                notes.append(f"нажат значок рядом с датами (значков рядом: {n}) {lab[:40]}")
                return "near-date"
    except Exception as e:  # noqa: BLE001
        notes.append(f"значок у дат: {str(e).splitlines()[0][:100]}")
    # запасной путь: любая кнопка с подписью «скачать»
    try:
        for el in page.locator("button, [role=button]").all()[:200]:
            lab = _label(el)
            if lab and DOWNLOAD_HINT.search(lab) and not FORBIDDEN.search(lab) and el.is_visible():
                el.click(timeout=5000)
                notes.append(f"нажата кнопка по подписи: {lab[:60]}")
                return "by-label"
    except Exception:
        pass
    notes.append("кнопка «Скачать» не найдена")
    return ""


def _pick_format(page, notes) -> str:
    """Если после нажатия открылось меню (формат, период) — выбрать первый пункт со скачиванием."""
    try:
        for el in page.locator('[role=menuitem], [role=option], [role=menu] button, [class*="popper" i] button, '
                               '[class*="dropdown" i] [role=button], [class*="ActionSheet" i] [role=button]').all()[:30]:
            lab = _label(el)
            if lab and FORMAT_HINT.search(lab) and not FORBIDDEN.search(lab) and el.is_visible():
                el.click(timeout=5000)
                return lab[:60]
    except Exception as e:  # noqa: BLE001
        notes.append(f"меню после нажатия: {e}")
    return ""


def _settle(page):
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(2500)


def _on_stats(page) -> bool:
    """Открыта ли статистика сообщества (а не лента или вход)."""
    u = urlsplit(page.url)
    if "/dashboard" not in u.path and "/stats" not in u.path:
        return False
    try:
        t = page.evaluate("document.body.innerText.slice(0, 20000)") or ""
    except Exception:
        t = ""
    return bool(re.search(r"(Охват|Просмотры|Посетители|Подписчики|Статистика)", t))


def _visible_click(loc, notes, what) -> bool:
    try:
        for el in loc.all()[:8]:
            if el.is_visible() and not FORBIDDEN.search(_label(el)):
                el.click(timeout=5000)
                return True
    except Exception as e:  # noqa: BLE001
        notes.append(f"{what}: {str(e).splitlines()[0][:100]}")
    return False


def _via_group(page, url, go, snap, notes, steps, report) -> bool:
    """Открыть группу и перейти в «Статистику» по её меню — как это сделал бы человек."""
    sp = urlsplit(url)
    m = re.search(r"/dashboard/@?([\w.]+)", sp.path)
    if not m:
        notes.append("в ссылке нет короткого имени группы — не из чего открыть группу")
        return False
    go(f"{sp.scheme}://{sp.netloc or 'vk.ru'}/{m.group(1)}", "Открываю страницу группы")
    # что видно админу: ссылки на статистику и управление (по ним понятно, админ ли аккаунт в браузере Crosspost)
    try:
        links = page.evaluate("""() => [...document.querySelectorAll('a[href], [role=button], button')]
            .map(e => ({t: (e.innerText || e.getAttribute('aria-label') || '').trim().slice(0, 40), h: (e.getAttribute('href') || '').split('?')[0]}))
            .filter(x => /статист|управлен|dashboard|stats|manage|edit/i.test(x.t + ' ' + x.h)).slice(0, 30)""")
    except Exception:
        links = []
    steps[-1]["admin_links"] = links
    if not links:
        notes.append("на странице группы нет ссылок «Статистика»/«Управление» — похоже, аккаунт в браузере Crosspost не админ этой группы")
    report(message="Ищу «Статистику» в меню группы…")
    clicked = (_visible_click(page.locator('a[href*="dashboard"], a[href*="/stats"]'), notes, "ссылка на статистику")
               or _visible_click(page.get_by_text(re.compile(r"^\s*Статистика\s*$")), notes, "кнопка «Статистика»"))
    if not clicked:
        # иногда «Статистика» спрятана в меню «Ещё» / «Управление»
        if _visible_click(page.get_by_text(re.compile(r"^\s*(Ещё|Управление)\s*$")), notes, "меню группы"):
            page.wait_for_timeout(1200)
            clicked = _visible_click(page.get_by_text(re.compile(r"^\s*Статистика\s*$")), notes, "«Статистика» в меню")
    if not clicked:
        notes.append("кнопку «Статистика» на странице группы не нашла")
        return False
    _settle(page)
    return snap("Нажала «Статистика» в группе")


def _open_posts_content(page, notes):
    """Вкладки «Посты» → «Контент», если открыто что-то другое."""
    q = urlsplit(page.url).query
    if "top_posts" in q and "stat_board_content" in q:
        return False
    for name in ("Посты", "Контент"):
        if _visible_click(page.get_by_text(re.compile(rf"^\s*{name}\s*$")), notes, f"вкладка «{name}»"):
            _settle(page)
        else:
            notes.append(f"вкладку «{name}» не нашла")
    return True


DATE_RANGE = r"\d{1,2}\.\d{1,2}(\.\d{2,4})?\s*[–—-]\s*\d{1,2}\.\d{1,2}"


def _layout(page) -> dict:
    """Шапка статистики (кусок разметки вокруг выбора дат) и все кнопки на странице — чтобы найти «Скачать» точно."""
    try:
        return page.evaluate("""(re) => {
          const rx = new RegExp(re);
          const els = [...document.querySelectorAll('body *')].filter(e => e.children.length <= 3 && rx.test(e.innerText || '') && (e.innerText || '').length < 40);
          const date = els[els.length - 1];
          let box = date; for (let i = 0; box && i < 4; i++) box = box.parentElement;
          const btns = [...document.querySelectorAll('button, [role=button], a[download]')].filter(b => b.offsetParent).slice(0, 120).map(b => ({
            text: (b.innerText || '').trim().slice(0, 30), aria: b.getAttribute('aria-label') || '', title: b.getAttribute('title') || '',
            testid: b.getAttribute('data-testid') || '', cls: (b.className && b.className.baseVal === undefined ? b.className : '').toString().slice(0, 80),
            icon: !!b.querySelector('svg'), near_date: !!(box && box.contains(b))}));
          return {date_text: date ? date.innerText.trim() : '', header_html: box ? box.outerHTML.slice(0, 15000) : '', buttons: btns};
        }""", DATE_RANGE)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}
