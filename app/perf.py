"""Производительность: сколько памяти и процессора берёт каждая часть Crosspost и чем заняты фоновые задачи.

- snapshot(): процессы прямо сейчас — окно, фоновая отправка, теггер, подпроцессы задач, браузер для ВК.
- track(name): журнал фоновых задач (сколько длилась, сколько памяти добавила) — файл perf.jsonl в папке данных,
  в него пишут и окно, и фоновая отправка.
- sample(): раз в минуту — память и процессор своего процесса (по ним видно, растёт ли память со временем).
- report(): файл для отправки разработчику — без ключей, токенов и названий постов.

psutil нужен для цифр по процессам; без него отчёт всё равно собирается, только беднее.
"""
import json
import os
import platform
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from app.paths import data_dir

LOG_MAX = 4000      # строк в журнале — потом старые отрезаются
SLOW_S = 5.0        # задача дольше — помечается как медленная
_wlock = threading.Lock()
ROLE = "window"     # фоновая отправка выставляет "worker"


def _ps():
    try:
        import psutil
        return psutil
    except ImportError:
        return None


def _log_path():
    return data_dir() / "perf.jsonl"


def _rss_mb(pid: int | None = None) -> float | None:
    ps = _ps()
    if not ps:
        return None
    try:
        return round(ps.Process(pid or os.getpid()).memory_info().rss / 2 ** 20, 1)
    except Exception:  # noqa: BLE001
        return None


def _write(rec: dict):
    rec = dict(rec, at=round(time.time(), 1), role=ROLE, pid=os.getpid())
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with _wlock:
        p = _log_path()
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
            if p.stat().st_size > LOG_MAX * 220:   # обрезаем до последних LOG_MAX/2 строк
                lines = p.read_text(encoding="utf-8").splitlines(True)[-LOG_MAX // 2:]
                p.write_text("".join(lines), encoding="utf-8")
        except OSError:
            pass


@contextmanager
def track(name: str):
    """with perf.track("замеры"): … — в журнал: длительность и прирост памяти."""
    t0, m0 = time.perf_counter(), _rss_mb()
    err = None
    try:
        yield
    except BaseException as e:
        err = type(e).__name__
        try:   # коротко и без секретов — чтобы по отчёту было понятно, что сломалось
            from app.jobs import mask_secrets
            msg = mask_secrets(" ".join(str(e).split()))[:140]
            if msg:
                err += ": " + msg
        except Exception:
            pass
        raise
    finally:
        dt = time.perf_counter() - t0
        m1 = _rss_mb()
        if dt >= 0.5 or err:   # мгновенные «нечего делать» не засоряют журнал
            _write({"kind": "task", "name": name, "s": round(dt, 2), "mb": m1,
                    "dmb": round(m1 - m0, 1) if m0 is not None and m1 is not None else None, "err": err})


_cpu_prev: dict = {}


def sample():
    """Память и процессор своего процесса — в журнал (раз в минуту из фона)."""
    ps = _ps()
    if not ps:
        return
    try:
        p = ps.Process()
        cpu = p.cpu_percent(None)   # с прошлого вызова
        _write({"kind": "sample", "mb": round(p.memory_info().rss / 2 ** 20, 1), "cpu": round(cpu, 1),
                "threads": p.num_threads()})
    except Exception:  # noqa: BLE001
        pass


def start_sampler(every: float = 60.0):
    def loop():
        sample()
        while True:
            time.sleep(every)
            sample()
    threading.Thread(target=loop, daemon=True, name="perf-sample").start()


# --- процессы прямо сейчас ---------------------------------------------------------------

def _kind(proc, me: int, worker_pid: int | None, tagger_pid: int | None) -> str:
    try:
        name = (proc.name() or "").lower()
        cmd = " ".join(proc.cmdline() or [])
    except Exception:  # noqa: BLE001
        name, cmd = "", ""
    if proc.pid == me:
        return "Окно Crosspost" if ROLE == "window" else "Фоновая отправка"
    if worker_pid and proc.pid == worker_pid:
        return "Фоновая отправка"
    if tagger_pid and proc.pid == tagger_pid or "app.tagger.serve" in cmd:
        return "Теггер"
    if "msedge" in name or "chrome" in name or "chromium" in name:
        return "Браузер (ВК, DeviantArt)"
    if "app.subproc" in cmd or "app.trends" in cmd:
        return "Фоновая задача"
    if "node" in name or "playwright" in cmd:
        return "Playwright"
    return name or "процесс"


def _ours(ps):
    """Наши процессы: этот, фоновая отправка, их потомки (теггер, задачи, браузер)."""
    me = os.getpid()
    roots = [me]
    worker_pid = None
    try:
        from app import worker
        st = worker.status()
        worker_pid = st.get("pid") if st.get("running") else None
    except Exception:  # noqa: BLE001
        pass
    if worker_pid and worker_pid != me:
        roots.append(worker_pid)
    seen, out = set(), []
    for pid in roots:
        try:
            p = ps.Process(pid)
        except Exception:  # noqa: BLE001
            continue
        for q in [p] + p.children(recursive=True):
            if q.pid not in seen:
                seen.add(q.pid)
                out.append(q)
    # «потерянные» браузеры: процессы Edge/Chrome с профилем Crosspost, чей родитель уже закрылся
    prof = str(data_dir() / "browser").lower()
    for q in ps.process_iter(["pid", "name"]):
        if q.pid in seen:
            continue
        n = (q.info.get("name") or "").lower()
        if not any(x in n for x in ("msedge", "chrome", "chromium")):
            continue
        try:
            if prof in " ".join(q.cmdline()).lower():
                seen.add(q.pid)
                out.append(q)
        except Exception:  # noqa: BLE001
            pass
    return out, worker_pid


def snapshot() -> dict:
    ps = _ps()
    if not ps:
        return {"psutil": False, "groups": [], "note": "Нет библиотеки psutil — запустите Install.bat ещё раз, чтобы видеть цифры по процессам"}
    try:
        from app.tagger import remote
        tagger_pid = remote._proc.pid if remote.alive() else None
    except Exception:  # noqa: BLE001
        tagger_pid = None
    procs, worker_pid = _ours(ps)
    for q in procs:   # процессор считается за интервал — первый замер «обнуляем»
        try:
            q.cpu_percent(None)
        except Exception:  # noqa: BLE001
            pass
    time.sleep(0.5)
    groups: dict = {}
    for q in procs:
        try:
            with q.oneshot():
                mb = q.memory_info().rss / 2 ** 20
                cpu = q.cpu_percent(None)
        except Exception:  # noqa: BLE001
            continue
        k = _kind(q, os.getpid(), worker_pid, tagger_pid)
        g = groups.setdefault(k, {"name": k, "n": 0, "mb": 0.0, "cpu": 0.0})
        g["n"] += 1
        g["mb"] += mb
        g["cpu"] += cpu
    ncpu = ps.cpu_count() or 1
    out = sorted(({"name": g["name"], "n": g["n"], "mb": round(g["mb"]), "cpu": round(g["cpu"] / ncpu, 1)} for g in groups.values()),
                 key=lambda g: -g["mb"])
    vm = ps.virtual_memory()
    return {"psutil": True, "groups": out, "total_mb": sum(g["mb"] for g in out),
            "system": {"ram_gb": round(vm.total / 2 ** 30, 1), "ram_used": vm.percent, "cpu": ps.cpu_percent(None)}}


# --- журнал и отчёт ------------------------------------------------------------------------

def journal(hours: float = 24) -> list[dict]:
    edge = time.time() - hours * 3600
    try:
        lines = _log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("at", 0) >= edge:
            out.append(r)
    return out


def summary(hours: float = 24) -> dict:
    j = journal(hours)
    tasks: dict = {}
    for r in j:
        if r.get("kind") != "task":
            continue
        t = tasks.setdefault((r["role"], r["name"]), {"role": r["role"], "name": r["name"], "n": 0, "s": 0.0, "max_s": 0.0, "errors": 0})
        t["n"] += 1
        t["s"] += r["s"]
        t["max_s"] = max(t["max_s"], r["s"])
        t["errors"] += 1 if r.get("err") else 0
    mem: dict = {}
    for r in j:
        if r.get("kind") == "sample":
            m = mem.setdefault(r["role"], {"role": r["role"], "first": r["mb"], "last": r["mb"], "max": r["mb"], "cpu": [], "at0": r["at"]})
            m["last"] = r["mb"]
            m["max"] = max(m["max"], r["mb"])
            m["cpu"].append(r.get("cpu") or 0)
    for m in mem.values():
        c = m.pop("cpu")
        m["cpu_avg"] = round(sum(c) / len(c), 1) if c else 0
        m["hours"] = round((time.time() - m.pop("at0")) / 3600, 1)
    return {"tasks": sorted(({**t, "s": round(t["s"], 1), "max_s": round(t["max_s"], 1)} for t in tasks.values()), key=lambda t: -t["s"])[:15],
            "memory": list(mem.values())}


def gpu_info() -> dict:
    """Видеокарта и драйвер — через nvidia-smi (есть вместе с драйвером NVIDIA)."""
    from app.tagger import gpu
    return gpu.detect()


def report() -> str:
    """Сохранить отчёт (JSON) в папку reports. Ключей, токенов, названий и текстов постов в нём нет."""
    from app import db
    from app.publish import settings as PS
    info = {"made": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "app": __import__("app").__version__, "python": sys.version.split()[0],
            "os": f"{platform.system()} {platform.release()} {platform.version()}", "machine": platform.machine(),
            "cpu": platform.processor()}
    try:
        info["gpu"] = gpu_info()
    except Exception as e:  # noqa: BLE001
        info["gpu"] = {"error": str(e)[:200]}
    try:
        from app.tagger import jtp
        st = jtp.status()
        info["tagger"] = {k: st.get(k) for k in ("ready", "loaded", "device")}
        info["tagger"]["torch"] = jtp.torch_info()
    except Exception as e:  # noqa: BLE001
        info["tagger"] = {"error": str(e)[:200]}
    try:
        info["modules"] = PS.load(raw=True)["modules"]
    except Exception:  # noqa: BLE001
        pass
    counts = {}
    for t in ("posts", "publications", "channels", "scheduled", "vk_snap"):
        try:
            counts[t] = db.query(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
        except Exception:  # noqa: BLE001
            pass
    info["counts"] = counts
    try:
        info["data_mb"] = {p.name: round(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 2 ** 20, 1)
                           for p in data_dir().iterdir() if p.is_dir()}
    except Exception:  # noqa: BLE001
        pass
    info["now"] = snapshot()
    info["summary"] = summary(24)
    info["journal"] = journal(6)
    out = data_dir() / "reports"
    out.mkdir(exist_ok=True)
    f = out / f"perf-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    f.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(f)


def kill_stray_browsers() -> int:
    """Закрыть процессы браузера с профилем Crosspost, которые остались после сбора статистики или публикации."""
    ps = _ps()
    if not ps:
        return 0
    prof = str(data_dir() / "browser").lower()
    n = 0
    for q in ps.process_iter(["pid", "name"]):
        nm = (q.info.get("name") or "").lower()
        if not any(x in nm for x in ("msedge", "chrome", "chromium")):
            continue
        try:
            if prof in " ".join(q.cmdline()).lower():
                q.kill()
                n += 1
        except Exception:  # noqa: BLE001
            pass
    return n
