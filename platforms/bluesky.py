"""Bluesky (AT Protocol): пост от вашего аккаунта через пароль приложения.

Вход: ник (handle) + пароль приложения (Настройки Bluesky → Privacy and security → App passwords).
Сессия (accessJwt/refreshJwt) хранится в tokens.json и обновляется сама; основной пароль не нужен и не хранится.

Ограничения Bluesky:
  • текст поста — до 300 символов (графем); длиннее — обрезаем с «…»;
  • до 4 картинок, каждая не больше ~1 МБ (лёгкая версия под этот лимит);
  • видео — одно, до 100 МБ и 3 минут; GIF Bluesky не принимает — уходит как короткое видео (MP4);
  • 18+ помечается самоотметками: эротика — nudity, откровенное — porn.
Ссылки [Текст](https://…) становятся кликабельным словом (facet), #хэштеги — кликабельными тегами.
"""
import re
import time
from datetime import datetime, timezone

import requests

from core.common import PlatformError, load_token, save_token

NAME = "Bluesky"
MAX_IMAGES = 4
IMAGE_MAX_BYTES = 976 * 1024
VIDEO_MAX_BYTES = 100 * 1024 * 1024
VIDEO_MAX_SECONDS = 180
TEXT_LIMIT = 300
DEFAULT_PDS = "https://bsky.social"
LABELS = {"erotic": "nudity", "explicit": "porn"}

LINK = re.compile(r"\[([^\]\n]+)\]\(<?(https?://[^\s<>()]+)>?\)")
BARE = re.compile(r"https?://[^\s<>()\[\]]+[^\s<>()\[\].,;:!?»\"')]")
TAG = re.compile(r"(?<![\w#])#([^\s#.,;:!?()\[\]«»\"']{1,64})", re.UNICODE)


def _pds(c: dict) -> str:
    return (c.get("pds") or DEFAULT_PDS).rstrip("/")


def _home(c: dict, s: dict) -> str:
    """Сервер, где на самом деле живёт аккаунт. bsky.social — общий «вход», он пересылает запросы дальше,
    и на загрузке файлов эта пересылка иногда падает («UpstreamFailure»). Напрямую — надёжнее."""
    return (s.get("home") or _pds(c)).rstrip("/")


def _home_from(j: dict) -> str:
    """Адрес сервера аккаунта из ответа на вход (didDoc → сервис #atproto_pds)."""
    for svc in ((j.get("didDoc") or {}).get("service") or []):
        if str(svc.get("id", "")).endswith("#atproto_pds") and str(svc.get("serviceEndpoint", "")).startswith("https://"):
            return svc["serviceEndpoint"].rstrip("/")
    return ""


def _resolve_home(did: str) -> str:
    """Для старого входа без адреса сервера: спросить каталог DID (только did:plc, публично, без входа)."""
    if not str(did).startswith("did:plc:"):
        return ""
    try:
        r = requests.get(f"https://plc.directory/{did}", timeout=15)
        return _home_from({"didDoc": r.json()}) if r.status_code == 200 else ""
    except (requests.RequestException, ValueError):
        return ""


TRANSIENT = ("UpstreamFailure", "UpstreamTimeout", "InternalServerError", "NotEnoughResources")


def _err(r, what: str) -> PlatformError:
    try:
        j = r.json()
        msg = j.get("message") or j.get("error") or r.text[:200]
    except ValueError:
        msg = r.text[:200]
    if r.status_code == 401:
        return PlatformError(f"Bluesky не принял вход ({msg}). Проверьте ник и пароль приложения")
    return PlatformError(f"Bluesky ({what}): {msg}")


def login(c: dict, handle: str, app_password: str) -> dict:
    """Проверить ник и пароль приложения, сохранить сессию. Возвращает {"handle", "did"}."""
    handle = (handle or "").strip().lstrip("@")
    if not handle or not app_password:
        raise PlatformError("Укажите ник Bluesky и пароль приложения")
    if "." not in handle:
        handle += ".bsky.social"
    r = requests.post(f"{_pds(c)}/xrpc/com.atproto.server.createSession",
                      json={"identifier": handle, "password": app_password.strip()}, timeout=30)
    if r.status_code != 200:
        raise _err(r, "вход")
    j = r.json()
    save_token("bluesky", {"accessJwt": j["accessJwt"], "refreshJwt": j["refreshJwt"], "did": j["did"],
                           "handle": j.get("handle", handle), "at": time.time(),
                           "home": _home_from(j) or _resolve_home(j["did"])})
    return {"handle": j.get("handle", handle), "did": j["did"]}


def logout():
    save_token("bluesky", {})


def session(c: dict) -> dict:
    t = load_token("bluesky") or {}
    if not t.get("accessJwt"):
        raise PlatformError("Bluesky: не выполнен вход — «Настройки» → Bluesky")
    if time.time() - t.get("at", 0) > 60 * 60:   # accessJwt живёт ~2 часа — обновляем заранее
        r = requests.post(f"{_pds(c)}/xrpc/com.atproto.server.refreshSession",
                          headers={"Authorization": f"Bearer {t['refreshJwt']}"}, timeout=30)
        if r.status_code != 200:
            raise PlatformError("Bluesky: сессия истекла — войдите заново в «Настройках» → Bluesky")
        j = r.json()
        t.update(accessJwt=j["accessJwt"], refreshJwt=j["refreshJwt"], at=time.time(), handle=j.get("handle", t.get("handle")),
                 home=_home_from(j) or t.get("home", ""))
        save_token("bluesky", t)
    if not t.get("home"):   # вход сделан до этой версии — узнаём сервер аккаунта один раз
        h = _resolve_home(t.get("did", ""))
        if h:
            t["home"] = h
            save_token("bluesky", t)
    return t


def _transient(r) -> bool:
    if r.status_code in (502, 503, 504):
        return True
    try:
        return (r.json() or {}).get("error") in TRANSIENT
    except ValueError:
        return r.status_code >= 500


def upload_blob(c: dict, s: dict, data: bytes, mime: str, timeout=120) -> dict:
    """Файл — сначала прямо на сервер аккаунта; при временном сбое — ещё две попытки (и через общий вход)."""
    hosts = [_home(c, s)]
    if _pds(c) not in hosts:
        hosts.append(_pds(c))
    last = None
    for attempt in range(3):
        host = hosts[min(attempt, len(hosts) - 1)] if attempt < 2 else hosts[0]
        try:
            r = requests.post(f"{host}/xrpc/com.atproto.repo.uploadBlob", data=data,
                              headers={"Authorization": f"Bearer {s['accessJwt']}", "Content-Type": mime}, timeout=timeout)
        except requests.RequestException as e:
            last = PlatformError(f"Bluesky (загрузка файла): нет связи — {type(e).__name__}")
        else:
            if r.status_code == 200:
                return r.json()["blob"]
            if not _transient(r) and not (r.status_code == 401 and host != _pds(c)):   # 401 у сервера аккаунта — попробуем через общий вход
                raise _err(r, "загрузка файла")
            last = _err(r, "загрузка файла")
        time.sleep(2 + 3 * attempt)
    raise PlatformError(f"{last} — сервер Bluesky временно не принял файл. Ничего не опубликовано; нажмите «Повторить неудачные» "
                        "через пару минут")


# --- текст: ссылки и хэштеги → facets ----------------------------------------------------

def _graphemes(s: str) -> int:
    try:
        import regex
        return len(regex.findall(r"\X", s))
    except ImportError:
        return len(s)


def build_text(text: str, limit: int = TEXT_LIMIT) -> tuple[str, list]:
    """Markdown-ссылки → слово + facet; голые ссылки и #теги → facets. Обрезка до limit символов."""
    out, links, pos = [], [], 0
    for m in LINK.finditer(text or ""):
        out.append(text[pos:m.start()])
        start = len("".join(out))
        out.append(m[1])
        links.append((start, start + len(m[1]), m[2]))
        pos = m.end()
    out.append((text or "")[pos:])
    plain = "".join(out).strip()
    lead = len("".join(out)) - len("".join(out).lstrip())
    links = [(a - lead, b - lead, u) for a, b, u in links]
    if _graphemes(plain) > limit:   # режем по словам, ссылки за краем теряются
        cut = plain[:limit - 1]
        if " " in cut[limit // 2:]:
            cut = cut[:cut.rfind(" ")]
        plain = cut.rstrip(" ,.;:—-") + "…"
        links = [x for x in links if x[1] <= len(plain) - 1]
    taken = [(a, b) for a, b, _ in links]
    for m in BARE.finditer(plain):
        if not any(a <= m.start() < b for a, b in taken):
            links.append((m.start(), m.end(), m[0]))
            taken.append((m.start(), m.end()))
    facets = []

    def bpos(i):
        return len(plain[:i].encode("utf-8"))
    for a, b, url in links:
        facets.append({"index": {"byteStart": bpos(a), "byteEnd": bpos(b)},
                       "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}]})
    for m in TAG.finditer(plain):
        if any(a <= m.start() < b for a, b in taken):
            continue
        facets.append({"index": {"byteStart": bpos(m.start()), "byteEnd": bpos(m.end())},
                       "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": m[1]}]})
    return plain, facets


def post(c: dict, text: str, rating: str, images=None, video=None, alt: str = "", langs=None) -> str:
    """images — список Prepared (name, data, mime, width, height); video — {"data", "width", "height", "mime"}.
    Возвращает ссылку на пост."""
    s = session(c)
    plain, facets = build_text(text)
    record = {"$type": "app.bsky.feed.post", "text": plain,
              "createdAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")}
    if facets:
        record["facets"] = facets
    if langs:
        record["langs"] = list(langs)[:3]
    if LABELS.get(rating):
        record["labels"] = {"$type": "com.atproto.label.defs#selfLabels", "values": [{"val": LABELS[rating]}]}
    if video:
        blob = upload_blob(c, s, video["data"], video.get("mime", "video/mp4"), timeout=1800)
        emb = {"$type": "app.bsky.embed.video", "video": blob, "alt": alt[:1000]}
        if video.get("width") and video.get("height"):
            emb["aspectRatio"] = {"width": int(video["width"]), "height": int(video["height"])}
        record["embed"] = emb
    elif images:
        imgs = []
        for im in images[:MAX_IMAGES]:
            blob = upload_blob(c, s, im.data, im.mime)
            imgs.append({"alt": alt[:1000], "image": blob, "aspectRatio": {"width": int(im.width), "height": int(im.height)}})
        record["embed"] = {"$type": "app.bsky.embed.images", "images": imgs}
    body = {"repo": s["did"], "collection": "app.bsky.feed.post", "record": record}
    hdr = {"Authorization": f"Bearer {s['accessJwt']}"}
    r = requests.post(f"{_home(c, s)}/xrpc/com.atproto.repo.createRecord", json=body, headers=hdr, timeout=60)
    if r.status_code == 401 and _home(c, s) != _pds(c):
        # сервер аккаунта не принял ключ — пост точно не создан, повторяем через общий вход.
        # При сбое сервера (5xx) не повторяем: пост мог успеть создаться, повтор его бы задвоил.
        r = requests.post(f"{_pds(c)}/xrpc/com.atproto.repo.createRecord", json=body, headers=hdr, timeout=60)
    if r.status_code != 200:
        raise _err(r, "пост")
    uri = r.json()["uri"]
    rkey = uri.rsplit("/", 1)[-1]
    return {"link": f"https://bsky.app/profile/{s.get('handle') or s['did']}/post/{rkey}", "uri": uri}


def stats(uri_or_link: str, c: dict | None = None) -> dict:
    """Лайки, репосты, ответы, цитаты. Сначала — через сервер вашего аккаунта (тот же путь, что и публикация:
    если публикация проходит, пройдёт и это), затем — публичные адреса Bluesky (они доступны не из всех сетей)."""
    import re as _re
    uri = uri_or_link
    m = _re.search(r"bsky\.app/profile/([^/]+)/post/([^/?#]+)", uri_or_link or "")
    if m:
        uri = f"at://{m[1]}/app.bsky.feed.post/{m[2]}"
    tries = []
    if c is not None:
        try:
            sess = session(c)
            hdr = {"Authorization": f"Bearer {sess['accessJwt']}", "atproto-proxy": "did:web:api.bsky.app#bsky_appview"}
            for base in dict.fromkeys([_home(c, sess), _pds(c)]):
                tries.append((base, hdr))
        except PlatformError:
            pass   # не вошли — остаются публичные адреса
    tries += [("https://public.api.bsky.app", {}), ("https://api.bsky.app", {})]
    last = None
    for base, hdr in tries:
        try:
            r = requests.get(f"{base}/xrpc/app.bsky.feed.getPosts", params={"uris": uri}, headers=hdr, timeout=20)
        except requests.RequestException as e:
            last = PlatformError(f"нет связи с {base.split('//')[-1]}: {type(e).__name__}")
            continue
        if r.status_code != 200:
            last = _err(r, "статистика")
            continue
        posts = r.json().get("posts") or []
        if not posts:
            raise PlatformError("пост не найден — возможно, его удалили")
        p = posts[0]
        return {"likes": p.get("likeCount", 0), "reposts": p.get("repostCount", 0), "replies": p.get("replyCount", 0),
                "quotes": p.get("quoteCount", 0)}
    raise PlatformError(f"Цифры Bluesky недоступны — {last}" if last else "Цифры Bluesky недоступны")
