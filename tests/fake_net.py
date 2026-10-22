"""Имитация Bot API Telegram, вебхуков Discord и API ВКонтакте — для проверки публикации без сети.

Подменяет requests.post / requests.get. Все «отправленные» сообщения складываются в SENT.
FAIL = {"discord"} и т.п. — заставить площадку отвечать ошибкой.
"""
import json
import re

import requests

SENT = []
FAIL = set()
E6_TAGS = {}   # post_id → {категория: [теги]} — итоговые теги поста после модераторов
_mid = {"n": 100}
CHATS = {
    "@my_art_sfw": {"id": -1001111, "title": "Арт SFW", "type": "channel", "username": "my_art_sfw"},
    "-1001111": {"id": -1001111, "title": "Арт SFW", "type": "channel", "username": "my_art_sfw"},
    "-1002222": {"id": -1002222, "title": "Арт 18+", "type": "channel", "linked_chat_id": -1004444},
    "-1003333": {"id": -1003333, "title": "Арт-группа", "type": "supergroup", "is_forum": True},
    "-1004444": {"id": -1004444, "title": "Обсуждение 18+", "type": "supergroup"},
}
_pending_updates = []


class R:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.text = data, status, json.dumps(data, ensure_ascii=False)

    def json(self):
        return self._d


def _next():
    _mid["n"] += 1
    return _mid["n"]


def _tg(method, data, files):
    data = data or {}
    if "telegram" in FAIL and method.startswith("send"):
        return R({"ok": False, "description": "Bad Request: not enough rights to send photos to the chat"}, 400)
    if method == "getMe":
        return R({"ok": True, "result": {"id": 42, "is_bot": True, "first_name": "Арт-бот", "username": "art_poster_bot"}})
    chat = str(data.get("chat_id", ""))
    info = CHATS.get(chat)
    if method == "getChat":
        if not info:
            return R({"ok": False, "description": "Bad Request: chat not found"}, 400)
        return R({"ok": True, "result": info})
    if method == "getChatMember":
        return R({"ok": True, "result": {"status": "administrator", "can_post_messages": True}})
    if method == "getUpdates":
        ups, _pending_updates[:] = list(_pending_updates), []
        return R({"ok": True, "result": ups})
    if method in ("sendPhoto", "sendDocument", "sendMessage", "sendAnimation", "sendVideo"):
        mid = _next()
        SENT.append({"m": method, "chat": chat, "data": {k: v for k, v in data.items()}, "files": list((files or {}).keys()), "id": mid})
        msg = {"message_id": mid, "chat": info or {"id": chat}}
        if info and info.get("linked_chat_id") and method != "sendDocument":   # автокопия в обсуждение
            _pending_updates.append({"update_id": mid, "message": {
                "message_id": mid + 1000, "is_automatic_forward": True, "chat": {"id": info["linked_chat_id"]},
                "forward_origin": {"chat": {"id": info["id"]}, "message_id": mid}}})
        return R({"ok": True, "result": msg})
    if method == "sendMediaGroup":
        media = json.loads(data["media"])
        res = []
        for _ in media:
            res.append({"message_id": _next(), "chat": info or {"id": chat}})
        SENT.append({"m": method, "chat": chat, "data": {k: v for k, v in data.items() if k != "media"}, "media": media,
                     "id": res[0]["message_id"]})
        if info and info.get("linked_chat_id") and media[0]["type"] == "photo":
            _pending_updates.append({"update_id": res[0]["message_id"], "message": {
                "message_id": res[0]["message_id"] + 1000, "is_automatic_forward": True, "chat": {"id": info["linked_chat_id"]},
                "forward_origin": {"chat": {"id": info["id"]}, "message_id": res[0]["message_id"]}}})
        return R({"ok": True, "result": res})
    if method == "forwardMessages":
        ids = json.loads(data["message_ids"])
        SENT.append({"m": method, "chat": chat, "data": dict(data)})
        return R({"ok": True, "result": [{"message_id": _next()} for _ in ids]})
    return R({"ok": False, "description": f"unknown method {method}"}, 400)


SITE = []   # запросы, ушедшие на e621


def fake_post(url, data=None, files=None, params=None, timeout=None, **kw):
    if url.startswith("https://e621.net/"):   # e621 — тот же движок: отвечаем как e6ai, но помечаем сайт
        SITE.append("e621")
        url = "https://e6ai.net/" + url[len("https://e621.net/"):]
    m = re.match(r"https://api\.telegram\.org/bot[^/]+/(\w+)", url)
    if m:
        return _tg(m.group(1), data, files)
    if "discord.com/api/webhooks" in url:
        if "discord" in FAIL:
            return R({"message": "Missing Permissions", "code": 50013}, 403)
        SENT.append({"m": "discord", "url": url[-6:], "payload": json.loads(data["payload_json"]), "files": list(files.keys())})
        return R({"id": str(_next()), "channel_id": "777"})
    if url.startswith("https://api.vk.com/method/"):
        method = url.rsplit("/", 1)[1]
        if method == "docs.getWallUploadServer":
            return R({"response": {"upload_url": "https://pu.vk.com/doc"}})
        if method == "docs.save":
            return R({"response": {"type": "doc", "doc": {"owner_id": -123, "id": _next()}}})
        if method == "video.save":
            return R({"response": {"upload_url": "https://pu.vk.com/video", "video_id": _next(), "owner_id": -123}})
        if method == "photos.getWallUploadServer":
            return R({"response": {"upload_url": "https://pu.vk.com/upload"}})
        if method == "photos.saveWallPhoto":
            return R({"response": [{"owner_id": -123, "id": _next()}]})
        if method == "wall.post":
            SENT.append({"m": "vk", "data": {k: v for k, v in (data or {}).items() if k != "access_token"}})
            return R({"response": {"post_id": _next()}})
        if method == "groups.getById":
            return R({"response": {"groups": [{"id": 123, "name": "Арт-группа ВК"}]}})
        if method == "groups.get":
            return R({"response": {"count": 1, "items": [123]}})
    if url.startswith("https://bsky.social/xrpc/"):
        method = url.rsplit("/", 1)[1]
        if "bluesky" in FAIL:
            return R({"error": "InvalidToken", "message": "Token has expired"}, 400)
        if method == "com.atproto.server.createSession":
            j = kw.get("json") or {}
            if j.get("password") != "abcd-efgh-ijkl-mnop":
                return R({"error": "AuthenticationRequired", "message": "Invalid identifier or password"}, 401)
            return R({"accessJwt": "acc", "refreshJwt": "ref", "did": "did:plc:test", "handle": j["identifier"]})
        if method == "com.atproto.server.refreshSession":
            return R({"accessJwt": "acc2", "refreshJwt": "ref2", "did": "did:plc:test", "handle": "art.bsky.social"})
        if method == "com.atproto.repo.uploadBlob":
            SENT.append({"m": "bsky-blob", "mime": kw.get("headers", {}).get("Content-Type"), "size": len(data or b"")})
            return R({"blob": {"$type": "blob", "ref": {"$link": f"baf{_next()}"}, "mimeType": kw["headers"]["Content-Type"], "size": len(data)}})
        if method == "com.atproto.repo.createRecord":
            SENT.append({"m": "bsky-post", "record": kw["json"]["record"]})
            return R({"uri": f"at://did:plc:test/app.bsky.feed.post/3k{_next()}", "cid": "x"})
    if url == "https://www.deviantart.com/oauth2/token":
        return R({"access_token": "datok", "refresh_token": "daref", "expires_in": 3600, "status": "success"})
    if url == "https://www.deviantart.com/api/v1/oauth2/stash/submit":
        SENT.append({"m": "da-submit", "data": dict(data), "name": list(files.values())[0][0]})
        return R({"status": "success", "itemid": _next(), "stackid": 1})
    if url == "https://www.deviantart.com/api/v1/oauth2/stash/publish":
        if "deviantart" in FAIL:
            return R({"error": "invalid_request", "error_description": "Must accept DA submission policy", "status": "error"}, 400)
        SENT.append({"m": "da-publish", "data": dict(data)})
        return R({"status": "success", "url": f"https://www.deviantart.com/artist/art/Test-{_next()}", "deviationid": "X"})
    if url.startswith("https://e6ai.net/uploads.json"):
        auth = kw.get("auth")
        if auth != ("tester", "key123"):
            return R({"success": False, "reason": "access denied"}, 401)
        if "e6ai" in FAIL:
            return R({"success": False, "reason": "invalid", "message": "Tag string: must have at least 4 tags"}, 412)
        fname = list(files.values())[0][0]
        if "e6ai_nomp4" in FAIL and fname.lower().endswith(".mp4"):
            return R({"success": False, "reason": "invalid", "message": "File ext is invalid (only jpg, png, gif, webm are allowed)"}, 412)
        pid = _next()
        SENT.append({"m": "e6ai", "data": dict(data), "file": list(files.keys()), "name": fname})
        if "dup" in (data or {}).get("upload[tag_string]", ""):
            return R({"success": False, "reason": "duplicate", "location": "/posts/42", "post_id": 42}, 412)
        return R({"success": True, "location": f"/posts/{pid}", "post_id": pid})
    if url == "https://pu.vk.com/doc":
        SENT.append({"m": "vk-doc", "files": {k: v[0] for k, v in files.items()}})
        return R({"file": "docfile"})
    if url == "https://pu.vk.com/video":
        SENT.append({"m": "vk-video", "files": {k: v[0] for k, v in files.items()}})
        return R({"video_id": 1, "size": 1})
    if url == "https://pu.vk.com/upload":
        return R({"photo": "[{\"x\":1}]", "server": 1, "hash": "h"})
    raise AssertionError(f"неожиданный запрос {url}")


def fake_get(url, timeout=None, **kw):
    if url.startswith("https://e621.net/"):
        SITE.append("e621")
        url = "https://e6ai.net/" + url[len("https://e621.net/"):]
    m = re.match(r"https://e6ai\.net/posts/(\d+)\.json", url)
    if m:
        return R({"post": {"id": int(m.group(1)), "score": {"up": 14, "down": -1, "total": 13}, "fav_count": 21, "comment_count": 2,
                            "tags": E6_TAGS.get(int(m.group(1)), {"general": ["solo"], "species": [], "meta": ["hi_res"]})}})
    if url.startswith("https://e6ai.net/favorites.json"):
        return R([], 200) if kw.get("auth") == ("tester", "key123") else R({"success": False}, 401)
    if url.startswith("https://e6ai.net/users/"):
        return R({"id": 7, "name": "tester", "upload_limit": 10})
    if url.startswith("https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts"):
        return R({"posts": [{"likeCount": 42, "repostCount": 7, "replyCount": 3, "quoteCount": 1}]})
    if url.startswith("https://www.deviantart.com/api/v1/oauth2/deviation/metadata"):
        n = len([p for p in (kw.get("params") or []) if p[0] == "deviationids[]"])
        return R({"metadata": [{"stats": {"views": 120, "favourites": 9, "comments": 1, "downloads": 0}}] * n})
    if url.startswith("https://www.deviantart.com/api/v1/oauth2/user/whoami"):
        return R({"username": "FennecArtist", "userid": "u1"})
    if url.startswith("https://www.deviantart.com/api/v1/oauth2/gallery/folders"):
        return R({"results": [{"folderid": "F-1", "name": "Featured"}, {"folderid": "F-2", "name": "AI furry"}]})
    if "discord.com/api/webhooks" in url:
        return R({"name": "Crosspost", "channel_id": "777", "guild_id": "1"})
    raise AssertionError(f"неожиданный запрос {url}")


def install():
    requests.post = fake_post
    requests.get = fake_get
