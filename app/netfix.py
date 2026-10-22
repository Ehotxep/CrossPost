"""Запасной поиск адресов сайтов: DNS через HTTPS (DoH).

Бывает, что DNS провайдера, роутера или фильтра (семейный DNS, Pi-hole, блокировщик рекламы) не отдаёт адрес отдельного
поддомена — например static1.e621.net, где лежат файлы выгрузок, — хотя сам сайт открывается. Обычный запрос тогда
падает с «getaddrinfo failed». Здесь системный поиск подменяется обёрткой: если он не нашёл адрес, спрашиваем его
у независимых DoH-серверов (по IP-адресам, поэтому им самим DNS не нужен) и подключаемся по полученному адресу.
Имя сайта и проверка сертификата остаются прежними — меняется только то, откуда взят IP.

Только стандартная библиотека: модуль подключается при импорте app. Отключить: переменная CROSSPOST_NO_DOH=1.
"""
import ipaddress
import json
import os
import socket
import ssl
import threading
import time
import urllib.parse
import urllib.request

# (адрес запроса, заголовки). Cloudflare и Google отвечают JSON-ом на ?name=&type=A.
PROVIDERS = [
    ("https://1.1.1.1/dns-query", {"accept": "application/dns-json"}),
    ("https://8.8.8.8/resolve", {"accept": "application/dns-json"}),
    ("https://9.9.9.9:5053/dns-query", {"accept": "application/dns-json"}),
    ("https://94.140.14.140/dns-query", {"accept": "application/dns-json"}),   # AdGuard без фильтров
]
TIMEOUT = 4.0
NEG_TTL = 300.0     # адрес не нашёлся нигде — столько не пробуем снова для этого имени
DOWN_TTL = 60.0     # ни один DoH-сервер не ответил (нет интернета?) — столько не пробуем вообще

_orig = socket.getaddrinfo
_lock = threading.Lock()
_cache: dict = {}   # имя → (до какого времени, [ip])
_down_until = 0.0
_installed = False
stats = {"rescued": 0, "failed": 0}


def _eligible(host) -> bool:
    if not isinstance(host, str) or "." not in host or host.endswith((".local", ".localhost", ".lan", ".internal")):
        return False
    try:
        ipaddress.ip_address(host)
        return False   # уже адрес
    except ValueError:
        return True


def _query(url: str, headers: dict, host: str) -> tuple[list, int]:
    """Один DoH-запрос → ([ip], ttl). Исключение — сервер недоступен."""
    full = url + "?" + urllib.parse.urlencode({"name": host, "type": "A"})
    req = urllib.request.Request(full, headers=dict(headers, **{"user-agent": "Crosspost"}))
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:   # адрес сервера — IP: системный DNS не нужен
        data = json.loads(r.read().decode("utf-8", errors="replace"))
    ips, ttl = [], 300
    for a in data.get("Answer") or []:
        if a.get("type") == 1 and a.get("data"):
            ips.append(a["data"])
            ttl = min(ttl, int(a.get("TTL") or 300))
    return ips, max(60, min(ttl, 3600))


def lookup(host: str) -> list:
    """Адреса имени через DoH ([] — не нашлось). Результат запоминается."""
    global _down_until
    now = time.time()
    with _lock:
        hit = _cache.get(host)
        if hit and hit[0] > now:
            return list(hit[1])
        if _down_until > now:
            return []
    reached = False
    for url, headers in PROVIDERS:
        try:
            ips, ttl = _query(url, headers, host)
        except Exception:   # noqa: BLE001 — сервер недоступен: пробуем следующий
            continue
        reached = True
        if ips:
            with _lock:
                _cache[host] = (time.time() + ttl, ips)
            return list(ips)
    with _lock:
        if reached:   # серверы отвечают, но такого имени нет
            _cache[host] = (time.time() + NEG_TTL, [])
        else:         # не достучались ни до одного — вероятно, нет интернета
            _down_until = time.time() + DOWN_TTL
    return []


def _getaddrinfo(host, port, *args, **kwargs):
    try:
        return _orig(host, port, *args, **kwargs)
    except socket.gaierror:
        if not _eligible(host):
            raise
        ips = lookup(host)
        if not ips:
            stats["failed"] += 1
            raise
        out = []
        for ip in ips:
            try:
                out += _orig(ip, port, *args, **kwargs)
            except socket.gaierror:
                continue
        if not out:
            raise
        stats["rescued"] += 1
        return out


def install():
    global _installed
    if _installed or os.environ.get("CROSSPOST_NO_DOH"):
        return
    _installed = True
    socket.getaddrinfo = _getaddrinfo
