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


db.conn().executescript("CREATE TABLE IF NOT EXISTS cal_rules (a TEXT NOT NULL, b TEXT NOT NULL, kind TEXT NOT NULL, PRIMARY KEY (a, b));")


def rules_key():
    r = db.query("SELECT COUNT(*) AS n, MAX(rowid) AS m FROM cal_rules")[0]
    return (r["n"], r["m"])


def _rules():
    joins, splits = [], set()
    for r in db.query("SELECT a, b, kind FROM cal_rules"):
        if r["kind"] == "join":
            joins.append((r["a"], r["b"]))
        else:
            splits.add(frozenset((r["a"], r["b"])))
    return joins, splits


def _set_rule(a: str, b: str, kind: str | None):
    """kind: 'join' | 'split' | None (убрать правило)."""
    with db.tx() as c:
        c.execute("DELETE FROM cal_rules WHERE (a=? AND b=?) OR (a=? AND b=?)", (a, b, b, a))
        if kind:
            c.execute("INSERT INTO cal_rules (a, b, kind) VALUES (?,?,?)", (a, b, kind))


def merge(keys: list) -> int:
    """Вручную объединить посты (ключи исходных постов) в одну работу."""
    keys = list(dict.fromkeys(str(k) for k in keys or []))
    if len(keys) < 2:
        raise ValueError("Выберите хотя бы две работы")
    with db.tx() as c:
        for i, a in enumerate(keys):   # прежние «не склеивать» между этими постами снимаем
            for b in keys[i + 1:]:
                c.execute("DELETE FROM cal_rules WHERE kind='split' AND ((a=? AND b=?) OR (a=? AND b=?))", (a, b, b, a))
        for k in keys[1:]:
            c.execute("INSERT OR REPLACE INTO cal_rules (a, b, kind) VALUES (?,?,'join')", (keys[0], k))
    return len(keys)


def split(keys: list) -> int:
    """Разъединить: между всеми постами работы — «не склеивать» (в том числе автосклейку)."""
    keys = list(dict.fromkeys(str(k) for k in keys or []))
    if len(keys) < 2:
        raise ValueError("В этой работе только один пост")
    with db.tx() as c:
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                c.execute("DELETE FROM cal_rules WHERE (a=? AND b=?) OR (a=? AND b=?)", (a, b, b, a))
                c.execute("INSERT INTO cal_rules (a, b, kind) VALUES (?,?,'split')", (a, b))
        for a in keys:   # ручные объединения с этой работой тоже снимаем
            c.execute("DELETE FROM cal_rules WHERE kind='join' AND (a=? OR b=?)", (a, a))
    return len(keys)


def decide(a: list, b: list, yes: bool) -> bool:
    """Ответ на подсказку «Похоже на тот же арт»: объединить или запомнить, что это разные работы."""
    if yes:
        merge([*a, *b])
        return True
    with db.tx() as c:
        for x in a:
            for y in b:
                c.execute("DELETE FROM cal_rules WHERE (a=? AND b=?) OR (a=? AND b=?)", (x, y, y, x))
                c.execute("INSERT INTO cal_rules (a, b, kind) VALUES (?,?,'split')", (x, y))
    return True


def _decorate(it: dict, plat: str):
    it["platform"] = plat
    it["atoms"] = [it["key"]]
    it["parts"] = [{"plat": plat, "label": (it["channels"] or [""])[0], "link": it.get("link") or "", "index": it["index"],
                    "est": it["est"], "views": it["views"], "reactions": it["reactions"], "key": it["key"]}]


def _hamming(x, y):
    return bin(x ^ y).count("1")


def _group(atoms: list):
    """Группы постов-копий: ручные объединения, затем автосклейка (одна публикация или похожее превью за 36 ч)."""
    joins, splits = _rules()
    by_key = {a["key"]: a for a in atoms}
    parent = {k: k for k in by_key}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in joins:
        if a in by_key and b in by_key:
            parent[find(a)] = find(b)
    bucket: dict = {}
    for a in atoms:
        bucket.setdefault(find(a["key"]), []).append(a)
    glist = sorted(bucket.values(), key=lambda g: min(x["ts"] for x in g))

    def blocked(g1, g2):
        return any(frozenset((x["key"], y["key"])) in splits for x in g1 for y in g2)

    def same(g1, g2):
        for x in g1:
            for y in g2:
                if abs(x["ts"] - y["ts"]) > 36 * 3600:
                    continue
                if x["pub_id"] and x["pub_id"] == y["pub_id"]:
                    return True
                if x["_h"] is not None and y["_h"] is not None and _hamming(x["_h"], y["_h"]) <= 6:
                    return True
        return False
    out: list = []
    for g in glist:
        twin = next((o for o in out[-12:] if not blocked(g, o) and same(g, o)), None)
        if twin is None:
            out.append(list(g))
        else:
            twin.extend(g)
    return out, splits


def _suggest(groups: list, splits: set, limit: int = 5) -> list:
    """Пары работ на разных площадках, похожие по превью, но не склеенные сами: спросим у человека."""
    res = []
    plats = [{p["plat"] for x in g for p in x["parts"]} for g in groups]
    for i, g1 in enumerate(groups):
        for j in range(i + 1, len(groups)):
            g2 = groups[j]
            if plats[i] & plats[j]:
                continue
            if any(frozenset((x["key"], y["key"])) in splits for x in g1 for y in g2):
                continue
            dt = min(abs(x["ts"] - y["ts"]) for x in g1 for y in g2)
            if dt > 7 * 86400:
                continue
            bits = min((_hamming(x["_h"], y["_h"]) for x in g1 for y in g2 if x["_h"] is not None and y["_h"] is not None), default=None)
            if bits is None or bits > 14:
                continue
            res.append((bits + dt / 86400 / 2, bits, dt, g1, g2))
    res.sort(key=lambda r: r[0])
    return [r[1:] for r in res[:limit]]


def _collapse(g: list) -> dict:
    """Группа постов → одна работа в календаре; цифры и превью — от лучшей копии."""
    main = max(g, key=lambda x: (x["index"] if x["index"] is not None else -1, bool(x["preview"])))
    first = min(g, key=lambda x: x["ts"])
    it = {k: v for k, v in main.items() if k != "_h"}
    it.update(day=first["day"], time=first["time"], ts=first["ts"])
    it["channels"] = sorted({c for x in g for c in x["channels"]})
    it["pub_id"] = next((x["pub_id"] for x in g if x["pub_id"]), None)
    it["app"] = any(x["app"] for x in g)
    it["post_id"] = main["post_id"] or next((x["post_id"] for x in g if x["post_id"]), None)
    for k in ("preview", "text", "link"):
        it[k] = main[k] or next((x[k] for x in g if x[k]), main[k])
    it["parts"] = [p for x in sorted(g, key=lambda x: x["ts"]) for p in x["parts"]]
    it["atoms"] = [x["key"] for x in sorted(g, key=lambda x: x["ts"])]
    joined = {frozenset(j) for j in _rules()[0]}
    it["manual"] = any(frozenset((a, b)) in joined for a in it["atoms"] for b in it["atoms"] if a != b)
    return it



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
    for it in items:
        _decorate(it, "telegram")
    try:   # посты, вышедшие в обход Crosspost (ВК, Bluesky)
        from app.insights import ext_posts
        for it in ext_posts.items_between(t0.timestamp(), t1.timestamp(), tz):
            _decorate(it, it["ext"])
            items.append(it)
    except Exception:   # noqa: BLE001 — внешние посты не должны ломать календарь
        pass
    # склеиваем копии одного арта: ручные объединения → по публикации Crosspost/похожему превью, кроме разъединённых
    for it in items:
        it["_h"] = _ahash(it["preview"])
    groups, splits = _group(items)
    suggest_raw = _suggest(groups, splits)
    merged = []
    by_group = {}
    for g in groups:
        it = _collapse(g)
        by_group[id(g)] = it
        merged.append(it)
    suggestions = [{"a": [x["key"] for x in g1], "b": [x["key"] for x in g2], "ka": by_group[id(g1)]["key"], "kb": by_group[id(g2)]["key"],
                    "bits": bits, "days": round(dt / 86400, 1)} for bits, dt, g1, g2 in suggest_raw]

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
        "days": days, "sched": sched, "leaders": leaders, "suggestions": suggestions,
        "summary": {"posts": len(in_month), "hits": sum(1 for i in scored if i["index"] >= __import__("app.insights.norms", fromlist=["x"]).levels()["hit"]),
                    "typical": round(geo, 2) if geo else None, "est": sum(1 for i in in_month if i["est"])},
    }
