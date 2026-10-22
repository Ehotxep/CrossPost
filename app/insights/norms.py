"""Пороги «лучше обычного / хит / супер-хит» и «необычно много реакций» — из вашей же истории, а не заданные вручную.

Индекс успеха уже считается от нормы канала (×1 — обычный пост). Здесь решаем, какой индекс у ЭТОГО канала
считать хитом: тот, что выше, чем у 90% его постов за полгода. Так у ровного канала хит — например ×1,6,
а у канала с редкими «выстрелами» — ×2,5.

Пока постов мало (меньше 20 зрелых), такие пороги ненадёжны — берём стартовые ×1,3 / ×2 / ×3 и по мере
накопления истории плавно переходим на свои (полностью — с 60 постов).

Реакции и пересылки свежего поста сравниваем с тем, сколько их обычно бывает у постов канала к этому
же возрасту (итог поста × доля, набранная к этому часу по кривой набора).
"""
import math
import time
from datetime import datetime, timedelta, timezone

import numpy as np

DEFAULT = {"good": 1.3, "hit": 2.0, "super": 3.0}
QUANT = {"good": 0.75, "hit": 0.90, "super": 0.97}
MIN_POSTS, FULL_POSTS = 20, 60
HISTORY_DAYS = 180
# сколько новостей: какие ступени (доля постов канала, которых пост обогнал) объявлять
VOLUME = {"few": (0.97,), "normal": (0.90, 0.97), "many": (0.75, 0.90, 0.97)}
VOLUME_LABEL = {"few": "мало", "normal": "средне", "many": "много"}

_memo: dict = {}


def volume() -> str:
    from app import settings
    v = settings.load().get("news_volume", "normal")
    return v if v in VOLUME else "normal"


def tiers() -> tuple:
    return VOLUME[volume()]


def _mature(chat_id):
    from app import stats
    df = stats.indexed(str(chat_id) if chat_id else None)
    if df is None or df.empty:
        return df
    now = datetime.now(timezone.utc)
    return df[(df["dt"] <= now - stats.MIN_AGE) & (df["dt"] >= now - timedelta(days=HISTORY_DAYS)) & df["index"].notna()]


def _key(chat_id):
    from app import stats
    return (str(chat_id or ""), stats._data_key())


def levels(chat_id=None) -> dict:
    """{"good", "hit", "super"} — пороги индекса для канала (или всех каналов), плюс сколько постов за ними."""
    k = ("lv",) + _key(chat_id)
    hit = _memo.get(k)
    if hit:
        return hit
    out = dict(DEFAULT, n=0, own=0.0, q=QUANT)
    try:
        m = _mature(chat_id)
        idx = m["index"].to_numpy(dtype=float) if m is not None and not m.empty else np.array([])
        idx = idx[np.isfinite(idx) & (idx > 0)]
        n = len(idx)
        out["n"] = n
        w = min(max((n - MIN_POSTS) / (FULL_POSTS - MIN_POSTS), 0.0), 1.0)
        out["own"] = round(w, 2)
        if w > 0:
            for name, q in QUANT.items():   # смешиваем в логарифмах: ×1,5 и ×2 — «одинаково далеко» от ×1,73
                own = float(np.quantile(idx, q))
                out[name] = math.exp(w * math.log(max(own, 1.01)) + (1 - w) * math.log(DEFAULT[name]))
    except Exception:  # noqa: BLE001 — без статистики остаются стартовые пороги
        pass
    # порядок и разумные границы: «лучше обычного» хотя бы ×1,1, ступени не сливаются
    out["good"] = max(out["good"], 1.1)
    out["hit"] = max(out["hit"], out["good"] * 1.12)
    out["super"] = max(out["super"], out["hit"] * 1.2)
    for name in DEFAULT:
        out[name] = round(out[name], 2)
    if len(_memo) > 200:
        _memo.clear()
    _memo[k] = out
    return out


def levels_cached(chat_id=None) -> dict:
    """Для частых запросов (info раз в секунду): готовое — сразу, иначе стартовые пороги и расчёт в фоне."""
    try:
        k = ("lv",) + _key(chat_id)
    except Exception:  # noqa: BLE001
        return dict(DEFAULT, n=0, own=0.0, q=QUANT)
    if k in _memo:
        return _memo[k]
    if not _memo.get(("busy", k)):
        import threading
        _memo[("busy", k)] = True
        threading.Thread(target=levels, args=(chat_id,), daemon=True, name="levels").start()
    for kk, v in list(_memo.items()):   # прошлый расчёт лучше стартовых чисел
        if isinstance(kk, tuple) and kk[:2] == ("lv", str(chat_id or "")):
            return v
    return dict(DEFAULT, n=0, own=0.0, q=QUANT)


def index_rank(chat_id, idx: float) -> float | None:
    """Какую долю постов канала (за полгода) обгоняет такой индекс. None — постов слишком мало."""
    try:
        m = _mature(chat_id)
        if m is None or len(m) < MIN_POSTS:
            return None
        v = m["index"].to_numpy(dtype=float)
        v = v[np.isfinite(v)]
        return float((v < idx).mean())
    except Exception:  # noqa: BLE001
        return None


def _curve():
    hit = _memo.get("curve")
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    from app.insights import model as M
    c = M.growth_curve()
    _memo["curve"] = (time.time(), c)
    return c


def metric_rank(chat_id, metric: str, value: float, age_h: float) -> float | None:
    """Доля постов канала, у которых к этому возрасту было меньше `metric` (reactions / forwards / replies / views).
    Ожидаемое к возрасту = итог поста × доля, набранная к этому часу по кривой набора."""
    if value is None:
        return None
    try:
        from app.insights import model as M
        m = _mature(chat_id)
        if m is None or len(m) < MIN_POSTS or metric not in m:
            return None
        final = m[metric].astype(float).to_numpy()
        final = final[np.isfinite(final)]
        if len(final) < MIN_POSTS:
            return None
        frac = M.frac_at(_curve(), max(age_h, 0.25)) if age_h < 168 else 1.0
        expected = final * frac
        return float((expected < value).mean())
    except Exception:  # noqa: BLE001
        return None


def tier_text(q: float) -> str:
    return f"больше, чем у {int(round(q * 100))}% постов"
