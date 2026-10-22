"""Статистика одного опубликованного поста и слежение за свежими постами.

Telegram: цифры берутся из импорта (тот же пост в базе статистики), плюс замеры в первые дни —
фоновая отправка снимает просмотры через 1, 3, 6, 12, 24… часа. По ним видно, «взлетает» ли пост.
e6ai: score, избранное и комментарии — прямо с сайта.
Discord и ВК просмотров не отдают.
"""
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np

from app import db, stats
from app.insights import model as M
from app.paths import data_dir, sub

TRACK_DAYS = 8
_e6_cache: dict = {}
CACHE_MAX = 400   # цифры с сайтов в памяти — не больше стольких постов, старые выбрасываются


def _prune():
    if len(_e6_cache) > CACHE_MAX:
        for k, _ in sorted(_e6_cache.items(), key=lambda kv: kv[1][0])[:len(_e6_cache) - CACHE_MAX // 2]:
            _e6_cache.pop(k, None)
_track_lock = threading.Lock()
_last_track = {"t": 0.0, "note": ""}


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _pubs(days: int | None = None, pub_id: int | None = None) -> list[dict]:
    import app.publish.runner  # noqa: F401  (таблица publications)
    if pub_id is not None:
        return db.query("SELECT * FROM publications WHERE id=?", (int(pub_id),))
    since = (datetime.now(timezone.utc) - timedelta(days=days or TRACK_DAYS)).isoformat()
    return db.query("SELECT * FROM publications WHERE created>=? ORDER BY id DESC", (since,))


def _tg_dests(pub: dict) -> list[dict]:
    return [d for d in json.loads(pub["dests"] or "[]")
            if d.get("platform") == "telegram" and d.get("state") == "done" and d.get("chat_id") and d.get("message_ids")]


# --- e6ai -----------------------------------------------------------------------------------

def e6ai_stats(link: str) -> dict | None:
    import re
    import requests
    from app.publish import settings as PS
    from app.trends.dumps import user_agent
    m = re.search(r"/posts/(\d+)", link or "")
    if not m:
        return None
    pid = m.group(1)
    site = "e621" if "e621.net" in (link or "") else "e6ai"   # цифры — с того сайта, где лежит пост
    hit = _e6_cache.get((site, pid))
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    raw = PS.load(raw=True)["e6ai"]
    acc = raw if site == "e6ai" else ((raw.get("accounts") or {}).get(site) or {})
    cfg = {"username": acc.get("username", ""), "api_key": acc.get("api_key", "")}
    hm = re.match(r"(https?://[^/]+)", link or "")
    base = hm.group(1) if hm else PS.BOORU[site]
    auth = (cfg["username"], cfg["api_key"]) if cfg.get("username") and cfg.get("api_key") else None
    try:
        r = requests.get(f"{base}/posts/{pid}.json", headers={"User-Agent": user_agent(cfg.get("username"))},
                         auth=auth, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"{site} ответил {r.status_code}")
        p = r.json().get("post") or {}
        out = {"score": (p.get("score") or {}).get("total"), "up": (p.get("score") or {}).get("up"),
               "favs": p.get("fav_count"), "comments": p.get("comment_count")}
    except RuntimeError as e:
        out = {"error": str(e)}
    except Exception as e:  # noqa: BLE001
        out = {"error": f"{site} не ответил: {e.__class__.__name__}"}
    out["site"] = site
    _prune()
    _e6_cache[(site, pid)] = (time.time(), out)
    return out


# --- Bluesky и DeviantArt ---------------------------------------------------------------------

def _cached(key: str, fn, ttl: float = 600):
    hit = _e6_cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        out = fn()
    except Exception as e:  # noqa: BLE001
        out = {"error": str(e)[:200]}
    # ошибку помним минуту, а не 10 — чтобы после восстановления связи цифры появились сразу
    _prune()
    _e6_cache[key] = (time.time() - (ttl - 60 if "error" in out else 0), out)
    return out


def _bs_nums(d: dict) -> dict:
    import app.publish.runner  # noqa: F401  (путь к токенам — для входа на сервер аккаунта)
    from app.publish import settings as PS
    from platforms import bluesky
    st = bluesky.stats(d.get("uri") or d.get("link"), PS.load()["bluesky"])
    return {"cells": [["Лайки", st["likes"]], ["Репосты", st["reposts"]], ["Ответы", st["replies"]], ["Цитаты", st["quotes"]]]}


def _da_nums(d: dict) -> dict:
    import app.publish.runner  # noqa: F401  (путь к токенам)
    from app.publish import settings as PS
    from platforms import deviantart
    if not d.get("deviation_ids"):
        return {"error": "Номер работы не сохранился — цифры для этой публикации недоступны"}
    st = deviantart.stats(PS.load()["deviantart"], d["deviation_ids"])
    return {"cells": [["Просмотры", st["views"]], ["В избранном", st["favourites"]], ["Комментарии", st["comments"]],
                      ["Скачивания", st["downloads"]]]}


# --- Telegram ------------------------------------------------------------------------------

def _channel_frame(chat_id: str):
    """Посты канала с индексом успеха (запоминаются, пока в базе ничего не поменялось)."""
    return stats.indexed(chat_id)


def tg_stats(dest: dict, curve: dict) -> dict:
    chat_id, ids = str(dest["chat_id"]), dest["message_ids"]
    p = M.post_for(chat_id, ids)
    ch = db.query("SELECT title, selected FROM channels WHERE platform='telegram' AND chat_id=?", (chat_id,))
    if not p:
        return {"ok": False, "why": ("Цифр ещё нет — они появятся после первого замера (фоновая отправка снимает их сама) "
                                     "или после «Обновить» в «Статистике»") if ch else
                "Этот канал не подключён к статистике — импортируйте его, чтобы видеть цифры поста"}
    now = datetime.now(timezone.utc)
    d0 = _dt(p["date_utc"])
    age_h = (now - d0).total_seconds() / 3600
    by_views = p["views"] is not None
    score = p["views"] if by_views else (p["reactions"] or 0)
    out = {"ok": True, "chat_id": chat_id, "views": p["views"], "reactions": p["reactions"], "forwards": p["forwards"], "replies": p["replies"],
           "age_h": round(age_h, 1), "unit": "просмотров" if by_views else "реакций", "by_views": by_views,
           "updated": p["updated_at"], "link": p["link"], "post_id": p["id"], "selected": bool(ch and ch[0]["selected"])}
    if p["views"]:
        out["react_rate"] = round((p["reactions"] or 0) / p["views"], 4)
    # замеры: сколько было к какому часу
    snaps = db.query("SELECT taken_at, views, reactions FROM snapshots WHERE post_id=? ORDER BY taken_at", (p["id"],))
    series = []
    for s in snaps:
        v = s["views"] if by_views else s["reactions"]
        if v is None:
            continue
        h = (_dt(s["taken_at"]) - d0).total_seconds() / 3600
        if h >= 0:
            series.append({"h": round(h, 2), "v": int(v)})
    out["series"] = series
    # сравнение с каналом
    df = _channel_frame(chat_id)
    if not df.empty:
        row = df[df["id"] == p["id"]]
        cb = M.channel_base(df, chat_id)
        if cb:
            out["channel_median"] = round(cb["base"])
            # для свежего поста — проекция на неделю по кривой набора просмотров
            final = score
            if age_h < 168 and by_views:
                frac = M.frac_at(curve, max(age_h, 0.25))
                final = score / max(frac, 0.02)
                out["projected"] = round(final)
                out["projected_frac"] = round(frac, 2)
            k = 0 if by_views else 1
            out["index_now"] = round((final + k) / (cb["base"] + k), 2) if cb["base"] + k > 0 else None
            if len(cb["scores"]) >= 5:
                out["percentile"] = round(float((cb["scores"] < final).mean()), 2)
        if not row.empty and age_h >= 168 and not np.isnan(row["index"].iloc[0]):
            out["index"] = round(float(row["index"].iloc[0]), 2)
    return out


def verdict(st: dict, fc: dict | None) -> str | None:
    """Одна строка «как идёт пост»."""
    if not st.get("ok"):
        return None
    idx = st.get("index", st.get("index_now"))
    if idx is None:
        return None
    fresh = st["age_h"] < 168
    if fc and fc.get("expected"):
        got = st.get("projected", st["views"] if st["by_views"] else st["reactions"]) or 0
        r = got / max(fc["expected"], 1)
        rel = "лучше прогноза" if r >= 1.15 else "хуже прогноза" if r <= 0.87 else "в пределах прогноза"
    else:
        rel = None
    from app.insights import norms
    lv = norms.levels(st.get("chat_id"))
    level = ("супер-хит" if idx >= lv["super"] else "хит" if idx >= lv["hit"] else "лучше обычного" if idx >= lv["good"]
             else "как обычно" if idx >= 0.83 else "слабее обычного")
    head = ("Идёт на " if fresh else "Итог: ") + f"×{idx:.2f}".replace(".", ",") + f" к норме канала — {level}"
    return head + (f", {rel}" if rel else "") + (" (оценка по первым часам)" if fresh and st["age_h"] < 24 else "")


def for_publication(pub_id: int) -> dict:
    rows = _pubs(pub_id=pub_id)
    if not rows:
        raise ValueError("Публикация не найдена")
    pub = rows[0]
    curve = M.growth_curve()
    fcs = {r["chat_id"]: json.loads(r["data"]) for r in db.query("SELECT chat_id, data FROM forecasts WHERE pub_id=?", (pub["id"],))}
    dests = []
    for d in json.loads(pub["dests"] or "[]"):
        item = {"key": d.get("key"), "name": d.get("name"), "platform": d.get("platform"), "state": d.get("state"),
                "link": d.get("link") or ""}
        from app.publish import settings as _PS
        if d.get("state") != "done":
            item["why"] = "не опубликовано"
        elif d.get("platform") not in ("telegram", "discord", "vk") and not _PS.module_on("collect"):
            item["why"] = "Сбор статистики выключен в «Настройки» → «Сбор статистики» — цифры с сайтов не запрашиваем"
        elif d.get("platform") != "telegram" and not _PS.module_on(d.get("platform") or ""):
            item["why"] = "Площадка выключена в «Настройки» → «Функции» — цифры не запрашиваем"
        elif d.get("platform") == "telegram" and d.get("key") == "telegram:group":
            item["why"] = "В группах Telegram просмотров нет — цифры смотрите у поста в канале"
        elif d.get("platform") == "telegram" and d.get("chat_id"):
            st = tg_stats(d, curve)
            fc = fcs.get(str(d["chat_id"]))
            item.update(stats=st, forecast=fc, verdict=verdict(st, fc))
        elif d.get("platform") == "e6ai" or d.get("key") == "e6ai":
            item["e6ai"] = e6ai_stats(d.get("link"))
            try:
                from app.insights import e6learn
                item["e6mod"] = e6learn.for_publication(pub["id"])
            except Exception:
                pass
        elif d.get("platform") == "bluesky":
            item["nums"] = _cached(f"bs:{d.get('uri') or d.get('link')}", lambda d=d: _bs_nums(d))
        elif d.get("platform") == "deviantart":
            item["nums"] = _cached(f"da:{','.join(d.get('deviation_ids') or [])}", lambda d=d: _da_nums(d))
        elif d.get("platform") == "discord":
            item["why"] = "Discord не показывает просмотры"
        elif d.get("platform") == "vk":
            from app.publish import vk_stats
            item["nums"] = vk_stats.nums_for(d.get("link"))
        dests.append(item)
    return {"id": pub["id"], "title": pub["title"] or "", "created": pub["created"], "rating": pub["rating"],
            "files": json.loads(pub["files"] or "[]"), "dests": dests, "curve": curve,
            "tracking": _last_track["note"]}


def for_post(post_id: int) -> dict:
    """Шторка для поста из импорта (не из Crosspost): тот же вид, что у публикации."""
    rows = db.query("""SELECT p.*, c.title AS channel FROM posts p LEFT JOIN channels c ON c.platform=p.platform AND c.chat_id=p.chat_id
                       WHERE p.id=?""", (int(post_id),))
    if not rows:
        raise ValueError("Пост не найден")
    p = rows[0]
    ids = [int(x) for x in str(p["msg_ids"] or p["msg_id"]).split(",") if x.strip().isdigit()]
    dest = {"key": "telegram:import", "platform": "telegram", "name": f"Telegram · {p['channel'] or p['chat_id']}", "state": "done",
            "link": p["link"], "chat_id": p["chat_id"], "message_ids": ids}
    curve = M.growth_curve()
    st = tg_stats(dest, curve)
    return {"id": None, "post_id": p["id"], "title": (p["text"] or "").split("\n")[0][:80], "created": p["date_utc"], "rating": None,
            "files": [], "preview": p["preview"], "curve": curve, "tracking": _last_track["note"],
            "dests": [{"key": dest["key"], "name": dest["name"], "platform": "telegram", "state": "done", "link": p["link"] or "",
                       "stats": st, "forecast": None, "verdict": verdict(st, None)}]}


def summary(pub_ids: list[int]) -> dict:
    """Коротко для списка «Истории»: главная цифра Telegram и как идёт пост."""
    out = {}
    curve = None
    for pid in pub_ids or []:
        rows = _pubs(pub_id=pid)
        if not rows:
            continue
        best = None
        for d in _tg_dests(rows[0]):
            if d.get("key") == "telegram:group":
                continue
            curve = curve or M.growth_curve()
            st = tg_stats(d, curve)
            if st.get("ok"):
                v = st["views"] if st["by_views"] else st["reactions"]
                if best is None or (v or 0) > (best["value"] or 0):
                    best = {"value": v, "unit": st["unit"], "index": st.get("index", st.get("index_now")),
                            "fresh": st["age_h"] < 168}
        if best:
            out[str(pid)] = best
    return out


# --- прогноз при публикации ------------------------------------------------------------------

def save_forecasts(pub_id: int, items: list[dict]):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.tx() as c:
        for f in items:
            if f.get("ok"):
                keep = {k: f.get(k) for k in ("channel", "unit", "expected", "low", "high", "index", "p_hit", "base", "when")}
                keep["factors"] = f.get("factors", [])[:6]
                c.execute("INSERT OR REPLACE INTO forecasts (pub_id, chat_id, made_at, data) VALUES (?,?,?,?)",
                          (int(pub_id), str(f["chat_id"]), now, json.dumps(keep, ensure_ascii=False)))


def refresh_post(tg, post_id: int, report):
    """«Обновить цифры» для поста из импорта (без публикации из Crosspost)."""
    from app.telegram.service import save_messages
    rows = db.query("SELECT chat_id, msg_id, msg_ids FROM posts WHERE id=?", (int(post_id),))
    if not rows:
        raise ValueError("Пост не найден")
    if not tg.keys():
        raise ValueError("Цифры берутся через вход в Telegram по API («Импорт для статистики» → API)")
    p = rows[0]
    ids = [int(x) for x in str(p["msg_ids"] or p["msg_id"]).split(",") if x.strip().isdigit()]
    report(progress=0.2, message="Обновляю цифры…")
    ch = db.query("SELECT chat_id, username FROM channels WHERE platform='telegram' AND chat_id=?", (p["chat_id"],))
    fresh = tg._run(tg.backend().refresh(p["chat_id"], ids, previews_dir=sub("previews")), 120)
    save_messages(ch[0] if ch else {"chat_id": p["chat_id"], "username": None}, fresh, snapshot=True)
    report(progress=1.0, message="Цифры обновлены")


def refresh_publication(tg, pub_id: int, report):
    from app.telegram.service import save_messages
    rows = _pubs(pub_id=pub_id)
    if not rows:
        raise ValueError("Публикация не найдена")
    _e6_cache.clear()   # e6ai, Bluesky, DeviantArt — спросим заново
    dests = [d for d in _tg_dests(rows[0]) if d.get("key") != "telegram:group"]
    if dests and not tg.keys():
        report(progress=1.0, message="Цифры Telegram берутся через вход по API («Импорт для статистики» → API); остальные обновлены")
        return
    for i, d in enumerate(dests):
        report(progress=i / max(len(dests), 1), message=f"Обновляю цифры: {d.get('name') or d['chat_id']}")
        cid = str(d["chat_id"])
        row = db.query("SELECT chat_id, username FROM channels WHERE platform='telegram' AND chat_id=?", (cid,))
        fresh = tg._run(tg.backend().refresh(cid, [int(x) for x in d["message_ids"]], previews_dir=sub("previews")), 120)
        save_messages(row[0] if row else {"chat_id": cid, "username": None}, fresh, snapshot=True)
    report(progress=1.0, message="Цифры обновлены")


# --- слежение за свежими постами (из фоновой отправки) ----------------------------------------

# точки замеров: в первые сутки часто (когда пост «взлетает»), дальше реже
TRACK_AGES = [0.25, 0.5, 1, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 30, 36, 48, 60, 72, 96, 120, 144, 168, 192]


def _due(age_h: float, last_age_h: float | None) -> bool:
    """Пора ли снять замер: прошли очередную точку (15 мин, 30 мин, 1, 1,5, 2… ч), а замера после неё нет."""
    passed = [a for a in TRACK_AGES if a <= age_h]
    if not passed:
        return last_age_h is None and age_h >= 0.1
    return last_age_h is None or last_age_h < passed[-1] * 0.95


def track_tick(tg, min_gap: float = 60) -> dict:
    """Обновить цифры свежих постов, у которых подошёл срок замера. tg — TgService (с входом через API)."""
    from app.telegram.service import save_messages
    if not _track_lock.acquire(blocking=False):
        return {"skipped": "busy"}
    try:
        if time.time() - _last_track["t"] < min_gap:
            return {"skipped": "recent"}
        _last_track["t"] = time.time()
        if not tg.keys():
            _last_track["note"] = "Замеры не идут: нет входа в Telegram через API (только импорт из выгрузки)"
            return {"skipped": "no_api"}
        now = datetime.now(timezone.utc)
        todo: dict[str, set] = {}
        for pub in _pubs(TRACK_DAYS):
            for d in _tg_dests(pub):
                if d.get("key") == "telegram:group":
                    continue
                cid = str(d["chat_id"])
                p = M.post_for(cid, d["message_ids"])
                t0 = _dt(p["date_utc"]) if p else _dt(pub["created"])
                age = (now - t0).total_seconds() / 3600
                if age > TRACK_DAYS * 24:
                    continue
                last = None
                if p:
                    r = db.query("SELECT MAX(taken_at) AS t FROM snapshots WHERE post_id=?", (p["id"],))[0]["t"]
                    last = (_dt(r) - t0).total_seconds() / 3600 if r else None
                if _due(age, last):
                    todo.setdefault(cid, set()).update(int(x) for x in d["message_ids"])
        n = 0
        for cid, ids in todo.items():
            row = db.query("SELECT chat_id, username FROM channels WHERE platform='telegram' AND chat_id=?", (cid,))
            ch = row[0] if row else {"chat_id": cid, "username": None}
            fresh = tg._run(tg.backend().refresh(cid, sorted(ids), previews_dir=sub("previews")), 120)
            save_messages(ch, fresh, snapshot=True)
            n += len(fresh)
        try:
            fill_previews(tg)
        except Exception:  # noqa: BLE001 — превью не критичны
            pass
        _last_track["note"] = f"Последний замер: {datetime.now().strftime('%H:%M')}" + (f", обновлено сообщений: {n}" if n else "")
        return {"updated": n, "channels": len(todo)}
    except Exception as e:  # noqa: BLE001
        _last_track["note"] = f"Замер не удался: {e}"
        return {"error": str(e)}
    finally:
        _track_lock.release()


_last_fill = {"t": 0.0}


def fill_previews(tg, limit: int = 40, gap: float = 600) -> int:
    """Посты с картинкой, но без превью (попали в базу через замеры или импорт без фото) — скачать превью.
    Не чаще раза в 10 минут и понемногу, чтобы не нагружать Telegram."""
    from app.telegram.service import save_messages
    if time.time() - _last_fill["t"] < gap:
        return 0
    _last_fill["t"] = time.time()
    since = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    rows = db.query("""SELECT chat_id, msg_id FROM posts WHERE platform='telegram' AND preview IS NULL
                       AND COALESCE(media_type, '') NOT IN ('', 'text') AND date_utc>=? ORDER BY date_utc DESC LIMIT ?""", (since, limit))
    by_chat: dict[str, list] = {}
    for r in rows:
        by_chat.setdefault(str(r["chat_id"]), []).append(int(r["msg_id"]))
    n = 0
    for cid, ids in by_chat.items():
        row = db.query("SELECT chat_id, username FROM channels WHERE platform='telegram' AND chat_id=?", (cid,))
        fresh = tg._run(tg.backend().refresh(cid, ids, previews_dir=sub("previews")), 120)
        save_messages(row[0] if row else {"chat_id": cid, "username": None}, fresh, snapshot=False)
        n += sum(1 for m in fresh if m.get("preview"))
    return n
