"""Посты, вышедшие в обход Crosspost (ВКонтакте и Bluesky), — чтобы они попадали в календарь с превью.

ВК: берём то, что уже собирает «Статистика ВК» (vk_posts + последний замер vk_snap), превью — по ссылке из таблицы.
Bluesky: лента вашего аккаунта (getAuthorFeed) — вход не обязателен, хватит имени аккаунта в настройках.
Всё складывается в таблицу ext_posts; обновление — в фоне, не чаще раза в 3 часа (или кнопкой).
Статистику площадок между собой не смешиваем: у каждой свой индекс — цифра поста к медиане этой же площадки.
"""
import re
import statistics
import threading
import time
from datetime import datetime, timezone

import requests

from app import db
from app.paths import sub
from core.common import PlatformError

EVERY_H = 3
MIN_AGE_D = 3        # индекс (как оценка) — у постов старше 3 суток
MATURE_D = 7         # «зрелый» пост (по нему считается норма): цифры устоялись — старше 7 суток, как у Telegram
MIN_POSTS = 5        # и только если на площадке есть из чего считать норму
LABEL = {"vk": "ВКонтакте", "bsky": "Bluesky", "e6ai": "e6ai", "e621": "e621", "da": "DeviantArt"}
VIEWS = ("vk", "da")   # где есть просмотры — индекс по ним, иначе по реакциям

db.conn().executescript("""
CREATE TABLE IF NOT EXISTS ext_posts (
    key TEXT PRIMARY KEY,          -- vk:-224369563_3161 / bsky:3kabcdef
    platform TEXT NOT NULL, date INTEGER NOT NULL, text TEXT, link TEXT, kind TEXT, n_media INTEGER DEFAULT 1,
    thumb_url TEXT, preview TEXT,
    views INTEGER, likes INTEGER, reposts INTEGER, replies INTEGER, updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS ext_meta (k TEXT PRIMARY KEY, v TEXT);
""")

_lock = threading.Lock()
_busy = False


def _meta(k, default=None):
    r = db.query("SELECT v FROM ext_meta WHERE k=?", (k,))
    return r[0]["v"] if r else default


def _set_meta(k, v):
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO ext_meta (k, v) VALUES (?,?)", (k, str(v)))


# --- ВКонтакте: из уже собранной статистики -----------------------------------------------------------

def mirror_vk() -> int:
    """Переносит посты ВК из vk_posts в ext_posts (цифры — последний замер). Локально, без сети."""
    try:
        rows = db.query("""SELECT p.key, p.date, p.title, p.text, p.kind, p.href, p.thumb,
                           s.views, s.likes, s.reposts, s.comments, s.at AS snap_at
                           FROM vk_posts p LEFT JOIN vk_snap s ON s.key = p.key
                           AND s.at = (SELECT MAX(at) FROM vk_snap WHERE key = p.key)""")
    except Exception:   # noqa: BLE001 — таблиц ВК ещё нет
        return 0
    now = int(time.time())
    n = 0
    with db.tx() as c:
        for r in rows:
            key = "vk:" + r["key"]
            text = (r["title"] or r["text"] or "").strip()
            kind = "vid" if r["kind"] in ("video", "short_video") else "img"
            old = c.execute("SELECT thumb_url, preview, views, likes, reposts, replies, text, updated_at FROM ext_posts WHERE key=?", (key,)).fetchone()
            keep_prev = old["preview"] if old and old["thumb_url"] == (r["thumb"] or "") else None
            stamp = int(r["snap_at"] or now)   # «обновлено» = время замера ВК, а не момент пересчёта: так видно, насколько цифры свежие
            if old and (old["views"], old["likes"], old["reposts"], old["replies"], old["text"], old["updated_at"]) == (
                    r["views"], r["likes"], r["reposts"], r["comments"], text[:300], stamp):
                continue   # ничего не поменялось — не трогаем (иначе ключ календаря менялся бы при каждом вызове)
            c.execute("""INSERT INTO ext_posts (key, platform, date, text, link, kind, thumb_url, preview, views, likes, reposts, replies, updated_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(key) DO UPDATE SET date=excluded.date, text=excluded.text, link=excluded.link, kind=excluded.kind,
                           thumb_url=excluded.thumb_url, preview=excluded.preview, views=excluded.views, likes=excluded.likes,
                           reposts=excluded.reposts, replies=excluded.replies, updated_at=excluded.updated_at""",
                      (key, "vk", int(r["date"] or 0), text[:300], r["href"] or "", kind, r["thumb"] or "", keep_prev,
                       r["views"], r["likes"], r["reposts"], r["comments"], stamp))
            n += 1
    return n


# --- Bluesky: лента аккаунта --------------------------------------------------------------------------

def _thumb_of(embed: dict) -> tuple[str, str, int]:
    """(ссылка на превью, тип, число картинок) из вложения поста."""
    if not isinstance(embed, dict):
        return "", "img", 1
    t = str(embed.get("$type", ""))
    if "recordWithMedia" in t:
        return _thumb_of(embed.get("media") or {})
    if t.startswith("app.bsky.embed.images"):
        ims = embed.get("images") or []
        return (ims[0].get("thumb") or ims[0].get("fullsize") or "") if ims else "", "img", max(1, len(ims))
    if t.startswith("app.bsky.embed.video"):
        return embed.get("thumbnail") or "", "vid", 1
    if t.startswith("app.bsky.embed.external"):
        ex = embed.get("external") or {}
        uri = str(ex.get("uri", ""))
        return ex.get("thumb") or "", "gif" if re.search(r"tenor|giphy|\.gif", uri, re.I) else "img", 1
    return "", "img", 1


def parse_feed(feed: list, handle_fallback: str = "") -> list[dict]:
    """Элементы getAuthorFeed → строки ext_posts (репосты чужого пропускаем)."""
    out = []
    for it in feed or []:
        if it.get("reason"):
            continue   # репост, закреп
        p = it.get("post") or {}
        rec = p.get("record") or {}
        if rec.get("reply"):
            continue
        uri = p.get("uri") or ""
        rkey = uri.rsplit("/", 1)[-1]
        if not rkey:
            continue
        try:
            dt = datetime.fromisoformat(str(rec.get("createdAt") or p.get("indexedAt")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        thumb, kind, n = _thumb_of(p.get("embed") or {})
        who = (p.get("author") or {}).get("handle") or handle_fallback
        out.append({"key": "bsky:" + rkey, "platform": "bsky", "date": int(dt.timestamp()), "text": (rec.get("text") or "").strip()[:300],
                    "link": f"https://bsky.app/profile/{who}/post/{rkey}", "kind": kind, "n_media": n, "thumb_url": thumb,
                    "views": None, "likes": p.get("likeCount", 0), "reposts": (p.get("repostCount", 0) or 0) + (p.get("quoteCount", 0) or 0),
                    "replies": p.get("replyCount", 0)})
    return out


def fetch_bluesky(days: int, max_pages: int = 20) -> tuple[list[dict], float]:
    from app.publish import settings as PS
    from core.common import load_token
    from platforms import bluesky as BS
    c = PS.load(raw=True).get("bluesky") or {}
    tok = load_token("bluesky") or {}
    handle = (c.get("handle") or tok.get("handle") or "").strip().lstrip("@")
    actor = tok.get("did") or handle
    if not actor:
        raise PlatformError("Bluesky: не указан аккаунт — «Настройки» → Bluesky")
    tries = []
    if tok.get("accessJwt"):
        try:
            sess = BS.session(c)
            hdr = {"Authorization": f"Bearer {sess['accessJwt']}", "atproto-proxy": "did:web:api.bsky.app#bsky_appview"}
            for base in dict.fromkeys([BS._home(c, sess), BS._pds(c)]):
                tries.append((base, hdr))
        except PlatformError:
            pass
    tries += [("https://public.api.bsky.app", {}), ("https://api.bsky.app", {})]
    edge = time.time() - days * 86400
    last_err = None
    for base, hdr in tries:
        rows, cursor, ok = [], None, False
        try:
            for _ in range(max_pages):
                params = {"actor": actor, "limit": 100, "filter": "posts_no_replies"}
                if cursor:
                    params["cursor"] = cursor
                r = requests.get(f"{base}/xrpc/app.bsky.feed.getAuthorFeed", params=params, headers=hdr, timeout=25)
                if r.status_code != 200:
                    raise BS._err(r, "лента")
                j = r.json()
                ok = True
                page = parse_feed(j.get("feed") or [], handle)
                rows += page
                cursor = j.get("cursor")
                oldest = min((x["date"] for x in page), default=None)
                if not cursor or not j.get("feed") or (oldest is not None and oldest < edge):
                    break
            return [r for r in rows if r["date"] >= edge], (0 if not cursor else edge)
        except requests.RequestException as e:
            last_err = PlatformError(f"нет связи с {base.split('//')[-1]}: {type(e).__name__}")
        except PlatformError as e:
            last_err = e
        if ok and rows:
            return [r for r in rows if r["date"] >= edge], edge
    raise last_err or PlatformError("Bluesky: лента недоступна")


def prune(platform: str, keys: set, since: float) -> int:
    """Убрать посты, которых на площадке больше нет (удалены), — только в той части ленты, что мы прочитали целиком."""
    gone = [r["key"] for r in db.query("SELECT key FROM ext_posts WHERE platform=? AND date>=?", (platform, int(since))) if r["key"] not in keys]
    with db.tx() as c:
        for k in gone:
            c.execute("DELETE FROM ext_posts WHERE key=?", (k,))
    return len(gone)


# --- e6ai / e621: ваши загрузки (публичный API) ----------------------------------------------------

def parse_booru(site: str, base: str, posts: list) -> list[dict]:
    out = []
    for p in posts or []:
        pid = p.get("id")
        try:
            dt = datetime.fromisoformat(str(p.get("created_at")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if not pid:
            continue
        f = p.get("file") or {}
        ext = str(f.get("ext") or "").lower()
        thumb = (p.get("sample") or {}).get("url") or (p.get("preview") or {}).get("url") or ""
        if ext in ("webm", "mp4"):
            kind = "vid"
        else:
            kind = "gif" if ext == "gif" else "img"
        desc = (p.get("description") or "").strip().split("\n")[0]
        sc = p.get("score") or {}
        out.append({"key": f"{site}:{pid}", "platform": site, "date": int(dt.timestamp()), "text": (desc or f"{site} #{pid}")[:300],
                    "link": f"{base}/posts/{pid}", "kind": kind, "n_media": 1, "thumb_url": thumb, "views": None,
                    "likes": (p.get("fav_count") or 0) + max(int(sc.get("total") or 0), 0), "reposts": 0, "replies": p.get("comment_count") or 0})
    return out


def fetch_booru(site: str, base: str, username: str, api_key: str, days: int, max_pages: int = 15) -> tuple[list[dict], float]:
    from app import __version__
    edge = time.time() - days * 86400
    hdr = {"User-Agent": f"Crosspost/{__version__} (by {username} on {site})"}
    auth = (username, api_key) if api_key else None
    rows, page, reached = [], None, False
    for _ in range(max_pages):
        params = {"tags": f"user:{username.replace(' ', '_')}", "limit": 320}
        if page:
            params["page"] = f"b{page}"
        try:
            r = requests.get(f"{base}/posts.json", params=params, headers=hdr, auth=auth, timeout=30)
        except requests.RequestException as e:
            raise PlatformError(f"{site}: нет связи ({type(e).__name__})")
        if r.status_code in (401, 403):
            raise PlatformError(f"{site} не принял имя пользователя или API-ключ")
        if r.status_code != 200:
            raise PlatformError(f"{site}: HTTP {r.status_code}")
        posts = r.json().get("posts") or []
        rows += parse_booru(site, base, posts)
        if not posts:
            reached = True
            break
        page = min(p["id"] for p in posts)
        if min(x["date"] for x in parse_booru(site, base, posts) or [{"date": edge}]) < edge:
            break
    return [x for x in rows if x["date"] >= edge], (0 if reached else edge)


# --- DeviantArt: ваша галерея --------------------------------------------------------------------

def parse_da(items: list, stats_by_id: dict) -> list[dict]:
    out = []
    for d in items or []:
        did = d.get("deviationid")
        if not did:
            continue
        try:
            ts = int(float(d.get("published_time")))
        except (TypeError, ValueError):
            continue
        st = stats_by_id.get(did) or d.get("stats") or {}
        prev = (d.get("preview") or {}).get("src") or ((d.get("thumbs") or [{}])[-1].get("src")) or ""
        ct = (d.get("content") or {}).get("src") or ""
        out.append({"key": f"da:{did}", "platform": "da", "date": ts, "text": (d.get("title") or "")[:300], "link": d.get("url") or "",
                    "kind": "gif" if ct.lower().split("?")[0].endswith(".gif") else "img", "n_media": 1, "thumb_url": prev,
                    "views": st.get("views"), "likes": st.get("favourites") or 0, "reposts": 0, "replies": st.get("comments") or 0})
    return out


def fetch_da(c: dict, days: int, max_pages: int = 20) -> tuple[list[dict], float]:
    from platforms import deviantart as DA
    tok = DA._token(c)
    hdr = {"Authorization": f"Bearer {tok}"}
    edge = time.time() - days * 86400
    rows, offset, reached = [], 0, False
    for _ in range(max_pages):
        try:
            r = requests.get(f"{DA.API}/gallery/all", params={"limit": 24, "offset": offset}, headers=hdr, timeout=30)
        except requests.RequestException as e:
            raise PlatformError(f"DeviantArt: нет связи ({type(e).__name__})")
        j = DA._json(r, "галерея")
        res = j.get("results") or []
        ids = [d["deviationid"] for d in res if d.get("deviationid")]
        sts = {}
        if ids:
            try:
                m = DA._json(requests.get(f"{DA.API}/deviation/metadata", params=[("deviationids[]", i) for i in ids] + [("ext_stats", "true")],
                                          headers=hdr, timeout=30), "цифры")
                sts = {x.get("deviationid"): (x.get("stats") or {}) for x in m.get("metadata", [])}
            except PlatformError:
                pass   # цифры не пришли — покажем работы без них
        rows += parse_da(res, sts)
        if not j.get("has_more") or not res:
            reached = True
            break
        offset = j.get("next_offset") or offset + len(res)
        if min((x["date"] for x in parse_da(res, {})), default=edge) < edge:
            break
    return [x for x in rows if x["date"] >= edge], (0 if reached else edge)


def save_rows(rows: list[dict]) -> int:
    now = int(time.time())
    with db.tx() as c:
        for r in rows:
            old = c.execute("SELECT thumb_url, preview FROM ext_posts WHERE key=?", (r["key"],)).fetchone()
            prev = old["preview"] if old and old["thumb_url"] == r["thumb_url"] else None
            c.execute("""INSERT INTO ext_posts (key, platform, date, text, link, kind, n_media, thumb_url, preview, views, likes, reposts, replies, updated_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(key) DO UPDATE SET date=excluded.date, text=excluded.text, link=excluded.link, kind=excluded.kind,
                           n_media=excluded.n_media, thumb_url=excluded.thumb_url, preview=excluded.preview, views=excluded.views,
                           likes=excluded.likes, reposts=excluded.reposts, replies=excluded.replies, updated_at=excluded.updated_at""",
                      (r["key"], r["platform"], r["date"], r["text"], r["link"], r["kind"], r.get("n_media", 1), r["thumb_url"], prev,
                       r.get("views"), r.get("likes"), r.get("reposts"), r.get("replies"), now))
    return len(rows)


def fetch_previews(limit: int = 80) -> int:
    """Скачивает недостающие превью в previews/ (для календаря и поиска одинаковых артов)."""
    got = 0
    for r in db.query("SELECT key, thumb_url FROM ext_posts WHERE preview IS NULL AND thumb_url != '' ORDER BY date DESC LIMIT ?", (limit,)):
        try:
            resp = requests.get(r["thumb_url"], timeout=20)
            if resp.status_code != 200 or len(resp.content) < 200:
                continue
            from io import BytesIO
            from PIL import Image
            with Image.open(BytesIO(resp.content)) as im:
                im = im.convert("RGB")
                im.thumbnail((640, 640))
                name = "ext_" + re.sub(r"[^\w-]+", "_", r["key"]) + ".jpg"
                im.save(sub("previews") / name, "JPEG", quality=82)
        except Exception:   # noqa: BLE001 — картинка не скачалась: попробуем в следующий раз
            continue
        with db.tx() as c:
            c.execute("UPDATE ext_posts SET preview=? WHERE key=?", (name, r["key"]))
        got += 1
    return got


def sync(force: bool = False) -> dict:
    """Один круг: ВК (локально) + Bluesky (сеть) + превью. Ошибки площадок не мешают друг другу."""
    global _busy
    with _lock:
        if _busy:
            return {"busy": True}
        _busy = True
    res = {"vk": 0, "bsky": 0, "e6ai": 0, "e621": 0, "da": 0, "previews": 0, "errors": []}
    try:
        res["vk"] = mirror_vk()
        from app.publish import settings as PS
        from core.common import load_token
        cfg = PS.load(raw=False)
        raw = PS.load(raw=True)

        def run(plat, fn):
            try:
                have = bool(db.query("SELECT 1 FROM ext_posts WHERE platform=? LIMIT 1", (plat,)))
                rows, since = fn(45 if have else 400)
                res[plat] = save_rows(rows)
                if rows or since == 0:
                    prune(plat, {r["key"] for r in rows}, since)
            except PlatformError as e:
                res["errors"].append(str(e))
            except Exception as e:  # noqa: BLE001
                res["errors"].append(f"{LABEL[plat]}: {type(e).__name__}")
        bs = cfg.get("bluesky") or {}
        if bs.get("enabled") and (bs.get("handle") or (load_token("bluesky") or {}).get("did")):
            run("bsky", fetch_bluesky)
        e6 = raw.get("e6ai") or {}
        if cfg["e6ai"].get("enabled"):
            if (e6.get("username") or "").strip():
                run("e6ai", lambda d: fetch_booru("e6ai", "https://e6ai.net", e6["username"].strip(), e6.get("api_key") or "", d))
            acc = (e6.get("accounts") or {}).get("e621") or {}
            if (acc.get("username") or "").strip():
                run("e621", lambda d: fetch_booru("e621", "https://e621.net", acc["username"].strip(), acc.get("api_key") or "", d))
        da = cfg.get("deviantart") or {}
        if da.get("enabled") and (load_token("deviantart") or {}).get("access_token"):
            run("da", lambda d: fetch_da(da, d))
        res["previews"] = fetch_previews()
        _set_meta("synced_at", int(time.time()))
        _set_meta("errors", "; ".join(res["errors"]))
    finally:
        _busy = False
    return res


def due() -> bool:
    try:
        return time.time() - float(_meta("synced_at", 0)) > EVERY_H * 3600
    except ValueError:
        return True


def sync_async(force: bool = False) -> bool:
    """Запустить обновление в фоне, если пора (или force). True — запустили."""
    if _busy or not (force or due()):
        return False
    threading.Thread(target=sync, kwargs={"force": force}, daemon=True, name="extposts").start()
    return True


def state() -> dict:
    return {"busy": _busy, "synced_at": float(_meta("synced_at", 0) or 0), "errors": _meta("errors", "")}


def data_key():
    """Меняется, когда пришли/обновились внешние посты или превью."""
    r = db.query("SELECT COUNT(*) AS n, MAX(updated_at) AS u, COUNT(preview) AS p FROM ext_posts")[0]
    return (r["n"], r["u"], r["p"])


# --- для календаря ------------------------------------------------------------------------------------

def _score(r) -> float | None:
    if r["platform"] in VIEWS and r["views"]:
        return float(r["views"])
    s = (r["likes"] or 0) + 2 * (r["reposts"] or 0) + (r["replies"] or 0)
    return float(s)


WINDOW_D = 28   # норма — по постам ±4 недели вокруг поста (как у Telegram): аудитория растёт, старые цифры её не тянут вниз


def _norms() -> dict:
    """Зрелые посты площадки (дата, оценка): пост старше 7 суток И последний замер сделан не раньше чем через 7 суток
    после выхода — иначе в норму попадают ранние, ещё не набравшие цифры замеры. Норму для конкретного поста даёт base()."""
    out = {}
    edge = time.time() - MATURE_D * 86400
    for plat in LABEL:
        rows = db.query("SELECT * FROM ext_posts WHERE platform=? AND date<? AND updated_at-date>=? ORDER BY date",
                        (plat, edge, MATURE_D * 86400))
        for by_views in (True, False):
            xs = [(r["date"], _score(r)) for r in rows
                  if bool(r["platform"] in VIEWS and r["views"]) == by_views and _score(r) is not None]
            if len(xs) >= MIN_POSTS:
                out[(plat, by_views)] = xs
    return out


def base(series, ts: float):
    """Норма для поста вышедшего в ts: медиана зрелых постов ±4 недели от него; если их мало — ближайшие по времени."""
    if not series:
        return None
    near = [v for d, v in series if abs(d - ts) <= WINDOW_D * 86400]
    if len(near) < MIN_POSTS:
        near = [v for d, v in sorted(series, key=lambda x: abs(x[0] - ts))[:MIN_POSTS * 2]]
    m = statistics.median(near)
    return m if m > 0 else None


VK_BINS = (72, 96, 120, 144)   # часы: по каким возрастам строим кривую набора просмотров


def vk_curve():
    """Доля итоговых просмотров ВК к возрасту поста — по истории замеров (vk_snap) зрелых постов. None — данных мало."""
    try:
        rows = db.query("""SELECT s.key AS k, s.at AS at, s.views AS v, p.date AS d FROM vk_snap s JOIN vk_posts p ON p.key = s.key
                           WHERE s.views IS NOT NULL ORDER BY s.key, s.at""")
    except Exception:  # noqa: BLE001 — таблиц ВК ещё нет
        return None
    by: dict = {}
    for r in rows:
        by.setdefault(r["k"], []).append(((r["at"] - r["d"]) / 3600, r["v"]))
    fr: dict = {b: [] for b in VK_BINS}
    for snaps in by.values():
        ripe = [v for h, v in snaps if h >= MATURE_D * 24]
        if not ripe or ripe[-1] <= 0:
            continue
        final = ripe[-1]
        for b in VK_BINS:
            near = [(abs(h - b), v) for h, v in snaps if abs(h - b) <= 12]
            if near:
                fr[b].append(min(1.0, min(near)[1] / final))
    pts = [(b, statistics.median(x)) for b, x in fr.items() if len(x) >= MIN_POSTS]
    if len(pts) < 2:
        return None
    pts.sort()
    fixed, top = [], 0.0
    for h, f in pts:   # доля не убывает
        top = max(top, f); fixed.append((h, top))
    return fixed + [(MATURE_D * 24, 1.0)]


def frac_at(curve, h: float) -> float:
    if h <= curve[0][0]:
        return max(0.05, curve[0][1] * h / curve[0][0])
    for (h0, f0), (h1, f1) in zip(curve, curve[1:]):
        if h <= h1:
            return max(0.05, f0 + (f1 - f0) * (h - h0) / (h1 - h0))
    return 1.0


def pub_links() -> dict:
    """ключ внешнего поста → id публикации Crosspost (по ссылке и uri из итога отправки)."""
    import json
    import app.publish.runner  # noqa: F401
    out = {}
    for r in db.query("SELECT id, dests FROM publications"):
        try:
            dests = json.loads(r["dests"] or "[]")
        except Exception:   # noqa: BLE001
            continue
        for d in dests:
            link = str(d.get("link") or "")
            m = re.search(r"wall(-?\d+_\d+)", link)
            if m:
                out["vk:" + m[1]] = r["id"]
            m = re.search(r"(e6ai|e621)\.net/posts/(\d+)", link)
            if m:
                out[f"{m[1]}:{m[2]}"] = r["id"]
            for did in d.get("deviation_ids") or []:
                out["da:" + str(did)] = r["id"]
            uri = str(d.get("uri") or "")
            m = re.search(r"/post/([^/?#]+)", link) or re.search(r"app\.bsky\.feed\.post/([^/?#]+)", uri)
            if m and ("bsky" in link or "bsky" in uri):
                out["bsky:" + m[1]] = r["id"]
    return out


def items_between(t0: float, t1: float, tz) -> list[dict]:
    """Внешние посты за окно — в формате элементов календаря (без склейки)."""
    rows = db.query("SELECT * FROM ext_posts WHERE date>=? AND date<? ORDER BY date", (int(t0), int(t1)))
    if not rows:
        return []
    from app.insights import calendar as K
    norms, pubs, now = _norms(), pub_links(), time.time()
    vkc = vk_curve()
    out = []
    for r in rows:
        by_views = bool(r["platform"] in VIEWS and r["views"])
        score, med = _score(r), base(norms.get((r["platform"], by_views)), r["date"])
        age_d = (now - r["date"]) / 86400
        idx, est, proj = None, False, None
        if med and age_d >= MIN_AGE_D and score is not None:
            est = age_d < MATURE_D   # младше недели — ещё набирает: это оценка, как у Telegram
            val = score
            if est and r["platform"] == "vk" and by_views and vkc:   # ВК: ждём итог по кривой набора из истории замеров
                proj = int(round(score / frac_at(vkc, age_d * 24)))
                val = proj
            idx = round(val / med, 2)
        local = datetime.fromtimestamp(r["date"], tz)
        react = (r["likes"] or 0) + (r["reposts"] or 0) + (r["replies"] or 0)
        pub = pubs.get(r["key"])
        out.append({
            "key": r["key"], "post_id": None, "pub_id": pub, "app": pub is not None,
            "day": local.date().isoformat(), "time": local.strftime("%H:%M"), "ts": local.timestamp(),
            "preview": r["preview"], "text": (r["text"] or "")[:160], "channels": [LABEL[r["platform"]]], "rating": None,
            "link": r["link"] or "", "views": r["views"], "reactions": react, "forwards": r["reposts"] or 0, "proj": proj,
            "index": idx, "est": est, "level": K._level(idx) if idx is not None else "", "by_views": by_views,
            "n_media": r["n_media"] or 1, "age_h": round(age_d * 24, 1), "kind": r["kind"] or "img",
            "ext": r["platform"], "stat_at": r["updated_at"],
        })
    return out
