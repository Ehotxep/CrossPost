"""Повторы сетевых запросов, которым это безопасно: загрузка файла на сервер (ничего не публикуется), чтение.

Правила:
 - Файл заливается, пока не получится: слабая связь, прокси, блокировки — пользователь сам сеть не ускорит. Пауза растёт
   1, 2, 4 … до минуты. Остановить может только «Отмена» (check_cancel зовётся каждую секунду ожидания).
 - Запрос, который СОЗДАЁТ запись (пост, сообщение), после обрыва не повторяется: неизвестно, дошёл ли он, а второй
   запрос дал бы дубль. Исключение — отказ «слишком часто» (429) и обрыв до соединения: тогда запрос точно не принят.
"""
import socket
import ssl
import threading
import time

import requests
from urllib3 import exceptions as U3

from core.common import PlatformError, PublicationUnknown

_tl = threading.local()


def set_cancel_check(fn) -> None:
    """Поток публикации регистрирует функцию, которая бросает «Отмена», если пользователь нажал кнопку."""
    _tl.check = fn


def check_cancel() -> None:
    fn = getattr(_tl, "check", None)
    if fn:
        fn()


def _wait(seconds: float, what: str, attempt: int, note=None) -> None:
    for left in range(int(seconds), 0, -1):
        if note:
            note(f"{what}: обрыв, повтор через {left} с (попытка {attempt + 1})")
        check_cancel()
        time.sleep(1)


def _pause(attempt: int) -> int:
    return min(60, 2 ** min(attempt, 6))


def network_reason(error):
    """Useful transport details without exception text, URLs, bodies or credentials."""
    pending, chain, seen = [error], [], set()
    while pending and len(chain) < 12:
        item = pending.pop(0)
        if not isinstance(item, BaseException) or id(item) in seen:
            continue
        seen.add(id(item))
        chain.append(item)
        pending.extend([item.__cause__, item.__context__, getattr(item, 'reason', None)])
        pending.extend(arg for arg in item.args if isinstance(arg, BaseException))
    if any(isinstance(e, (socket.gaierror, getattr(U3, 'NameResolutionError', socket.gaierror))) for e in chain):
        reason = "не удалось определить адрес сайта (DNS)"
    elif any(isinstance(e, requests.exceptions.ProxyError) for e in chain):
        reason = "ошибка соединения через прокси"
    elif any(isinstance(e, (ssl.SSLError, requests.exceptions.SSLError, U3.SSLError)) for e in chain):
        reason = "ошибка защищённого соединения TLS или проверки сертификата"
    elif any(isinstance(e, (requests.exceptions.ReadTimeout, U3.ReadTimeoutError)) for e in chain):
        reason = "сервер не ответил за отведённое время"
    elif any(isinstance(e, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)) for e in chain):
        reason = "соединение сброшено или закрыто во время обмена данными"
    else:
        reason = "сетевое соединение прервано"
    names = list(dict.fromkeys(type(e).__name__ for e in chain))
    codes = list(dict.fromkeys(value for e in chain for value in (getattr(e, 'winerror', None), getattr(e, 'errno', None)) if isinstance(value, int)))
    detail = ' → '.join(names)
    if codes:
        detail += '; системные коды: ' + ', '.join(map(str, codes))
    return f"{reason} ({detail})"


def request(method: str, url: str, *, idempotent: bool, what: str = "запрос", note=None, **kw) -> requests.Response:
    """requests.request с повторами. idempotent=True — повторяем любой обрыв/5xx/429; False — только 429 и обрыв до соединения."""
    attempt = 0
    kw.setdefault("timeout", 120)
    while True:
        check_cancel()
        try:
            for v in (kw.get("files") or {}).values():   # файловые объекты перематываем перед повтором
                fh = v[1] if isinstance(v, tuple) and len(v) > 1 else v
                if hasattr(fh, "seek"):
                    fh.seek(0)
            r = requests.request(method, url, **kw)
        except requests.ConnectTimeout:
            attempt += 1   # соединение не установилось — запрос не дошёл, повторять безопасно всегда
            _wait(_pause(attempt), what, attempt, note)
            continue
        except requests.RequestException as e:
            if not idempotent:
                raise PublicationUnknown(f"{what}: связь оборвалась, неизвестно, дошёл ли запрос — {network_reason(e)}. "
                                    "Проверьте площадку — возможно, запись уже появилась; повторять вслепую нельзя.")
            attempt += 1
            _wait(_pause(attempt), what, attempt, note)
            continue
        if r.status_code == 429 or (idempotent and r.status_code >= 500):
            attempt += 1
            try:
                ra = float(r.headers.get("Retry-After") or (r.json() or {}).get("retry_after") or 0)
            except (ValueError, TypeError, AttributeError):
                ra = 0
            _wait(max(ra, _pause(attempt)) if ra else _pause(attempt), what, attempt, note)
            continue
        if not idempotent and r.status_code >= 500:
            raise PublicationUnknown(f"{what}: сервер ответил {r.status_code}; возможно, пост уже опубликован. Проверьте площадку перед повтором.")
        return r


def post(url, **kw):
    return request("POST", url, **kw)


def get(url, **kw):
    return request("GET", url, **kw)
