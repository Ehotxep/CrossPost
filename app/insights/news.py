"""Новости по свежим артам: «хит канала», «много реакций», «новые комментарии», «модераторы поправили теги»…

Раз в полторы минуты (фоновая отправка, а если её нет — окно) смотрим публикации последних 7 дней:
Telegram — по базе статистики (замеры снимает фоновая отправка), e6ai, Bluesky и DeviantArt — с сайтов
(свежие — раз в 10 минут, старше суток — раз в полчаса). Сравниваем с прошлым разом и записываем заметные
изменения. Пороги для Telegram — из истории канала (insights/norms.py): «хит» — лучше 90% его постов,
«много реакций» — больше, чем у 90% постов к этому же часу. Сколько новостей — настройка «мало / средне / много».
Первый взгляд на пост только запоминает цифры — новостей «из ничего» не бывает.
"""
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from app import db

DAYS = 7
REMOTE_GAP = 30 * 60
REMOTE_GAP_FRESH = 10 * 60   # первые сутки сайты спрашиваем чаще
CHECK_GAP = 90               # как часто вообще смотреть
WINDOW_H = 6       # за какое время копится прирост («+34 реакции за 3 ч»)
_lock = threading.Lock()
_last = {"t": 0.0}

db.conn().executescript("""
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, at TEXT, pub_id INTEGER, platform TEXT,
    kind TEXT, level INTEGER DEFAULT 1, text TEXT, read INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS news_state (k TEXT PRIMARY KEY, data TEXT, at REAL);
""")


def _now():
    return datetime.now(timezone.utc)


def _state(k):
    r = db.query("SELECT data, at FROM news_state WHERE k=?", (k,))
    return (json.loads(r[0]["data"]), r[0]["at"]) if r else (None, 0)


def _save(k, data):
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO news_state (k, data, at) VALUES (?,?,?)", (k, json.dumps(data), time.time()))


def _add(key, pub_id, platform, kind, text, level=1):
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO news (key, at, pub_id, platform, kind, level, text) VALUES (?,?,?,?,?,?,?)",
                  (key, _now().isoformat(timespec="seconds"), pub_id, platform, kind, level, text))


def _mult(v):
    return "×" + f"{v:.1f}".replace(".", ",")


def _title(pub):
    t = (pub.get("title") or (pub.get("text") or "").split("\n")[0]).strip()
    return f"«{t[:40]}»" if t else "Пост"


def _delta_news(k, pub, plat, cur: dict, rules, where: str):
    """rules: [(поле, порог, вид, текст-шаблон)]. Сравнить с прошлым разом, записать новости, запомнить."""
    prev, at = _state(k)
    if prev is None:   # первый взгляд — только запоминаем
        _save(k, cur)
        return
    hours = max((time.time() - at) / 3600, 0.1)
    span = f"за {round(hours)} ч" if hours >= 1.5 else "за последний час"
    fired = False
    for field, need, kind, tmpl in rules:
        if field not in prev:   # новое поле (раньше не следили) — без новости «из ничего», просто запомним
            fired = True
            continue
        d = (cur.get(field) or 0) - (prev.get(field) or 0)
        if d >= need:
            fired = True
            _add(f"{kind}:{k}:{field}:{int(time.time())}", pub["id"], plat, kind,
                 tmpl.format(t=_title(pub), d=d, n=cur.get(field), span=span, where=where))
    if fired or hours > WINDOW_H:   # точка отсчёта — с последней новости или раз в WINDOW_H часов
        _save(k, cur)


def collect(force: bool = False) -> int:
    """Посмотреть свежие публикации. Возвращает, сколько новостей появилось."""
    if not _lock.acquire(blocking=False):
        return 0
    try:
        if not force and time.time() - _last["t"] < CHECK_GAP:
            return 0
        _last["t"] = time.time()
        before = db.query("SELECT COUNT(*) AS n FROM news")[0]["n"]
        from app.insights import post_stats as PS
        from app.publish import settings as PSet
        from app.insights import model as M
        curve = M.growth_curve()
        since = (_now() - timedelta(days=DAYS)).isoformat()
        import app.publish.runner  # noqa: F401  (таблица publications)
        for pub in db.query("SELECT * FROM publications WHERE created>=? ORDER BY id DESC", (since,)):
            for d in json.loads(pub["dests"] or "[]"):
                if d.get("state") != "done":
                    continue
                if d.get("platform") != "telegram" and not PSet.module_on(d.get("platform") or ""):
                    continue   # площадка выключена — её не опрашиваем
                try:
                    _one(pub, d, curve, PS)
                except Exception:   # одна площадка не ответила — остальные смотрим дальше
                    continue
        try:
            _moderators()
        except Exception:
            pass
        return db.query("SELECT COUNT(*) AS n FROM news")[0]["n"] - before
    finally:
        _lock.release()


def _one(pub, d, curve, PS):
    plat = d.get("platform")
    name = (d.get("name") or "").split(" · ")[-1]
    if plat == "telegram" and d.get("key") != "telegram:group" and d.get("chat_id"):
        from app.insights import norms
        st = PS.tg_stats(d, curve)
        if not st.get("ok"):
            return
        cid = str(d["chat_id"])
        lv, tiers = norms.levels(cid), norms.tiers()
        idx = st.get("index") or st.get("index_now")
        est = not st.get("index")
        k = f"tg:{pub['id']}:{cid}"
        rk = norms.index_rank(cid, idx) if idx else None
        place = f" — лучше {int(rk * 100)}% постов канала" if rk is not None and rk >= 0.5 else ""
        first = " (оценка по первым часам)" if est else ""
        # ступени — из истории канала: хит = индекс выше, чем у 90% его постов (пока постов мало — ×2)
        if idx and idx >= lv["super"]:
            _add(f"hit3:{k}", pub["id"], plat, "hit", f"{_title(pub)} — супер-хит в «{name}»: {_mult(idx)} к норме{place}{first}", 3)
        elif idx and idx >= lv["hit"] and 0.90 in tiers:
            _add(f"hit2:{k}", pub["id"], plat, "hit",
                 f"{_title(pub)} — {'стартует как хит' if est else 'хит'} в «{name}»: {_mult(idx)} к норме{place}", 2)
        elif idx and idx >= lv["good"] and 0.75 in tiers:
            _add(f"good:{k}", pub["id"], plat, "good", f"{_title(pub)} идёт лучше обычного в «{name}»: {_mult(idx)} к норме{place}{first}", 1)
        if volume_allows_norm(tiers):
            _milestone(k, pub, plat, st, name)
        # реакции и пересылки: сравниваем с тем, сколько их обычно у постов этого канала к такому же часу
        _rank_news(k, pub, plat, cid, st, name, tiers)
        _delta_news(k, pub, plat, {"replies": st.get("replies") or 0},
                    [("replies", 3 if tiers == norms.VOLUME["few"] else 1, "comment", "{t}: новые комментарии в «{where}» — +{d}")], name)
        return
    k = f"{plat}:{pub['id']}"
    _, at = _state("fetch:" + k)
    try:
        fresh = (_now() - datetime.fromisoformat(str(pub["created"]).replace("Z", "+00:00"))).total_seconds() < 86400
    except Exception:
        fresh = False
    if time.time() - at < (REMOTE_GAP_FRESH if fresh else REMOTE_GAP):   # сайты — не чаще раза в 10/30 минут на пост
        return
    _save("fetch:" + k, {})
    from app.insights import norms
    f = {"few": 2.0, "normal": 1.0, "many": 0.6}[norms.volume()]   # истории замеров у сайтов пока нет — стартовые пороги

    def need(base, cur, share=0.2):
        return max(1, int(round(max(base, cur * share) * f)))
    if plat == "e6ai":
        st = PS.e6ai_stats(d.get("link"))
        site = (st or {}).get("site") or "e6ai"
        if not st or st.get("error"):
            return
        _delta_news(k, pub, plat, {"favs": st.get("favs") or 0, "comments": st.get("comments") or 0, "score": st.get("score") or 0},
                    [("favs", need(3, st.get("favs") or 0), "fav", "{t}: +{d} в избранном на " + site + " {span}, всего {n}"),
                     ("comments", 1, "comment", "{t}: новые комментарии на " + site + " — +{d}"),
                     ("score", need(5, st.get("score") or 0), "score", "{t}: score на " + site + " вырос на {d} — теперь {n}")], site)
    elif plat == "bluesky":
        from platforms import bluesky
        import app.publish.runner  # noqa: F401  (путь к токенам)
        from app.publish import settings as _PS
        st = bluesky.stats(d.get("uri") or d.get("link"), _PS.load()["bluesky"])
        _delta_news(k, pub, plat, st,
                    [("likes", need(3, st.get("likes") or 0), "react", "{t}: +{d} лайков в Bluesky {span}, всего {n}"),
                     ("reposts", need(2, 0), "share", "{t}: +{d} репостов в Bluesky"),
                     ("replies", 1, "comment", "{t}: новые ответы в Bluesky — +{d}")], "Bluesky")
    elif plat == "deviantart" and d.get("deviation_ids"):
        from app.publish import settings as S
        from platforms import deviantart
        st = deviantart.stats(S.load()["deviantart"], d["deviation_ids"])
        _delta_news(k, pub, plat, st,
                    [("favourites", need(3, st.get("favourites") or 0), "fav", "{t}: +{d} в избранном на DeviantArt {span}, всего {n}"),
                     ("views", need(25, st.get("views") or 0, 0.3), "views", "{t}: +{d} просмотров на DeviantArt {span}, всего {n}"),
                     ("comments", 1, "comment", "{t}: новые комментарии на DeviantArt — +{d}")], "DeviantArt")


def _plural(n, a, b, c):
    n = abs(int(n)) % 100
    k = n % 10
    return c if 10 < n < 20 else a if k == 1 else b if 2 <= k <= 4 else c


def _age(h: float) -> str:
    if h < 1:
        return f"{max(1, round(h * 60))} мин"
    if h < 36:
        return f"{round(h)} ч"
    return f"{round(h / 24)} дн"


def volume_allows_norm(tiers) -> bool:
    return 0.90 in tiers   # «мало» — только хиты и супер-хиты


def _milestone(k, pub, plat, st, name):
    """«Набрал больше обычного поста канала» — когда просмотры обогнали медиану канала (один раз, первые 3 дня)."""
    v, med = st.get("views"), st.get("channel_median")
    if not v or not med or (st.get("age_h") or 0) > 72:
        return
    prev, _ = _state("ms:" + k)
    if prev is None:   # первый взгляд — запоминаем, где пост сейчас
        _save("ms:" + k, {"norm": v >= med})
        return
    if v >= med and not prev.get("norm"):
        _save("ms:" + k, {"norm": True})
        _add(f"norm:{k}", pub["id"], plat, "views",
             f"{_title(pub)} набрал больше обычного поста «{name}» ({v} просмотров при норме {int(med)}) за {_age(st.get('age_h') or 0)}", 1)


def _rank_news(k, pub, plat, cid, st, name, tiers):
    """Реакции и пересылки: «больше, чем у 90% постов канала к этому часу». Каждую ступень — один раз."""
    from app.insights import norms
    prev, _ = _state("rk:" + k)
    age = st.get("age_h") or 0
    if age > 168:
        return
    cur = dict(prev or {})
    words = {"reactions": (("реакция", "реакции", "реакций"), "react", 3), "forwards": (("пересылка", "пересылки", "пересылок"), "share", 2)}
    for metric, (forms, kind, least) in words.items():
        val = st.get(metric) or 0
        r = norms.metric_rank(cid, metric, val, age)
        if r is None or val < least:   # 1 пересылка «лучше 97%», если у остальных ноль, — не новость
            continue
        word = _plural(val, *forms)
        reached = max([q for q in norms.VOLUME["many"] if r >= q], default=0)
        before = cur.get(metric, 0)
        if prev is not None and reached > before and reached in tiers:
            _add(f"rk:{k}:{metric}:{reached}", pub["id"], plat, kind,
                 f"{_title(pub)}: {val} {word} в «{name}» за {_age(age)} — {norms.tier_text(reached)} канала к этому часу",
                 2 if reached >= 0.97 else 1)
        cur[metric] = max(before, reached)
    if cur != prev:
        _save("rk:" + k, cur)


def _moderators():
    from app.insights import e6learn  # noqa: F401  (таблица e6_sent)
    for r in db.query("SELECT * FROM e6_sent WHERE checks>0"):
        rem, add = json.loads(r["removed"] or "[]"), json.loads(r["added"] or "[]")
        if not rem and not add:
            continue
        pub = (db.query("SELECT * FROM publications WHERE id=?", (r["pub_id"],)) or [{"id": r["pub_id"], "title": "", "text": ""}])[0]
        parts = ([f"убрали {', '.join(rem[:4])}"] if rem else []) + ([f"добавили {', '.join(add[:4])}"] if add else [])
        _add(f"mod:{r.get('site') or 'e6ai'}:{r['post_id']}:{len(rem)}:{len(add)}", pub["id"], "e6ai", "mod",
             f"{_title(pub)}: модераторы {r.get('site') or 'e6ai'} поправили теги — {'; '.join(parts)}")


def items(limit: int = 40) -> dict:
    rows = db.query("SELECT * FROM news ORDER BY id DESC LIMIT ?", (limit,))
    unread = db.query("SELECT COUNT(*) AS n FROM news WHERE read=0")[0]["n"]
    from app.insights import norms
    return {"items": rows, "unread": unread, "checked": _last["t"], "volume": norms.volume()}


def mark_read():
    with db.tx() as c:
        c.execute("UPDATE news SET read=1 WHERE read=0")


def collect_async():
    threading.Thread(target=collect, daemon=True, name="news").start()
