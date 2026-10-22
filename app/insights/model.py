"""Прогноз популярности поста и выводы из истории каналов.

Что предсказываем. Не «сырые» просмотры, а индекс успеха (во сколько раз пост лучше медианы канала
в то же время — как в «Статистике»). Так модель не путает «канал вырос» с «пост хороший».
Прогноз просмотров = текущая медиана канала × предсказанный индекс.

Как. Гребневая (ridge) линейная регрессия по логарифму индекса. Признаки — то, что известно до публикации:
день недели и время, тип поста (одна картинка / альбом / анимация), SFW-работа в 18+ канале,
упоминание соавтора, длинный текст и теги картинки от теггера JTP. «Гребень» подтягивает
вклад редких признаков к нулю: тег, который встречался в 5 постах, не получит огромный вес случайно.

Насколько верить. Точность честно меряется перекрёстной проверкой: модель учится на 4/5 постов и
угадывает оставшуюся 1/5. Отсюда и разброс прогноза, и шанс на «хит» (индекс ≥ ×2).
"""
import math
import re
import threading
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app import db, stats

LAMBDA = 4.0            # сила «подтягивания к нулю» (как PRIOR в статистике: ~4 воображаемых обычных поста)
TAG_PROB = 0.5          # тег картинки считаем, если теггер уверен хотя бы на 50%
MAX_TAGS = 400
HIT = 2.0
SLOTS = 8               # окна по 3 часа
AGES = [1, 3, 6, 12, 24, 48, 72, 120, 168]   # часы — точки кривой набора просмотров
DEFAULT_CURVE = {1: 0.15, 3: 0.3, 6: 0.42, 12: 0.55, 24: 0.68, 48: 0.82, 72: 0.89, 120: 0.96, 168: 1.0}
LINK = re.compile(r"https?://\S+|#\w+|@\w+")
# совсем общие теги ничего не различают (стоят почти на каждом арте)
NOISE_TAGS = {"hi_res", "absurd_res", "digital_media_(artwork)", "ai_generated", "ai_assisted", "anthro", "mammal",
              "solo", "simple_background", "text", "english_text", "signature", "watermark", "url"}

def _taxonomy() -> set:
    """Общие ветки «дерева видов» (canid, mammal…) дублируют конкретный вид — в признаки не берём."""
    from app.publish.hashtags import BROAD
    return set(BROAD) - {"anthro", "feral", "taur", "humanoid", "human", "hybrid", "animal_humanoid"}


_lock = threading.Lock()
_cache: dict = {"key": None, "model": None}


def _tz():
    return datetime.now().astimezone().tzinfo


# --- признаки ----------------------------------------------------------------------------

def _kind(media_type: str | None, n_media: int | None) -> str:
    if media_type in ("video", "gif"):
        return "anim"
    return "album" if (n_media or 1) > 1 else "single"


def base_features(*, local_dt, kind: str, n_media: int, sfw_in_nsfw: bool, text: str) -> dict:
    f = {f"dow:{local_dt.weekday()}": 1.0, f"slot:{local_dt.hour // 3}": 1.0}
    if kind == "anim":
        f["kind:anim"] = 1.0
    elif kind == "album":
        f["kind:album"] = 1.0
        if n_media >= 4:
            f["kind:album4"] = 1.0
    if sfw_in_nsfw:
        f["sfw_in_nsfw"] = 1.0
    t = text or ""
    if stats.MENTION.search(t):
        f["mention"] = 1.0
    if len(LINK.sub("", t).strip()) > 150:
        f["longtext"] = 1.0
    return f


def _label(name: str) -> str:
    kind, _, val = name.partition(":")
    if kind == "dow":
        return stats.DAYS[int(val)]
    if kind == "slot":
        s = int(val) * 3
        return f"{s:02d}:00–{(s + 3) % 24:02d}:00"
    if kind == "tag":
        return val.replace("_", " ")
    return {"kind:anim": "Анимация / видео", "kind:album": "Альбом", "kind:album4": "Большой альбом (4+)",
            "sfw_in_nsfw": "SFW-работа в 18+ канале", "mention": "Упоминание @соавтора",
            "longtext": "Длинный текст", "untagged": "Теги картинки неизвестны"}.get(name, name)


def _all_tags() -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    skip = NOISE_TAGS | _taxonomy()
    for r in db.query("SELECT post_id, tag FROM post_tags WHERE prob>=?", (TAG_PROB,)):
        if r["tag"] not in skip:
            out.setdefault(r["post_id"], []).append(r["tag"])
    return out


def _tagged_ids() -> set[int]:
    return {r["post_id"] for r in db.query("SELECT post_id FROM post_tagged")}


# --- обучение -----------------------------------------------------------------------------

def _cache_key():
    """Модель учится только на постах старше недели — замеры свежих постов её не трогают.
    Переобучается после импорта, разметки теггером, смены каналов и раз в день."""
    edge = (datetime.now(timezone.utc) - stats.MIN_AGE).isoformat()
    r = db.query("""SELECT COUNT(*) AS n, '' AS u, SUM(COALESCE(excluded,0)) AS x FROM posts WHERE date_utc<=?""", (edge,))[0]
    t = db.query("SELECT COUNT(*) AS n FROM post_tagged")[0]["n"]
    c = db.query("SELECT GROUP_CONCAT(chat_id || rating || selected) AS s FROM channels")[0]["s"]
    return (r["n"], r["u"], r["x"], t, c, datetime.now().strftime("%Y-%m-%d"))


def _history() -> pd.DataFrame:
    df = stats._load(None)
    if df.empty:
        return df
    df = stats._with_index(df)
    now = datetime.now(timezone.utc)
    df["mature"] = (df["dt"] <= now - stats.MIN_AGE) & df["index"].notna() & (df["index"] > 0)
    return df


def _ridge(X: np.ndarray, y: np.ndarray, lam: float):
    xm = X.mean(axis=0)
    ym = y.mean()
    Xc = X - xm
    A = Xc.T @ Xc + lam * np.eye(X.shape[1])
    beta = np.linalg.solve(A, Xc.T @ (y - ym))
    return beta, xm, ym


def fit(force: bool = False):
    """Обучает модель по зрелым постам (старше 7 дней). Результат кешируется до изменения данных."""
    with _lock:
        key = _cache_key()
        if not force and _cache["key"] == key:
            return _cache["model"]
        m = _fit()
        _cache.update(key=key, model=m)
        return m


def _fit():
    df = _history()
    if df.empty:
        return None
    tags = _all_tags()
    tagged = _tagged_ids()
    tz = _tz()
    mat = df[df["mature"]].copy()
    if len(mat) < 30:
        return {"too_few": True, "n": int(len(mat)), "df": df}
    rows, tag_lists = [], []
    for _, r in mat.iterrows():
        local = r["dt"].to_pydatetime().astimezone(tz)
        sfw_nsfw = r["ch_rating"] == "nsfw" and bool(re.search(r"#sfw\b", (r["text"] or "").lower()))
        f = base_features(local_dt=local, kind=_kind(r["media_type"], r["n_media"]), n_media=int(r["n_media"] or 1),
                          sfw_in_nsfw=sfw_nsfw, text=r["text"] or "")
        pid = int(r["id"])
        if pid not in tagged:
            f["untagged"] = 1.0
        rows.append(f)
        tag_lists.append(tags.get(pid, []))
    # какие теги брать признаками: не редкие и не «на каждом посте»
    n_tagged = sum(1 for f in rows if "untagged" not in f)
    vocab = []
    if n_tagged >= 20:
        cnt = pd.Series([t for tl in tag_lists for t in set(tl)]).value_counts()
        lo, hi = max(5, int(0.015 * n_tagged)), 0.7 * n_tagged
        vocab = [t for t, c in cnt.items() if lo <= c <= hi][:MAX_TAGS]
    vs = set(vocab)
    for f, tl in zip(rows, tag_lists):
        for t in tl:
            if t in vs:
                f["tag:" + t] = 1.0
    names = sorted({k for f in rows for k in f})
    idx = {k: i for i, k in enumerate(names)}
    X = np.zeros((len(rows), len(names)))
    for i, f in enumerate(rows):
        for k, v in f.items():
            X[i, idx[k]] = v
    y = np.log(mat["index"].to_numpy(dtype=float))

    # честная проверка точности: 5 частей, каждая угадывается моделью, обученной на остальных
    rng = np.random.default_rng(7)
    fold = rng.integers(0, 5, len(y))
    pred_cv = np.zeros_like(y)
    for k in range(5):
        tr, te = fold != k, fold == k
        if te.sum() == 0 or tr.sum() < 10:
            continue
        b, xm, ym = _ridge(X[tr], y[tr], LAMBDA)
        pred_cv[te] = ym + (X[te] - xm) @ b
    res = y - pred_cv
    sigma = float(max(np.std(res), 0.15))
    var_y = float(np.var(y)) or 1e-9
    r2 = float(1 - np.mean(res ** 2) / var_y)
    # насколько чаще модель угадывает «лучше/хуже обычного», чем монетка
    direction = float(np.mean(np.sign(pred_cv - np.median(y)) == np.sign(y - np.median(y))))
    beta, xm, ym = _ridge(X, y, LAMBDA)
    counts = X.sum(axis=0)
    last_seen = {}
    for f, dt in zip(rows, mat["dt"]):
        for k in f:
            if k.startswith("tag:"):
                last_seen[k] = max(last_seen.get(k, dt), dt)
    return {"too_few": False, "n": int(len(y)), "n_tagged": n_tagged, "names": names, "idx": idx, "beta": beta,
            "xm": xm, "ym": float(ym), "sigma": sigma, "r2": r2, "direction": direction,
            "typical_err": float(math.exp(np.median(np.abs(res)))), "counts": counts, "last_seen": last_seen,
            "vocab": vocab, "df": df}


# --- прогноз ------------------------------------------------------------------------------

def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def channel_base(df: pd.DataFrame, chat_id: str) -> dict | None:
    """Текущая «норма» канала: медиана постов возрастом 7–42 дня (свежие ещё набирают)."""
    g = df[df["chat_id"] == str(chat_id)]
    if g.empty:
        return None
    now = datetime.now(timezone.utc)
    ripe = g[(g["dt"] <= now - stats.MIN_AGE)]
    win = ripe[ripe["dt"] >= now - timedelta(days=42)]
    use = win if len(win) >= 3 else ripe.tail(15)
    if use.empty:
        return None
    by_views = bool(g["by_views"].iloc[0])
    recent = ripe[ripe["dt"] >= now - timedelta(days=120)]
    return {"base": float(use["score"].median()), "by_views": by_views, "title": g["channel"].iloc[0],
            "rating": g["ch_rating"].iloc[0], "scores": recent["score"].dropna().to_numpy(dtype=float)}


def _predict_log(m, feats: dict) -> tuple[float, dict]:
    x = np.zeros(len(m["names"]))
    for k, v in feats.items():
        if k in m["idx"]:
            x[m["idx"][k]] = v
    contrib = m["beta"] * (x - m["xm"])
    return m["ym"] + float(contrib.sum()), {n: float(c) for n, c in zip(m["names"], contrib) if abs(c) > 1e-6}


def _factors(contrib: dict, feats: dict) -> list[dict]:
    """Понятные «что прибавляет / что убавляет» — относительно вашего среднего поста."""
    out = []
    t = sum(v for k, v in contrib.items() if k.startswith(("dow:", "slot:")))
    when = [k for k in feats if k.startswith(("dow:", "slot:"))]
    if when:
        out.append({"name": "Время: " + ", ".join(_label(k) for k in sorted(when)), "mult": round(math.exp(t), 2), "kind": "time"})
    for k in ("kind:anim", "kind:album", "kind:album4", "sfw_in_nsfw", "mention", "longtext"):
        c = sum(v for kk, v in contrib.items() if kk == k)
        # отсутствие признака тоже вклад (например «не альбом»), но показываем только присутствующие
        if k in feats and abs(c) > 0.02:
            out.append({"name": _label(k), "mult": round(math.exp(c), 2), "kind": "post"})
    tag_c = sorted(((v, k) for k, v in contrib.items() if k.startswith("tag:") and k in feats), reverse=True)
    pos = [x for x in tag_c if x[0] > 0.03][:4]
    neg = [x for x in tag_c if x[0] < -0.03][-4:]
    for v, k in pos + neg:
        out.append({"name": _label(k), "mult": round(math.exp(v), 2), "kind": "tag"})
    return sorted(out, key=lambda f: -abs(math.log(max(f["mult"], 1e-3))))


def _hit_level(chat_id) -> float:
    try:
        from app.insights import norms
        return norms.levels(chat_id)["hit"]
    except Exception:  # noqa: BLE001
        return HIT


def forecast(*, chat_id: str, when: datetime | None, kind: str, n_media: int, rating: str,
             text: str, tags: list[str] | None) -> dict:
    """Прогноз для одного канала. when — время публикации (UTC или с поясом), None — сейчас."""
    m = fit()
    if m is None:
        return {"ok": False, "why": "Нет статистики — импортируйте каналы в «Настройки» → «Импорт для статистики»"}
    df = m["df"]
    cb = channel_base(df, chat_id)
    if cb is None:
        return {"ok": False, "why": "Этот канал не подключён к статистике — импортируйте его, чтобы видеть прогноз"}
    if m.get("too_few"):
        return {"ok": False, "why": f"Для прогноза нужно хотя бы 30 постов старше недели (сейчас {m['n']})"}
    tz = _tz()
    when = (when or datetime.now(timezone.utc)).astimezone(tz)
    feats = base_features(local_dt=when, kind=kind, n_media=n_media,
                          sfw_in_nsfw=cb["rating"] == "nsfw" and rating == "sfw", text=text)
    if tags is None:
        feats["untagged"] = 1.0
    else:
        for t in tags:
            feats["tag:" + t] = 1.0
    yhat, contrib = _predict_log(m, feats)
    s = m["sigma"]
    z = 0.674 * s
    k = 0.0 if cb["by_views"] else 1.0   # для реакций — та же поправка +1, что и в индексе успеха
    val = lambda y: max((cb["base"] + k) * math.exp(y) - k, 0.0)  # noqa: E731
    exp_ = val(yhat)
    scores = cb["scores"]
    pct = float((scores < exp_).mean()) if len(scores) >= 5 else None

    # лучшее время в ближайшую неделю (при тех же картинках и тексте)
    best = []
    now = datetime.now(tz)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seen = set()
    f_base = {k: v for k, v in feats.items() if not k.startswith(("dow:", "slot:"))}
    for d in range(8):
        for slot in range(SLOTS):
            t = day0 + timedelta(days=d, hours=slot * 3, minutes=90)   # середина окна
            if t <= now + timedelta(minutes=15) or (t.weekday(), slot) in seen:
                continue
            seen.add((t.weekday(), slot))
            y2, _ = _predict_log(m, dict(f_base, **{f"dow:{t.weekday()}": 1.0, f"slot:{slot}": 1.0}))
            best.append((y2, t))
    avg = float(np.mean([b[0] for b in best])) if best else yhat
    best.sort(key=lambda b: -b[0])
    best_times = [{"at": b[1].astimezone(timezone.utc).isoformat(timespec="minutes"),
                   "label": f"{stats.DAYS[b[1].weekday()]} {b[1].strftime('%d.%m %H:%M')}",
                   "gain": round(math.exp(b[0] - yhat), 2), "vs_avg": round(math.exp(b[0] - avg), 2)} for b in best[:3]]
    unit = "просмотров" if cb["by_views"] else "реакций"
    return {
        "ok": True, "chat_id": str(chat_id), "channel": cb["title"], "unit": unit, "by_views": cb["by_views"],
        "expected": round(exp_), "low": round(val(yhat - z)), "high": round(val(yhat + z)),
        "index": round(math.exp(yhat), 2), "base": round(cb["base"]),
        "p_hit": round(1 - _phi((math.log(_hit_level(chat_id)) - yhat) / s), 3),
        "percentile": round(pct, 2) if pct is not None else None,
        "factors": _factors(contrib, feats), "best_times": best_times,
        "tags_known": tags is not None, "quality": quality(m),
        "when": when.astimezone(timezone.utc).isoformat(timespec="minutes"),
    }


def quality(m) -> dict:
    """Насколько модели можно верить — простыми словами."""
    r2, d = m["r2"], m["direction"]
    if r2 < 0.03:
        level, text = "low", "пока почти не лучше угадывания: данных мало или успех мало зависит от этих признаков"
    elif r2 < 0.12:
        level, text = "mid", "грубый ориентир: угадывает направление, но не точную цифру"
    else:
        level, text = "good", "неплохо угадывает, какие посты зайдут лучше"
    return {"level": level, "text": text, "r2": round(r2, 3), "direction": round(d, 2),
            "typical_err": round(m["typical_err"], 2), "n": m["n"], "n_tagged": m["n_tagged"]}


# --- кривая набора просмотров -----------------------------------------------------------

def growth_curve(chat_id: str | None = None) -> dict:
    """Какую долю недельных просмотров пост набирает к 1, 3, 6… часам (по вашим замерам)."""
    sql = """SELECT s.post_id, s.taken_at, s.views, p.date_utc, p.views AS final, p.updated_at FROM snapshots s
             JOIN posts p ON p.id=s.post_id WHERE s.views IS NOT NULL AND p.views>0"""
    args = ()
    if chat_id:
        sql += " AND p.chat_id=?"
        args = (str(chat_id),)
    rows = db.query(sql, args)
    pts = {a: [] for a in AGES}
    for r in rows:
        try:
            d0 = datetime.fromisoformat(r["date_utc"].replace("Z", "+00:00"))
            ts = datetime.fromisoformat(r["taken_at"].replace("Z", "+00:00"))
            up = datetime.fromisoformat((r["updated_at"] or r["taken_at"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if (up - d0).total_seconds() < 6.5 * 86400:   # «итог» поста ещё не зрелый
            continue
        age = (ts - d0).total_seconds() / 3600
        for a in AGES:
            if a * 0.75 <= age <= a * 1.33:
                pts[a].append(min(r["views"] / r["final"], 1.0))
    own = {a: float(np.median(v)) for a, v in pts.items() if len(v) >= 5}
    curve = {a: own.get(a, DEFAULT_CURVE[a]) for a in AGES}
    # кривая не может убывать
    run = 0.0
    for a in AGES:
        run = max(run, curve[a])
        curve[a] = min(run, 1.0)
    curve[168] = 1.0
    return {"points": [{"h": a, "frac": round(curve[a], 3), "own": a in own or (a == 168 and len(own) >= 3), "n": len(pts[a])} for a in AGES],
            "own": len(own) >= 3}


def frac_at(curve: dict, hours: float) -> float:
    pts = curve["points"]
    if hours <= pts[0]["h"]:
        return max(pts[0]["frac"] * hours / pts[0]["h"], 0.02)
    for a, b in zip(pts, pts[1:]):
        if hours <= b["h"]:
            w = (math.log(hours) - math.log(a["h"])) / (math.log(b["h"]) - math.log(a["h"]))
            return a["frac"] + w * (b["frac"] - a["frac"])
    return 1.0


# --- выводы для «Статистики» ----------------------------------------------------------------

def insights() -> dict | None:
    m = fit()
    if m is None:
        return None
    if m.get("too_few"):
        return {"too_few": True, "n": m["n"]}
    eff = []
    for name, b, c in zip(m["names"], m["beta"], m["counts"]):
        if name.startswith("tag:"):
            eff.append({"tag": name[4:], "label": _label(name), "mult": round(math.exp(b), 2), "n": int(c),
                        "last": m["last_seen"].get(name)})
    now = pd.Timestamp.now(tz="UTC")
    up = sorted([e for e in eff if e["mult"] >= 1.05], key=lambda e: -e["mult"])[:12]
    down = sorted([e for e in eff if e["mult"] <= 0.95], key=lambda e: e["mult"])[:8]
    # заходило, но давно не было
    forgotten = []
    for e in sorted(eff, key=lambda e: -e["mult"]):
        if e["mult"] >= 1.12 and e["last"] is not None and (now - e["last"]).days >= 30:
            forgotten.append(dict(e, days=int((now - e["last"]).days)))
        if len(forgotten) >= 6:
            break
    for e in up + down + forgotten:
        e.pop("last", None)
    struct = []
    for name in ("kind:anim", "kind:album", "kind:album4", "sfw_in_nsfw", "mention", "longtext"):
        if name in m["idx"]:
            i = m["idx"][name]
            if m["counts"][i] >= 3:
                struct.append({"name": _label(name), "mult": round(math.exp(m["beta"][i]), 2), "n": int(m["counts"][i])})
    return {"too_few": False, "quality": quality(m), "tags_up": up, "tags_down": down, "forgotten": forgotten,
            "structure": struct, "curve": growth_curve(), "accuracy": accuracy(),
            "tagging": None}


def accuracy() -> dict:
    """Прогнозы, сделанные при публикации, против того, что вышло на деле (для постов старше недели)."""
    import json
    import app.publish.runner  # noqa: F401  (создаёт таблицу publications)
    rows = db.query("""SELECT f.pub_id, f.chat_id, f.data, f.made_at, pb.title FROM forecasts f
                       JOIN publications pb ON pb.id=f.pub_id ORDER BY f.pub_id DESC LIMIT 60""")
    items = []
    for r in rows:
        try:
            fc = json.loads(r["data"])
        except Exception:
            continue
        actual = actual_for(r["pub_id"], r["chat_id"])
        if not actual:
            continue
        items.append({"pub_id": r["pub_id"], "title": r["title"] or "", "channel": fc.get("channel"),
                      "expected": fc.get("expected"), "low": fc.get("low"), "high": fc.get("high"),
                      "actual": actual["score"], "mature": actual["mature"], "unit": fc.get("unit")})
    mature = [i for i in items if i["mature"] and i["expected"]]
    inside = sum(1 for i in mature if i["low"] <= i["actual"] <= i["high"])
    return {"items": items[:12], "n": len(mature), "inside": inside,
            "typical_err": round(float(math.exp(np.median([abs(math.log(max(i["actual"], 1) / max(i["expected"], 1)))
                                                            for i in mature]))), 2) if mature else None}


def actual_for(pub_id: int, chat_id: str) -> dict | None:
    import json
    r = db.query("SELECT dests FROM publications WHERE id=?", (int(pub_id),))
    if not r:
        return None
    for d in json.loads(r[0]["dests"] or "[]"):
        if str(d.get("chat_id")) == str(chat_id) and d.get("message_ids"):
            p = post_for(str(chat_id), d["message_ids"])
            if p:
                d0 = datetime.fromisoformat(p["date_utc"].replace("Z", "+00:00"))
                by_views = p["views"] is not None
                return {"score": p["views"] if by_views else (p["reactions"] or 0),
                        "mature": datetime.now(timezone.utc) - d0 >= stats.MIN_AGE}
    return None


def post_for(chat_id: str, message_ids: list) -> dict | None:
    if not message_ids:
        return None
    q = ",".join("?" * len(message_ids))
    r = db.query(f"""SELECT p.* FROM messages m JOIN posts p ON p.id=m.post_id
                     WHERE m.platform='telegram' AND m.chat_id=? AND m.msg_id IN ({q}) LIMIT 1""",
                 (str(chat_id), *[int(x) for x in message_ids]))
    return r[0] if r else None
