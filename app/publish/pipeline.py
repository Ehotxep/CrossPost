"""Конвейер: вы складываете готовые посты в очередь, Crosspost сам выбирает для каждого время и публикует.

Как выбирается время
  • сетка «день недели × 3 часа» (56 окон) из вашей статистики: у каждого окна — во сколько раз посты, вышедшие
    в нём, обгоняют обычный пост (с поправкой на малое число постов), и сколько постов в нём было вообще;
  • окну дают очки: «ожидаемый успех» + бонус за неизученность (чем меньше постов в окне, тем больше бонус, его
    размер — настройка «Пробовать новое»). Так конвейер в основном постит в проверенные лучшие окна, но иногда
    пробует окна, о которых пока почти ничего не известно, — иначе статистика никогда бы не пополнилась;
  • правила: не больше N постов в сутки, не чаще раза в M часов (учитываются и уже вышедшие, и запланированные
    вручную посты); ближе чем через 25 минут от «сейчас» не ставим. Минуты внутри окна «размазываются»,
    чтобы посты не выходили ровно в :00.
  • очередь идёт по порядку добавления: первому достаётся лучшее свободное окно.

Сам конвейер ничего не публикует — он только расставляет время в таблице запланированных постов (scheduled,
queue=1); публикуют их те же расписание и фоновая отправка. Время вручную можно закрепить — его не тронут.
Сорвавшийся пост (ни одно место не вышло) повторяется через 20 минут, до двух раз; пропущенный из-за закрытой
программы — снова встаёт в очередь и получает новое время.
"""
import hashlib
import json
import math
import threading
import time
from datetime import datetime, timedelta, timezone

from app import db

FAR = "9999-12-31T00:00:00+00:00"   # «времени ещё нет»
DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
SOON = timedelta(minutes=25)
DELAY_COST = 0.004                  # лёгкий штраф за каждый день ожидания: без него очередь уезжала бы в дальние лучшие окна
_lock = threading.Lock()
_last = {"t": 0.0, "sig": None}
_grid_cache: dict = {"t": 0.0, "grid": None}


def _schedule():
    from app.publish import schedule
    return schedule


def cfg() -> dict:
    from app.publish import settings as PS
    c = dict(PS.DEFAULTS["pipeline"])
    c.update(PS.load(raw=True).get("pipeline") or {})
    return c


def set_cfg(changes: dict) -> dict:
    from app.publish import settings as PS
    out = {}
    for k, lo, hi, cast in (("per_day", 1, 5, int), ("min_gap_h", 1, 48, int), ("explore", 0.0, 0.6, float), ("horizon_days", 7, 60, int)):
        if k in changes:
            try:
                v = cast(changes[k])
            except (TypeError, ValueError):
                raise ValueError("Число в настройках конвейера не распознано")
            out[k] = max(lo, min(hi, v))
    if "enabled" in changes:
        out["enabled"] = bool(changes["enabled"])
    PS.update("pipeline", out)
    maintain(force=True)
    return cfg()


# --- сетка окон из статистики ---------------------------------------------------------------

def grid() -> dict:
    """{(день недели, окно): (множитель успеха, число постов)} — из «Статистики» (на 10 минут запоминается)."""
    if _grid_cache["grid"] is not None and time.time() - _grid_cache["t"] < 600:
        return _grid_cache["grid"]
    out = {}
    try:
        from app import stats
        o = stats.overview("all") or {}
        cov = (o.get("coverage") or {}).get("grid") or []
        for d, row in enumerate(o.get("heat") or []):
            for s_, cell in enumerate(row.get("cells") or []):
                v = cell.get("v")
                n = (cov[d][s_] if d < len(cov) and s_ < len(cov[d]) else cell.get("n")) or 0
                out[(d, s_)] = (math.exp(v) if v is not None else None, int(n))
    except Exception:   # noqa: BLE001 — статистики ещё нет: все окна считаем неизученными
        out = {}
    _grid_cache.update(t=time.time(), grid=out)
    return out


def _jitter(day, s) -> int:
    return 20 + int(hashlib.md5(f"{day}-{s}".encode()).hexdigest()[:4], 16) % 130   # минуты от начала окна: 20…149


def _score(cell, explore: float) -> float:
    mult, n = cell
    return (mult if mult is not None else 1.0) + explore / math.sqrt(n + 1)


def _posts(n: int) -> str:
    return f"{n} " + ("пост" if n % 10 == 1 and n % 100 != 11 else "поста" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "постов")


def _why(d, s, cell) -> str:
    mult, n = cell
    win = f"{DAYS[d]} {s * 3:02d}–{(s * 3 + 3) % 24:02d}"
    if n < 3 or mult is None:
        return f"{win}: окно почти не изучено ({_posts(n)}) — пробуем"
    return f"{win}: лучшее из свободных, обычно ×{str(round(mult, 2)).replace('.', ',')} ({_posts(n)})"


# --- очередь --------------------------------------------------------------------------------

def _rows():
    return db.query("""SELECT * FROM scheduled WHERE queue=1 AND state IN ('queued','pending') ORDER BY COALESCE(qpos, id), id""")


def add(draft: dict) -> dict:
    from app.publish import runner
    p = runner.plan(draft)
    if p["errors"]:
        raise ValueError(p["errors"][0])
    n = sum(1 for d in p["dests"] if d["will"])
    if not n:
        raise ValueError("Выберите, куда публиковать: сейчас ни одно место не включено")
    sch = _schedule()
    with db.tx() as c:
        pos = (c.execute("SELECT MAX(COALESCE(qpos, id)) FROM scheduled WHERE queue=1").fetchone()[0] or 0) + 1
        cur = c.execute("""INSERT INTO scheduled (run_at, created, title, rating, draft, dests, state, queue, qpos)
                           VALUES (?,?,?,?,?,?, 'queued', 1, ?)""",
                        (FAR, sch._now().isoformat(timespec="seconds"),
                         (draft.get("title") or (draft.get("text") or "").split("\n")[0])[:80], draft.get("rating"),
                         json.dumps(draft, ensure_ascii=False), n, pos))
    maintain(force=True)
    return {"id": cur.lastrowid}


def remove(sid: int):
    sch = _schedule()
    r = db.query("SELECT state FROM scheduled WHERE id=? AND queue=1", (int(sid),))
    if not r or r[0]["state"] == "running":
        raise ValueError("Этот пост сейчас нельзя убрать")
    sch._set(int(sid), state="cancelled")
    maintain(force=True)


def move(sid: int, direction: int):
    rows = [r["id"] for r in _rows()]
    if int(sid) not in rows:
        return
    i = rows.index(int(sid))
    j = max(0, min(len(rows) - 1, i + (1 if direction > 0 else -1)))
    if i == j:
        return
    rows[i], rows[j] = rows[j], rows[i]
    with db.tx() as c:
        for pos, rid in enumerate(rows, start=1):
            c.execute("UPDATE scheduled SET qpos=? WHERE id=?", (pos, rid))
    maintain(force=True)


def pin(sid: int, run_at: str | None):
    """Закрепить время (run_at) или снять закрепление (None — пусть конвейер выберет сам)."""
    sch = _schedule()
    if run_at:
        dt = sch._parse(run_at)
        if dt < sch._now() + timedelta(minutes=1):
            raise ValueError("Время уже прошло — выберите время в будущем")
        sch._set(int(sid), run_at=dt.isoformat(timespec="seconds"), state="pending", pinned=1, error=None, why="время задано вами")
    else:
        sch._set(int(sid), run_at=FAR, state="queued", pinned=0, why=None)
    maintain(force=True)


def state() -> dict:
    sch = _schedule()
    items = []
    for r in db.query("""SELECT * FROM scheduled WHERE queue=1 AND state IN ('queued','pending','running','missed','failed')
                         ORDER BY CASE state WHEN 'running' THEN 0 WHEN 'pending' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END, run_at, COALESCE(qpos, id)"""):
        d = json.loads(r.pop("draft") or "{}")
        r["files"] = d.get("files") or []
        r["text"] = (d.get("text") or "")[:140]
        r["human"] = sch.human(sch._parse(r["run_at"])) if r["state"] in ("pending", "running", "missed") else ""
        items.append(r)
    return {"cfg": cfg(), "items": items, "windows": _windows_summary()}


def _windows_summary() -> dict:
    g = grid()
    solid = sum(1 for v in g.values() if v[1] >= 3)
    return {"cells": 56, "solid": solid}


# --- расстановка ----------------------------------------------------------------------------

def _occupied(now: datetime, skip_ids: set) -> list:
    """Времена, которые уже заняты: вышедшие недавно, запланированные вручную и закреплённые."""
    sch = _schedule()
    out = []
    for r in db.query("SELECT run_at, id FROM scheduled WHERE state IN ('pending','running') "):
        if r["id"] in skip_ids:
            continue
        try:
            out.append(sch._parse(r["run_at"]))
        except ValueError:
            pass
    try:
        import app.publish.runner  # noqa: F401
        for r in db.query("SELECT created FROM publications WHERE created >= ?", ((now - timedelta(days=3)).isoformat(),)):
            try:
                out.append(datetime.fromisoformat(r["created"].replace("Z", "+00:00")).astimezone(timezone.utc))
            except ValueError:
                pass
    except Exception:   # noqa: BLE001
        pass
    return out


def plan_slots(n_items: int, occ: list, c: dict, g: dict, now: datetime | None = None) -> list:
    """Для n_items подряд идущих постов выбрать (время, окно, почему). Чистая функция — удобно проверять."""
    now = now or datetime.now(timezone.utc)
    tz = datetime.now().astimezone().tzinfo
    now_l = now.astimezone(tz)
    occ = list(occ)
    g0 = g
    g = dict(g)   # внутри расстановки «занятое» нами окно считается уже изученным на один пост больше
    gap = timedelta(hours=c["min_gap_h"])
    per_day = {}
    for t in occ:
        k = t.astimezone(tz).date()
        per_day[k] = per_day.get(k, 0) + 1
    cands = []
    for d_ahead in range(int(c["horizon_days"]) + 1):
        day = (now_l + timedelta(days=d_ahead)).date()
        for s in range(8):
            t = (datetime(day.year, day.month, day.day, tzinfo=tz) + timedelta(hours=3 * s, minutes=_jitter(day, s))).astimezone(timezone.utc)
            if t >= now + SOON:
                cands.append((t, day, day.weekday(), s, d_ahead))
    out = []
    for _ in range(n_items):
        best = None
        for t, day, d, s, d_ahead in cands:
            if per_day.get(day, 0) >= c["per_day"] or any(abs(t - o) < gap for o in occ):
                continue
            cell = g.get((d, s), (None, 0))
            val = _score(cell, c["explore"]) - DELAY_COST * d_ahead
            if best is None or val > best[0] + 1e-9:
                best = (val, t, day, d, s, cell)
        if best is None:
            out.append(None)   # в горизонт не помещается
            continue
        _, t, day, d, s, cell = best
        g[(d, s)] = (cell[0], cell[1] + 1)
        occ.append(t)
        per_day[day] = per_day.get(day, 0) + 1
        out.append((t, _why(d, s, g0.get((d, s), (None, 0)))))
    return out


def maintain(force: bool = False):
    """Раз в минуту (или сразу, если force): повторы, пропущенные, расстановка свободных постов."""
    if not _lock.acquire(blocking=False):
        return
    try:
        if not force and time.time() - _last["t"] < 45:
            return
        _last["t"] = time.time()
        sch = _schedule()
        now = sch._now()
        c = cfg()
        with db.tx() as cn:
            # пропущенные (программа была закрыта) — обратно в очередь, получат новое время
            cn.execute("UPDATE scheduled SET state='queued', run_at=?, error=NULL, why=NULL, pinned=0 WHERE queue=1 AND state='missed'", (FAR,))
            # сорвавшиеся целиком — повтор через 20 минут, до двух раз
            for r in cn.execute("SELECT id, tries, done_at FROM scheduled WHERE queue=1 AND state='failed' AND pub_id IS NULL AND tries<2").fetchall():
                try:
                    recent = now - sch._parse(r["done_at"]) < timedelta(hours=6) if r["done_at"] else False
                except ValueError:
                    recent = False
                if recent:
                    cn.execute("UPDATE scheduled SET state='pending', run_at=?, pinned=1, tries=tries+1, why=? WHERE id=?",
                               ((now + timedelta(minutes=20)).isoformat(timespec="seconds"), f"повтор после сбоя ({r['tries'] + 1}-й)", r["id"]))
        rows = _rows()
        free = [r for r in rows if not r["pinned"] and (r["state"] == "queued" or sch._parse(r["run_at"]) > now + SOON)]
        if not c["enabled"]:   # пауза: время снимаем, посты ждут в очереди
            with db.tx() as cn:
                for r in free:
                    if r["state"] == "pending":
                        cn.execute("UPDATE scheduled SET state='queued', run_at=?, why=NULL WHERE id=?", (FAR, r["id"]))
            return
        if not free:
            return
        occ = _occupied(now, {r["id"] for r in free})
        res = plan_slots(len(free), occ, c, grid(), now)
        with db.tx() as cn:
            for r, x in zip(free, res):
                if x is None:
                    if r["state"] == "pending":
                        cn.execute("UPDATE scheduled SET state='queued', run_at=?, why=? WHERE id=?", (FAR, "нет свободного окна в ближайшие дни", r["id"]))
                    else:
                        cn.execute("UPDATE scheduled SET why=? WHERE id=?", ("нет свободного окна в ближайшие дни", r["id"]))
                    continue
                t, why = x
                iso = t.isoformat(timespec="seconds")
                if r["state"] != "pending" or r["run_at"] != iso or r["why"] != why:
                    cn.execute("UPDATE scheduled SET state='pending', run_at=?, why=? WHERE id=?", (iso, why, r["id"]))
    finally:
        _lock.release()
