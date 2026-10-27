"""Фоновые задачи с прогрессом (скачивание и обработка выгрузок и т.п.)."""
import re
import threading
import time
import traceback

_TOKEN = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")


_OTHER = re.compile(r"(?i)((?:access_token|refresh_token|api_key|password|client_secret|authorization)[\"']?\s*[:=]\s*[\"']?(?:bearer\s+)?)[A-Za-z0-9._~+/=-]{12,}")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}")
_HOOK = re.compile(r"(discord(?:app)?\.com/api/webhooks/\d+/)[\w-]{20,}")


def mask_secrets(text: str) -> str:
    """Токены ботов, ключи, пароли и адреса вебхуков не должны попадать на экран, в скриншоты и в журнал."""
    t = _TOKEN.sub("<токен скрыт>", text or "")
    t = _JWT.sub("<токен скрыт>", t)
    t = _HOOK.sub(r"\1<скрыто>", t)
    return _OTHER.sub(r"\1<скрыто>", t)


_DNS = re.compile(r"Failed to resolve '([^']+)'|Name or service not known|getaddrinfo failed|NameResolutionError")


def friendly(text: str) -> str:
    """Сырую ошибку сети — в понятный текст. Сейчас: не нашёлся адрес сайта (DNS)."""
    text = text or ""
    if _DNS.search(text):
        host = (re.search(r"Failed to resolve '([^']+)'", text) or re.search(r"host='([^']+)'", text) or [None, "сайта"])[1]
        return (f"Не удалось найти адрес {host}: DNS на этом компьютере его не знает. "
                "Приложение уже пробовало спросить у независимых DNS-серверов (через HTTPS) — не вышло. "
                "Обычно помогает сменить DNS на 1.1.1.1 / 8.8.8.8 в настройках сети Windows, отключить фильтрующий DNS "
                "(семейный DNS, Pi-hole, блокировщик рекламы) или включить VPN.")
    return text


class Cancelled(BaseException):
    """Пользователь нажал «Отмена». BaseException — чтобы общие `except Exception` в задачах не проглотили отмену."""


class Jobs:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}

    def start(self, key: str, fn, *args) -> bool:
        """Запускает fn(report, *args) в фоне. False, если задача с таким ключом уже идёт."""
        with self._lock:
            j = self._jobs.get(key)
            if j and j["state"] == "running":
                return False
            job = {"state": "running", "progress": 0.0, "message": "Запуск…", "error": None,
                   "started": time.time(), "finished": None, "cancel": False}
            self._jobs[key] = job

        def report(progress=None, message=None):
            with self._lock:
                if job["cancel"] and not (progress is not None and progress >= 1.0):
                    raise Cancelled()
                if progress is not None:
                    job["progress"] = max(0.0, min(1.0, float(progress)))
                if message is not None:
                    job["message"] = mask_secrets(message)

        def run():
            from app import perf
            try:
                with perf.track(key):
                    fn(report, *args)
                with self._lock:
                    job.update(state="done", progress=1.0, finished=time.time())
            except Cancelled:
                with self._lock:
                    job.update(state="cancelled", message="Отменено", finished=time.time())
            except Exception as e:  # показываем пользователю понятную ошибку, подробности — в лог
                traceback.print_exc()
                with self._lock:
                    job.update(state="error", error=mask_secrets(friendly(str(e)) or type(e).__name__), finished=time.time())

        threading.Thread(target=run, daemon=True, name=f"job:{key}").start()
        return True

    def cancel(self, key: str) -> bool:
        """Просит задачу остановиться: она прервётся при ближайшем отчёте о прогрессе."""
        with self._lock:
            j = self._jobs.get(key)
            if not j or j["state"] != "running":
                return False
            j["cancel"] = True
            j["message"] = "Отменяю…"
            return True

    def all(self) -> dict:
        with self._lock:
            return {k: dict(v) for k, v in self._jobs.items()}

    def get(self, key: str) -> dict | None:
        with self._lock:
            j = self._jobs.get(key)
            return dict(j) if j else None
