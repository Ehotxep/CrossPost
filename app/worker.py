"""Фоновая отправка: отдельный маленький процесс, который публикует запланированные посты.

Основное окно Crosspost можно закрыть — этот процесс работает сам (значок в трее у часов).
Запуск:  pythonw -m app.worker   (так его запускает приложение и автозапуск Windows)
Остановить: меню значка в трее → «Выключить фоновую отправку», или кнопка в «Истории».

Работает в одном экземпляре (файл-замок). Раз в 15 секунд проверяет расписание и пишет «пульс»
в worker.json — по нему окно приложения понимает, что отправка жива.
"""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from app.paths import data_dir  # noqa: E402

EVERY = 15
ALIVE = 60          # пульс свежее минуты — процесс жив
STARTUP_NAME = "Crosspost — фоновая отправка.vbs"


def _state_path() -> Path:
    return data_dir() / "worker.json"


def _stop_path() -> Path:
    return data_dir() / "worker.stop"


def _log(msg: str):
    try:
        p = data_dir() / "worker.log"
        if p.exists() and p.stat().st_size > 512 * 1024:
            p.replace(p.with_suffix(".log.1"))
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
    except Exception:
        pass


# --- состояние для окна приложения ------------------------------------------------

def status() -> dict:
    p = _state_path()
    try:
        st = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        st = {}
    age = time.time() - st.get("beat", 0) if st else None
    st["running"] = bool(st) and age is not None and age < ALIVE
    st["age"] = round(age) if age is not None else None
    st["autostart"] = autostart_path().exists() if sys.platform == "win32" else False
    st["platform"] = sys.platform
    return st


def _pythonw() -> str:
    exe = Path(sys.executable)
    if sys.platform == "win32":
        w = exe.with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return str(exe)


def start_detached() -> bool:
    """Запустить фоновую отправку отдельным процессом (если ещё не запущена)."""
    if status()["running"]:
        return False
    _stop_path().unlink(missing_ok=True)
    kw = {"cwd": str(ROOT), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
          "close_fds": True}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW
    else:
        kw["start_new_session"] = True
    subprocess.Popen([_pythonw(), "-m", "app.worker"], **kw)
    return True


def request_stop():
    _stop_path().write_text("stop", encoding="utf-8")


# --- автозапуск с Windows (файл .vbs в папке «Автозагрузка»: запускает без чёрного окна) ----------

def autostart_path() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / STARTUP_NAME


def set_autostart(on: bool) -> bool:
    if sys.platform != "win32":
        raise RuntimeError("Автозапуск настраивается только в Windows")
    p = autostart_path()
    if not on:
        p.unlink(missing_ok=True)
        return False
    data = os.environ.get("CROSSPOST_DATA", "")
    vbs = ('Set sh = CreateObject("WScript.Shell")\r\n'
           f'sh.CurrentDirectory = "{ROOT}"\r\n'
           + (f'sh.Environment("PROCESS")("CROSSPOST_DATA") = "{data}"\r\n' if data else "")
           + f'sh.Run """{_pythonw()}"" -m app.worker", 0, False\r\n')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(vbs, encoding="utf-16")   # WSH понимает UTF-16 — путь с кириллицей не сломается
    return True


# --- один экземпляр -------------------------------------------------------------------

def _single_instance():
    f = open(data_dir() / "worker.lock", "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return f   # держим открытым, пока процесс жив


# --- сам процесс ------------------------------------------------------------------------


_e6_lock = threading.Lock()


def _e6_check():
    """Сверка тегов e6ai с правками модераторов (сутки и трое после загрузки) и новости по свежим артам.
    Не наслаивается: если прошлая проверка ещё идёт (медленный сайт), новая не запускается."""
    if not _e6_lock.acquire(blocking=False):
        return
    try:
        from app import perf
        from app.publish import settings as PS
        if not PS.module_on("collect"):   # сбор цифр выключен — сайты не опрашиваем
            return
        try:
            with perf.track("сверка правок модераторов"):
                from app.insights import e6learn
                e6learn.check_due()
        except Exception:
            pass
        if PS.module_on("news"):
            try:
                with perf.track("новости"):
                    from app.insights import news
                    news.collect()
            except Exception:
                pass
    finally:
        _e6_lock.release()

class Worker:
    def __init__(self):
        from app.api import Api
        self.api = Api()
        self.stop = threading.Event()
        self.icon = None
        self.next_label = ""
        self.last = ""

    def beat(self):
        from app.publish import schedule
        nxt = [r for r in schedule.items(0) if r["state"] == "pending"]
        self.next_label = f"Следующий пост: {nxt[0]['human']}" if nxt else "Запланированных постов нет"
        _state_path().write_text(json.dumps({
            "pid": os.getpid(), "beat": time.time(), "started": self.started, "next": self.next_label,
            "pending": len(nxt), "last": self.last, "track": self._track_note()}, ensure_ascii=False), encoding="utf-8")
        if self.icon is not None:
            try:
                self.icon.title = f"Crosspost — фоновая отправка\n{self.next_label}"
            except Exception:
                pass

    @staticmethod
    def _track_note() -> str:
        from app.insights import post_stats
        return post_stats._last_track["note"]

    def notify(self, text: str):
        self.last = text
        _log(text)
        if self.icon is not None:
            try:
                self.icon.notify(text, "Crosspost")
            except Exception:
                pass

    def loop(self):
        from app import db, perf
        from app.publish import schedule
        perf.ROLE = "worker"
        perf.start_sampler()
        while not self.stop.is_set():
            if _stop_path().exists():
                _stop_path().unlink(missing_ok=True)
                break
            try:
                before = {r["id"]: r["state"] for r in db.query("SELECT id, state FROM scheduled")}
                with perf.track("расписание"):
                    schedule.tick(self.api)
                for r in db.query("SELECT id, state, title, error FROM scheduled"):
                    if before.get(r["id"]) != r["state"] and r["state"] in ("done", "partial", "failed", "missed"):
                        title = r["title"] or "пост"
                        self.notify({"done": f"Опубликовано: {title}",
                                     "partial": f"Опубликовано с ошибками: {title} ({r['error']})",
                                     "failed": f"Не опубликовано: {title} — {r['error']}",
                                     "missed": f"Пропущен: {title} — откройте Crosspost, чтобы опубликовать"}[r["state"]])
                self.beat()
            except Exception as e:  # noqa: BLE001
                _log(f"ошибка: {e}")
            if time.time() - getattr(self, "_warm_t", 0) > 300:   # раз в 5 минут — досчитать экраны заранее
                self._warm_t = time.time()
                try:
                    from app.insights import warmup
                    warmup.warm_async()
                except Exception as e:  # noqa: BLE001
                    _log(f"расчёт экранов: {e}")
            try:   # замеры просмотров свежих постов (в своём потоке — не мешают расписанию)
                from app.insights import post_stats
                mods = self.api.modules()
                if mods.get("collect", True) and any(mods.get(k, True) for k in ("history", "stats")):   # замеры — только если сбор включён
                    def _track():
                        with perf.track("замеры просмотров"):
                            post_stats.track_tick(self.api.tg)
                    threading.Thread(target=_track, daemon=True, name="track").start()
                threading.Thread(target=_e6_check, daemon=True, name="e6learn").start()
                if mods.get("collect", True) and mods.get("vk", True) and not getattr(self, "_vk_busy", False):
                    from app.publish import vk_stats
                    if vk_stats.due():   # цифры постов ВК — раз в 3 часа, браузер без окна
                        self._vk_busy = True

                        def _vk():
                            try:
                                with perf.track("статистика ВК"):
                                    vk_stats.run_and_record(headless=True, wait=5)   # браузер занят публикацией — в следующий раз
                            except Exception as e:  # noqa: BLE001
                                _log(f"статистика ВК: {e}")
                            finally:
                                self._vk_busy = False
                        threading.Thread(target=_vk, daemon=True, name="vkstats").start()
            except Exception as e:  # noqa: BLE001
                _log(f"замеры: {e}")
            self.stop.wait(EVERY)
        _state_path().unlink(missing_ok=True)
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass

    def run(self):
        self.started = time.time()
        _log("фоновая отправка запущена")
        self.beat()
        t = threading.Thread(target=self.loop, daemon=True, name="worker")
        t.start()
        try:
            self._tray()   # значок в трее (если есть pystray), блокирует до выхода
        except Exception as e:  # noqa: BLE001
            _log(f"без значка в трее: {e}")
            t.join()
        self.stop.set()
        t.join(timeout=30)
        _log("фоновая отправка остановлена")

    def _tray(self):
        import pystray
        from PIL import Image, ImageDraw
        try:
            from app import icon
            icon.set_app_id()
            img = icon.image(64)   # тот же значок, что у окна приложения
        except Exception:  # noqa: BLE001 — запасной простой значок
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle((4, 4, 60, 60), 14, fill=(52, 70, 196))
            d.polygon([(20, 18), (48, 32), (20, 46), (26, 32)], fill="white")   # «самолётик» отправки

        def open_app(icon, item):
            kw = {"cwd": str(ROOT)}
            if sys.platform == "win32":
                kw["creationflags"] = 0x00000008 | 0x08000000
            subprocess.Popen([_pythonw(), "-m", "app"], **kw)

        def quit_(icon, item):
            self.stop.set()
            icon.stop()

        menu = pystray.Menu(
            pystray.MenuItem(lambda item: self.next_label or "Crosspost", None, enabled=False),
            pystray.MenuItem("Открыть Crosspost", open_app, default=True),
            pystray.MenuItem("Выключить фоновую отправку", quit_),
        )
        self.icon = pystray.Icon("crosspost", img, "Crosspost — фоновая отправка", menu)
        self.icon.run()


def main():
    os.environ["CROSSPOST_BOT_SESSION"] = "telegram_bot_worker"   # своя сессия бота: окно приложения может работать параллельно
    if os.environ.get("CROSSPOST_FAKE_NET"):   # только для проверок без сети
        from tests import fake_net
        fake_net.install()
    lock = _single_instance()
    if lock is None:
        return   # уже запущен
    # Раньше здесь делалась копия входа в Telegram — это тот же ключ, и при одновременной работе с окном Telegram
    # его аннулировал. Теперь вход один, а окно и фоновая отправка подключаются по очереди (telegram/backend.SessionLock).
    try:
        from app.paths import data_dir
        for p in data_dir().glob("telegram_worker.session*"):
            p.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        _log(f"старая копия входа в Telegram: {e}")
    Worker().run()


if __name__ == "__main__":
    main()
