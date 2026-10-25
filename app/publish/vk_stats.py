"""Статистика постов ВК — со страницы «Статистика → Посты → Контент», через браузер Crosspost.

Как это устроено у ВК (по разведке): страница просит данные методом statsDashboard.getOwnerStats, а сама
таблица приходит не в ответе, а кусками через очередь событий (queuev4…/im…): событие «vboardcard» с
task_result — {total_chunks, current_chunk, chunk}. Склеиваем куски, разбираем таблицу post_content_list:
по каждому посту — охват, просмотры, лайки, комментарии, «поделились», закладки. Следующие 20 постов
страница подгружает при прокрутке — прокручиваем, пока не дойдём до постов старше нужного.

Только чтение: ничего не нажимаем, кроме прокрутки. Ключей не сохраняем — только цифры постов.
"""
import json
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app import db
from core.common import PlatformError

COLS = ("reach", "views", "likes", "comments", "reposts", "bookmarks")
LABEL = {"reach": "Охват", "views": "Просмотры", "likes": "Лайки", "comments": "Комментарии",
         "reposts": "Поделились", "bookmarks": "Закладки"}
EVERY_H = 3   # фоновый сбор — раз в 3 часа

db.conn().executescript("""
CREATE TABLE IF NOT EXISTS vk_posts (
    key   TEXT PRIMARY KEY,      -- -224369563_3161
    owner INTEGER, post INTEGER, date INTEGER, title TEXT, text TEXT, kind TEXT, href TEXT, thumb TEXT
);
CREATE TABLE IF NOT EXISTS vk_snap (
    key TEXT NOT NULL, at INTEGER NOT NULL,
    reach INTEGER, views INTEGER, likes INTEGER, comments INTEGER, reposts INTEGER, bookmarks INTEGER,
    PRIMARY KEY (key, at)
);
""")


# --- разбор ответов страницы ----------------------------------------------------------------

class Collector:
    """Склеивает куски таблицы из очереди событий ВК."""

    def __init__(self):
        self.parts: dict = {}
        self.tables: list = []   # готовые таблицы post_content_list по порядку прихода

    def feed(self, payload):
        for ev in _events(payload):
            data = ev.get("data") if isinstance(ev, dict) else None
            tr = (data or {}).get("task_result") if isinstance(data, dict) else None
            if not isinstance(tr, dict) or "chunk" not in tr:
                continue
            tid = str(data.get("task_id"))
            p = self.parts.setdefault(tid, {"total": int(float(tr.get("total_chunks") or 1)), "got": {}})
            p["got"][int(tr.get("current_chunk") or 1)] = tr["chunk"]
            if len(p["got"]) >= p["total"]:
                txt = "".join(p["got"][k] for k in sorted(p["got"]))
                self.parts.pop(tid, None)
                try:
                    obj = json.loads(txt)
                except ValueError:
                    continue
                for card in _cards(obj):
                    # первой ВК присылает «заготовку» таблицы (isLazy, без строк) — её пропускаем, ждём строки
                    if card.get("id") == "post_content_list" and isinstance(card.get("rows"), list):
                        self.tables.append(card)


def _events(payload):
    if isinstance(payload, list):
        for x in payload:
            yield from _events(x)
    elif isinstance(payload, dict):
        for ev in payload.get("events") or []:
            yield ev


def _cards(obj):
    if not isinstance(obj, dict):
        return []
    out = []
    if isinstance(obj.get("item"), dict):
        out.append(obj["item"])
    for x in obj.get("items") or []:
        if isinstance(x, dict):
            out.append(x)
    return out


def rows_of(card: dict) -> list[dict]:
    """Строки таблицы → {key, owner, post, date, title, text, kind, href, thumb, reach, views, …}."""
    cols = [c.get("key") for c in card.get("columns") or []]
    out = []
    for row in card.get("rows") or []:
        cells = row.get("cells") or []
        if not cells:
            continue
        head = (cells[0] or {}).get("data") or {}
        raw = str(head.get("rawId") or "")
        m = re.fullmatch(r"(-?\d+)_(\d+)", raw)
        if not m:
            continue
        att = (head.get("attachments") or [{}])[0] or {}
        sizes = ((att.get("image") or {}).get("sizes") or [{}])
        r = {"key": raw, "owner": int(m.group(1)), "post": int(m.group(2)), "date": int(head.get("uploadDate") or 0),
             "title": (head.get("title") or "").strip(), "text": head.get("text") or "", "kind": att.get("type") or "",
             "href": head.get("href") or f"https://vk.com/wall{raw}", "thumb": (sizes[0] or {}).get("url") or ""}
        for i, c in enumerate(cells[1:], start=1):
            k = cols[i] if i < len(cols) else None
            if k in COLS:
                v = ((c or {}).get("data") or {}).get("value")
                r[k] = int(v) if isinstance(v, (int, float)) and v >= 0 else None   # −1 у ВК — «нет данных»
        out.append(r)
    return out


def save(rows: list[dict], at: int | None = None) -> int:
    """Посты — в vk_posts, цифры — новым замером (если поменялись или прошлому больше 12 часов)."""
    at = int(at or time.time())
    n = 0
    with db.tx() as c:
        for r in rows:
            c.execute("INSERT OR REPLACE INTO vk_posts (key, owner, post, date, title, text, kind, href, thumb) VALUES (?,?,?,?,?,?,?,?,?)",
                      (r["key"], r["owner"], r["post"], r["date"], r["title"], r["text"], r["kind"], r["href"], r["thumb"]))
            last = c.execute("SELECT * FROM vk_snap WHERE key=? ORDER BY at DESC LIMIT 1", (r["key"],)).fetchone()
            vals = tuple(r.get(k) for k in COLS)
            if last is not None and tuple(last[k] for k in COLS) == vals and at - last["at"] < 12 * 3600:
                continue
            c.execute("INSERT OR REPLACE INTO vk_snap (key, at, " + ", ".join(COLS) + ") VALUES (?,?,?,?,?,?,?,?)", (r["key"], at) + vals)
            n += 1
    return n


# --- живые цифры со страницы «Статистика записи» ---------------------------------------------
# Таблица в кабинете статистики отстаёт от стены, а страница записи (…?w=posts_reach_wall…) показывает текущие цифры:
# «Просмотры записи», подписчики / не подписчики, «Обратная связь» — лайки, репосты, комментарии. Разметку не угадываем,
# читаем текст окна по подписям: так мелкие правки вёрстки ломают меньше.

def _num(t: str):
    t = (t or "").replace("\u00a0", " ").replace("\u202f", " ").strip()
    return int(re.sub(r"\s+", "", t)) if re.fullmatch(r"\d{1,3}(?: \d{3})*|\d+", t) else None


def parse_post_stats(text: str) -> dict | None:
    """Текст окна «Статистика записи» → {views, likes, reposts, comments, subs, nonsubs} или None."""
    lines = [x.strip() for x in (text or "").replace("\r", "").split("\n") if x.strip()]
    low = [x.lower() for x in lines]
    try:
        i = next(k for k, x in enumerate(low) if x.startswith("просмотры записи"))
    except StopIteration:
        return None
    out: dict = {}
    for x in lines[i + 1:i + 5]:
        v = _num(x)
        if v is not None:
            out["views"] = v
            break
    for x in lines[i + 1:i + 8]:
        m = re.fullmatch(r"(\d[\d ]*)\s*/\s*(\d[\d ]*)", x.replace("\u00a0", " "))
        if m:
            out["subs"], out["nonsubs"] = _num(m.group(1)), _num(m.group(2))
            break
    try:
        j = next(k for k, x in enumerate(low) if k > i and x.startswith("обратная связь"))
        nums = []
        for x in lines[j + 1:j + 8]:
            v = _num(x)
            if v is not None:
                nums.append(v)
            if len(nums) == 3:
                break
        if len(nums) == 3:
            out["likes"], out["reposts"], out["comments"] = nums
    except StopIteration:
        pass
    return out if "views" in out else None


def live_post(page, key: str) -> dict | None:
    """Открыть статистику записи и прочитать текущие цифры. None — окно не открылось или текст не разобрался."""
    from app.publish import browser as B
    page.goto(f"https://vk.ru/wall{key}?w=posts_reach_wall{key}", wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_function("() => /Просмотры записи/i.test(document.body.innerText)", timeout=15000)
    except Exception:  # noqa: BLE001
        B.dump(page, "vk_post_live")
        return None
    for _ in range(2):   # цифры подгружаются чуть позже заголовка
        page.wait_for_timeout(700)
        res = parse_post_stats(page.evaluate("() => document.body.innerText"))
        if res and "likes" in res:
            return res
    B.dump(page, "vk_post_live")
    return res


def overlay_live(page, rows: dict, report, days: int = 10, limit: int = 12) -> int:
    """Поверх цифр таблицы — живые цифры свежих постов. Ошибка на одном посте остальных не останавливает."""
    edge, n, tried = time.time() - days * 86400, 0, 0
    for r in sorted(rows.values(), key=lambda x: -x["date"]):
        if r["date"] < edge or tried >= limit:
            break
        tried += 1
        report(message=f"ВК: живые цифры поста {tried}…")
        try:
            lv = live_post(page, r["key"])
        except Exception:  # noqa: BLE001
            lv = None
        if not lv:
            continue
        for k in ("views", "likes", "reposts", "comments"):
            if lv.get(k) is not None:
                r[k] = lv[k]
        n += 1
    return n


# --- открыть страницу и собрать ------------------------------------------------------------

def stats_url(vk: dict) -> str:
    u = (vk.get("stats_url") or "").strip()
    if not u:
        gid = str(vk.get("group_id") or "").lstrip("-")
        if not gid:
            raise PlatformError("Укажите группу ВК или вставьте ссылку на её статистику")
        u = f"https://vk.ru/groups/dashboard/@club{gid}" if gid.isdigit() else f"https://vk.ru/groups/dashboard/@{gid}"
    sp = urlsplit(u if u.startswith("http") else "https://" + u)
    q = dict(parse_qsl(sp.query))
    q.update(sectionId="top_posts", subsectionId="stat_board_content")
    return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(q), ""))


def collect(report=lambda **kw: None, days: int = 45, max_pages: int = 15, headless: bool = True, wait: float = 600) -> dict:
    """Открыть статистику постов и собрать цифры постов за последние `days` дней (по 20 постов за прокрутку)."""
    from app.publish import browser as B, settings as PS, vk_recon, vk_web
    vk = PS.load(raw=True)["vk"]
    url = stats_url(vk)
    col = Collector()
    edge = time.time() - days * 86400
    report(progress=0.05, message="Открываю статистику ВК…")
    with B.session(headless=headless, wait=wait) as (ctx, page):
        if not vk_web._logged(ctx):
            raise PlatformError("Во ВК не выполнен вход в браузере Crosspost — «Настройки» → ВКонтакте → «Войти во ВКонтакте»")

        def on_response(resp):
            try:
                host = urlsplit(resp.url).hostname or ""
                if not host.startswith("queue") or resp.request.resource_type not in ("xhr", "fetch"):
                    return
                col.feed(json.loads(resp.text()))
            except Exception:   # noqa: BLE001 — не наш ответ или пустой
                pass
        page.on("response", on_response)

        def wait_table(n_before: int, sec: float) -> bool:
            end = time.time() + sec
            while time.time() < end:
                if len(col.tables) > n_before:
                    return True
                page.wait_for_timeout(400)
            return False

        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if not wait_table(0, 25):
            if not vk_recon._on_stats(page):
                B.dump(page, "vk_stats")
                raise PlatformError("Статистика ВК не открылась — проверьте, что аккаунт в браузере Crosspost — админ группы, "
                                    "и вставьте ссылку на «Статистика → Посты → Контент» в настройках ВК")
            raise PlatformError("Страница статистики открылась, но таблица постов не пришла — ВК мог поменять страницу. "
                                "Нажмите «Снять образец» и пришлите архив")
        report(progress=0.3, message="Читаю таблицу постов…")
        pages = 1
        while pages < max_pages:
            rows = [r for t in col.tables for r in rows_of(t)]
            if rows and min(r["date"] for r in rows) < edge:
                break   # дошли до старых постов
            if not (col.tables[-1].get("nextFrom")):
                break   # постов больше нет
            n = len(col.tables)
            for _ in range(3):   # следующие 20 постов страница грузит при прокрутке вниз
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(500)
            if not wait_table(n, 12):
                break
            pages += 1
            report(progress=min(0.3 + 0.6 * pages / max_pages, 0.9), message=f"Читаю таблицу постов… ({pages * 20})")
        rows = {}
        for t in col.tables:
            for r in rows_of(t):
                rows[r["key"]] = r
        live = 0
        if rows:
            try:
                live = overlay_live(page, rows, report)
            except Exception:  # noqa: BLE001 — живые цифры — добавка, таблица всё равно сохранится
                pass
        B.save_cookies(ctx)
    if not rows:
        raise PlatformError("Таблица постов пришла пустой — ВК мог поменять страницу. Нажмите «Снять образец» и пришлите архив")
    n = save(list(rows.values()))
    report(progress=1.0, message=f"ВК: прочитано постов {len(rows)}, новых замеров {n}" + (f", живые цифры: {live}" if live else ""))
    return {"posts": len(rows), "snaps": n, "live": live}


# --- для экранов --------------------------------------------------------------------------

def key_of(link: str) -> str | None:
    m = re.search(r"wall(-?\d+_\d+)", link or "")
    return m.group(1) if m else None


def latest(key: str) -> dict | None:
    r = db.query("SELECT * FROM vk_snap WHERE key=? ORDER BY at DESC LIMIT 1", (key,))
    return r[0] if r else None


def nums_for(link: str) -> dict:
    key = key_of(link)
    if not key:
        return {"error": "Ссылка на пост ВК не сохранилась — цифры не найти"}
    st = latest(key)
    if not st:
        return {"error": "Цифр этого поста ещё нет — они появятся после сбора статистики ВК (раз в 3 часа или кнопкой в «Настройки» → ВКонтакте)"}
    ago = (time.time() - st["at"]) / 3600
    when = "только что" if ago < 1 else f"{int(ago)} ч назад" if ago < 48 else f"{int(ago / 24)} дн назад"
    return {"cells": [[LABEL[k], st[k] if st[k] is not None else "—"] for k in COLS], "note": f"Статистика ВК, обновлено {when}"}


def due() -> bool:
    from app.publish import settings as PS
    vk = PS.load()["vk"]
    if not (vk.get("enabled") and vk.get("browser_ok") and vk.get("stats_auto", True) and (vk.get("stats_url") or vk.get("group_id"))):
        return False
    try:
        last = datetime.fromisoformat(vk.get("stats_at") or "2000-01-01T00:00:00+00:00")
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last > timedelta(hours=EVERY_H)


def run_and_record(report=lambda **kw: None, headless: bool = True, wait: float = 600) -> dict:
    """Собрать и запомнить итог в настройках ВК (для строки состояния)."""
    from app.publish import settings as PS
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    try:
        res = collect(report, headless=headless, wait=wait)
    except Exception as e:  # noqa: BLE001
        if "занят" not in str(e):   # браузер был занят — попробуем на следующем круге, а не через 3 часа
            PS.update("vk", {"stats_at": now, "stats_err": str(e)[:300]})
        raise
    PS.update("vk", {"stats_at": now, "stats_err": "", "stats_msg": f"Прочитано постов: {res['posts']}"})
    return res
