"""Запас MTProxy из открытых списков: скачать → отсеять → проверить настоящим подключением → держать N рабочих.

Нужен, когда Telegram режут: прокси из чужих списков живут часами или днями, поэтому запас пополняется сам.
Запасные прокси — это уже существующий список «Прокси в запасе»: при обрыве связи программа переключается на
рабочий (TgService._maybe_failover), и тем же путём идёт и импорт статистики, и бот в режиме MTProto.
Обычный Bot API (HTTPS к api.telegram.org) через MTProxy не ходит.

Порядок работы одного круга (refresh):
  1. перепроверить прокси из запаса: рабочие остаются (с задержкой в мс), сорвавшиеся два круга подряд — убираются;
  2. если рабочих меньше нужного — скачать списки, разобрать ссылки tg://proxy, убрать дубли и «плохих» (после
     неудачи прокси не пробуется сутки), быстро отсеять недоступные по TCP, а оставшиеся проверить настоящим
     подключением к Telegram (как «Проверить связь») по несколько штук сразу, пока не наберётся нужное число.

Выключено по умолчанию: хозяева бесплатных прокси видят ваш IP и факт подключения к Telegram (содержимое
зашифровано между вами и Telegram и им недоступно). Ничего, кроме адресов и ссылок, наружу не отправляется.
"""
import queue
import random
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

DEFAULT_SOURCES = [
    "https://raw.githubusercontent.com/Argh94/Proxy-List/main/MTProto.txt",
    "https://raw.githubusercontent.com/kort0881/telegram-proxy-collector/main/proxy_ru.txt",
    "https://tgmtproxy.github.io/mtproxy/proxies.txt",
    "https://raw.githubusercontent.com/SoliSpirit/mtproto/master/all_proxies.txt",
]
DEFAULTS = {"enabled": False, "target": 10, "sources": DEFAULT_SOURCES, "at": 0.0, "msg": "", "bad": {}}
EVERY_H = 6            # плановый круг — раз в 6 часов
RETRY_MIN = 30         # если рабочих меньше нужного — не чаще раза в 30 минут
BAD_TTL = 24 * 3600    # неудачный адрес не пробуем сутки
MAX_FETCH = 2_000_000  # байт с одного источника
MAX_CHECK = 36         # настоящих проверок за круг
WORKERS = 3            # проверок подключением одновременно
POOL_MAX = 20          # столько запасных поддерживает остальной код

_run_lock = threading.Lock()


def key_of(p: dict) -> tuple:
    return (p.get("type"), p.get("host"), int(p.get("port") or 0), p.get("secret", ""))


def cfg(tg) -> dict:
    c = dict(DEFAULTS)
    c.update(tg._cfg().get("pool") or {})
    return c


def public(tg) -> dict:
    c = cfg(tg)
    return {"enabled": bool(c["enabled"]), "target": int(c["target"]), "sources": list(c["sources"]), "at": c["at"], "msg": c["msg"],
            "running": _run_lock.locked(), "defaults": DEFAULT_SOURCES}


def set_cfg(tg, changes: dict) -> dict:
    from app.telegram.service import AuthError
    c = cfg(tg)
    if "enabled" in changes:
        c["enabled"] = bool(changes["enabled"])
    if "target" in changes:
        try:
            c["target"] = max(3, min(POOL_MAX, int(changes["target"])))
        except (TypeError, ValueError):
            raise AuthError("Число прокси в запасе — от 3 до 20")
    if "sources" in changes:
        src = []
        for line in (changes["sources"] if isinstance(changes["sources"], list) else str(changes["sources"]).splitlines()):
            u = str(line).strip()
            if not u:
                continue
            if not u.startswith("https://"):
                raise AuthError("Источник списка — ссылка, начинающаяся с https://")
            if u not in src:
                src.append(u)
        c["sources"] = src[:12] or list(DEFAULT_SOURCES)
    keep = {k: c[k] for k in DEFAULTS}
    tg._write_cfg(pool=keep)
    return public(tg)


# --- кандидаты ---------------------------------------------------------------------------

def fetch(urls: list) -> tuple[str, list]:
    """Тексты всех источников подряд; ошибки одного источника другим не мешают."""
    out, errs = [], []
    for u in urls:
        try:
            r = requests.get(u, timeout=20, stream=True, headers={"User-Agent": "Crosspost"})
            if r.status_code != 200:
                errs.append(f"{u.split('/')[2]}: HTTP {r.status_code}")
                continue
            data = r.raw.read(MAX_FETCH + 1, decode_content=True)
            out.append(data[:MAX_FETCH].decode("utf-8", errors="replace"))
        except requests.RequestException as e:
            errs.append(f"{u.split('/')[2]}: {type(e).__name__}")
    return "\n".join(out), errs


def candidates(text: str, skip: set, bad: dict, faketls_ok: bool, now: float | None = None) -> list:
    """Разобрать ссылки → только MTProxy, без дублей, без известных и недавно плохих; FakeTLS — первыми."""
    from app.telegram.backend import parse_proxy_links
    now = now or time.time()
    out = []
    for p in parse_proxy_links(text):
        if p["type"] != "mtproxy" or key_of(p) in skip:
            continue
        if now - bad.get(f"{p['host']}:{p['port']}", 0) < BAD_TTL:
            continue
        ftls = str(p.get("secret", "")).lower().startswith("ee")
        if ftls and not faketls_ok:
            continue
        p["_ftls"] = ftls
        out.append(p)
    random.shuffle(out)
    out.sort(key=lambda p: not p["_ftls"])   # устойчивая сортировка: внутри групп порядок случайный
    for p in out:
        p.pop("_ftls", None)
    return out


def tcp_alive(cands: list, timeout: float = 3.0, workers: int = 48) -> list:
    """Быстрый отсев: кто вообще принимает соединение на порт. Порядок — по скорости ответа."""
    def one(p):
        t0 = time.time()
        try:
            with socket.create_connection((p["host"], p["port"]), timeout=timeout):
                return p, time.time() - t0
        except OSError:
            return p, None
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(one, cands))
    return [p for p, dt in sorted(((p, dt) for p, dt in res if dt is not None), key=lambda x: x[1])]


# --- один круг ---------------------------------------------------------------------------

def refresh(tg, report=lambda **k: None) -> dict:
    """Перепроверить запас и добрать до нужного числа. Блокирует до конца (минуты) — запускать в фоне."""
    from app.telegram.backend import faketls_ready
    from app.telegram.service import AuthError
    if not tg.keys():
        raise AuthError("Сначала сохраните api_id и api_hash — без них прокси нельзя проверить настоящим подключением")
    if not _run_lock.acquire(blocking=False):
        return {"busy": True}
    try:
        c = cfg(tg)
        target = int(c["target"])
        bad = {k: t for k, t in (c.get("bad") or {}).items() if time.time() - t < BAD_TTL}
        tg._write_cfg(pool=dict(c, at=time.time(), msg="идёт проверка…", bad=bad))   # «занято» для второго процесса
        net = tg.net()
        have = [p for p in net.get("proxies") or [] if p.get("type") == "mtproxy"]
        other = [p for p in net.get("proxies") or [] if p.get("type") != "mtproxy"]

        slots = queue.Queue()   # у каждой одновременной проверки — своя временная сессия
        for i_ in range(WORKERS):
            slots.put(i_)

        tg.clean_probe_files()   # остатки прошлых кругов

        def probe(p):
            slot = slots.get()
            try:
                return p, tg.probe_proxy({k: v for k, v in p.items() if k not in ("ms", "fails", "ok_at")}, slot)
            except Exception as e:  # noqa: BLE001 — сбой одной проверки не должен рвать весь круг
                return p, {"ok": False, "error": type(e).__name__}
            finally:
                slots.put(slot)

        report(progress=0.05, message=f"Проверяю запас: {len(have)}…")
        alive, kept_flaky, dropped = [], [], 0
        with ThreadPoolExecutor(WORKERS) as ex:
            for p, r in ex.map(probe, have):
                q = dict(p)
                if r["ok"]:
                    q.update(ms=r["ms"], fails=0, ok_at=int(time.time()))
                    alive.append(q)
                else:
                    q["fails"] = int(p.get("fails") or 0) + 1
                    if q["fails"] >= 2:
                        dropped += 1
                        bad[f"{p['host']}:{p['port']}"] = time.time()
                    else:
                        kept_flaky.append(q)
        added, checked, errs = 0, 0, []
        if len(alive) < target:
            report(progress=0.3, message="Скачиваю списки…")
            text, errs = fetch(c["sources"])
            skip = {key_of(p) for p in have}
            cands = candidates(text, skip, bad, faketls_ready())
            report(progress=0.4, message=f"Кандидатов: {len(cands)}, проверяю доступность…")
            cands = tcp_alive(cands[:400])
            i = 0
            with ThreadPoolExecutor(WORKERS) as ex:
                while len(alive) < target and i < len(cands) and checked < MAX_CHECK:
                    batch = cands[i:i + WORKERS]
                    i += len(batch)
                    for p, r in ex.map(probe, batch):
                        checked += 1
                        if r["ok"] and len(alive) < target:
                            alive.append(dict(p, ms=r["ms"], fails=0, ok_at=int(time.time())))
                            added += 1
                        elif not r["ok"]:
                            bad[f"{p['host']}:{p['port']}"] = time.time()
                    report(progress=min(0.4 + 0.55 * checked / MAX_CHECK, 0.95), message=f"Проверено {checked}, рабочих в запасе: {len(alive)}")
        alive.sort(key=lambda p: p.get("ms") or 10 ** 6)
        final = other + (alive + kept_flaky)[:max(0, POOL_MAX - len(other))]   # добавленные вручную SOCKS5/HTTP остаются как были
        msg = f"В запасе {len(alive)} из {target}" + (f" (новых: {added})" if added else "") + (f", убрано сорвавшихся: {dropped}" if dropped else "")
        if len(alive) < target:
            msg += " — рабочих прокси в списках не хватило" + (f" ({'; '.join(errs[:2])})" if errs else "")
        now_net = tg.net()   # за время круга настройки могли поменяться — берём свежие и меняем только запас
        tg._write_cfg(net=dict(now_net, proxies=final), pool=dict(cfg(tg), at=time.time(), msg=msg, bad=dict(list(bad.items())[-500:])))
        report(progress=1.0, message=msg)
        tg.clean_probe_files()
        return {"alive": len(alive), "target": target, "added": added, "dropped": dropped, "checked": checked, "msg": msg}
    finally:
        _run_lock.release()


def due(tg) -> bool:
    c = cfg(tg)
    if not c["enabled"] or not tg.keys() or _run_lock.locked():
        return False
    age = time.time() - float(c["at"] or 0)
    have = sum(1 for p in tg.net().get("proxies") or [] if p.get("type") == "mtproxy" and not p.get("fails"))
    return age > EVERY_H * 3600 or (have < int(c["target"]) and age > RETRY_MIN * 60)
