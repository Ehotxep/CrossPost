"""Telegram в приложении: ключи, вход, выбор каналов, импорт истории и обновление цифр."""
import asyncio
import json
import os
import threading
from datetime import datetime, timedelta, timezone

from app import db
from app.paths import data_dir, sub
from app.telegram.backend import AuthError, CodeAlreadySent, NeedPassword, TelethonBackend

BATCH = 400
REFRESH_DAYS = 30   # у постов младше месяца обновляем цифры (у старых просмотры уже почти не растут)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _link(chat_id: str, username: str | None, msg_id: int) -> str:
    if username:
        return f"https://t.me/{username}/{msg_id}"
    cid = str(chat_id)
    return f"https://t.me/c/{cid[4:] if cid.startswith('-100') else cid.lstrip('-')}/{msg_id}"


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 10 < n < 20:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


def _net_error(e: Exception) -> str:
    """Понятное описание сетевой ошибки."""
    text = str(e)
    if isinstance(e, AuthError):
        return text
    if "proxy" in text.lower():
        return "прокси не отвечает — проверьте адрес, порт и что программа-прокси запущена"
    if isinstance(e, (TimeoutError, asyncio.TimeoutError)) or "Timeout" in type(e).__name__ or not text:
        return "нет ответа — соединение, похоже, блокируется"
    if "IncompleteRead" in type(e).__name__ or "closed" in text.lower() or "reset" in text.lower():
        return "соединение обрывается — похоже на блокировку провайдером"
    if "failed" in text.lower() or "refused" in text.lower():
        return "не удаётся подключиться к серверам Telegram"
    return text


# ошибки, после которых файл входа уже не годится: только войти заново
DEAD_LOGIN = {"AuthKeyDuplicatedError", "AuthKeyUnregisteredError", "SessionRevokedError", "SessionExpiredError",
              "UserDeactivatedError", "UserDeactivatedBanError", "AuthKeyInvalidError"}


class TgService:
    backend_factory = TelethonBackend   # в тестах подменяется имитацией

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True, name="telegram").start()
        self._backend = None
        self._lock = threading.Lock()
        self._need_password = False
        self._code_sent = False
        self._busy = 0
        self._idle = None

    IDLE = 30   # секунд без дела — отключаемся от Telegram и отдаём вход другой части Crosspost

    # --- служебное ---
    def _run(self, coro, timeout=600, user=True):
        """user=False — запросы бота (у него своя сессия): очередь за вход пользователя их не касается."""
        import concurrent.futures
        if user:
            with self._lock:
                self._busy += 1
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            with self._lock:
                b, self._backend = self._backend, None   # клиент мог зависнуть на переподключениях — следующая попытка начнёт заново
            if b is not None and hasattr(b, "close"):
                asyncio.run_coroutine_threadsafe(b.close(), self._loop)
            if self._maybe_failover(TimeoutError("Telegram не отвечает")):
                raise AuthError("Telegram не отвечает через текущий прокси — пробую запасные, повторите через минуту.")
            raise AuthError("Telegram не отвечает. Проверьте интернет; если Telegram у вас работает только через прокси или VPN — "
                            "включите VPN на весь компьютер и попробуйте ещё раз.")
        except Exception as e:
            name = type(e).__name__
            if not isinstance(e, AuthError) or "не отвечает" in str(e):
                if self._maybe_failover(e):   # есть запасные прокси — подбираем рабочий в фоне
                    raise AuthError(f"{_net_error(e) if not isinstance(e, AuthError) else e} Пробую запасные прокси — повторите через минуту.") from e
            if user and name in DEAD_LOGIN:
                self._forget_login()
                why = ("один и тот же вход использовался с двух адресов одновременно. Теперь окно и фоновая отправка "
                       "подключаются по очереди — это не повторится" if name == "AuthKeyDuplicatedError"
                       else "сохранённый вход больше не действует (его отменил Telegram или вы завершили сеанс на телефоне)")
                raise AuthError(f"Telegram сбросил вход Crosspost: {why}. Войдите заново: «Настройки» → «Импорт из Telegram» → "
                                "подключение через API.") from e
            raise
        finally:
            if user:
                with self._lock:
                    self._busy -= 1
                self._loop.call_soon_threadsafe(self._arm_idle)

    def _arm_idle(self):
        if self._idle is not None:
            self._idle.cancel()
        wait = 600 if (self._code_sent or self._need_password) else self.IDLE   # пока вводят код — не рвём
        self._idle = self._loop.call_later(wait, lambda: asyncio.ensure_future(self._idle_close()))

    async def _idle_close(self):
        self._idle = None
        with self._lock:
            b = self._backend if self._busy == 0 else None
        if b is not None and hasattr(b, "close"):
            await b.close()   # отключиться и освободить очередь (клиент подключится снова при следующем запросе)

    def _forget_login(self):
        """Ключ входа аннулирован Telegram — файл сессии больше не годится."""
        with self._lock:
            b, self._backend = self._backend, None
            mine = getattr(self, "_backend_gen", "") == self._read_gen()
        if b is not None:
            self._discard(b)
        if not mine:   # в другом процессе уже вошли заново — новый вход не трогаем
            return
        for pat in ("telegram.session*", "telegram_worker.session*", "telegram.pending.json"):
            for p in data_dir().glob(pat):
                if not p.name.endswith(".lock"):
                    try:
                        p.unlink(missing_ok=True)
                    except OSError:   # Windows: файл ещё открыт другим процессом — новый вход просто перезапишет его
                        pass
        self._bump_gen()

    @property
    def _keys_path(self):
        return data_dir() / "telegram.json"

    def keys(self) -> dict | None:
        p = self._keys_path
        if not p.exists():
            return None
        k = json.loads(p.read_text(encoding="utf-8"))
        return k if k.get("api_id") and k.get("api_hash") else None

    # «Поколение» входа: меняется при каждом новом входе и выходе. Окно и фоновая отправка — разные процессы
    # с одним файлом входа; если в одном вошли заново, другой не должен работать со старым ключом из памяти
    # (получит «key is not registered» и, хуже того, может записать старый ключ поверх нового).
    @staticmethod
    def _gen_path():
        return data_dir() / "telegram.login"

    def _read_gen(self) -> str:
        try:
            return self._gen_path().read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _bump_gen(self) -> str:
        import secrets
        g = secrets.token_hex(8)
        self._gen_path().write_text(g, encoding="utf-8")
        return g

    def backend(self):
        stale = None
        with self._lock:
            if self._backend is not None and getattr(self, "_backend_gen", "") != self._read_gen():
                stale, self._backend = self._backend, None   # вход сменился в другом процессе — старый клиент не годится
            if self._backend is None:
                k = self.keys()
                if not k:
                    raise AuthError("Сначала укажите api_id и api_hash")
                name = os.environ.get("CROSSPOST_TG_SESSION", "telegram")
                self._backend_gen = self._read_gen()
                self._backend = self.backend_factory(data_dir() / name, k["api_id"], k["api_hash"], self.net())
            b = self._backend
        if stale is not None:
            self._discard(stale)
        return b

    def _discard(self, b):
        """Выбросить клиент со старым ключом, ничего не записывая в файл входа."""
        async def go():
            try:
                from telethon.sessions import MemorySession
                b.client.session = MemorySession()   # отключение без записи старого ключа поверх нового
            except Exception:
                pass
            if hasattr(b, "close"):
                await b.close()
        try:
            asyncio.run_coroutine_threadsafe(go(), self._loop).result(10)
        except Exception:
            pass

    # --- вход ---
    def set_keys(self, api_id, api_hash: str):
        api_id = str(api_id).strip()
        api_hash = str(api_hash).strip()
        if not api_id.isdigit() or len(api_hash) < 20:
            raise AuthError("api_id — это число, api_hash — длинная строка из букв и цифр (с my.telegram.org)")
        self._write_cfg(api_id=int(api_id), api_hash=api_hash)
        self._drop_backend()
        return self.status()

    # --- настройки соединения (zapret, прокси) ---
    def _cfg(self) -> dict:
        p = self._keys_path
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def _write_cfg(self, **changes):
        cfg = self._cfg()
        cfg.update(changes)
        self._keys_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    def net(self) -> dict:
        return self._cfg().get("net") or {"transport": "obfuscated", "proxy": {}}

    def _drop_backend(self):
        with self._lock:
            b, self._backend = self._backend, None
        if b is not None and hasattr(b, "close"):
            try:
                asyncio.run_coroutine_threadsafe(b.close(), self._loop).result(5)
            except Exception:
                pass

    @staticmethod
    def _clean_net(net: dict, old: dict | None = None) -> dict:
        transport = net.get("transport") if net.get("transport") in ("obfuscated", "full") else "obfuscated"
        p = net.get("proxy") or {}
        t = p.get("type") if p.get("type") in ("socks5", "http", "mtproxy") else ""
        proxy = {}
        if t:
            host = str(p.get("host") or "").strip()
            port = str(p.get("port") or "").strip()
            if not host or not port.isdigit():
                raise AuthError("Для прокси укажите адрес и порт, например 127.0.0.1 и 1080")
            proxy = {"type": t, "host": host, "port": int(port)}
            if t == "mtproxy":
                secret = str(p.get("secret") or "").strip()
                if not secret:
                    raise AuthError("Для MTProxy нужен секрет (secret) — длинная строка из букв и цифр")
                from app.telegram.backend import _secret_hex
                proxy["secret"] = _secret_hex(secret)   # ee… (FakeTLS) — через TelethonFakeTLS, base64 → hex
            else:
                proxy["user"] = str(p.get("user") or "").strip()
                pw = p.get("password")
                if pw is None or pw == "":   # пустое поле = оставить прежний пароль
                    pw = ((old or {}).get("proxy") or {}).get("password", "")
                proxy["password"] = pw if proxy["user"] else ""
        out = {"transport": transport, "proxy": proxy}
        out["proxies"] = ((old or {}).get("proxies") or [])[:20]   # запасные меняются только ссылками и «Забыть»
        return out

    # --- запасные прокси: ссылки из Telegram и автоматическое переключение ---
    def add_proxy_links(self, text: str) -> dict:
        """Ссылки на прокси из Telegram (tg://proxy…, t.me/proxy…, tg://socks…) — в список запасных,
        затем проверка: сохраняется первый рабочий вариант."""
        from app.telegram.backend import parse_proxy_links
        found = parse_proxy_links(text)
        if not found:
            raise AuthError("Не нашла ссылок на прокси. Нужна ссылка вида tg://proxy?server=…&port=…&secret=… "
                            "или https://t.me/proxy?… — в Telegram: Настройки → Продвинутые → Тип соединения → у прокси «Поделиться»")
        cur = self.net()
        key = lambda p: (p.get("type"), p.get("host"), int(p.get("port") or 0), p.get("secret", ""))
        merged, seen = [], set()
        for p in found + (cur.get("proxies") or []):
            if key(p) not in seen:
                seen.add(key(p))
                merged.append(p)
        self._write_cfg(net=dict(cur, proxies=merged[:20]))
        results = self.check_network(prefer=found)
        return {"added": len(found), "results": results, "status": self.status()}

    def forget_proxy(self, host: str, port: int):
        cur = self.net()
        cur["proxies"] = [p for p in cur.get("proxies") or [] if not (p.get("host") == host and int(p.get("port") or 0) == int(port))]
        self._write_cfg(net=cur)
        return self.status()

    _failover_at = 0.0

    def _maybe_failover(self, e) -> bool:
        """Связи нет — в фоне подобрать рабочий вариант из запасных прокси (не чаще раза в 5 минут)."""
        import time
        txt = f"{type(e).__name__} {e}".lower()
        if not any(w in txt for w in ("не отвечает", "timeout", "connection", "connect", "proxy", "refused", "reset", "network")):
            return False
        from app.telegram import proxy_pool
        if not ((self.net().get("proxies") or []) or proxy_pool.cfg(self)["enabled"]) or time.time() - TgService._failover_at < 300:
            return False
        TgService._failover_at = time.time()
        def run():
            res = self.check_network()
            if not any(r.get("ok") for r in res):   # ни запасные, ни напрямую — пробуем пополнить запас из списков
                from app.telegram import proxy_pool
                if proxy_pool.cfg(self)["enabled"]:
                    proxy_pool.refresh(self)
        threading.Thread(target=run, daemon=True, name="tg-failover").start()
        return True

    def set_net(self, net: dict):
        self._write_cfg(net=self._clean_net(net, self.net()))
        self._drop_backend()
        return self.status()

    def check_network(self, net: dict | None = None, prefer: list | None = None) -> list[dict]:
        """Пробует варианты подключения по очереди (каждый — с отдельной временной сессией)
        и сохраняет первый рабочий. Без входа в аккаунт: проверяется только связь с серверами.
        Порядок: только что добавленные прокси, текущий, запасные, напрямую."""
        import time
        k = self.keys()
        if not k:
            raise AuthError("Сначала сохраните api_id и api_hash")
        cur = self._clean_net(net, self.net()) if net else self.net()
        backups = cur.get("proxies") or []
        label = lambda p: ("MTProxy" + (" FakeTLS" if str(p.get("secret", "")).startswith("ee") else "") if p["type"] == "mtproxy"
                           else p["type"].upper()) + f" {p['host']}:{p['port']}"
        variants, seen = [], set()
        for p in (prefer or []) + ([cur["proxy"]] if cur["proxy"] else []) + backups:
            kk = (p.get("type"), p.get("host"), int(p.get("port") or 0), p.get("secret", ""))
            if p and kk not in seen and len(variants) < 8:
                seen.add(kk)
                variants.append(("Через " + label(p), {"transport": cur.get("transport", "obfuscated"), "proxy": p, "proxies": backups}))
        variants += [("Напрямую, как Telegram Desktop (обфускация)", {"transport": "obfuscated", "proxy": {}, "proxies": backups}),
                     ("Напрямую, обычный протокол", {"transport": "full", "proxy": {}, "proxies": backups})]
        results, chosen = [], None
        tmp = data_dir() / "telegram_netcheck"
        for label, variant in variants:
            t0 = time.time()
            b = None
            try:
                b = self.backend_factory(tmp, k["api_id"], k["api_hash"], variant)
                asyncio.run_coroutine_threadsafe(b.ping(), self._loop).result(60)
                results.append({"label": label, "ok": True, "ms": int((time.time() - t0) * 1000)})
                if chosen is None:
                    chosen = variant
            except Exception as e:  # noqa: BLE001
                msg = _net_error(e)
                results.append({"label": label, "ok": False, "error": msg})
            finally:
                if b is not None and hasattr(b, "close"):
                    try:
                        asyncio.run_coroutine_threadsafe(b.close(), self._loop).result(5)
                    except Exception:
                        pass
                self.clean_probe_files("telegram_netcheck.session")
            if chosen is not None:
                break   # дальше проверять незачем
        if chosen is not None:
            self._write_cfg(net=chosen)
            self._drop_backend()
        return results

    def probe_proxy(self, p: dict, slot: int = 0, timeout: float = 25) -> dict:
        """Настоящая проверка одного прокси: подключение к Telegram через него и один лёгкий запрос.
        Настройки не меняются. slot — номер временной сессии (параллельным проверкам нужны разные)."""
        import time
        k = self.keys()
        if not k:
            raise AuthError("Сначала сохраните api_id и api_hash")
        import uuid
        stem = f"telegram_netcheck_p{slot}_{uuid.uuid4().hex[:6]}"   # у каждой проверки своя сессия: недоудалённый файл прошлой не мешает
        tmp = data_dir() / stem
        variant = {"transport": self.net().get("transport", "obfuscated"), "proxy": p, "proxies": []}
        t0, b = time.time(), None
        try:
            b = self.backend_factory(tmp, k["api_id"], k["api_hash"], variant)
            asyncio.run_coroutine_threadsafe(asyncio.wait_for(b.ping(), timeout), self._loop).result(timeout + 5)
            return {"ok": True, "ms": int((time.time() - t0) * 1000)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": _net_error(e)}
        finally:
            if b is not None and hasattr(b, "close"):
                try:
                    asyncio.run_coroutine_threadsafe(b.close(), self._loop).result(5)
                except Exception:
                    pass
            self.clean_probe_files(stem)

    @staticmethod
    def clean_probe_files(stem: str = "telegram_netcheck_p") -> None:
        """Убрать временные сессии проверок. В Windows файл может быть ещё занят — тогда тихо оставляем до следующего раза."""
        import time
        for f in data_dir().glob(f"{stem}*"):
            for _ in range(3):
                try:
                    f.unlink(missing_ok=True)
                    break
                except OSError:
                    time.sleep(0.3)

    def status_net(self) -> dict:
        net = json.loads(json.dumps(self.net()))
        if (net.get("proxy") or {}).get("password"):
            net["proxy"]["password"] = ""
            net["proxy"]["has_password"] = True
        for p in net.get("proxies") or []:   # пароли запасных прокси в окно не отдаём
            if p.get("password"):
                p["password"], p["has_password"] = "", True
        from app.telegram.backend import faketls_ready
        net["faketls"] = faketls_ready()
        from app.telegram import proxy_pool
        net["pool"] = proxy_pool.public(self)
        return net

    def status(self) -> dict:
        net = self.status_net()
        st = {"configured": self.keys() is not None, "authorized": False, "me": None,
              "code_sent": self._code_sent, "need_password": self._need_password, "net": net}
        if st["configured"]:
            try:
                b = self.backend()
                st["authorized"] = self._run(b.is_authorized(), 30)
                if st["authorized"]:
                    st["me"] = self._run(b.me(), 30)
            except AuthError as e:
                st["error"] = str(e)
            except Exception as e:  # нет сети и т.п.
                st["error"] = f"Не удалось связаться с Telegram: {e or type(e).__name__}"
        return st

    def send_code(self, phone: str):
        phone = "+" + "".join(ch for ch in phone if ch.isdigit())
        notice = ""
        try:
            self._run(self.backend().send_code(phone), 150)
        except CodeAlreadySent as e:   # код уже у вас — показываем поле для кода и объяснение
            notice = str(e)
        self._code_sent, self._need_password = True, False
        st = self.status()
        if notice:
            st["notice"] = notice
        return st

    def sign_in(self, code: str):
        try:
            self._run(self.backend().sign_in(code), 150)
        except NeedPassword:
            self._need_password = True
            return self.status()
        self._code_sent = self._need_password = False
        self._backend_gen = self._bump_gen()   # новый вход — другие процессы перечитают файл входа
        return self.status()

    def sign_in_password(self, password: str):
        self._run(self.backend().sign_in_password(password), 150)
        self._code_sent = self._need_password = False
        self._backend_gen = self._bump_gen()
        return self.status()

    def log_out(self):
        try:
            self._run(self.backend().log_out(), 60)
        finally:
            with self._lock:
                self._backend = None
            for pat in ("telegram.session*", "telegram_worker.session*", "telegram.pending.json"):   # и старую копию фоновой отправки
                for p in data_dir().glob(pat):
                    if not p.name.endswith(".lock"):
                        try:
                            p.unlink(missing_ok=True)
                        except OSError:
                            pass
            self._bump_gen()
        return self.status()

    # --- каналы ---
    def channels(self, refresh: bool = False) -> list[dict]:
        if refresh:
            found = self._run(self.backend().channels(), 120)
            with db.tx() as c:
                for ch in found:
                    c.execute("""INSERT INTO channels (platform, chat_id, title, username) VALUES ('telegram', ?, ?, ?)
                                 ON CONFLICT(platform, chat_id) DO UPDATE SET title=excluded.title, username=excluded.username""",
                              (ch["chat_id"], ch["title"], ch["username"]))
            extra = {ch["chat_id"]: ch for ch in found}
        else:
            extra = {}
        rows = db.query("""SELECT c.*, (SELECT COUNT(*) FROM posts p WHERE p.platform=c.platform AND p.chat_id=c.chat_id) AS posts
                           FROM channels c WHERE platform='telegram' ORDER BY selected DESC, title""")
        for r in rows:
            e = extra.get(r["chat_id"], {})
            r["broadcast"] = e.get("broadcast")
            r["members"] = e.get("members")
        return rows

    def forum_topics(self, chat_id: str) -> list[dict]:
        return self._run(self.backend().forum_topics(str(chat_id)), 60)

    def select(self, chat_id: str, selected: bool, rating: str = "all"):
        if rating not in ("sfw", "nsfw", "all"):
            raise ValueError("rating: sfw / nsfw / all")
        with db.tx() as c:
            c.execute("UPDATE channels SET selected=?, rating=? WHERE platform='telegram' AND chat_id=?",
                      (1 if selected else 0, rating, str(chat_id)))
        return self.channels()

    # --- импорт ---
    def sync(self, report):
        chans = db.query("SELECT * FROM channels WHERE platform='telegram' AND selected=1")
        if not chans:
            raise AuthError("Не выбрано ни одного канала")
        b = self.backend()
        previews = sub("previews")
        for k, ch in enumerate(chans):
            base, span = k / len(chans), 1 / len(chans)
            name = ch["title"] or ch["chat_id"]
            top = self._run(b.latest_id(ch["chat_id"]), 60) or 1
            last = ch["last_msg_id"] or 0
            start = last
            while True:
                batch = self._run(b.messages(ch["chat_id"], last, BATCH, previews, ch["username"]))
                if not batch:
                    break
                self._save(ch, batch)
                last = max(m["msg_id"] for m in batch)
                with db.tx() as c:
                    c.execute("UPDATE channels SET last_msg_id=? WHERE platform='telegram' AND chat_id=?", (last, ch["chat_id"]))
                frac = (last - start) / max(top - start, 1)
                report(progress=base + span * 0.85 * min(frac, 1), message=f"«{name}»: импортировано до сообщения {last} из {top}")
            report(progress=base + span * 0.88, message=f"«{name}»: обновляю цифры свежих постов…")
            self._refresh_recent(ch, lambda done, total: report(
                progress=base + span * (0.88 + 0.12 * done / max(total, 1)),
                message=f"«{name}»: обновляю цифры постов за последние {REFRESH_DAYS} дней — {done} из {total}"))
            with db.tx() as c:
                c.execute("UPDATE channels SET synced_at=? WHERE platform='telegram' AND chat_id=?", (_now(), ch["chat_id"]))
        total = db.query("""SELECT COUNT(*) AS n FROM posts p JOIN channels c ON c.platform=p.platform AND c.chat_id=p.chat_id
                            WHERE c.selected=1 AND p.platform='telegram'""")[0]["n"]
        report(progress=1.0, message=f"загружено {total} {_plural(total, 'пост', 'поста', 'постов')} из "
                                     f"{len(chans)} {_plural(len(chans), 'канала', 'каналов', 'каналов')}")

    def _refresh_recent(self, ch, progress=lambda done, total: None):
        since = (datetime.now(timezone.utc) - timedelta(days=REFRESH_DAYS)).isoformat()
        ids = [r["msg_id"] for r in db.query(
            """SELECT m.msg_id FROM messages m JOIN posts p ON p.id=m.post_id
               WHERE m.platform='telegram' AND m.chat_id=? AND p.date_utc>=?""", (ch["chat_id"], since))]
        step = 100
        for i in range(0, len(ids), step):
            chunk = ids[i:i + step]
            fresh = self._run(self.backend().refresh(ch["chat_id"], chunk, previews_dir=sub("previews")))
            self._save(ch, fresh, snapshot=True)
            progress(min(i + step, len(ids)), len(ids))

    def _save(self, ch, msgs: list[dict], snapshot: bool = False):
        save_messages(ch, msgs, snapshot)


def save_messages(ch, msgs: list[dict], snapshot: bool = False):
    """Сохраняет сообщения; сообщения одного альбома собираются в один пост."""
    chat, user = ch["chat_id"], ch["username"]
    touched = set()
    own = {r["chat_id"] for r in db.query("SELECT chat_id FROM channels WHERE platform='telegram'")}
    with db.tx() as c:
        for m in msgs:
            if m.get("is_forward") and m.get("fwd_chat") in own:
                m["is_forward"] = False   # пересылка из вашего же канала (SFW-копия поста из 18+) — это ваш арт
            row = c.execute("SELECT post_id FROM messages WHERE platform='telegram' AND chat_id=? AND msg_id=?",
                            (chat, m["msg_id"])).fetchone()
            post_id = row["post_id"] if row else None
            if post_id is None and m.get("grouped_id"):
                r = c.execute("SELECT id FROM posts WHERE platform='telegram' AND chat_id=? AND grouped_id=?",
                              (chat, m["grouped_id"])).fetchone()
                post_id = r["id"] if r else None
            if post_id is None:
                cur = c.execute("""INSERT INTO posts (platform, chat_id, msg_id, grouped_id, date_utc, text, media_type,
                                   link, is_forward, updated_at) VALUES ('telegram', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (chat, m["msg_id"], m.get("grouped_id"), m["date"], m.get("text") or "",
                                 m["media_type"], _link(chat, user, m["msg_id"]), int(m.get("is_forward", False)), _now()))
                post_id = cur.lastrowid
            c.execute("""INSERT INTO messages (platform, chat_id, msg_id, post_id, media_type, views, forwards, replies, reactions)
                         VALUES ('telegram', ?, ?, ?, ?, ?, ?, ?, ?)
                         ON CONFLICT(platform, chat_id, msg_id) DO UPDATE SET views=excluded.views,
                           forwards=excluded.forwards, replies=excluded.replies, reactions=excluded.reactions""",
                      (chat, m["msg_id"], post_id, m["media_type"], m.get("views"), m.get("forwards"),
                       m.get("replies"), m.get("reactions")))
            if m.get("text"):
                c.execute("UPDATE posts SET text=? WHERE id=? AND (text IS NULL OR text='')", (m["text"], post_id))
            if m.get("preview"):
                c.execute("UPDATE posts SET preview=? WHERE id=? AND preview IS NULL", (m["preview"], post_id))
            touched.add(post_id)
        for pid in touched:
            c.execute("""UPDATE posts SET
                views=(SELECT MAX(views) FROM messages WHERE post_id=?),
                forwards=(SELECT MAX(forwards) FROM messages WHERE post_id=?),
                replies=(SELECT SUM(replies) FROM messages WHERE post_id=?),
                reactions=(SELECT SUM(reactions) FROM messages WHERE post_id=?),
                n_media=(SELECT COUNT(*) FROM messages WHERE post_id=? AND media_type!='text'),
                media_type=COALESCE((SELECT media_type FROM messages WHERE post_id=? AND media_type!='text'
                                     ORDER BY msg_id LIMIT 1), 'text'),
                msg_ids=(SELECT GROUP_CONCAT(msg_id) FROM messages WHERE post_id=?),
                msg_id=(SELECT MIN(msg_id) FROM messages WHERE post_id=?),
                updated_at=? WHERE id=?""", (pid,) * 8 + (_now(), pid))
            if snapshot:
                c.execute("""INSERT INTO snapshots (post_id, taken_at, views, forwards, replies, reactions)
                             SELECT id, ?, views, forwards, replies, reactions FROM posts WHERE id=?""", (_now(), pid))
