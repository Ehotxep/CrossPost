"""Календарь «Истории»: что выходило по дням месяца и как зашло.

Берём все посты каналов из статистики (импортированные и опубликованные из Crosspost), плюс запланированные.
Один арт, вышедший в двух каналах (SFW-канал и 18+), — одна работа в календаре: склеиваем по
публикации из Crosspost или по похожести превью (перцептивный хэш), лучший индекс из копий.
"""
import json
import math
from datetime import date, datetime, timedelta, timezone

import numpy as np

from app import db, stats
from app.paths import sub

_hashes: dict = {}



def _kind(media_type) -> str:
    """Тип содержимого для значка: img / gif / vid."""
    return {"gif": "gif", "animation": "gif", "video": "vid"}.get(str(media_type or ""), "img")


def _pub_kind(files) -> str:
    kinds = {(f.get("kind") if isinstance(f, dict) else "") for f in files or []}
    return "vid" if "video" in kinds else "gif" if "gif" in kinds else "img"

def _ahash(preview: str | None) -> int | None:
    """64-битный «отпечаток» картинки: похожие арты — близкие отпечатки."""
    if not preview:
        return None
    if preview in _hashes:
        return _hashes[preview]
    h = None
    try:
        from PIL import Image
        with Image.open(sub("previews") / preview) as im:
            g = np.asarray(im.convert("L").resize((9, 8)), dtype=np.int16)
        bits = (g[:, 1:] > g[:, :-1]).flatten()   # dHash: устойчив к пережатию и смене размера
        h = int("".join("1" if b else "0" for b in bits), 2)
    except Exception:
        h = None
    _hashes[preview] = h
    return h


def _int(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return int(v)


def _level(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    from app.insights import norms
    lv = norms.levels()   # ступени — из вашей истории (по всем каналам)
    return "l4" if v >= lv["super"] else "l3" if v >= lv["hit"] else "l2" if v >= lv["good"] else "l0" if v < 0.77 else "l1"


def _pub_map() -> dict:
    """(chat_id, msg_id) → публикация из Crosspost."""
    import app.publish.runner  # noqa: F401
    out = {}
    for r in db.query("SELECT id, created, title, dests, files FROM publications"):
        try:
            dests = json.loads(r["dests"] or "[]")
        except Exception:
            continue
        for d in dests:
            if d.get("chat_id") and d.get("message_ids"):
                for m in d["message_ids"]:
                    out[(str(d["chat_id"]), int(m))] = r["id"]
    return out


def month(ym: str) -> dict:
    y, m = (int(x) for x in ym.split("-"))
    tz = datetime.now().astimezone().tzinfo
    first = date(y, m, 1)
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    grid_start = first - timedelta(days=first.weekday())          # неделя с понедельника
    grid_end = nxt + timedelta(days=(7 - nxt.weekday()) % 7)
    t0 = datetime(grid_start.year, grid_start.month, grid_start.day, tzinfo=tz)
    t1 = datetime(grid_end.year, grid_end.month, grid_end.day, tzinfo=tz)

    df = stats.indexed(None)
    items = []
    pubmap = _pub_map()
    if not df.empty:
        part = df[(df["dt"] >= t0) & (df["dt"] < t1)]
        for _, r in part.iterrows():
            est = bool(r.get("est", False))
            idx = r["index_est"] if est else r["index"]
            idx = None if idx is None or (isinstance(idx, float) and math.isnan(idx)) else round(float(idx), 2)
            local = r["dt"].to_pydatetime().astimezone(tz)
            msg_ids = [int(x) for x in str(r["msg_ids"] or r["msg_id"]).split(",") if x.strip().isdigit()]
            pub = next((pubmap[(str(r["chat_id"]), mi)] for mi in msg_ids if (str(r["chat_id"]), mi) in pubmap), None)
            views = _int(r["views"])
            items.append({
                "key": f"p{int(r['id'])}", "post_id": int(r["id"]), "pub_id": pub, "app": pub is not None,
                "day": local.date().isoformat(), "time": local.strftime("%H:%M"), "ts": local.timestamp(),
                "preview": r["preview"] if isinstance(r["preview"], str) else None,
                "text": (r["text"] if isinstance(r["text"], str) else "").strip()[:160],
                "channels": [r["channel"]], "rating": r["ch_rating"], "link": r["link"] if isinstance(r["link"], str) else "",
                "views": views, "reactions": int(r["reactions"] or 0), "forwards": _int(r["forwards"]) or 0,
                "proj": _int(r["proj"]) if est and r["by_views"] else None,
                "index": idx, "est": est, "level": _level(idx), "by_views": bool(r["by_views"]),
                "n_media": int(r["n_media"] or 1), "age_h": round(float(r.get("age_h", 0) or 0), 1),
                "kind": _kind(r.get("media_type")),
            })
    # склеиваем копии одного арта: по публикации из Crosspost или по похожему превью в пределах суток
    merged: list = []
    for it in sorted(items, key=lambda x: x["ts"]):
        h = _ahash(it["preview"])
        twin = None
        for o in merged[-12:]:
            if abs(o["ts"] - it["ts"]) > 36 * 3600:
                continue
            if it["pub_id"] and o["pub_id"] == it["pub_id"]:
                twin = o
                break
            oh = o.get("_h")
            if h is not None and oh is not None and bin(h ^ oh).count("1") <= 6:
                twin = o
                break
        if twin is None:
            it["_h"] = h
            merged.append(it)
            continue
        twin["channels"] = sorted(set(twin["channels"] + it["channels"]))
        twin["pub_id"] = twin["pub_id"] or it["pub_id"]
        twin["app"] = twin["app"] or it["app"]
        if (it["index"] or 0) > (twin["index"] or 0):   # лучшая из копий — главная
            for k in ("index", "est", "level", "views", "reactions", "forwards", "proj", "link", "post_id", "preview", "kind", "n_media"):
                twin[k] = it[k]
    for it in merged:
        it.pop("_h", None)

    # публикации из Crosspost без постов в статистике (например, только Discord/ВК)
    seen = {it["pub_id"] for it in merged if it["pub_id"]}
    for r in db.query("SELECT id, created, title, text, dests, files, rating FROM publications"):
        if r["id"] in seen:
            continue
        try:
            dt = datetime.fromisoformat(r["created"].replace("Z", "+00:00")).astimezone(tz)
        except Exception:
            continue
        if not (t0 <= dt < t1):
            continue
        dests = json.loads(r["dests"] or "[]")
        merged.append({"key": f"u{r['id']}", "post_id": None, "pub_id": r["id"], "app": True,
                       "day": dt.date().isoformat(), "time": dt.strftime("%H:%M"), "ts": dt.timestamp(),
                       "preview": None, "text": (r["title"] or r["text"] or "")[:160], "channels": [d.get("name", "") for d in dests if d.get("state") == "done"],
                       "rating": r["rating"], "link": next((d.get("link") for d in dests if str(d.get("link", "")).startswith("http")), ""),
                       "views": None, "reactions": None, "forwards": None, "proj": None, "index": None, "est": False, "level": "",
                       "by_views": True, "n_media": len(json.loads(r["files"] or "[]")), "age_h": None,
                       "kind": _pub_kind(json.loads(r["files"] or "[]"))})

    # запланированные
    from app.publish import schedule
    sched = []
    for r in db.query("SELECT * FROM scheduled WHERE state IN ('pending','running','missed')"):
        dt = schedule._parse(r["run_at"]).astimezone(tz)
        if not (t0 <= dt < t1):
            continue
        d = json.loads(r["draft"] or "{}")
        files = d.get("files") or []
        sched.append({"sid": r["id"], "day": dt.date().isoformat(), "time": dt.strftime("%H:%M"), "state": r["state"],
                      "title": r["title"] or (d.get("text") or "")[:80], "file": files[0] if files else None, "dests": r["dests"]})

    days: dict = {}
    for it in merged:
        days.setdefault(it["day"], []).append(it)
    for v in days.values():
        v.sort(key=lambda x: -(x["index"] if x["index"] is not None else -1))
    in_month = [it for it in merged if it["day"][:7] == ym]
    scored = [it for it in in_month if it["index"] is not None]
    leaders = sorted(scored, key=lambda x: -x["index"])[:4]
    geo = math.exp(sum(math.log(max(i["index"], 0.05)) for i in scored) / len(scored)) if scored else None
    return {
        "ym": ym, "start": grid_start.isoformat(), "end": grid_end.isoformat(),
        "today": datetime.now(tz).date().isoformat(),
        "days": days, "sched": sched, "leaders": leaders,
        "summary": {"posts": len(in_month), "hits": sum(1 for i in scored if i["index"] >= __import__("app.insights.norms", fromlist=["x"]).levels()["hit"]),
                    "typical": round(geo, 2) if geo else None, "est": sum(1 for i in in_month if i["est"])},
    }
