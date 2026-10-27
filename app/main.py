"""Запуск приложения.

  python -m app             — своё окно (pywebview); если его нет — откроется браузер
  python -m app --browser   — всегда в браузере
  python -m app --port 8800 — фиксированный порт (удобно для отладки)
"""
import argparse
import os
import sys
import time
import webbrowser

from app import __version__
from app.api import Api
from app.server import serve_in_background


def main():
    ap = argparse.ArgumentParser(description="Crosspost")
    ap.add_argument("--browser", action="store_true", help="открыть в браузере вместо окна")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-open", action="store_true", help="не открывать окно (только сервер, для тестов)")
    ap.add_argument("--no-worker", action="store_true", help="не запускать фоновую отправку отдельным процессом")
    args = ap.parse_args()

    from app import instance
    if not args.no_open and not os.environ.get("CROSSPOST_MULTI"):
        if not instance.acquire():   # Crosspost уже открыт — показываем его окно, второе не запускаем
            if not instance.wake_existing():
                instance.tell("Crosspost уже запущен. Если его окна не видно, оно, возможно, зависло: "
                              "закройте Crosspost в диспетчере задач и запустите снова.")
            return

    api = Api()
    api.window_expected = not args.browser and not args.no_open   # окно ещё будет создано — второй запуск должен его дождаться
    server, url = serve_in_background(api, args.port)
    instance.publish(url, getattr(server, "cp_token", ""))
    from app import perf
    perf.start_sampler()   # память и процессор окна — раз в минуту, для «Производительности»
    _start_sender(api, args)
    try:   # досчитать устаревшие экраны в фоне, пока человек смотрит на первый
        from app.insights import warmup
        warmup.warm_async()
    except Exception:
        pass
    if not args.no_open:
        def _refresh():   # в фоне: без интернета это ждёт таймаутов, а окно не должно появляться позже из-за этого
            try:
                api._auto_refresh()
            except Exception as e:  # нет интернета и т.п. — не мешаем запуску
                print(f"Автообновление трендов не удалось: {e}")
        import threading
        threading.Thread(target=_refresh, daemon=True, name="auto-refresh").start()
    print(f"Crosspost {__version__}: {url}", flush=True)

    if args.no_open:
        _wait()
        return

    if not args.browser:
        try:
            import webview  # pywebview
        except ImportError:
            api.window_expected = False
            print("pywebview не установлен — открываю в браузере. Установка: pip install pywebview")
        else:
            from app import icon
            icon.set_app_id()   # Windows: своё приложение в панели задач, а не «Python»
            title = f"Crosspost {__version__}"
            window = webview.create_window(title, url, width=1360, height=900, min_size=(900, 600))
            api.window = window
            import threading
            threading.Thread(target=icon.set_window_icon, args=(title,), daemon=True, name="icon").start()   # значок окна
            webview.start()  # блокирует до закрытия окна
            server.shutdown()
            return

    webbrowser.open(url)
    print("Окно браузера открыто. Закройте это окно (Ctrl+C), чтобы остановить приложение.")
    _wait()


def _start_sender(api, args):
    """Запланированные посты отправляет отдельный фоновый процесс (окно можно закрывать).
    Если его запустить не получилось — расписание проверяет само окно, пока оно открыто."""
    if os.environ.get("CROSSPOST_NO_SCHEDULER"):
        return
    from app import worker
    from app.publish import schedule
    try:
        if not args.no_worker:
            worker.start_detached()
            for _ in range(10):
                time.sleep(0.5)
                if worker.status()["running"]:
                    return
    except Exception as e:  # noqa: BLE001
        print(f"Фоновая отправка не запустилась: {e}")
    if not worker.status()["running"]:
        schedule.start_worker(api)


def _wait():
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
