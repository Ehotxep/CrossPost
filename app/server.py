"""Локальный HTTP-сервер: отдаёт интерфейс и принимает вызовы API.

Слушает только 127.0.0.1. Каждый запуск получает случайный токен: без него API не ответит,
поэтому посторонние сайты в браузере не смогут дёрнуть приложение.
"""
import json
import mimetypes

mimetypes.add_type("font/woff", ".woff")
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB = Path(__file__).resolve().parent / "web"


def make_server(api, port: int = 0):
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, f, ctype):
            """Файл с поддержкой Range — без неё <video> не умеет перематывать."""
            size = f.stat().st_size
            start, end, code = 0, size - 1, 200
            rng = self.headers.get("Range") or ""
            if rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                try:
                    if a:
                        start = int(a)
                        end = int(b) if b else size - 1
                    else:
                        start = max(0, size - int(b))
                    end = min(end, size - 1)
                    code = 206
                except ValueError:
                    start, end, code = 0, size - 1, 200
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                with open(f, "rb") as fh:
                    fh.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = fh.read(min(left, 1 << 20))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass   # окно перемотало или закрыло видео на полпути

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                from app import ui_state
                html = ((WEB / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", token)
                        .replace("__UI_STATE__", ui_state.inline_js()))
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if path.startswith("/media/"):  # превью постов; токен в адресе, т.к. <img> не шлёт заголовки
                from urllib.parse import parse_qs, urlsplit
                from app.paths import sub
                if parse_qs(urlsplit(self.path).query).get("t", [""])[0] != token:
                    return self._send(403, b'{"ok":false,"error":"forbidden"}')
                base = sub("previews").resolve()
                f = (base / path[len("/media/"):]).resolve()
                if base in f.parents and f.is_file():
                    return self._send(200, f.read_bytes(), "image/jpeg")
                return self._send(404, b'{"ok":false,"error":"not found"}')
            if path.startswith("/bdebug/"):  # снимки экрана браузерной публикации
                from urllib.parse import parse_qs, urlsplit
                from app.paths import sub
                if parse_qs(urlsplit(self.path).query).get("t", [""])[0] != token:
                    return self._send(403, b'{"ok":false,"error":"forbidden"}')
                base = sub("browser_debug").resolve()
                f = (base / path[len("/bdebug/"):]).resolve()
                if base in f.parents and f.is_file() and f.suffix == ".png":
                    return self._send(200, f.read_bytes(), "image/png")
                return self._send(404, b'{"ok":false,"error":"not found"}')
            if path.startswith("/pbig/"):  # крупное превью публикации
                from urllib.parse import parse_qs, urlsplit
                if parse_qs(urlsplit(self.path).query).get("t", [""])[0] != token:
                    return self._send(403, b'{"ok":false,"error":"forbidden"}')
                try:
                    from app.publish import storage
                    return self._send(200, storage.big_for(int(path[len("/pbig/"):])).read_bytes(), "image/jpeg")
                except Exception:
                    return self._send(404, b'{"ok":false,"error":"not found"}')
            if path.startswith(("/pthumb/", "/mthumb/")):  # постоянная миниатюра публикации / уменьшенное превью поста
                from urllib.parse import parse_qs, urlsplit
                from app.paths import sub
                if parse_qs(urlsplit(self.path).query).get("t", [""])[0] != token:
                    return self._send(403, b'{"ok":false,"error":"forbidden"}')
                name = path[len("/pthumb/"):]
                try:
                    if path.startswith("/pthumb/"):
                        from app.publish import storage
                        f = storage.thumb_for(int(name))
                    else:
                        base = sub("previews").resolve()
                        src = (base / name).resolve()
                        if base not in src.parents or not src.is_file():
                            raise FileNotFoundError
                        f = sub("previews", "_s") / src.name
                        if not f.exists():
                            from app.publish import storage
                            storage._thumb_from(src, f, 300)
                    return self._send(200, f.read_bytes(), "image/jpeg")
                except Exception:
                    return self._send(404, b'{"ok":false,"error":"not found"}')
            if path.startswith("/ubig/"):  # крупный просмотр файла нового поста
                from urllib.parse import parse_qs, urlsplit
                if parse_qs(urlsplit(self.path).query).get("t", [""])[0] != token:
                    return self._send(403, b'{"ok":false,"error":"forbidden"}')
                try:
                    from app.publish import uploads
                    return self._send(200, uploads.big(path[len("/ubig/"):]), "image/jpeg")
                except Exception:
                    return self._send(404, b'{"ok":false,"error":"not found"}')
            if path.startswith("/uthumb/"):  # миниатюра файла нового поста
                from urllib.parse import parse_qs, urlsplit
                if parse_qs(urlsplit(self.path).query).get("t", [""])[0] != token:
                    return self._send(403, b'{"ok":false,"error":"forbidden"}')
                try:
                    from app.publish import uploads
                    return self._send(200, uploads.thumb(path[len("/uthumb/"):]), "image/jpeg")
                except Exception:
                    return self._send(404, b'{"ok":false,"error":"not found"}')
            if path.startswith("/uloop/"):   # просмотр петли из окна правки
                from urllib.parse import parse_qs, urlsplit
                if parse_qs(urlsplit(self.path).query).get("t", [""])[0] != token:
                    return self._send(403, b'{"ok":false,"error":"forbidden"}')
                try:
                    from app.publish import uploads
                    from app.paths import sub
                    uid = path[len("/uloop/"):]
                    uploads.path_of(uid)
                    return self._send_file(sub("uploads") / f"{uid}.looprev.webm", "video/webm")
                except Exception:
                    return self._send(404, b'{"ok":false,"error":"not found"}')
            if path.startswith(("/uvideo/", "/ustrip/")):   # видео для окна правки (с перемоткой) и лента кадров
                from urllib.parse import parse_qs, urlsplit
                if parse_qs(urlsplit(self.path).query).get("t", [""])[0] != token:
                    return self._send(403, b'{"ok":false,"error":"forbidden"}')
                try:
                    from app.publish import media, uploads
                    from app.paths import sub
                    uid = path.split("/", 2)[2]
                    src = uploads.path_of(uid)
                    if path.startswith("/ustrip/"):
                        return self._send(200, (sub("uploads") / f"{uid}.strip.jpg").read_bytes(), "image/jpeg")
                    f = media.preview(uid, src)
                    return self._send_file(f, mimetypes.guess_type(f.name)[0] or "video/mp4")
                except Exception:
                    return self._send(404, b'{"ok":false,"error":"not found"}')
            if path == "/appicon.ico":   # значок для вкладки: свой, если выбран
                from app import icon
                return self._send(200, icon.active_ico().read_bytes(), "image/x-icon")
            if path.startswith("/static/"):
                f = (WEB / path[len("/static/"):]).resolve()
                if WEB in f.parents and f.is_file():
                    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    if ctype.startswith("text/") or ctype.endswith("javascript"):
                        ctype += "; charset=utf-8"
                    return self._send(200, f.read_bytes(), ctype)
            self._send(404, b'{"ok":false,"error":"not found"}')

        def do_POST(self):
            if self.path.startswith("/upload"):   # файл для нового поста: тело запроса — сами байты
                from urllib.parse import parse_qs, unquote, urlsplit
                if self.headers.get("X-Token") != token:
                    return self._send(403, b'{"ok":false,"error":"forbidden"}')
                try:
                    from app.publish import uploads
                    name = unquote(parse_qs(urlsplit(self.path).query).get("name", ["file"])[0])
                    length = int(self.headers.get("Content-Length") or 0)
                    if length > uploads.MAX_BYTES:
                        raise ValueError("Файл больше 500 МБ")
                    chunks, left = [], length
                    while left > 0:
                        chunk = self.rfile.read(min(left, 1 << 20))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        left -= len(chunk)
                    info = uploads.save(name, b"".join(chunks))
                    body = json.dumps({"ok": True, "result": info}, ensure_ascii=False)
                except Exception as e:
                    body = json.dumps({"ok": False, "error": str(e) or type(e).__name__}, ensure_ascii=False)
                return self._send(200, body.encode("utf-8"))
            if not self.path.startswith("/api/"):
                return self._send(404, b'{"ok":false,"error":"not found"}')
            if self.headers.get("X-Token") != token:
                return self._send(403, b'{"ok":false,"error":"forbidden"}')
            name = self.path[len("/api/"):].split("?", 1)[0]
            try:
                length = int(self.headers.get("Content-Length") or 0)
                args = json.loads(self.rfile.read(length) or b"{}") if length else {}
                result = api.call(name, args)
                from app.cache import clean
                body = json.dumps({"ok": True, "result": clean(result)}, ensure_ascii=False, default=str)
                self._send(200, body.encode("utf-8"))
            except Exception as e:
                from app.jobs import mask_secrets
                body = json.dumps({"ok": False, "error": mask_secrets(str(e) or type(e).__name__)}, ensure_ascii=False)
                self._send(200, body.encode("utf-8"))

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.cp_token = token   # для второго запуска: «разверни окно» (app.instance)
    return server, token


def serve_in_background(api, port: int = 0):
    server, token = make_server(api, port)
    threading.Thread(target=server.serve_forever, daemon=True, name="http").start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/"
