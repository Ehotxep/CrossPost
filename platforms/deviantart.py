"""DeviantArt: загрузка в Sta.sh и публикация как девиации (официальный API, OAuth2 с PKCE).

Свое приложение: deviantart.com/developers → Register your Application, адрес возврата
http://localhost:8765/callback (его же указать в Crosspost). Вход — в браузере, ключ обновляется сам;
refresh-ключ живёт 3 месяца, потом нужно войти заново.

Каждая картинка — отдельная работа («Название (2/3)»). Видео DeviantArt не принимает.
Откровенное (explicit) правила DeviantArt запрещают — такие посты сюда не уходят.
Эротика — Mature, уровень moderate, тип «nudity».
"""
import base64
import hashlib
import http.server
import re
import secrets
import threading
import time
import urllib.parse
import webbrowser

import requests

from core.common import PlatformError, load_token, save_token

NAME = "DeviantArt"
MAX_IMAGES = 10
API = "https://www.deviantart.com/api/v1/oauth2"
AUTH_URL = "https://www.deviantart.com/oauth2/authorize"
TOKEN_URL = "https://www.deviantart.com/oauth2/token"
SCOPE = "basic user browse stash publish"   # browse — папки галереи и цифры работ (просмотры, избранное)
FILE_MAX_BYTES = 30 * 1024 * 1024
TITLE_MAX = 50

_auth: dict = {}   # текущий вход: state, verifier, результат


def _json(r, what):
    try:
        j = r.json()
    except ValueError:
        raise PlatformError(f"DeviantArt ({what}): HTTP {r.status_code}")
    if r.status_code >= 400 or j.get("status") == "error" or j.get("error"):
        msg = j.get("error_description") or j.get("error") or j
        if r.status_code == 401 or j.get("error") == "invalid_token":
            raise PlatformError("DeviantArt: вход устарел — «Настройки» → DeviantArt → «Войти»")
        if j.get("error") == "insufficient_scope" or "higher scope" in str(msg):
            raise PlatformError("DeviantArt: при входе приложению не было выдано право смотреть галерею и цифры работ. "
                                "Нажмите «Войти заново» в «Настройки» → DeviantArt и подтвердите доступ (Authorize)")
        raise PlatformError(f"DeviantArt ({what}): {msg}")
    return j


# --- вход ---------------------------------------------------------------------------------

def auth_start(c: dict) -> str:
    """Открыть страницу входа DeviantArt и ждать ответ на локальном адресе возврата (в фоне, до 5 минут)."""
    cid, redirect = str(c.get("client_id") or "").strip(), c.get("redirect_uri") or "http://localhost:8765/callback"
    if not cid or not c.get("client_secret"):
        raise PlatformError("Укажите client_id и client_secret своего приложения DeviantArt")
    verifier = secrets.token_urlsafe(64)[:64]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": cid, "redirect_uri": redirect, "scope": SCOPE, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    _auth.clear()
    _auth.update(state=state, verifier=verifier, redirect=redirect, cfg=dict(c), done=False, error="")
    p = urllib.parse.urlparse(redirect)
    if p.hostname in ("localhost", "127.0.0.1"):
        threading.Thread(target=_serve, args=(p.port or 80, p.path or "/"), daemon=True).start()
    webbrowser.open(url)
    return url


def _serve(port: int, path: str):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            ok = False
            if self.path.startswith(path) and ("code=" in self.path or "error=" in self.path):
                try:
                    auth_finish(self.path)
                    ok = True
                except PlatformError as e:
                    _auth["error"] = str(e)
            body = ("<h2>Готово! Вкладку можно закрыть и вернуться в Crosspost.</h2>" if ok else
                    f"<h2>Не получилось</h2><p>{_auth.get('error') or 'Жду ответ DeviantArt…'}</p>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    try:
        srv = http.server.HTTPServer(("127.0.0.1", port), H)
    except OSError:
        _auth["error"] = f"порт {port} занят — вставьте адрес из браузера вручную"
        return
    srv.timeout = 1
    end = time.time() + 300
    while time.time() < end and not _auth.get("done"):
        srv.handle_request()
    srv.server_close()


def auth_finish(pasted: str) -> dict:
    """Код из адреса возврата → ключи. Работает и для адреса, вставленного вручную."""
    if not _auth.get("state"):
        raise PlatformError("Сначала нажмите «Войти через DeviantArt»")
    q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(pasted.strip()).query).items()}
    if "error" in q:
        raise PlatformError(f"DeviantArt отклонил вход: {q.get('error_description') or q['error']}")
    if q.get("state") != _auth["state"]:
        raise PlatformError("Этот адрес от другой попытки входа — нажмите «Войти» ещё раз")
    c = _auth["cfg"]
    r = requests.post(TOKEN_URL, data={"grant_type": "authorization_code", "client_id": c["client_id"],
                                       "client_secret": c["client_secret"], "code": q.get("code", ""),
                                       "redirect_uri": _auth["redirect"], "code_verifier": _auth["verifier"]}, timeout=30)
    j = _json(r, "вход")
    if "access_token" not in j:
        raise PlatformError(f"DeviantArt не выдал ключ: {j}")
    t = {"access_token": j["access_token"], "refresh_token": j.get("refresh_token", ""),
         "expires_at": time.time() + int(j.get("expires_in", 3600)), "refreshed": time.time(),
         "scope": j.get("scope") or SCOPE}
    try:
        me = _json(requests.get(f"{API}/user/whoami", headers={"Authorization": f"Bearer {t['access_token']}"}, timeout=30), "whoami")
        t["username"] = me.get("username", "")
    except PlatformError:
        pass
    save_token("deviantart", t)
    _auth["done"] = True
    return t


def auth_state() -> dict:
    return {"pending": bool(_auth.get("state")) and not _auth.get("done"), "done": bool(_auth.get("done")), "error": _auth.get("error", "")}


def logout():
    save_token("deviantart", {})


def _token(c: dict) -> str:
    t = load_token("deviantart") or {}
    if not t.get("access_token"):
        raise PlatformError("DeviantArt: не выполнен вход — «Настройки» → DeviantArt")
    if time.time() < t.get("expires_at", 0) - 60:
        return t["access_token"]
    r = requests.post(TOKEN_URL, data={"grant_type": "refresh_token", "client_id": c["client_id"],
                                       "client_secret": c["client_secret"], "refresh_token": t["refresh_token"]}, timeout=30)
    try:
        j = _json(r, "обновление входа")
    except PlatformError:
        raise PlatformError("DeviantArt: вход устарел (раз в 3 месяца нужно входить заново) — «Настройки» → DeviantArt")
    t.update(access_token=j["access_token"], refresh_token=j.get("refresh_token", t["refresh_token"]), scope=j.get("scope") or t.get("scope", ""),
             expires_at=time.time() + int(j.get("expires_in", 3600)), refreshed=time.time())
    save_token("deviantart", t)
    return t["access_token"]


def galleries(c: dict) -> list[dict]:
    tok = _token(c)
    j = _json(requests.get(f"{API}/gallery/folders", params={"limit": 50}, headers={"Authorization": f"Bearer {tok}"}, timeout=30), "папки")
    return [{"id": f["folderid"], "name": f.get("name", "")} for f in j.get("results", [])]


# --- публикация ---------------------------------------------------------------------------

def clean_tags(tags) -> list[str]:
    out = []
    for t in tags or []:
        t = re.sub(r"[^\w]", "", str(t).replace("-", "_"), flags=re.UNICODE)
        if t and t.lower() not in {x.lower() for x in out}:
            out.append(t)
    return out[:30]


def _b(v) -> str:
    return "true" if v else "false"


def publish(c: dict, files, title: str, text: str, tags, rating: str, progress=None) -> list[str]:
    """files — [(name, data, mime)]. Возвращает ссылки на работы."""
    if rating == "explicit":
        raise PlatformError("DeviantArt не принимает откровенный контент")
    tok = _token(c)
    auth = {"Authorization": f"Bearer {tok}"}
    tags = clean_tags(tags)
    base = (title or c.get("default_title") or "Untitled").strip()
    urls = []
    for i, (name, data, mime) in enumerate(files):
        t = base if len(files) == 1 else f"{base[:TITLE_MAX - 8]} ({i + 1}/{len(files)})"
        sub = {"title": t[:TITLE_MAX], "artist_comments": text or "",
               "is_ai_generated": _b(c.get("is_ai_generated", True)), "noai": _b(c.get("noai", True))}
        if tags:
            sub["tags[]"] = tags
        j = _json(requests.post(f"{API}/stash/submit", data=sub, files={"file": (name, data, mime)}, headers=auth, timeout=600), "загрузка")
        if not j.get("itemid"):
            raise PlatformError(f"DeviantArt (загрузка): {j}")
        if progress:
            progress((i + 0.6) / len(files))
        mature = rating == "erotic"
        pub = {"itemid": str(j["itemid"]), "is_mature": _b(mature), "agree_submission": "true", "agree_tos": "true",
               "allow_comments": _b(c.get("allow_comments", True)), "allow_free_download": _b(c.get("allow_free_download", False)),
               "is_ai_generated": _b(c.get("is_ai_generated", True)), "noai": _b(c.get("noai", True)), "feature": "true"}
        if mature:
            pub["mature_level"] = "moderate"
            pub["mature_classification[]"] = ["nudity"]
        if tags:
            pub["tags[]"] = tags
        if c.get("gallery_id"):
            pub["galleryids[]"] = [c["gallery_id"]]
        res = _json(requests.post(f"{API}/stash/publish", data=pub, headers=auth, timeout=120), "публикация")
        urls.append({"url": res.get("url") or "", "id": res.get("deviationid") or ""})
        if progress:
            progress((i + 1) / len(files))
    return urls


# --- для консольной версии (crosspost.py) ---------------------------------------------------

def limits(c: dict) -> dict:
    return {"max_bytes": FILE_MAX_BYTES, "formats": ("JPEG", "PNG", "GIF")}


def post(c: dict, images, p) -> list[str]:
    return [x["url"] for x in publish(c, [(im.name, im.data, im.mime) for im in images], p.title, p.text,
                                      p.tags or c.get("default_tags", []), "erotic" if p.mature else "sfw")]


def stats(c: dict, ids: list[str]) -> dict:
    """Просмотры, избранное, комментарии, скачивания — сумма по работам публикации."""
    ids = [i for i in ids if i]
    if not ids:
        raise PlatformError("нет номеров работ")
    tok = _token(c)
    j = _json(requests.get(f"{API}/deviation/metadata", params=[("deviationids[]", i) for i in ids] + [("ext_stats", "true")],
                           headers={"Authorization": f"Bearer {tok}"}, timeout=30), "статистика")
    out = {"views": 0, "favourites": 0, "comments": 0, "downloads": 0}
    for m in j.get("metadata", []):
        st = m.get("stats") or {}
        for k in out:
            out[k] += int(st.get(k) or 0)
    return out
