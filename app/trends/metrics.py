"""Метрики трендов по недельной статистике тегов.

Главная величина — «отклик» тега (lift): во сколько раз пост с этим тегом набирает больше
избранного, чем средний пост сайта той же недели и того же рейтинга. Считается в логарифмах,
со сглаживанием для редких тегов (K «воображаемых» средних постов), поэтому тег из
2 постов не выскочит в лидеры случайно.
"""
import json
import math
import re
import threading
from datetime import datetime

import numpy as np
import pandas as pd

from app.trends.aggregate import out_dir, week_of, week_start

K = 8            # сглаживание отклика: столько «средних» постов добавляем к тегу
PHI = 0.8        # затухание тренда в прогнозе
MIN = {          # минимум постов в окне, чтобы тег участвовал
    "e6ai": {"pop": 12, "rise": 8, "niche": 10, "fc": 4},
    "e621": {"pop": 40, "rise": 25, "niche": 40, "fc": 12},
}
YEAR_RE = re.compile(r"(?:^|[_(])(?:19|20)\d{2}(?:[)_]|$)")  # теги конкретного года не повторяются
CAT_RU = {"species": "вид", "character": "персонаж", "franchise": "франшиза", "general": "тема"}


class Model:
    """Матрицы тег × неделя для одного источника и рейтинга."""

    def __init__(self, source: str, rating: str):
        od = out_dir(source)
        meta = json.loads((od / "meta.json").read_text(encoding="utf-8"))
        tw = pd.read_pickle(od / "tags_weekly.pkl")
        site = pd.read_pickle(od / "site_weekly.pkl")
        if rating in ("s", "q", "e"):
            tw = tw[tw["rating"] == rating]
            site = site[site["rating"] == rating]
        self.source, self.rating, self.meta = source, rating, meta
        self.mins = MIN.get(source, MIN["e6ai"])

        wd = week_of(meta["dump_date"])
        self.today_week = wd
        self.wend = wd - 2                       # последняя неделя, где все посты старше 7 дней
        w0 = int(min(tw["week"].min(), site["week"].min())) if len(tw) else self.wend - 100
        self.weeks = np.arange(max(w0, self.wend - 117), self.wend + 1)
        W = len(self.weeks)
        idx = {w: i for i, w in enumerate(self.weeks)}

        s = site.groupby("week")[["n", "sy"]].sum()
        self.site_n = np.zeros(W)
        site_sy = np.zeros(W)
        for w, row in s.iterrows():
            if w in idx:
                self.site_n[idx[w]] = row["n"]
                site_sy[idx[w]] = row["sy"]
        m = np.divide(site_sy, self.site_n, out=np.zeros(W), where=self.site_n > 0)

        g = tw[tw["week"].isin(self.weeks)].groupby(["tag", "week"], as_index=False)[["n", "sy"]].sum()
        self.tags = np.array(sorted(g["tag"].unique()))
        ti = {t: i for i, t in enumerate(self.tags)}
        self.N = np.zeros((len(self.tags), W), dtype=np.float32)
        SY = np.zeros((len(self.tags), W), dtype=np.float32)
        r = g["tag"].map(ti).to_numpy()
        c = g["week"].map(idx).to_numpy()
        self.N[r, c] = g["n"].to_numpy()
        SY[r, c] = g["sy"].to_numpy()
        self.D = SY - self.N * m[None, :]          # «лишний» лог-отклик сверх фона недели
        self.ti = ti
        cats = meta.get("categories", {})
        self.cat = np.array([cats.get(t, "general") for t in self.tags])
        self.dated = np.array([bool(YEAR_RE.search(t)) for t in self.tags])  # halloween_2025 и т.п.
        self._backtest = None

    # --- базовые величины по окну последних недель ---
    def cols(self, start_back: int, end_back: int = 0) -> slice:
        W = len(self.weeks)
        return slice(max(0, W - start_back), W - end_back)

    def lift(self, sl: slice) -> np.ndarray:
        return np.exp(self.D[:, sl].sum(1) / (self.N[:, sl].sum(1) + K))

    def share(self, sl: slice, pseudo: float = 0.0) -> np.ndarray:
        tot = self.site_n[sl].sum()
        return (self.N[:, sl].sum(1) + pseudo) / max(tot, 1)

    def smooth_log(self, i: int) -> np.ndarray:
        """Сглаженный (3 недели) лог-отклик тега по неделям."""
        d = np.convolve(self.D[i], np.ones(3), "full")[: len(self.weeks)]
        n = np.convolve(self.N[i], np.ones(3), "full")[: len(self.weeks)]
        return d / (n + 3)

    def spark(self, i: int, weeks: int = 16) -> list[float]:
        return [round(float(v), 3) for v in np.exp(self.smooth_log(i)[-weeks:])]


_cache: dict = {}
_lock = threading.Lock()


KEEP_MODELS = 2   # в памяти — только последние открытые (источник × рейтинг); остальные пересоберутся за секунду


def get_model(source: str, rating: str) -> Model | None:
    od = out_dir(source)
    meta = od / "meta.json"
    if not meta.exists():
        return None
    key = (source, rating, meta.stat().st_mtime)
    with _lock:
        if key in _cache:
            _cache[key] = _cache.pop(key)   # свежий — в конец
        else:
            for k in [k for k in _cache if k[:2] == (source, rating)]:
                del _cache[k]
            _cache[key] = Model(source, rating)
            while len(_cache) > KEEP_MODELS:
                del _cache[next(iter(_cache))]
        return _cache[key]


def forget(source: str | None = None):
    """Выгрузить модели из памяти (после пересборки трендов)."""
    with _lock:
        for k in [k for k in _cache if source is None or k[0] == source]:
            del _cache[k]
    import gc
    gc.collect()


def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%d.%m")


from app.textutil import plural  # noqa: E402,F401  (общий помощник)


# --- Прогноз --------------------------------------------------------------------

RHO = 0.6       # насколько быстро краткосрочный всплеск возвращается к среднему (за неделю)


def _forecast_series(L: np.ndarray, n: np.ndarray, t: int, horizon: int = 4):
    """Прогноз лог-отклика из точки t.

    Три слагаемых: среднее за полгода (куда возвращаются случайные всплески),
    отклонение последних недель от него (затухает), устойчивый тренд за 13 недель (затухает медленнее)
    и сезонная поправка — как менялся отклик в те же недели прошлого года.
    """
    if t < 12:
        return None
    lo = max(0, t - 25)
    y, w = L[lo:t + 1], n[lo:t + 1] + 1.0
    long_mean = float(np.average(y, weights=w))
    level = float(L[t])
    # тренд: взвешенный наклон за последние 13 недель, со «скепсисом» к шуму
    y13, w13 = L[t - 12:t + 1], n[t - 12:t + 1] + 1.0
    x = np.arange(13, dtype=float)
    xm, ym = np.average(x, weights=w13), np.average(y13, weights=w13)
    var = np.average((x - xm) ** 2, weights=w13)
    slope = np.average((x - xm) * (y13 - ym), weights=w13) / var if var > 0 else 0.0
    resid = y13 - (ym + slope * (x - xm))
    sd = float(math.sqrt(np.average(resid ** 2, weights=w13)))
    se = sd / math.sqrt(max(var * 13, 1e-9))           # погрешность наклона
    t_stat = abs(slope) / se if se > 0 else 0.0
    slope *= min(1.0, max(0.0, (t_stat - 1.0) / 2.0))   # слабый наклон считаем шумом
    trend_now = ym + slope * (x[-1] - xm)               # где «был бы» тег без всплеска
    out = []
    for h in range(1, horizon + 1):
        base = long_mean + (trend_now - long_mean)       # устойчивая часть
        dev = (level - trend_now) * RHO ** h             # краткий всплеск затухает
        trend = slope * sum(PHI ** k for k in range(1, h + 1))
        season = 0.0
        if t - 52 >= 0:
            season = 0.5 * (L[t - 52 + h] - L[t - 52])
        out.append(base + dev + trend + season)
    return level, np.array(out), max(sd, 0.03)


def forecast(model: Model, tag: str, history: int = 16) -> dict | None:
    i = model.ti.get(tag)
    if i is None:
        return None
    L = model.smooth_log(i)
    nn = np.convolve(model.N[i], np.ones(3), "full")[: len(model.weeks)]
    t = len(L) - 1
    fc = _forecast_series(L, nn, t)
    if fc is None:
        return None
    level, f, sd = fc
    band = np.array([1.28 * sd * math.sqrt(h) for h in range(1, 5)])  # ~80% диапазон
    change = math.exp(f[-1] - level) - 1
    weekly = model.N[i, -4:].mean()
    return {
        "tag": tag,
        "category": model.cat[i],
        "history": [round(float(v), 3) for v in np.exp(L[-history:])],
        "history_weeks": [_fmt_date(week_start(w)) for w in model.weeks[-history:]],
        "forecast": [round(float(v), 3) for v in np.exp(f)],
        "lo": [round(float(v), 3) for v in np.exp(f - band)],
        "hi": [round(float(v), 3) for v in np.exp(f + band)],
        "change": round(change, 3),
        "confidence": "высокая" if sd < 0.08 else ("средняя" if sd < 0.18 else "низкая"),
        "weekly_posts": round(float(weekly), 1),
        "posts_90d": int(model.N[i, model.cols(13)].sum()),
    }


def backtest(model: Model) -> dict:
    """Насколько прогноз «вырастет / упадёт» сбывался на истории (6 точек за полгода)."""
    if model._backtest is not None:
        return model._backtest
    W = len(model.weeks)
    n26 = model.N[:, model.cols(26)].sum(1)
    top = np.argsort(-n26)[:300]
    top = [i for i in top if n26[i] >= model.mins["fc"] * 6]
    hits = total = 0
    for i in top:
        L = model.smooth_log(i)
        nn = np.convolve(model.N[i], np.ones(3), "full")[:W]
        for k in range(0, 24, 4):
            t = W - 1 - 4 - k
            if t < 12:
                continue
            fc = _forecast_series(L, nn, t)
            if fc is None:
                continue
            level, f, _ = fc
            pred = f[-1] - level
            if abs(pred) < 0.05:
                continue
            actual = L[t + 4] - L[t]
            total += 1
            hits += int(np.sign(actual) == np.sign(pred))
    model._backtest = {"hits": hits, "total": total, "accuracy": round(hits / total, 3) if total else None}
    return model._backtest


# --- Обзор -----------------------------------------------------------------------

def overview(model: Model, category: str = "all", style: list[str] | None = None, mine_only: bool = False,
             hidden: list[str] | None = None) -> dict:
    style = set(style or [])
    mins = model.mins
    hidden = set(hidden or [])
    mask = np.array([t not in hidden for t in model.tags], dtype=bool)
    if category != "all":
        mask &= model.cat == category
    is_mine = np.array([t in style for t in model.tags])
    if mine_only:
        mask &= is_mine

    def item(i, **extra):
        return {"tag": str(model.tags[i]), "category": str(model.cat[i]), "mine": bool(is_mine[i]), **extra}

    # Популярно сейчас: отклик за последние 4 недели
    s4, s13, s26 = model.cols(4), model.cols(13), model.cols(26, 13)
    n4, n13, n26 = model.N[:, s4].sum(1), model.N[:, s13].sum(1), model.N[:, s26].sum(1)
    lift4, lift13, lift26 = model.lift(s4), model.lift(s13), model.lift(s26)
    ok = mask & (n4 >= mins["pop"])
    # ранжируем осторожно: отклик, «сжатый» к среднему сильнее, чем для показа, — у тега из 10 работ
    # высокий отклик может быть случайностью, у тега из 200 — уже нет
    k_rank = 3 * mins["pop"]
    cautious = np.exp(model.D[:, s4].sum(1) / (n4 + k_rank))
    popular = [item(i, lift=round(float(lift4[i]), 3), per_month=int(round(n13[i] / 3)), spark=model.spark(i))
               for i in np.argsort(-np.where(ok, cautious, -1))[:8] if ok[i]]

    # Набирает популярность: последние 13 недель против предыдущих 13
    shB, shA = model.share(s13), model.share(s26)
    ok = mask & ~model.dated & (n13 >= mins["rise"]) & (n26 >= mins["rise"])
    resp = np.where(ok, lift13 / lift26, 1.0)
    supply = np.where(ok & (shA > 0), shB / np.maximum(shA, 1e-12), 1.0)
    weight = np.minimum(1.0, np.sqrt(n13 / (4 * mins["rise"])))
    score = (np.log(resp) + 0.4 * np.log(np.maximum(supply, 1e-6))) * weight
    seasonal = _seasonal(model, mask & ~model.dated)
    seasonal_tags = {s["tag"] for s in seasonal}
    rising = []
    for i in np.argsort(-np.where(ok, score, -9)):
        if not ok[i] or score[i] <= 0.03 or len(rising) >= 8:
            break
        badge = ""
        if model.tags[i] in seasonal_tags:
            badge = "сезон"
        elif n13[i] < 3 * mins["rise"] and supply[i] > 1.5:
            badge = "ранний сигнал"
        elif is_mine[i]:
            badge = "ваш стиль"
        rising.append(item(i, response=round(float(resp[i] - 1), 3), supply=round(float(supply[i] - 1), 3),
                           per_month=int(round(n13[i] / 3)), spark=model.spark(i), badge=badge))

    # Ниши: спрос (отклик) против конкуренции (постов в месяц)
    ok = mask & (n13 >= mins["niche"])
    by_volume = [i for i in np.argsort(-n13) if ok[i]][:50]
    by_lift = [i for i in np.argsort(-lift13) if ok[i] and i not in by_volume][:25]
    by_mine = [i for i in np.nonzero(ok & is_mine)[0] if i not in by_volume and i not in by_lift]
    cand = by_volume + by_lift + list(by_mine)
    pts = [{"tag": model.tags[i], "category": model.cat[i], "mine": bool(is_mine[i]),
            "per_month": round(float(n13[i] / 3), 1), "lift": round(float(lift13[i]), 3)} for i in cand]
    x_med = float(np.median([p["per_month"] for p in pts])) if pts else 0.0
    niche_list = sorted([p for p in pts if p["per_month"] <= x_med and p["lift"] >= 1.15],
                        key=lambda p: (not p["mine"], -p["lift"]))
    growth = {model.tags[i]: float(resp[i] - 1) for i in cand}
    niche_top = []
    for p in niche_list[:3]:
        g = growth.get(p["tag"], 0)
        pm = int(round(p["per_month"]))
        txt = f"{pm} {plural(pm, 'пост', 'поста', 'постов')} в месяц, отклик ×{p['lift']:.2f} к среднему"
        if abs(g) >= 0.1:
            txt += f", {'растёт' if g > 0 else 'снижается'} {g:+.0%}"
        if p["mine"]:
            txt += " · ваш стиль"
        niche_top.append({"tag": p["tag"], "text": txt})
    labeled = {p["tag"] for p in niche_list[:5]} | {p["tag"] for p in sorted(pts, key=lambda p: -p["per_month"])[:3]} | \
              {p["tag"] for p in pts if p["mine"]}
    for p in pts:
        p["label"] = p["tag"] in labeled

    return {
        "source": model.source, "rating": model.rating,
        "dump_date": model.meta["dump_date"], "posts": model.meta["posts"],
        "week_end": _fmt_date(week_start(model.wend)),
        "popular": popular, "rising": rising,
        "niches": {"points": pts, "x_median": x_med}, "niche_top": niche_top,
        "seasonal": seasonal[:4],
        "backtest": backtest(model),
    }


def _seasonal(model: Model, mask: np.ndarray) -> list[dict]:
    """Теги, у которых в ближайшие 5 недель прошлого года была заметно большая доля постов."""
    W = len(model.weeks)
    first = model.weeks[0]
    t0 = model.today_week - 52 - first           # «сегодня» год назад, индекс столбца
    if t0 - 26 < 0 or t0 + 5 > W:
        return []
    tgt = slice(t0, t0 + 5)
    base = np.r_[max(0, t0 - 26):t0, t0 + 5:min(W, t0 + 26)]
    sh_t = model.N[:, tgt].sum(1) / max(model.site_n[tgt].sum(), 1)
    sh_b = model.N[:, base].sum(1) / max(model.site_n[base].sum(), 1)
    n_t = model.N[:, tgt].sum(1)
    ratio = np.where(sh_b > 0, sh_t / np.maximum(sh_b, 1e-12), 0)
    ok = mask & (n_t >= model.mins["rise"] * 2) & (ratio >= 1.8)
    out = []
    for i in np.argsort(-np.where(ok, ratio, 0))[:6]:
        if not ok[i]:
            break
        peak = int(np.argmax(model.N[i, tgt] / np.maximum(model.site_n[tgt], 1)))
        peak_date = week_start(model.weeks[t0 + peak] + 52)
        now = peak_date.date() <= datetime.fromisoformat(model.meta["dump_date"]).date()
        out.append({"tag": str(model.tags[i]), "ratio": round(float(ratio[i]), 2),
                    "peak": _fmt_date(peak_date), "now": bool(now), "category": str(model.cat[i])})
    return out


def leads(m621: Model, m6: Model, style: list[str] | None = None, limit: int = 4,
          hidden: list[str] | None = None) -> dict:
    """Опережающие сигналы: работ с тегом на e621 становится заметно больше, а на e6ai — нет.

    Рост считается с поправкой на общий объём сайта и со сглаживанием: тег, у которого было
    2 работы, а стало 30, не получает «+1400%» и не вытесняет устойчивые тренды.
    """
    style = set(style or [])
    hidden = set(hidden or [])
    B, A = m621.cols(13), m621.cols(26, 13)
    n_b, n_a = m621.N[:, B].sum(1), m621.N[:, A].sum(1)
    vol = m621.site_n[B].sum() / max(m621.site_n[A].sum(), 1)       # сайт тоже растёт
    k = m621.mins["rise"]
    gap621 = np.log((n_b + k) / (n_a * vol + k))
    B6, A6 = m6.cols(13), m6.cols(26, 13)
    n6_b, n6_a = m6.N[:, B6].sum(1), m6.N[:, A6].sum(1)
    vol6 = m6.site_n[B6].sum() / max(m6.site_n[A6].sum(), 1)
    k6 = m6.mins["rise"]
    out = []
    for i, tag in enumerate(m621.tags):
        if m621.dated[i] or tag in hidden or n_b[i] < k or gap621[i] < math.log(1.3):
            continue
        j = m6.ti.get(tag)
        g6 = float(np.log((n6_b[j] + k6) / (n6_a[j] * vol6 + k6))) if j is not None else 0.0
        diff = gap621[i] - max(g6, 0.0)
        if diff < math.log(1.25):
            continue
        out.append({
            "tag": str(tag), "category": str(m621.cat[i]), "mine": tag in style,
            "e621_before": int(round(n_a[i] / 3)), "e621_now": int(round(n_b[i] / 3)),
            "e6ai_before": int(round(n6_a[j] / 3)) if j is not None else 0,
            "e6ai_now": int(round(n6_b[j] / 3)) if j is not None else 0,
            "new": bool(n_a[i] / 3 < 5),                                 # тега почти не было полгода назад
            "score": diff * min(1.0, math.sqrt(n_b[i] / (4 * k))),
        })
    out.sort(key=lambda x: -x["score"])
    for x in out:
        del x["score"]
    return {"new": [x for x in out if x["new"]][:limit], "growing": [x for x in out if not x["new"]][:limit]}
