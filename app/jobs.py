"""Фоновые задачи с прогрессом (скачивание и обработка выгрузок и т.п.)."""
import re
import threading
import time
import traceback

_TOKEN = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")


def mask_secrets(text: str) -> str:
    """Токены ботов не должны попадать на экран и в скриншоты."""
    return _TOKEN.sub("<токен скрыт>", text or "")


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
                   "started": time.time(), "finished": None}
            self._jobs[key] = job

        def report(progress=None, message=None):
            with self._lock:
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
            except Exception as e:  # показываем пользователю понятную ошибку, подробности — в лог
                traceback.print_exc()
                with self._lock:
                    job.update(state="error", error=mask_secrets(friendly(str(e)) or type(e).__name__), finished=time.time())

        threading.Thread(target=run, daemon=True, name=f"job:{key}").start()
        return True

    def all(self) -> dict:
        with self._lock:
            return {k: dict(v) for k, v in self._jobs.items()}

    def get(self, key: str) -> dict | None:
        with self._lock:
            j = self._jobs.get(key)
            return dict(j) if j else None
