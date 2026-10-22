"""Получение кода авторизации OAuth через браузер.

Запускает маленький локальный сервер на адресе redirect_uri (например http://localhost:8765/callback),
открывает страницу входа и ловит код, который площадка присылает обратно.
Если локальный сервер не подходит, можно вставить адрес из браузера вручную (manual=True).
"""
import http.server
import json
import urllib.parse
import webbrowser


def _parse(url: str) -> dict:
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    res = {k: v[0] for k, v in q.items()}
    if "payload" in res:  # некоторые версии VK ID присылают всё в JSON-поле payload
        try:
            res.update(json.loads(res["payload"]))
        except ValueError:
            pass
    return res


def get_auth_code(auth_url: str, redirect_uri: str, state: str, manual: bool = False) -> dict:
    parsed = urllib.parse.urlparse(redirect_uri)
    result: dict = {}

    if not manual and parsed.hostname in ("localhost", "127.0.0.1"):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                got = _parse(self.path)
                if "code" in got or "error" in got:
                    result.update(got)
                    body = "<h2>Готово! Вкладку можно закрыть и вернуться в консоль.</h2>"
                else:
                    body = "<p>Жду код авторизации…</p>"
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer((parsed.hostname, parsed.port or 80), Handler)
        print("Открываю браузер для входа. Если он не открылся, перейдите по ссылке:\n" + auth_url)
        webbrowser.open(auth_url)
        while not result:
            server.handle_request()
        server.server_close()
    else:
        print("Откройте ссылку в браузере и разрешите доступ:\n" + auth_url)
        print("\nПосле этого браузер перейдёт на другой адрес (страница может не открыться — это нормально).")
        result = _parse(input("Скопируйте адрес из адресной строки и вставьте сюда: ").strip())

    if "error" in result:
        raise SystemExit(f"Авторизация отклонена: {result.get('error_description') or result['error']}")
    if "code" not in result:
        raise SystemExit("В адресе нет кода авторизации. Попробуйте ещё раз.")
    if result.get("state") and result["state"] != state:
        raise SystemExit("Параметр state не совпал — попробуйте авторизоваться заново.")
    return result
