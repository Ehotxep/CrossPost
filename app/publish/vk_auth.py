"""Вход во ВКонтакте (VK ID, OAuth 2.1 с PKCE) прямо из приложения.

Открывает браузер со страницей входа VK и ловит ответ на адресе redirect_uri
(по умолчанию http://localhost:8765/callback — он же должен быть указан в настройках приложения VK ID).
"""
import base64
import hashlib
import http.server
import secrets
import time
import urllib.parse
import webbrowser

import requests

import app.publish  # noqa: F401
from core.common import PlatformError, save_token
from core.oauth import _parse

TIMEOUT = 300
_pending: dict = {}   # начатый вход: verifier, state — ждём адрес из браузера


def start(app_id: str, redirect_uri: str) -> str:
    """Шаг 1: открыть страницу входа VK. VK ID требует HTTPS-адрес возврата, поэтому локальный сервер его
    не поймает — после входа браузер покажет «не удаётся открыть страницу», а адрес из строки браузера
    человек вставляет в приложение (в нём код входа)."""
    app_id = str(app_id or "").strip()
    if not app_id.isdigit():
        raise PlatformError("Укажите ID приложения VK ID (число)")
    verifier = secrets.token_urlsafe(64)[:64]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = "https://id.vk.com/authorize?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": app_id, "redirect_uri": redirect_uri,
        "scope": "wall photos groups video docs", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    _pending.clear()
    _pending.update(app_id=app_id, redirect_uri=redirect_uri, verifier=verifier, state=state)
    webbrowser.open(url)
    return url


def finish(pasted: str) -> None:
    """Шаг 2: адрес из браузера → ключ доступа."""
    if not _pending:
        raise PlatformError("Сначала нажмите «Войти через VK»")
    pasted = (pasted or "").strip()
    if "code=" not in pasted:
        raise PlatformError("В этом адресе нет кода входа. Скопируйте адрес целиком из адресной строки после входа во VK "
                            "(он начинается с https://localhost/…)")
    res = _parse(pasted if "?" in pasted else "?" + pasted)
    if "error" in res:
        raise PlatformError(f"VK отклонил вход: {res.get('error_description') or res['error']}")
    if res.get("state") and res["state"] != _pending["state"]:
        raise PlatformError("Этот адрес от другой попытки входа — нажмите «Войти через VK» ещё раз")
    if "device_id" not in res:
        raise PlatformError("В адресе нет device_id — приложение в VK ID должно быть типа «Веб»")
    r = requests.post("https://id.vk.com/oauth2/auth", data={
        "grant_type": "authorization_code", "code_verifier": _pending["verifier"], "redirect_uri": _pending["redirect_uri"],
        "code": res["code"], "client_id": _pending["app_id"], "device_id": res["device_id"], "state": _pending["state"],
    }, timeout=30)
    j = r.json()
    if "access_token" not in j:
        raise PlatformError(f"VK не выдал ключ доступа: {j.get('error_description') or j.get('error') or j}")
    save_token("vk", {"access_token": j["access_token"], "refresh_token": j.get("refresh_token", ""),
                      "device_id": res["device_id"], "expires_at": time.time() + int(j.get("expires_in", 3600)),
                      "scope": j.get("scope", "")})
    _pending.clear()


def login(app_id: str, redirect_uri: str, report=lambda **k: None) -> None:
    app_id = str(app_id or "").strip()
    if not app_id.isdigit():
        raise PlatformError("Укажите ID приложения VK ID (число)")
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.hostname not in ("localhost", "127.0.0.1"):
        raise PlatformError("Адрес возврата должен быть вида http://localhost:8765/callback")
    verifier = secrets.token_urlsafe(64)[:64]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = "https://id.vk.com/authorize?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": app_id, "redirect_uri": redirect_uri,
        "scope": "wall photos groups video docs", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            got = _parse(self.path)
            if "code" in got or "error" in got:
                result.update(got)
                body = "<h2>Готово! Вкладку можно закрыть и вернуться в Crosspost.</h2>"
            else:
                body = "<p>Жду ответ VK…</p>"
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    try:
        server = http.server.HTTPServer((parsed.hostname, parsed.port or 80), Handler)
    except OSError:
        raise PlatformError(f"Порт {parsed.port} занят другой программой — закройте её или поменяйте адрес возврата")
    server.timeout = 1
    report(progress=0.2, message="Открыла браузер — войдите во ВКонтакте и разрешите доступ")
    webbrowser.open(url)
    deadline = time.time() + TIMEOUT
    try:
        while not result and time.time() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not result:
        raise PlatformError("Вход не завершён за 5 минут — попробуйте ещё раз")
    if "error" in result:
        raise PlatformError(f"VK отклонил вход: {result.get('error_description') or result['error']}")
    if result.get("state") and result["state"] != state:
        raise PlatformError("Ответ VK не совпал с запросом — попробуйте войти заново")
    if "device_id" not in result:
        raise PlatformError("VK не прислал device_id — приложение в VK ID должно быть типа «Веб»")
    report(progress=0.8, message="Получаю ключ доступа…")
    r = requests.post("https://id.vk.com/oauth2/auth", data={
        "grant_type": "authorization_code", "code_verifier": verifier, "redirect_uri": redirect_uri,
        "code": result["code"], "client_id": app_id, "device_id": result["device_id"], "state": state,
    }, timeout=30)
    j = r.json()
    if "access_token" not in j:
        raise PlatformError(f"VK не выдал ключ доступа: {j.get('error_description') or j}")
    save_token("vk", {"access_token": j["access_token"], "refresh_token": j.get("refresh_token", ""),
                      "device_id": result["device_id"], "expires_at": time.time() + int(j.get("expires_in", 3600))})
    report(progress=1.0, message="Вход выполнен")


def group_info(group_id: str, app_id: str, access_token: str = "") -> dict:
    """Название группы — заодно проверка, что ключ работает."""
    from platforms import vk as V
    gid = str(group_id or "").strip().lstrip("-")
    if gid.startswith("club") or gid.startswith("public"):
        gid = gid.lstrip("clubpublic")
    token = V._token({"app_id": app_id, "access_token": access_token})
    res = V._call(token, "groups.getById", group_id=gid, fields="is_admin,admin_level")
    groups = res.get("groups", res) if isinstance(res, dict) else res
    g = groups[0]
    # is_admin приходит не всегда — надёжнее спросить список групп, где вы админ.
    # None = проверить не удалось (тогда не пугаем предупреждением).
    is_admin = True if g.get("is_admin") else None
    if not is_admin:
        try:
            mine = V._call(token, "groups.get", filter="admin,editor", count=1000)
            ids = {int(x) if not isinstance(x, dict) else int(x["id"]) for x in mine.get("items", [])}
            is_admin = int(g["id"]) in ids
        except Exception:
            is_admin = None
    return {"id": str(g["id"]), "name": g.get("name"), "is_admin": is_admin}
