"""Бот Telegram через MTProto (Telethon) — для сетей, где api.telegram.org недоступен.

Bot API — это HTTPS к api.telegram.org, а его в некоторых сетях режут целиком. Тот же бот умеет работать
по протоколу MTProto — так же, как Telegram Desktop и импорт статистики (с теми же настройками соединения:
обфускация, прокси, zapret). Нужны api_id и api_hash — те же, что для импорта.

Возможности те же: альбомы, спойлер для 18+, GIF/видео, PNG-оригиналы ответом или в комментарии,
пересылка в тему группы.
"""
import asyncio
import io
import random

from core.common import PlatformError


def _bio(data: bytes, name: str):
    f = io.BytesIO(data)
    f.name = name
    return f


def _locked(e) -> bool:
    return "database is locked" in str(e) or "database is locked" in str(getattr(e, "__cause__", "") or "")


class MtBot:
    """Живёт в потоке событий TgService (там же, где клиент импорта)."""

    PRE_FRESH = 4 * 3600   # заранее залитый файл используем, если ему меньше 4 часов (Telegram хранит части дольше)

    def __init__(self, tg_service, token: str):
        self.tg = tg_service
        self.token = token
        self.client = None
        self._lock = None      # одно подключение на файл сессии: два клиента на одном файле → «database is locked»
        self._made = []        # все когда-либо открытые клиенты — чтобы при сбое закрыть и «потерянные»
        self.resent: dict = {}  # (чат, пост) → {номер пачки: итог}: какие пачки PNG уже ушли
        self.resume: dict = {}  # (ключ файла, размер) → {"file_id", "next"}: докачка большого файла после обрыва
        self.pre: dict = {}    # id файла поста → {"fut", "at", "size", "name", "frac"} — PNG, залитые заранее

    # --- заранее залить PNG-оригинал, пока пишется пост ---
    def preupload(self, key: str, data: bytes, name: str):
        """В фоне: файл уходит на сервер Telegram, но никуда не публикуется. Если картинку уберут —
        он просто не пригодится и Telegram сам его удалит."""
        import time
        old = self.pre.get(key)
        if old and old["size"] == len(data) and not (old["fut"].done() and old["fut"].exception()) \
                and time.time() - old["at"] < self.PRE_FRESH:
            return
        rec = {"at": time.time(), "size": len(data), "name": name, "frac": 0.0}

        async def go():
            c = await self._client()

            def cb(cur, total):
                rec["frac"] = cur / total if total else 0.0
            return await self._upload_resumable(c, key, data, name, cb)
        rec["fut"] = asyncio.run_coroutine_threadsafe(go(), self.tg._loop)
        self.pre[key] = rec

    BIG = 10 * 1024 * 1024    # с этого размера Telegram принимает файл по частям (SaveBigFilePart) — их можно докачивать
    PART = 512 * 1024
    WINDOW = 4                # частей одновременно

    async def _upload_resumable(self, c, key, data: bytes, name: str, cb=None):
        """Файл → сервер Telegram с докачкой: при обрыве следующая попытка продолжит с первой непринятой части,
        а не с нуля (для слабых каналов это главное). Мелкие файлы (до 10 МБ) — обычной загрузкой Telethon."""
        import random
        from telethon.tl import functions, types
        size = len(data)
        if size <= self.BIG:
            return await c.upload_file(_bio(data, name), file_name=name, progress_callback=cb)
        parts = (size + self.PART - 1) // self.PART
        st = self.resume.get((key, size))
        if st is None or st["parts"] != parts:
            st = self.resume[(key, size)] = {"file_id": random.randrange(-2**63, 2**63), "next": 0, "parts": parts}

        async def one(i):
            ok = await c(functions.upload.SaveBigFilePartRequest(
                file_id=st["file_id"], file_part=i, file_total_parts=parts, bytes=data[i * self.PART:(i + 1) * self.PART]))
            if not ok:
                raise RuntimeError("Telegram не принял часть файла")
        while st["next"] < parts:
            batch = range(st["next"], min(st["next"] + self.WINDOW, parts))
            await asyncio.gather(*(one(i) for i in batch))
            st["next"] = batch[-1] + 1   # продвигаемся только когда принято всё окно целиком
            if cb:
                cb(min(st["next"] * self.PART, size), size)
        self.resume.pop((key, size), None)
        return types.InputFileBig(id=st["file_id"], parts=parts, name=name)

    def pre_state(self) -> dict:
        out = {}
        for k, r in self.pre.items():
            f = r["fut"]
            out[k] = "ready" if f.done() and not f.exception() else "error" if f.done() else round(r["frac"], 2)
        return out

    def pre_error(self, key) -> str:
        """Почему не залился файл (для решения «повторять или это не лечится повтором»)."""
        r = self.pre.get(key)
        if r and r["fut"].done() and not r["fut"].cancelled() and r["fut"].exception():
            e = r["fut"].exception()
            return f"{type(e).__name__}: {e}"
        return ""

    def forget_pre(self, keep: set):
        for k in [k for k in self.pre if k not in keep]:
            f = self.pre.pop(k)["fut"]
            if not f.done():
                f.cancel()

    async def _pre_file(self, key, size: int, log):
        """Готовый заранее залитый файл (дождётся, если ещё заливается) или None."""
        import time
        r = self.pre.get(key)
        if not r or r["size"] != size or time.time() - r["at"] > self.PRE_FRESH:
            return None
        f = r["fut"]
        if not f.done():
            log(f"дожидаюсь PNG, залитого заранее ({int(r['frac'] * 100)}%)")
        try:
            return await asyncio.wrap_future(f)
        except Exception:
            return None

    # --- служебное ---
    def run(self, coro, timeout=600, retry=False):
        """coro — корутина или функция, которая её создаёт (тогда при занятой сессии можно повторить сразу).
        retry=True — только для запросов без последствий (проверки): публикацию повторять сами не будем, чтобы не задвоить пост."""
        fn = coro if callable(coro) else None
        try:
            return self.tg._run(fn() if fn else coro, timeout, user=False)
        except Exception as e:
            if not _locked(e):
                raise
            try:   # файл сессии держит другое подключение — закрываем все и начинаем заново
                self.tg._run(self._reset(), 30, user=False)
            except Exception:
                pass
            if fn and retry:
                return self.tg._run(fn(), timeout, user=False)
            raise PlatformError("Файл сессии бота Telegram был занят вторым подключением. Подключение пересоздано — "
                                "нажмите «Повторить неудачные»") from e

    async def _reset(self):
        for c in self._made:
            try:
                await c.disconnect()
            except Exception:
                pass
        self._made = []
        self.client = None

    async def _client(self):
        if self._lock is None:
            self._lock = asyncio.Lock()   # создаётся в потоке событий Telegram — там же, где работают все корутины
        async with self._lock:   # две картинки заливаются одновременно — клиент всё равно создаётся один
            return await self._client_locked()

    async def _client_locked(self):
        import json as _json
        sig = _json.dumps((self.tg.net() or {}).get("proxy") or {}, sort_keys=True)
        if self.client is not None and getattr(self, "_net_sig", sig) != sig:   # прокси сменился (вручную или запасной) — новый клиент
            await self._reset()
        self._net_sig = sig
        if self.client is not None:
            if not self.client.is_connected():
                await self.client.connect()
            return self.client
        from telethon import TelegramClient
        from app.paths import data_dir
        from app.telegram.backend import client_kwargs
        keys = self.tg.keys()
        if not keys:
            raise PlatformError("Для связи с Telegram по MTProto нужны api_id и api_hash — «Настройки» → «Статистика и тренды» → «Telegram: статистика» → «Подключение через API»")
        import os
        name = os.environ.get("CROSSPOST_BOT_SESSION", "telegram_bot")
        sess = data_dir() / name
        marker = data_dir() / f"{name}.id"
        bot_id = self.token.split(":")[0]
        if marker.exists() and marker.read_text().strip() != bot_id:   # сменили токен — старая сессия не подходит
            for f in data_dir().glob(f"{name}.session*"):
                f.unlink(missing_ok=True)
        c = TelegramClient(str(sess), int(keys["api_id"]), keys["api_hash"], device_model="Crosspost", app_version="0.3",
                           **client_kwargs(self.tg.net()))
        self._made.append(c)
        await asyncio.wait_for(c.connect(), 40)
        if not await c.is_user_authorized():
            await c.sign_in(bot_token=self.token)
        marker.write_text(bot_id)
        self.client = c
        return c

    async def close(self):
        await self._reset()

    @staticmethod
    def _peer_arg(chat: str):
        s = str(chat)
        return int(s) if s.lstrip("-").isdigit() else s

    @staticmethod
    def _messages_from(result) -> list:
        from telethon.tl import types
        msgs = [u.message for u in getattr(result, "updates", [])
                if isinstance(u, (types.UpdateNewChannelMessage, types.UpdateNewMessage))]
        return sorted(msgs, key=lambda m: m.id)

    # --- проверки ---
    def me(self) -> dict:
        async def go():
            c = await self._client()
            u = await c.get_me()
            return {"id": u.id, "username": u.username, "name": u.first_name}
        return self.run(go, 90, retry=True)

    def check_chat(self, chat: str, need_post: bool = True) -> dict:
        async def go():
            from telethon.tl import functions
            c = await self._client()
            try:
                ent = await c.get_entity(self._peer_arg(chat))
            except Exception as e:  # noqa: BLE001
                raise PlatformError(f"Бот не видит {chat}: добавьте его админом и проверьте адрес ({e})")
            is_channel = bool(getattr(ent, "broadcast", False))
            problems = []
            try:
                perms = await c.get_permissions(ent, "me")
                if not perms.is_admin:
                    problems.append("бот не админ")
                elif is_channel and need_post and not getattr(perms, "post_messages", True):
                    problems.append("у бота нет права публиковать сообщения")
            except Exception:
                problems.append("не удалось проверить права бота")
            linked = None
            if is_channel:
                try:
                    full = await c(functions.channels.GetFullChannelRequest(channel=ent))
                    linked = getattr(full.full_chat, "linked_chat_id", None)
                except Exception:
                    pass
            cid = f"-100{ent.id}"
            return {"id": cid, "title": getattr(ent, "title", str(chat)), "username": getattr(ent, "username", None),
                    "type": "channel" if is_channel else "supergroup", "is_forum": bool(getattr(ent, "forum", False)),
                    "linked_chat_id": f"-100{linked}" if linked else None, "ok": not problems, "problems": problems}
        return self.run(go, 120, retry=True)

    # --- публикация ---
    def send_to_channel(self, cfg: dict, ch: dict, media: dict, caption_html: str, mature: bool, log, progress=None, on_posted=None) -> dict:
        progress = progress or (lambda f: None)
        # сколько байт загружаем: сам пост (0…0.6 прогресса) и оригиналы (0.6…1)
        post_bytes = sum(len(p.data) for p in media.get("photos", [])) or (media["file"].stat().st_size if media.get("file") else 1)
        orig_bytes = sum(len(o.data) for o in media.get("originals") or []) or 1
        sent = {"post": 0, "orig": 0}

        def up_cb(kind, base_done):
            def cb(cur, total):
                sent[kind] = base_done + cur
                if kind == "post":
                    progress(0.05 + 0.55 * min(sent["post"] / post_bytes, 1))
                else:
                    progress(0.6 + 0.4 * min(sent["orig"] / orig_bytes, 1))
            return cb
        async def go():
            from telethon.extensions import html as tl_html
            from telethon.tl import functions, types
            from telethon import utils
            c = await self._client()
            peer = await c.get_input_entity(self._peer_arg(ch["id"]))
            spoiler = bool(mature and cfg.get("spoiler_nsfw", True))
            text, entities = tl_html.parse(caption_html or "")
            catcher = None
            if media.get("originals") and ch.get("originals") == "comments":
                catcher = await self._start_catch(c, peer)   # ловим автокопию поста в обсуждении — до отправки
                if catcher:
                    holder.append(catcher[1])
            if len(text) > 1024:   # длинный текст — отдельным сообщением перед медиа
                await c.send_message(peer, caption_html, parse_mode="html")
                text, entities = "", []

            async def media_of(data: bytes, name: str, **kw):
                _, fm, _ = await c._file_to_media(_bio(data, name), progress_callback=up_cb("post", sent["post"]), **kw)
                if spoiler and hasattr(fm, "spoiler"):
                    fm.spoiler = True
                return fm

            if media["kind"] == "images":
                log("отправляю картинки")
                photos = media["photos"]
                if len(photos) == 1:
                    fm = await media_of(photos[0].data, photos[0].name)
                    res = await c(functions.messages.SendMediaRequest(peer=peer, media=fm, message=text, entities=entities or None,
                                                                      random_id=random.randrange(-2**63, 2**63)))
                else:
                    multi = []
                    for i, p in enumerate(photos):
                        fm = await media_of(p.data, p.name)
                        r = await c(functions.messages.UploadMediaRequest(peer=peer, media=fm))
                        im = utils.get_input_media(r.photo)
                        if spoiler:
                            im.spoiler = True
                        multi.append(types.InputSingleMedia(media=im, random_id=random.randrange(-2**63, 2**63),
                                                            message=text if i == 0 else "", entities=(entities or None) if i == 0 else None))
                    res = await c(functions.messages.SendMultiMediaRequest(peer=peer, multi_media=multi))
            else:
                f = media["file"]
                log("отправляю " + ("GIF" if media["kind"] == "gif" else "видео"))
                inf = media.get("info") or {}
                if media["kind"] == "gif":   # анимация, а не файл
                    attrs = [types.DocumentAttributeAnimated(), types.DocumentAttributeFilename(media.get("name") or f.name)]
                else:
                    attrs = [types.DocumentAttributeVideo(duration=float(inf.get("duration") or 0), w=int(inf.get("width") or 0),
                                                          h=int(inf.get("height") or 0), supports_streaming=True),
                             types.DocumentAttributeFilename(media.get("name") or f.name)]
                fm = await media_of(f.read_bytes(), media.get("name") or f.name, supports_streaming=True, attributes=attrs)
                res = await c(functions.messages.SendMediaRequest(peer=peer, media=fm, message=text, entities=entities or None,
                                                                  random_id=random.randrange(-2**63, 2**63)))
            msgs = self._messages_from(res)
            if not msgs:
                raise PlatformError("Telegram не вернул отправленное сообщение")
            first = msgs[0]
            cid = f"-100{first.peer_id.channel_id}" if hasattr(first.peer_id, "channel_id") else str(ch["id"])
            uname = getattr(await c.get_entity(peer), "username", None)
            link = f"https://t.me/{uname}/{first.id}" if uname else f"https://t.me/c/{cid[4:]}/{first.id}"
            out = {"link": link, "chat_id": cid, "message_ids": [m.id for m in msgs], "note": ""}
            if on_posted:   # пост уже в канале — остальным местам (пересылка в группу) можно не ждать оригиналов
                try:
                    on_posted(dict(out))
                except Exception:
                    pass

            originals = media.get("originals") or []
            mode = ch.get("originals", "reply")
            if originals and mode != "none":
                silent = cfg.get("silent_originals", True)
                keys = media.get("original_ids") or []
                files, ready = [], 0
                for i, o in enumerate(originals):   # залитые заранее — без повторной заливки
                    pre = await self._pre_file(keys[i], len(o.data), log) if i < len(keys) else None
                    files.append(pre if pre is not None else _bio(o.data, o.name))
                    ready += pre is not None
                if ready:
                    out["preuploaded"] = ready

                async def send(target, chunk, **kw):
                    """Пачка до 10 файлов. Если Telegram уже забыл заранее залитый файл — зальём заново."""
                    has_pre = any(not hasattr(f, "getbuffer") for f in chunk)
                    cbk = None if has_pre else self._multi_cb(chunk, up_cb, sent)
                    try:
                        return await c.send_file(target, chunk, force_document=True, silent=silent, progress_callback=cbk, **kw)
                    except Exception as e:  # noqa: BLE001
                        if not has_pre or "FILE_PART" not in str(e).upper().replace(" ", "_") and "FilePart" not in type(e).__name__:
                            raise
                        idx = [files.index(f) for f in chunk]
                        again = [_bio(originals[j].data, originals[j].name) for j in idx]
                        return await c.send_file(target, again, force_document=True, silent=silent,
                                                 progress_callback=self._multi_cb(again, up_cb, sent), **kw)
                try:
                    if mode == "comments":
                        log("жду копию поста в обсуждении")
                        target = await self._discussion_copy(c, peer, first.id, catcher, float(cfg.get("comments_wait_seconds", 60)))
                        if target:
                            gpeer, gid = target
                            log("кладу PNG-оригиналы в комментарии")
                            for k in range(0, len(files), 10):
                                await send(gpeer, files[k:k + 10], reply_to=gid)
                            out["note"] = f"{len(files)} PNG в комментариях"
                        else:
                            for k in range(0, len(files), 10):
                                await send(peer, files[k:k + 10], reply_to=first.id)
                            out["note"] = f"копия поста в обсуждении не нашлась — {len(files)} PNG ответом на пост"
                    else:
                        log("кладу PNG-оригиналы ответом на пост")
                        for k in range(0, len(files), 10):
                            await send(peer, files[k:k + 10], reply_to=first.id)
                        out["note"] = f"{len(files)} PNG ответом на пост"
                except Exception as e:  # noqa: BLE001 — пост уже вышел, повторять нельзя
                    out["note"] = f"⚠ пост вышел, но PNG не отправлены: {e} — их можно дослать кнопкой «Дослать PNG»"
                    out["originals_failed"] = True
            return out

        holder = []
        try:
            return self.run(go(), 4 * 3600)
        finally:
            if holder and self.client is not None:
                self.client.remove_event_handler(holder[0])

    def send_originals(self, cfg: dict, mode: str, originals: list, chat_id, first_id: int, keys: list, log) -> dict:
        """PNG к уже вышедшему посту (без нового поста). comments — в комментарии через comment_to, иначе ответом."""
        silent = cfg.get("silent_originals", True)

        async def go():
            c = await self._client()
            peer = await c.get_input_entity(self._peer_arg(chat_id))
            files = []
            for i, o in enumerate(originals):   # большие файлы — с докачкой: следующая попытка продолжит, а не начнёт с нуля
                files.append(await self._upload_resumable(c, f"re:{chat_id}:{first_id}:{i}", o.data, o.name))
            note = f"{len(files)} PNG ответом на пост"
            sent = self.resent.setdefault((str(chat_id), first_id), {})   # пачка → как ушла; уже ушедшая при повторе не дублируется
            for k in range(0, len(files), 10):
                chunk = files[k:k + 10]
                if k in sent:
                    note = sent[k]
                    continue
                if mode == "comments":
                    try:
                        await c.send_file(peer, chunk, force_document=True, silent=silent, comment_to=first_id)
                        note = sent[k] = f"{len(files)} PNG в комментариях"
                        continue
                    except OSError:
                        raise
                    except Exception as e:  # noqa: BLE001 — у канала нет обсуждения/копии: отвечаем в самом канале
                        if "ConnectionError" in type(e).__name__ or "Timeout" in type(e).__name__:
                            raise
                        note = f"{len(files)} PNG ответом на пост (комментарии недоступны)"
                await c.send_file(peer, chunk, force_document=True, silent=silent, reply_to=first_id)
                sent[k] = note
            self.resent.pop((str(chat_id), first_id), None)
            for i in range(len(originals)):
                self.resume.pop((f"re:{chat_id}:{first_id}:{i}", len(originals[i].data)), None)
            return {"note": note, "ok": True}
        try:
            return self.run(go(), 4 * 3600)
        except Exception as e:  # noqa: BLE001
            return {"note": f"⚠ PNG не отправлены: {e}", "ok": False}

    @staticmethod
    def _multi_cb(files, up_cb, sent):
        """send_file со списком файлов зовёт progress_callback(номер_файла + доля, всего_файлов)."""
        sizes = [len(f.getbuffer()) for f in files]
        base = sent["orig"]

        def cb(cur, total):
            i = int(cur) if cur < len(sizes) else len(sizes)
            done = sum(sizes[:i]) + (sizes[i] * (cur - i) if i < len(sizes) else 0)
            up_cb("orig", base)(done, sum(sizes))
        return cb

    # --- комментарии: найти копию поста в группе обсуждения ---
    async def _start_catch(self, c, peer):
        from telethon import events
        from telethon.tl import functions
        try:
            full = await c(functions.channels.GetFullChannelRequest(channel=peer))
            linked = getattr(full.full_chat, "linked_chat_id", None)
        except Exception:
            return None
        if not linked:
            return None
        seen = []

        async def on_msg(ev):
            seen.append(ev.message)
        c.add_event_handler(on_msg, events.NewMessage(chats=[int(f"-100{linked}")]))
        return (linked, on_msg, seen)

    async def _discussion_copy(self, c, peer, msg_id, catcher, wait):
        from telethon.tl import functions
        try:   # самый быстрый путь, если боту разрешено
            r = await c(functions.messages.GetDiscussionMessageRequest(peer=peer, msg_id=msg_id))
            if r.messages:
                return await c.get_input_entity(int(f"-100{catcher[0]}")) if catcher else r.chats[0], r.messages[0].id
        except Exception:
            pass
        if not catcher:
            return None
        linked, _, seen = catcher
        deadline = asyncio.get_event_loop().time() + wait
        while asyncio.get_event_loop().time() < deadline:
            for m in seen:
                f = getattr(m, "fwd_from", None)
                if f and (getattr(f, "channel_post", None) == msg_id or getattr(f, "saved_from_msg_id", None) == msg_id):
                    return await c.get_input_entity(int(f"-100{linked}")), m.id
            await asyncio.sleep(1)
        return None

    def forward_to_topic(self, group_id: str, topic: int, from_chat: str, message_ids: list[int]) -> str:
        async def go():
            from telethon.tl import functions
            c = await self._client()
            to_peer = await c.get_input_entity(self._peer_arg(group_id))
            from_peer = await c.get_input_entity(self._peer_arg(from_chat))
            kw = {"top_msg_id": int(topic)} if topic else {}
            res = await c(functions.messages.ForwardMessagesRequest(
                from_peer=from_peer, id=list(message_ids), to_peer=to_peer,
                random_id=[random.randrange(-2**63, 2**63) for _ in message_ids], **kw))
            msgs = self._messages_from(res)
            gid = str(group_id)
            if msgs and gid.startswith("-100"):
                return f"https://t.me/c/{gid[4:]}/{(str(topic) + '/') if topic else ''}{msgs[0].id}"
            return "переслано"
        return self.run(go(), 300)
