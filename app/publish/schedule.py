"""Отложенная публикация: пост сохраняется с датой и выходит сам, когда придёт время.

Crosspost должен быть запущен в это время (можно свернуть). Если компьютер или приложение были
выключены и время прошло меньше чем на 3 часа — пост выйдет при следующем запуске; если больше —
он помечается «пропущен» и ждёт вашего решения (опубликовать сейчас или перенести).
"""
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from app import db

GRACE = timedelta(hours=3)
DAYS_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS_RU = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

db.conn().executescript("""
CREATE TABLE IF NOT EXISTS scheduled (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at   TEXT NOT NULL,        -- UTC, ISO
    created  TEXT NOT NULL,
    title    TEXT, rating TEXT,
    draft    TEXT NOT NULL,        -- JSON черновика (файлы, текст, теги, исключённые места)
    dests    INTEGER,              -- сколько мест было в плане
    state    TEXT NOT NULL,        -- pending / running / done / partial / failed / missed / cancelled
    pub_id   INTEGER, error TEXT, done_at TEXT
);
""")
for _col in ("queue INTEGER DEFAULT 0",    # 1 — пост из конвейера (время выбирает сама программа)
             "pinned INTEGER DEFAULT 0",   # 1 — время закреплено вручную, конвейер его не двигает
             "qpos REAL",                  # место в очереди конвейера
             "tries INTEGER DEFAULT 0",    # повторы после сбоя
             "why TEXT"):                  # почему выбрано это время
    try:
        db.conn().execute("ALTER TABLE scheduled ADD COLUMN " + _col)
    except Exception:   # noqa: BLE001 — колонка уже есть
        pass


def _now():
    return datetime.now(timezone.utc)


def _parse(iso: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Не понимаю дату и время публикации")
    if dt.tzinfo is None:
        dt = dt.astimezone()   # без пояса — значит, время этого компьютера
    return dt.astimezone(timezone.utc)


def human(dt: datetime) -> str:
    loc = dt.astimezone()
    today = datetime.now().astimezone().date()
    d = loc.date()
    day = "сегодня" if d == today else "завтра" if d == today + timedelta(days=1) else \
        f"{DAYS_RU[loc.weekday()]} {loc.day} {MONTHS_RU[loc.month - 1]}"
    return f"{day}, {loc:%H:%M}"


def add(draft: dict, run_at: str) -> dict:
    from app.publish import runner
    dt = _parse(run_at)
    if dt < _now() + timedelta(minutes=1):
        raise ValueError("Время публикации уже прошло — выберите время в будущем")
    p = runner.plan(draft)
    if p["errors"]:
        raise ValueError(p["errors"][0])
    n = sum(1 for d in p["dests"] if d["will"])
    with db.tx() as c:
        cur = c.execute("""INSERT INTO scheduled (run_at, created, title, rating, draft, dests, state)
                           VALUES (?,?,?,?,?,?, 'pending')""",
                        (dt.isoformat(timespec="seconds"), _now().isoformat(timespec="seconds"),
                         (draft.get("title") or (draft.get("text") or "").split("\n")[0])[:80], draft.get("rating"),
                         json.dumps(draft, ensure_ascii=False), n))
    return {"id": cur.lastrowid, "run_at": dt.isoformat(), "human": human(dt)}


def items(limit_done: int = 10) -> list[dict]:
    rows = db.query("SELECT * FROM scheduled WHERE state IN ('pending','running','missed') ORDER BY run_at")
    rows += db.query("SELECT * FROM scheduled WHERE state IN ('done','partial','failed') ORDER BY done_at DESC LIMIT ?", (limit_done,))
    for r in rows:
        d = json.loads(r.pop("draft") or "{}")
        r["files"] = d.get("files") or []
        r["text"] = (d.get("text") or "")[:140]
        r["human"] = human(_parse(r["run_at"]))
    return rows


def _set(sid: int, **kw):
    keys = ", ".join(f"{k}=?" for k in kw)
    with db.tx() as c:
        c.execute(f"UPDATE scheduled SET {keys} WHERE id=?", (*kw.values(), sid))


def cancel(sid: int):
    _set(int(sid), state="cancelled")


def reschedule(sid: int, run_at: str) -> dict:
    dt = _parse(run_at)
    if dt < _now() + timedelta(minutes=1):
        raise ValueError("Время уже прошло — выберите время в будущем")
    _set(int(sid), run_at=dt.isoformat(timespec="seconds"), state="pending", error=None)
    with db.tx() as c:   # перенесённый вручную пост конвейера дальше не двигаем
        c.execute("UPDATE scheduled SET pinned=1, why='время задано вами' WHERE id=? AND queue=1", (int(sid),))
    return {"human": human(dt)}


def publish_now(sid: int):
    _set(int(sid), run_at=_now().isoformat(timespec="seconds"), state="pending", error=None)


def best_times(n: int = 3) -> list[dict]:
    """Ближайшие даты для лучших окон из статистики (день недели × 3 часа, по времени этого компьютера)."""
    from app import stats
    try:
        o = stats.overview("all")
    except Exception:
        return []
    bt = (o or {}).get("best_times") or []
    now = datetime.now().astimezone()
    out = []
    for b in bt[:n]:
        if "dow" not in b:
            continue
        hour = b["slot"] * 3 + 1   # середина трёхчасового окна
        days = (b["dow"] - now.weekday()) % 7
        cand = (now + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
        if cand < now + timedelta(minutes=10):
            cand += timedelta(days=7)
        out.append({"iso": cand.isoformat(timespec="minutes"), "local": cand.strftime("%Y-%m-%dT%H:%M"),
                    "human": human(cand), "window": b["label"], "index": b["index"], "n": b["n"]})
    return out


# --- фоновая проверка расписания --------------------------------------------------

_started = False


def start_worker(api, every: float = 15.0):
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, args=(api, every), daemon=True, name="schedule").start()


def _loop(api, every):
    from app.insights import post_stats
    while True:
        try:
            tick(api)
        except Exception:
            pass
        # замеры свежих постов — отдельным потоком, чтобы не задерживать публикацию по расписанию
        threading.Thread(target=post_stats.track_tick, args=(api.tg,), daemon=True, name="track").start()
        from app.worker import _e6_check
        threading.Thread(target=_e6_check, daemon=True, name="e6learn").start()
        try:   # запас прокси из открытых списков (если включён) — и из окна, не только из фоновой отправки
            from app.telegram import proxy_pool
            if proxy_pool.due(api.tg):
                threading.Thread(target=lambda: proxy_pool.refresh(api.tg), daemon=True, name="proxypool").start()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(every)


def tick(api):
    """Один проход: опубликовать подошедшие посты (по одному, по порядку)."""
    from app.publish import pipeline, runner
    try:
        pipeline.maintain()   # конвейер: расставить очередь по лучшим окнам, повторить сорвавшееся
    except Exception:  # noqa: BLE001
        pass
    now = _now()
    for r in db.query("SELECT * FROM scheduled WHERE state='pending' ORDER BY run_at"):
        at = _parse(r["run_at"])
        if at > now:
            break
        if now - at > GRACE:
            _set(r["id"], state="missed", error="приложение было закрыто в назначенное время")
            continue
        j = api.jobs.get("publish")
        if j and j["state"] == "running":
            return   # дождёмся конца текущей публикации
        draft = json.loads(r["draft"])
        draft["_scheduled_run"] = True   # если теги e6ai вдруг перестали проходить проверку — e6ai пропускаем, остальное публикуем
        with db.tx() as c:   # забираем пост атомарно — второй процесс его уже не возьмёт
            if c.execute("UPDATE scheduled SET state='running' WHERE id=? AND state='pending'", (r["id"],)).rowcount != 1:
                continue
        try:
            p = runner.plan(draft)
            if p["errors"]:
                raise ValueError(p["errors"][0])
        except Exception as e:  # noqa: BLE001
            _set(r["id"], state="failed", error=str(e), done_at=_now().isoformat(timespec="seconds"))
            continue
        api._tg_ctx()
        api.jobs.start("publish", lambda report, d=draft: runner.start(d, report))
        while True:   # ждём конца публикации, чтобы записать итог
            time.sleep(1)
            j = api.jobs.get("publish")
            if not j or j["state"] != "running":
                break
        st = runner.state() or {}
        dests = st.get("dests") or []
        ok = sum(1 for d in dests if d.get("state") == "done")
        bad = sum(1 for d in dests if d.get("state") == "error")
        state = "failed" if (j and j["state"] == "error") or (dests and ok == 0) else ("partial" if bad else "done")
        _set(r["id"], state=state, pub_id=st.get("pub_id"), done_at=_now().isoformat(timespec="seconds"),
             error=(j or {}).get("error") or (((runner.NETFAIL_TEXT if runner.net_failed(dests) else f"ошибок: {bad}") if bad else None)))
        return   # следующий — на следующем проходе
