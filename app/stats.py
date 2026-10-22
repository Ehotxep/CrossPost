"""Статистика ваших постов: индекс успеха, лучшие работы, лучшее время, рост каналов.

Индекс успеха = просмотры поста ÷ медиана просмотров постов того же канала за ±4 недели.
Так пост 2023 года сравнивается с постами 2023 года (канал тогда был меньше), а не с сегодняшними.

Для каналов, импортированных из выгрузки Telegram Desktop, просмотров нет — там индекс считается по реакциям
(с поправкой +1, чтобы 2 реакции против 1 не выглядели как «вдвое лучше» на пустом месте).
"""
import math
import re
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app import db

WINDOW = timedelta(days=28)
MIN_AGE = timedelta(days=7)        # свежие посты ещё набирают просмотры — в рейтинг не берём
DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
HASHTAG = re.compile(r"#(\w{2,40})", re.UNICODE)
MENTION = re.compile(r"@\w{3,}")
PRIOR = 4   # «воображаемых» постов с индексом ×1: группа из 3 постов не обгонит группу из 30 случайно


def shrunk(li: pd.Series) -> float:
    """Средний индекс группы (геометрический), подтянутый к ×1 тем сильнее, чем меньше в ней постов."""
    return float(math.exp(li.sum() / (len(li) + PRIOR)))


def _load(chat_id: str | None) -> pd.DataFrame:
    sql = """SELECT p.*, c.title AS channel, c.rating AS ch_rating FROM posts p
             JOIN channels c ON c.platform=p.platform AND c.chat_id=p.chat_id
             WHERE c.selected=1 AND p.is_forward=0 AND p.media_type!='text' AND COALESCE(p.excluded,0)=0"""
    args = ()
    if chat_id:
        sql += " AND p.chat_id=?"
        args = (str(chat_id),)
    df = pd.DataFrame(db.query(sql, args))
    if df.empty:
        return df
    df["dt"] = pd.to_datetime(df["date_utc"], utc=True, format="ISO8601")
    df["views"] = pd.to_numeric(df["views"], errors="coerce")
    df["reactions"] = pd.to_numeric(df["reactions"], errors="coerce").fillna(0)
    # у канала есть просмотры (импорт через API) или только реакции (выгрузка Telegram Desktop)
    has_views = df.groupby("chat_id")["views"].transform(lambda v: v.notna().mean() > 0.5)
    df["by_views"] = has_views.astype(bool)
    df["score"] = np.where(df["by_views"], df["views"], df["reactions"])
    return df.sort_values("dt").reset_index(drop=True)


def _with_index(df: pd.DataFrame) -> pd.DataFrame:
    """Добавляет baseline (медиана соседей по времени в том же канале) и индекс успеха."""
    df = df.copy()
    df["baseline"] = np.nan
    w = f"{int(WINDOW.total_seconds() * 2)}s"   # окно ±4 недели вокруг поста
    for _, g in df.groupby("chat_id"):
        try:   # быстро: скользящая медиана по времени
            s_ = pd.Series(g["score"].to_numpy(dtype=float), index=pd.DatetimeIndex(g["dt"]))
            base = s_.rolling(w, center=True, min_periods=3, closed="both").median().to_numpy()
        except Exception:   # запасной путь (старые версии pandas)
            t = (g["dt"] - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().to_numpy()
            v = g["score"].to_numpy(dtype=float)
            ws = WINDOW.total_seconds()
            lo = np.searchsorted(t, t - ws, "left")
            hi = np.searchsorted(t, t + ws, "right")
            base = [np.nanmedian(v[a:b]) if b - a >= 3 else np.nan for a, b in zip(lo, hi)]
        df.loc[g.index, "baseline"] = base
    k = np.where(df["by_views"], 0.0, 1.0)   # сглаживание для маленьких чисел реакций
    df["index"] = (df["score"] + k) / (df["baseline"] + k)
    df.loc[df["by_views"] & ~(df["views"] > 0), "index"] = np.nan
    df["react_rate"] = np.where(df["views"] > 0, df["reactions"] / df["views"], np.nan)
    return df


def _with_estimates(df: pd.DataFrame) -> pd.DataFrame:
    """Свежим постам (младше недели) — оценка итога по кривой набора просмотров и предварительный индекс.

    Их собственный индекс занижен (ещё набирают), поэтому сравниваем ожидаемый итог с нормой канала —
    медианой постов возрастом 7–42 дня.
    """
    from app.insights import model as M
    now = datetime.now(timezone.utc)
    df = df.copy()
    age_h = (now - df["dt"]).dt.total_seconds() / 3600
    df["age_h"] = age_h
    df["est"] = age_h < MIN_AGE.total_seconds() / 3600
    df["proj"] = df["score"].astype(float)
    df["index_est"] = df["index"]
    if not df["est"].any():
        return df
    curve = M.growth_curve()
    for cid, g in df.groupby("chat_id"):
        fresh = g[g["est"]]
        if fresh.empty:
            continue
        ripe = g[(g["dt"] <= now - MIN_AGE) & (g["dt"] >= now - timedelta(days=42))]
        if len(ripe) < 3:
            ripe = g[g["dt"] <= now - MIN_AGE].tail(15)
        if ripe.empty:
            continue
        base = float(ripe["score"].median())
        k = 0.0 if bool(g["by_views"].iloc[0]) else 1.0
        fr = np.array([max(M.frac_at(curve, max(h, 0.25)), 0.02) for h in fresh["age_h"]])
        proj = fresh["score"].to_numpy(dtype=float) / fr
        df.loc[fresh.index, "proj"] = proj
        df.loc[fresh.index, "index_est"] = (proj + k) / (base + k)
    return df


_frames: dict = {}


def _data_key():
    r = db.query("SELECT COUNT(*) AS n, MAX(updated_at) AS u, SUM(COALESCE(excluded,0)) AS x FROM posts")[0]
    c = db.query("SELECT GROUP_CONCAT(chat_id || rating || selected) AS s FROM channels")[0]["s"]
    return (r["n"], r["u"], r["x"], c, datetime.now(timezone.utc).strftime("%Y%m%d%H"))


def indexed(chat_id: str | None = None) -> pd.DataFrame:
    """Посты с индексом успеха и оценками свежих — запоминаются, пока данные не поменялись."""
    key = (chat_id or "", _data_key())
    hit = _frames.get(chat_id or "")
    if hit is not None and hit[0] == key:
        return hit[1]
    df = _load(chat_id)
    if not df.empty:
        df = _with_estimates(_with_index(df))
    _frames[chat_id or ""] = (key, df)
    return df


def _tz_label() -> str:
    off = datetime.now().astimezone().utcoffset()
    mins = int(off.total_seconds() // 60) if off else 0
    sign = "+" if mins >= 0 else "−"
    h, m = divmod(abs(mins), 60)
    return f"UTC{sign}{h}" + (f":{m:02d}" if m else "")


def set_excluded(post_id: int | None, excluded: bool):
    """Пометить пост «не учитывать» (или вернуть). post_id=None и excluded=False — вернуть все."""
    with db.tx() as c:
        if post_id is None:
            c.execute("UPDATE posts SET excluded=0 WHERE excluded=1")
        else:
            c.execute("UPDATE posts SET excluded=? WHERE id=?", (1 if excluded else 0, int(post_id)))


def _fmt_local(dt) -> str:
    return dt.to_pydatetime().astimezone().strftime("%d.%m.%Y %H:%M")


def overview(period: str = "90", chat_id: str | None = None) -> dict | None:
    full = indexed(chat_id)
    if full.empty:
        return None
    df = full
    now = datetime.now(timezone.utc)
    week = period == "7"   # неделя: почти все посты свежие — работаем с оценками итога
    from app.insights import norms
    lv = norms.levels(chat_id)   # «хит» — лучше 90% постов канала за полгода (пока постов мало — ×2)
    prev = None
    if period != "all":
        days = int(period)
        # тот же отрезок перед выбранным периодом — для «+14% к прошлым 90 дням»
        pdf = df[(df["dt"] >= now - timedelta(days=2 * days)) & (df["dt"] < now - timedelta(days=days))]
        if len(pdf) >= 2:
            col = "index_est" if week else "index"
            pm = pdf[pdf[col].notna() & ((pdf["dt"] <= now - MIN_AGE) | week)]
            val = pdf["proj"] if week else pdf["score"]
            prev = {"posts": int(len(pdf)),
                    "median_views": float(val[pdf["by_views"]].median()) if pdf["by_views"].any() else None,
                    "median_reactions": float(pdf["reactions"].median()),
                    "hits_share": float((pm[col] >= lv["hit"]).mean()) if len(pm) else None}
        df = df[df["dt"] >= now - timedelta(days=days)]
    if week:
        df = df.assign(index=df["index_est"])
        mature = df[df["index"].notna()]
    else:
        mature = df[(df["dt"] <= now - MIN_AGE) & df["index"].notna()]
    metric = "views" if df["by_views"].all() else ("reactions" if not df["by_views"].any() else "mixed")
    if df.empty:
        return {"empty_period": True}

    def post_item(r):
        return {"id": int(r["id"]), "channel": r["channel"], "rating": r["ch_rating"], "date": _fmt_local(r["dt"]),
                "views": int(r["views"]) if not math.isnan(r["views"]) else None, "reactions": int(r["reactions"]),
                "index": round(float(r["index"]), 2) if not math.isnan(r["index"]) else None,
                "link": r["link"], "preview": r["preview"], "n_media": int(r["n_media"] or 1),
                "text": (r["text"] or "")[:140], "est": bool(r.get("est", False)) and week,
                "kind": {"gif": "gif", "animation": "gif", "video": "vid"}.get(str(r.get("media_type") or ""), "img")}

    hits = int((mature["index"] >= lv["hit"]).sum())
    best = mature.loc[mature["index"].idxmax()] if not mature.empty else None
    top = [post_item(r) for _, r in mature.sort_values("index", ascending=False).head(8).iterrows()]

    # Лучшее время: день недели × окно 3 часа, по местному времени компьютера.
    # За неделю в каждой клетке 0–1 пост — для «когда постить» берём полгода истории.
    hm = mature
    if week:
        hm = full[(full["dt"] <= now - MIN_AGE) & (full["dt"] >= now - timedelta(days=180)) & full["index"].notna()]
    loc = hm["dt"].dt.tz_convert(datetime.now().astimezone().tzinfo)
    m2 = hm.assign(dow=loc.dt.dayofweek.values, slot=(loc.dt.hour // 3).values, li=np.log(hm["index"].clip(lower=0.05)))
    # индекс считаем относительно вашего среднего поста: ×1 — как обычно, ×1.3 — на 30% лучше
    if not m2.empty:
        m2["li"] = m2["li"] - m2["li"].mean()
    mg = mature.assign(li=np.log(mature["index"].clip(lower=0.05)))   # для групп — сам период
    if not mg.empty:
        mg["li"] = mg["li"] - mg["li"].mean()
    grid = []
    best_cells = []
    for d in range(7):
        row = []
        for s in range(8):
            cell = m2[(m2["dow"] == d) & (m2["slot"] == s)]
            if len(cell) >= 2:
                val = shrunk(cell["li"])
                row.append({"v": round(val, 2), "raw": round(float(math.exp(cell["li"].mean())), 2), "n": int(len(cell))})
                best_cells.append((val, len(cell), d, s))
            else:
                row.append({"v": None, "n": int(len(cell))})
        grid.append({"day": DAYS[d], "cells": row})
    # Изученность окон за всю историю (а не только за выбранный период): сколько из 56 окон имеют данные.
    # Нужна, чтобы видеть, куда ещё не постили, — иначе всё внимание уходит на уже проверенные окна.
    hist = full[(full["dt"] <= now - MIN_AGE) & full["index"].notna()]
    cnt = {}
    if not hist.empty:
        hl = hist["dt"].dt.tz_convert(datetime.now().astimezone().tzinfo)
        for d_, s_ in zip(hl.dt.dayofweek.values, (hl.dt.hour // 3).values):
            cnt[(int(d_), int(s_))] = cnt.get((int(d_), int(s_)), 0) + 1
    ns = [cnt.get((d_, s_), 0) for d_ in range(7) for s_ in range(8)]
    coverage = {"cells": 56, "solid": sum(1 for n_ in ns if n_ >= 3), "weak": sum(1 for n_ in ns if 1 <= n_ < 3),
                "none": sum(1 for n_ in ns if n_ == 0), "posts": int(len(hist)),
                "grid": [[cnt.get((d_, s_), 0) for s_ in range(8)] for d_ in range(7)]}
    # надёжные лучшие окна: хотя бы 3 поста
    best_cells = sorted([b for b in best_cells if b[1] >= 3], key=lambda b: -b[0])[:3]
    best_times = [{"label": f"{DAYS[d]}, {s * 3:02d}:00–{(s * 3 + 3) % 24:02d}:00", "index": round(v, 2), "n": n,
                   "dow": d, "slot": s} for v, n, d, s in best_cells]

    # Что заходит лучше: типы постов и прочие признаки
    def kind(r):
        if r["media_type"] in ("video", "gif"):
            return "Анимация / видео"
        return "Альбом" if (r["n_media"] or 1) > 1 else "Одна картинка"
    groups = []
    if not mg.empty and not week:
        m2 = mg
        tx = m2["text"].fillna("")
        tags = tx.str.lower()
        feats = [("Тип поста", m2.apply(kind, axis=1))]
        mention = np.where(tx.str.contains(MENTION), "С упоминанием @автора (коллаб, реквест, OC)", "Без упоминаний")
        feats.append(("Соавторы", pd.Series(mention, index=m2.index)))
        nsfw_ch = m2["ch_rating"] == "nsfw"
        if nsfw_ch.any():
            content = np.where(~nsfw_ch, "SFW-канал",
                               np.where(tags.str.contains(r"#sfw\b"), "18+ канал: SFW-работы", "18+ канал: откровенные работы"))
            feats.append(("Канал и содержание", pd.Series(content, index=m2.index)))
        for title, col in feats:
            rows = []
            for name, g in m2.groupby(col):
                if len(g) < 3:
                    continue
                rows.append({"name": name, "n": int(len(g)), "index": round(shrunk(g["li"]), 2),
                             "views": int(g["views"].median()) if g["views"].notna().any() else None,
                             "reactions": round(float(g["reactions"].median()), 1)})
            if len(rows) >= 2:
                groups.append({"title": title, "rows": sorted(rows, key=lambda r: -r["index"])})

    # Каналы: рост (медиана просмотров по месяцам) и сводка
    channels = []
    for cid, g in df.groupby("chat_id"):
        gm = mature[mature["chat_id"] == cid]
        gr = g[g["dt"] <= now - MIN_AGE]   # свежие посты ещё набирают просмотры — последний месяц иначе «проваливается»
        monthly = gr.set_index("dt")["score"].resample("MS").median().dropna() if not gr.empty else gr["score"]
        channels.append({
            "chat_id": cid, "title": g["channel"].iloc[0], "rating": g["ch_rating"].iloc[0], "posts": int(len(g)),
            "median_views": int(g["views"].median()) if g["views"].notna().any() else None,
            "by_views": bool(g["by_views"].iloc[0]),
            "median_reactions": int(g["reactions"].median()),
            "react_rate": round(float(gm["react_rate"].median()), 4) if not gm.empty and gm["react_rate"].notna().any() else None,
            "monthly": [{"m": d.strftime("%m.%y"), "v": round(float(v), 1)} for d, v in monthly.items()],
        })

    # Хэштеги из текста постов — пока нет теггера, это единственные «теги»
    tag_rows = []
    li0 = float(np.log(mature["index"].clip(lower=0.05)).mean()) if not mature.empty else 0.0
    for _, r in mature.iterrows():
        for t in set(x.lower() for x in HASHTAG.findall(r["text"] or "")):
            tag_rows.append((t, math.log(max(r["index"], 0.05)) - li0))
    hashtags = []
    if tag_rows:
        tdf = pd.DataFrame(tag_rows, columns=["tag", "li"]).groupby("tag")["li"].agg(["sum", "size"])
        tdf["mean"] = tdf["sum"] / (tdf["size"] + PRIOR)   # та же поправка на малое число постов
        # теги, стоящие почти на каждом посте (#sfw и т.п.), ничего не различают — убираем
        tdf = tdf[(tdf["size"] >= 3) & (tdf["size"] <= max(3, 0.6 * len(mature)))].sort_values("mean", ascending=False)
        hashtags = [{"tag": t, "index": round(math.exp(r["mean"]), 2), "n": int(r["size"])} for t, r in tdf.head(12).iterrows()]

    days = []
    if week:   # разбивка по дням вместо тепловой карты
        tz = datetime.now().astimezone().tzinfo
        loc_d = df["dt"].dt.tz_convert(tz).dt.date
        today = datetime.now(tz).date()
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            g = df[loc_d == d]
            gi = g["index"].dropna()
            days.append({"date": d.isoformat(), "dow": DAYS[d.weekday()], "n": int(len(g)),
                         "best": round(float(gi.max()), 2) if len(gi) else None,
                         "mean": round(float(math.exp(np.log(gi.clip(lower=0.05)).mean())), 2) if len(gi) else None,
                         "top": [post_item(r) for _, r in g.sort_values("index", ascending=False).head(4).iterrows()]})
    views_col = df["proj"] if week else df["views"]
    return {
        "posts": int(len(df)), "mature": int(len(mature)),
        "metric": metric, "week": week, "days": days,
        "median_views": int(views_col[df["by_views"]].median()) if df["by_views"].any() and df["views"].notna().any() else None,
        "median_reactions": round(float(df["reactions"].median()), 1),
        "hits": hits, "hits_share": round(hits / max(len(mature), 1), 3), "levels": lv,
        "best": post_item(best) if best is not None else None,
        "top": top, "heat": grid, "coverage": coverage, "groups": groups, "best_times": best_times, "channels": channels, "hashtags": hashtags,
        "tz": _tz_label(), "prev": prev, "period": period,
        "excluded": db.query("SELECT COUNT(*) AS n FROM posts WHERE COALESCE(excluded,0)=1")[0]["n"],
    }
