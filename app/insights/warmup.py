"""Заранее посчитанные экраны: «Статистика» (все периоды и каналы), выводы и прогноз, «История» и календарь.

Вызывается фоновой отправкой раз в несколько минут и окном приложения после импорта.
Считает только то, что устарело, — обычно это доли секунды.
"""
import threading
from datetime import datetime

from app import cache, db, stats

PERIODS = ("7", "30", "90", "365", "all")
_lock = threading.Lock()


def stats_name(period: str, chat_id: str | None) -> str:
    return f"stats_{period}_{chat_id or 'all'}"


def stats_key():
    return stats._data_key()


def stats_compute(period: str, chat_id: str | None):
    return lambda: stats.overview(period, chat_id or None)


def insights_compute():
    from app.insights import model as M
    try:
        return M.insights() or {}
    except Exception as e:  # noqa: BLE001
        return {"error": f"Не получилось посчитать: {e}"}


def insights_key():
    from app.insights import model as M
    return M._cache_key()


def summary_compute():
    from app.insights import post_stats
    import app.publish.runner  # noqa: F401
    rows = db.query("SELECT id, created FROM publications ORDER BY id DESC LIMIT 300")
    out = post_stats.summary([r["id"] for r in rows])
    # место в «Лидерах месяца» (1–3) — для медалей в списке «Истории»
    for ym in sorted({str(r["created"])[:7] for r in rows}):
        try:
            data = cache.get(calendar_name(ym), calendar_compute(ym), calendar_key(), max_age=1800)["data"]
        except Exception:
            continue
        for i, it in enumerate((data.get("leaders") or [])[:3]):
            if it.get("pub_id") and str(it["pub_id"]) in out:
                out[str(it["pub_id"])]["rank"] = i + 1
    return out


def calendar_name(ym: str) -> str:
    return f"calendar_{ym}"


def calendar_compute(ym: str):
    from app.insights import calendar
    return lambda: calendar.month(ym)


def calendar_key():
    import app.publish.runner  # noqa: F401
    from app.publish import schedule  # noqa: F401  (таблица scheduled)
    p = db.query("SELECT COUNT(*) AS n, MAX(id) AS m FROM publications")[0]
    s = db.query("SELECT COUNT(*) AS n, MAX(run_at) AS r, GROUP_CONCAT(state) AS st FROM scheduled WHERE state IN ('pending','running','missed')")[0]
    return (stats._data_key(), p["n"], p["m"], s["n"], s["r"], s["st"])


_seen: dict = {}   # имя экрана → (грубый ключ, когда пересчитан)
MIN_GAP = 1800     # не чаще раза в полчаса, если не появилось новых постов, публикаций или каналов


def coarse_key():
    """Меняется от новых постов, публикаций, расписания и выбора каналов — но не от замеров просмотров
    свежих постов (они идут раз в минуту, и раньше из-за них всё пересчитывалось каждые 5 минут)."""
    import app.publish.runner  # noqa: F401
    from app.publish import schedule  # noqa: F401
    n = db.query("SELECT COUNT(*) AS n FROM posts")[0]["n"]
    c = db.query("SELECT GROUP_CONCAT(chat_id || rating || selected) AS s FROM channels")[0]["s"]
    p = db.query("SELECT COUNT(*) AS n, MAX(id) AS m FROM publications")[0]
    s = db.query("SELECT COUNT(*) AS n, MAX(run_at) AS r, GROUP_CONCAT(state) AS st FROM scheduled WHERE state IN ('pending','running','missed')")[0]
    return (n, c, p["n"], p["m"], s["n"], s["r"], s["st"], datetime.now().strftime("%Y%m%d"))


def _warm(name, compute, key, coarse, max_age: float = 6 * 3600) -> int:
    import time
    seen = _seen.get(name)
    if seen and seen[0] == coarse and time.time() - seen[1] < MIN_GAP:
        return 0
    n = int(cache.warm(name, compute, key, max_age=max_age))
    _seen[name] = (coarse, time.time())
    return n


def _opened() -> tuple:
    """Какой период и канал вы смотрите в «Статистике» — их и считаем заранее (остальное — когда откроете)."""
    try:
        from app import ui_state
        u = ui_state.load()
        return str(u.get("sp") or "90"), (u.get("sc") or None)
    except Exception:  # noqa: BLE001
        return "90", None


def warm_all() -> int:
    """Пересчитать устаревшее. Возвращает, сколько экранов пересчитано."""
    if not _lock.acquire(blocking=False):
        return 0
    n = 0
    try:
        key = stats_key()
        coarse = coarse_key()
        from app.publish import settings as PS
        mods = PS.load(raw=True)["modules"]
        if mods.get("stats", True):
            per, chat = _opened()
            for c in dict.fromkeys([None, chat]):
                n += _warm(stats_name(per, c), stats_compute(per, c), key, coarse)
            n += _warm("insights", insights_compute, insights_key(), coarse)
        if mods.get("history", True) and mods.get("crosspost", True):
            n += _warm("hist_summary", summary_compute, calendar_key(), coarse, max_age=1800)
            now = datetime.now()
            for k in (0, 1):
                y, m = now.year, now.month - k
                if m < 1:
                    y, m = y - 1, 12
                ym = f"{y:04d}-{m:02d}"
                n += _warm(calendar_name(ym), calendar_compute(ym), calendar_key(), coarse, max_age=1800)
    finally:
        _lock.release()
    return n


def warm_async():
    def run():
        try:
            from app.publish import storage
            storage.maintain()   # миниатюры старых публикаций и уборка копий — раз в несколько часов
        except Exception:  # noqa: BLE001
            pass
        from app import perf
        with perf.track("расчёт экранов"):
            _safe(warm_all)
    threading.Thread(target=run, daemon=True, name="warmup").start()


def _safe(fn):
    try:
        fn()
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
