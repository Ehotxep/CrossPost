"""Telegram-бот для публикации: проверка бота и каналов, пост в один канал, пересылка в тему группы.

Низкоуровневые вызовы Bot API берутся из platforms/telegram.py (их же использует crosspost.py).
Публикация разбита на шаги, чтобы интерфейс показывал прогресс по каждому каналу.
"""
import json
import re

import app.publish  # noqa: F401  (путь к platforms/)
from core.common import PlatformError
from platforms import telegram as T

VIDEO_LIMIT = 50 * 1024 * 1024


class NetworkError(PlatformError):
    """api.telegram.org недоступен из этой сети."""


def call(token, method, data=None, files=None, timeout=60):
    import requests
    try:
        return T._call(token, method, data, files, timeout)
    except (requests.ConnectionError, requests.Timeout) as e:
        raise NetworkError("Нет связи с api.telegram.org" + (" (не отвечает)" if isinstance(e, requests.Timeout) or "timed out" in str(e) else ""))


def check_bot(token: str) -> dict:
    token = (token or "").strip()
    if not re.fullmatch(r"\d+:[\w-]{30,}", token):
        raise PlatformError("Токен бота выглядит как 123456789:AA… — скопируйте его из @BotFather целиком")
    me = call(token, "getMe", timeout=15)
    return {"id": me["id"], "username": me.get("username"), "name": me.get("first_name")}


def norm_chat(s) -> str:
    """@name, t.me/name, -100… → то, что понимает Bot API."""
    s = str(s or "").strip()
    m = re.match(r"(?:https?://)?t\.me/(?:c/(\d+)|([A-Za-z]\w{3,}))", s)
    if m:
        return f"-100{m.group(1)}" if m.group(1) else "@" + m.group(2)
    if re.fullmatch(r"-?\d+", s):
        return s if s.startswith("-") else f"-100{s}"
    if re.fullmatch(r"@?[A-Za-z]\w{3,}", s):
        return s if s.startswith("@") else "@" + s
    raise PlatformError(f"Не понимаю адрес канала: {s!r}. Нужен @username, ссылка t.me/… или id вида -100…")


def topic_id(s) -> int:
    """Номер темы или ссылка на неё (t.me/c/123/45 → 45)."""
    s = str(s or "").strip()
    if not s:
        return 0
    m = re.search(r"/(\d+)/?$", s)
    if m:
        return int(m.group(1))
    if s.isdigit():
        return int(s)
    raise PlatformError(f"Номер темы — число или ссылка на тему вида t.me/c/123/45, а не {s!r}")


def check_chat(token: str, chat: str, need_post: bool = True) -> dict:
    """Название чата и права бота в нём."""
    chat = norm_chat(chat)
    info = call(token, "getChat", {"chat_id": chat})
    me = call(token, "getMe")
    member = call(token, "getChatMember", {"chat_id": info["id"], "user_id": me["id"]})
    status = member.get("status")
    problems = []
    if status not in ("administrator", "creator"):
        problems.append("бот не админ")
    elif info.get("type") == "channel" and need_post and not member.get("can_post_messages", True):
        problems.append("у бота нет права публиковать сообщения")
    return {"id": str(info["id"]), "title": info.get("title") or chat, "username": info.get("username"),
            "type": info.get("type"), "is_forum": bool(info.get("is_forum")),
            "linked_chat_id": info.get("linked_chat_id"), "ok": not problems, "problems": problems}


# --- публикация ---------------------------------------------------------------

def _caption(text: str, title: str, include_title: bool):
    """Подпись в HTML: текст экранируется, ссылки [Текст](url) становятся кликабельными словами."""
    from app.publish import links
    body = links.telegram_html(text or "")
    if title and include_title:
        body = f"<b>{T._esc(title)}</b>\n{body}"
    return body.strip(), "HTML"


def send_to_channel(token: str, cfg: dict, ch: dict, media: dict, text: str, title: str, mature: bool, log, on_posted=None) -> dict:
    """Пост в один канал + оригиналы. media: {"kind": "images"|"gif"|"video", "photos", "originals", "file"}.

    Возвращает {"link", "chat_id", "message_ids", "note"}. Ошибка самого поста — исключение;
    ошибка с оригиналами — пост остаётся, в note предупреждение (повторять пост нельзя — будет дубль).
    """
    chat = norm_chat(ch["id"])
    caption, parse_mode = _caption(text, title, cfg.get("include_title", True))
    spoiler = bool(mature and cfg.get("spoiler_nsfw", True))
    import html as _h, re as _re
    long_text = len(_h.unescape(_re.sub(r"<[^>]+>", "", caption))) > T.CAPTION_LIMIT   # считаем видимые символы
    if long_text:
        call(token, "sendMessage", {"chat_id": chat, "text": caption[:4096], **({"parse_mode": parse_mode} if parse_mode else {})})
        caption = ""
    if media["kind"] == "images":
        log("отправляю картинки")
        msgs = T._send_photos(token, chat, media["photos"], caption, parse_mode, spoiler)
    else:
        f = media["file"]
        method, field = ("sendAnimation", "animation") if media["kind"] == "gif" else ("sendVideo", "video")
        data = {"chat_id": chat, "caption": caption}
        if parse_mode and caption:
            data["parse_mode"] = parse_mode
        if spoiler:
            data["has_spoiler"] = "true"
        if method == "sendVideo":
            data["supports_streaming"] = "true"
            inf = media.get("info") or {}
            for k in ("width", "height"):
                if inf.get(k):
                    data[k] = str(inf[k])
            if inf.get("duration"):
                data["duration"] = str(round(inf["duration"]))
        log("отправляю " + ("GIF" if media["kind"] == "gif" else "видео"))
        msgs = [call(token, method, data, {field: (media.get("name") or f.name, f.read_bytes(), "video/mp4" if f.suffix.lower() != ".gif" else "image/gif")}, timeout=900)]
    first = msgs[0]
    out = {"link": T._link(first), "chat_id": first["chat"]["id"], "message_ids": [m["message_id"] for m in msgs], "note": ""}
    if on_posted:   # пост уже в канале — остальным местам можно не ждать оригиналов
        try:
            on_posted(dict(out))
        except Exception:
            pass
    if media.get("originals"):
        mode = ch.get("originals", "reply")
        if mode != "none":
            log("кладу PNG-оригиналы " + ("в комментарии" if mode == "comments" else "ответом на пост"))
            tail = T._send_originals(token, cfg, dict(ch, id=chat, originals=mode), chat, first, media["originals"],
                                     cfg.get("silent_originals", True), float(cfg.get("comments_wait_seconds", 60)))
            out["note"] = tail.strip(" ·")
    return out


def forward_to_topic(token: str, group_id: str, topic: int, from_chat, message_ids: list[int]) -> str:
    data = {"chat_id": norm_chat(group_id), "from_chat_id": from_chat, "message_ids": json.dumps(message_ids)}
    if topic:
        data["message_thread_id"] = topic
    res = call(token, "forwardMessages", data)
    first = res[0] if isinstance(res, list) and res else None
    gid = str(norm_chat(group_id))
    if first and gid.startswith("-100"):
        return f"https://t.me/c/{gid[4:]}/{(str(topic) + '/') if topic else ''}{first['message_id']}"
    return "переслано"


# --- выбор способа связи: Bot API (HTTPS) или MTProto ---------------------------------

TG_SERVICE = None    # TgService приложения (задаёт api.py) — нужен для MTProto
_mt_cache = {}


def _mt(token):
    from app.publish.tg_mt import MtBot
    if token not in _mt_cache:
        for old in _mt_cache.values():
            try:
                old.run(old.close(), 10)
            except Exception:
                pass
        _mt_cache.clear()
        _mt_cache[token] = MtBot(TG_SERVICE, token)
    return _mt_cache[token]


def bot_me(token: str, transport: str) -> dict:
    if transport == "mtproto":
        if not re.fullmatch(r"\d+:[\w-]{30,}", (token or "").strip()):
            raise PlatformError("Токен бота выглядит как 123456789:AA… — скопируйте его из @BotFather целиком")
        return _mt(token.strip()).me()
    return check_bot(token)


def chat_info(cfg: dict, chat: str, need_post: bool = True) -> dict:
    if cfg.get("transport") == "mtproto":
        return _mt(cfg["bot_token"]).check_chat(norm_chat(chat), need_post)
    return check_chat(cfg["bot_token"], chat, need_post)


def publish_channel(cfg: dict, ch: dict, media: dict, text: str, title: str, mature: bool, log, progress=None, on_posted=None) -> dict:
    if cfg.get("transport") == "mtproto":
        cap, mode = _caption(text, title, cfg.get("include_title", True))
        html = cap if mode == "HTML" else T._esc(cap)
        return _mt(cfg["bot_token"]).send_to_channel(cfg, dict(ch, id=norm_chat(ch["id"])), media, html, mature, log, progress, on_posted)
    return send_to_channel(cfg["bot_token"], cfg, ch, media, text, title, mature, log, on_posted)


def forward(cfg: dict, group_id: str, topic: int, from_chat, message_ids) -> str:
    if cfg.get("transport") == "mtproto":
        return _mt(cfg["bot_token"]).forward_to_topic(norm_chat(group_id), topic, str(from_chat), message_ids)
    return forward_to_topic(cfg["bot_token"], group_id, topic, from_chat, message_ids)


# --- PNG-оригиналы заранее (только MTProto: Bot API так не умеет) ----------------------------

def can_preupload(cfg: dict) -> bool:
    return bool(cfg.get("enabled") and cfg.get("bot_token") and cfg.get("transport") == "mtproto" and TG_SERVICE is not None
                and cfg.get("preupload", True)
                and any(ch.get("originals", "reply") != "none" for ch in cfg.get("channels") or []))


def preupload(cfg: dict, files: list) -> dict:
    """files — [(id, path)]. Заливает оригиналы в фоне; возвращает состояние {id: "ready" | доля | "error"}."""
    if not can_preupload(cfg):
        return {}
    from core.images import load_original
    bot = _mt(cfg["bot_token"])
    for uid, path in files:
        try:
            o = load_original(path, T.BOT_FILE_LIMIT)
        except Exception:
            continue
        bot.preupload(uid, o.data, o.name)
    bot.forget_pre({uid for uid, _ in files})   # убранные из поста — больше не нужны
    return bot.pre_state()
