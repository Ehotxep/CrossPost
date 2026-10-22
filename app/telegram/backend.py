"""Работа с Telegram как пользователь (библиотека Telethon) — только чтение своих каналов.

Всё, что специфично для Telethon, собрано здесь. Остальное приложение видит простые словари,
поэтому логику импорта можно проверять на имитации без настоящего Telegram.
"""
import asyncio
import os
import time
from pathlib import Path


class NeedPassword(Exception):
    """Включена двухэтапная проверка — нужен облачный пароль."""


class AuthError(Exception):
    """Понятная ошибка входа для показа пользователю."""


def _reactions_total(m) -> int:
    r = getattr(m, "reactions", None)
    if not r or not getattr(r, "results", None):
        return 0
    return int(sum(getattr(x, "count", 0) or 0 for x in r.results))


def _media_type(m) -> str:
    if getattr(m, "photo", None):
        return "photo"
    if getattr(m, "gif", None):
        return "gif"
    if getattr(m, "video", None):
        return "video"
    if getattr(m, "document", None):
        return "document"
    return "text"


def _pick_size(sizes, target=640):
    """Из размеров фото/миниатюр выбираем ближайший к 640 px — хватает и для превью, и для теггера."""
    cand = [s for s in (sizes or []) if getattr(s, "w", None)]
    if not cand:
        return None
    return min(cand, key=lambda s: abs(s.w - target))


def faketls_ready() -> bool:
    try:
        import TelethonFakeTLS  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _secret_hex(s: str) -> str:
    """Секрет MTProxy: hex (dd…, ee…, 32 символа) или base64url, как в новых ссылках Telegram → hex."""
    import base64
    import re as _re
    s = (s or "").strip()
    if _re.fullmatch(r"[0-9a-fA-F]{32,}", s):
        return s.lower()
    try:
        raw = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        return raw.hex()
    except Exception:  # noqa: BLE001
        return s.lower()


def parse_proxy_links(text: str) -> list[dict]:
    """Ссылки на прокси из Telegram → настройки: tg://proxy?server=…&port=…&secret=…, https://t.me/proxy?…,
    tg://socks?server=…&port=…&user=…&pass=…, https://t.me/socks?… Можно несколько — по одной в строке или через пробел."""
    import re as _re
    from urllib.parse import parse_qs, unquote, urlsplit
    out, seen = [], set()
    for raw in _re.findall(r"(?:tg://|https?://(?:www\.)?(?:t\.me|telegram\.me)/)(?:proxy|socks)\?[^\s<>\"']+", text or "", _re.I):
        sp = urlsplit(raw.replace("tg://", "tg://x/") if raw.lower().startswith("tg://") else raw)
        kind = "socks5" if "socks" in (sp.path.lower() + raw[:12].lower()) else "mtproxy"
        q = {k.lower(): unquote(v[0]) for k, v in parse_qs(sp.query, keep_blank_values=True).items()}
        host, port = (q.get("server") or "").strip(), (q.get("port") or "").strip()
        if not host or not port.isdigit():
            continue
        p = {"type": kind, "host": host, "port": int(port)}
        if kind == "mtproxy":
            if not q.get("secret"):
                continue
            p["secret"] = _secret_hex(q["secret"])
        else:
            p["user"], p["password"] = q.get("user", ""), q.get("pass", "")
        key = (p["type"], p["host"], p["port"], p.get("secret", ""))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def client_kwargs(net: dict | None) -> dict:
    """Как подключаться к Telegram.

    transport: "obfuscated" — шифрованный «шум», как у Telegram Desktop (его DPI распознаёт хуже всего);
               "full" — обычный протокол MTProto.
    proxy: {"type": "socks5"|"http"|"mtproxy", "host", "port", "user", "password", "secret"} —
           например, локальный прокси от zapret-набора или тот же прокси, что стоит в Telegram Desktop.
    """
    from telethon import connection
    net = net or {}
    kw = {"timeout": 10, "connection_retries": 2, "retry_delay": 1, "request_retries": 3}
    p = net.get("proxy") or {}
    t = p.get("type") or ""
    if t == "mtproxy":
        secret = (p.get("secret") or "").strip().lower()
        if secret.startswith("ee"):   # FakeTLS: трафик выглядит как обычный HTTPS — нужна дополнительная библиотека
            if not faketls_ready():
                raise AuthError("Этот MTProxy — FakeTLS (секрет на ee…). Для него нужна библиотека TelethonFakeTLS — "
                                "запустите Install.bat ещё раз (он её поставит). Или выберите прокси с секретом dd… / SOCKS5.")
            import TelethonFakeTLS
            kw["connection"] = TelethonFakeTLS.ConnectionTcpMTProxyFakeTLS
            kw["proxy"] = (p["host"], int(p["port"]), secret[2:])   # библиотеке — без приставки ee
            return kw
        kw["connection"] = connection.ConnectionTcpMTProxyRandomizedIntermediate
        kw["proxy"] = (p["host"], int(p["port"]), secret)
        return kw
    kw["connection"] = connection.ConnectionTcpFull if net.get("transport") == "full" else connection.ConnectionTcpObfuscated
    if t in ("socks5", "http"):
        try:
            import python_socks  # noqa: F401
        except ImportError:
            raise AuthError("Для прокси нужна библиотека python-socks — запустите Install.bat ещё раз")
        kw["proxy"] = {"proxy_type": t, "addr": p["host"], "port": int(p["port"]), "rdns": True,
                       "username": p.get("user") or None, "password": p.get("password") or None}
    return kw


class CodeAlreadySent(AuthError):
    """Код уже отправлен — показываем поле для кода, а не ошибку."""


class SessionLock:
    """Один вход в Telegram — одно подключение. Окно приложения и фоновая отправка — два процесса с одним ключом
    входа; если они подключены одновременно (тем более через разные сети — VPN, прокси), Telegram считает ключ
    украденным и аннулирует его («used under two different IP addresses»). Поэтому подключаемся по очереди:
    кто подключён — держит этот замок, остальные ждут, пока он отключится."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.f = None

    def try_acquire(self) -> bool:
        if self.f is not None:
            return True
        f = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self.f = f
        return True

    def release(self):
        f, self.f = self.f, None
        if f is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        f.close()

    @property
    def held(self) -> bool:
        return self.f is not None


class TelethonBackend:
    LOCK_WAIT = 90   # сколько ждать, пока другая часть Crosspost отключится от Telegram

    def __init__(self, session_path: Path, api_id: int, api_hash: str, net: dict | None = None):
        try:
            from telethon import TelegramClient
        except ImportError:
            raise AuthError("Не установлена библиотека telethon — запустите Install.bat ещё раз")
        self.client = TelegramClient(str(session_path), int(api_id), api_hash,
                                     device_model="Crosspost", app_version="0.2", **client_kwargs(net))
        self._phone = None
        self._hash = None
        # запрос кода переживает перезапуск приложения: иначе код, уже пришедший в Telegram, нельзя ввести,
        # а новый Telegram после нескольких запросов не присылает
        self._pending = Path(str(session_path)).with_name(Path(str(session_path)).name + ".pending.json")
        try:
            import json as _json
            p = _json.loads(self._pending.read_text(encoding="utf-8"))
            if time.time() - p.get("at", 0) < 900:
                self._phone, self._hash, self._sent_at = p["phone"], p.get("hash"), p["at"]
        except (OSError, ValueError, KeyError):
            pass
        self.slock = SessionLock(Path(str(session_path)).with_name(Path(str(session_path)).name + ".lock"))

    async def ping(self):
        """Проверка связи: соединение + один лёгкий запрос к серверу."""
        from telethon import functions
        await asyncio.wait_for(self.connect(), 30)
        await asyncio.wait_for(self.client(functions.help.GetNearestDcRequest()), 20)

    async def close(self):
        try:
            await self.client.disconnect()
        except Exception:
            pass
        self.slock.release()

    async def connect(self):
        if not self.client.is_connected():
            await self._acquire()
            try:
                await self.client.connect()
            except BaseException:
                self.slock.release()   # не подключились — не держим очередь
                raise

    async def _acquire(self):
        deadline = time.time() + self.LOCK_WAIT
        while not self.slock.try_acquire():
            if time.time() > deadline:
                raise AuthError("Вход в Telegram сейчас занят другой частью Crosspost (окном или фоновой отправкой) — "
                                "повторите через минуту")
            await asyncio.sleep(1)

    async def is_authorized(self) -> bool:
        await self.connect()
        return await self.client.is_user_authorized()

    async def send_code(self, phone: str):
        from telethon import errors
        await self.connect()
        # Повторный запрос на тот же номер Telegram считает «отправить код ещё раз» — и быстро исчерпывает способы.
        # Код действует несколько минут, поэтому в это время просто ждём ввода уже отправленного.
        if self._phone == phone and time.time() - getattr(self, "_sent_at", 0) < 600:
            raise CodeAlreadySent("Код уже отправлен несколько минут назад — введите его. Он приходит в само приложение "
                                  "Telegram, в чат «Telegram» (на телефоне или компьютере), а не по SMS.")
        try:
            sent = await self.client.send_code_request(phone)
            self._phone = phone
            self._sent_at = time.time()
            self._hash = getattr(sent, "phone_code_hash", None)
            try:
                import json as _json
                self._pending.write_text(_json.dumps({"phone": phone, "hash": self._hash, "at": self._sent_at}), encoding="utf-8")
            except OSError:
                pass
        except errors.PhoneNumberInvalidError:
            raise AuthError("Неверный номер телефона. Формат: +79991234567")
        except errors.FloodWaitError as e:
            raise AuthError(f"Telegram просит подождать {e.seconds} с перед следующей попыткой")
        except errors.ApiIdInvalidError:
            raise AuthError("Неверные api_id / api_hash — проверьте их на my.telegram.org")
        except Exception as e:
            if type(e).__name__ == "SendCodeUnavailableError":   # все способы повторной отправки уже использованы
                self._phone = phone
                raise CodeAlreadySent("Код уже отправлен — повторно Telegram его пока не пришлёт. Найдите его в приложении "
                                      "Telegram: чат «Telegram» на телефоне или компьютере (не SMS). Если кода нет — "
                                      "подождите 10–15 минут и запросите снова.")
            raise

    async def sign_in(self, code: str):
        from telethon import errors
        if not self._phone:
            raise AuthError("Сначала введите номер и нажмите «Получить код»")
        await self.connect()
        kw = {"phone_code_hash": self._hash} if self._hash else {}
        try:
            await self.client.sign_in(phone=self._phone, code="".join(ch for ch in code if ch.isdigit()), **kw)
        except errors.SessionPasswordNeededError:
            self._done_pending()
            raise NeedPassword()
        except errors.PhoneCodeInvalidError:
            raise AuthError("Неверный код. Нужен последний код из чата «Telegram» с текстом «используется для входа» — "
                            "коды для сайта my.telegram.org сюда не подходят")
        except errors.PhoneCodeExpiredError:
            self._done_pending()
            raise AuthError("Код устарел — запросите новый (один раз) и введите его")
        self._done_pending()

    def _done_pending(self):
        self._pending.unlink(missing_ok=True)

    async def sign_in_password(self, password: str):
        from telethon import errors
        try:
            await self.client.sign_in(password=password)
        except errors.PasswordHashInvalidError:
            raise AuthError("Неверный облачный пароль")

    async def me(self) -> dict:
        await self.connect()
        u = await self.client.get_me()
        name = " ".join(x for x in (u.first_name, u.last_name) if x) or (u.username or "")
        return {"name": name, "username": u.username}

    async def log_out(self):
        await self.connect()
        await self.client.log_out()

    async def channels(self) -> list[dict]:
        """Каналы и группы, где вы создатель или администратор."""
        await self.connect()
        out = []
        async for d in self.client.iter_dialogs():
            if not d.is_channel:
                continue
            e = d.entity
            if not (getattr(e, "creator", False) or getattr(e, "admin_rights", None)):
                continue
            out.append({"chat_id": str(d.id), "title": d.name, "username": getattr(e, "username", None),
                        "broadcast": bool(getattr(e, "broadcast", False)),
                        "members": getattr(e, "participants_count", None)})
        return out

    async def forum_topics(self, chat_id: str) -> list[dict]:
        """Темы группы-форума: номер и название (номер нужен боту для пересылки в тему)."""
        await self.connect()
        from telethon.tl import functions
        entity = await self.client.get_input_entity(int(chat_id))
        req = getattr(functions.messages, "GetForumTopicsRequest", None)
        if req is not None:
            res = await self.client(req(peer=entity, offset_date=None, offset_id=0, offset_topic=0, limit=100))
        else:  # старые версии Telethon
            res = await self.client(functions.channels.GetForumTopicsRequest(
                channel=entity, offset_date=None, offset_id=0, offset_topic=0, limit=100))
        return [{"id": t.id, "title": getattr(t, "title", "")} for t in res.topics if getattr(t, "title", None)]

    def _norm(self, m, username) -> dict | None:
        if getattr(m, "action", None) is not None:  # служебные сообщения (закреп, смена названия…)
            return None
        replies = getattr(getattr(m, "replies", None), "replies", None)
        fwd_chat = None
        peer = getattr(getattr(m, "fwd_from", None), "from_id", None)
        if peer is not None and getattr(peer, "channel_id", None):
            fwd_chat = f"-100{peer.channel_id}"   # откуда переслано — чтобы узнать пересылки из своих каналов
        return {
            "msg_id": m.id, "date": m.date.isoformat(), "text": m.message or "",
            "grouped_id": m.grouped_id, "media_type": _media_type(m),
            "views": m.views, "forwards": m.forwards, "replies": replies,
            "reactions": _reactions_total(m), "is_forward": bool(m.fwd_from), "fwd_chat": fwd_chat,
            "_msg": m,
        }

    async def messages(self, chat_id: str, min_id: int, limit: int, previews_dir: Path | None, username=None) -> list[dict]:
        """Следующая порция сообщений после min_id (по возрастанию), с превью первых картинок."""
        await self.connect()
        from telethon import errors
        entity = await self.client.get_input_entity(int(chat_id))
        out = []
        while True:
            try:
                async for m in self.client.iter_messages(entity, min_id=min_id, reverse=True, limit=limit):
                    n = self._norm(m, username)
                    if n:
                        out.append(n)
                break
            except errors.FloodWaitError as e:
                if e.seconds > 120:
                    raise AuthError(f"Telegram ограничил частоту запросов на {e.seconds} с — попробуйте позже")
                await asyncio.sleep(e.seconds + 1)
        if previews_dir is not None:
            await self._previews(out, chat_id, previews_dir)
        for n in out:
            n.pop("_msg", None)
        return out

    async def _previews(self, out: list, chat_id: str, previews_dir: Path):
        """Превью первой картинки каждого поста (у альбома — одна на весь альбом)."""
        seen_groups = set()
        for n in out:
            m = n.get("_msg")
            key = n["grouped_id"] or ("single", n["msg_id"])
            if m is None or key in seen_groups or n["media_type"] == "text":
                continue
            path = previews_dir / f"tg_{chat_id}_{n['msg_id']}.jpg"
            sizes = m.photo.sizes if m.photo else (getattr(m.document, "thumbs", None) if m.document else None)
            size = _pick_size(sizes)
            if size is None:
                continue
            try:
                if not path.exists():
                    await self.client.download_media(m, file=str(path), thumb=size)
                n["preview"] = path.name
                seen_groups.add(key)
            except Exception:  # превью не критично
                pass

    async def latest_id(self, chat_id: str) -> int:
        await self.connect()
        entity = await self.client.get_input_entity(int(chat_id))
        msgs = await self.client.get_messages(entity, limit=1)
        return msgs[0].id if msgs else 0

    async def refresh(self, chat_id: str, ids: list[int], previews_dir: Path | None = None) -> list[dict]:
        """Свежие просмотры/реакции для уже импортированных сообщений. previews_dir — заодно скачать превью,
        если их ещё нет (посты, впервые попавшие в базу через замеры, приходят без картинки)."""
        await self.connect()
        entity = await self.client.get_input_entity(int(chat_id))
        out = []
        for i in range(0, len(ids), 100):
            for m in await self.client.get_messages(entity, ids=ids[i:i + 100]):
                if m is not None:
                    n = self._norm(m, None)
                    if n:
                        out.append(n)
        if previews_dir is not None:
            await self._previews(out, chat_id, previews_dir)
        for n in out:
            n.pop("_msg", None)
        return out
