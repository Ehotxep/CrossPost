"""Теггер в отдельном процессе: держит модель на видеокарте, пока к нему обращаются.

Запрос (строка JSON в stdin): {"path": "...", "min": 0.1}  →  ответ (строка в stdout): {"ok": [[тег, p], …]} или {"err": "…"}.
Родитель (app/tagger/remote.py) закрывает процесс после нескольких минут простоя — вместе с ним уходят
и PyTorch, и CUDA, и несколько гигабайт памяти.
"""
import json
import sys


def main():
    import os
    from app.tagger import jtp
    if os.environ.get("CROSSPOST_FAKE_TAGGER"):   # для проверок без видеокарты
        jtp.load = lambda: "fake"
        jtp.predict = lambda path, min_prob=0.1: [("solo", 0.9), ("fox", 0.8)]
    dev = jtp.load()
    print(json.dumps({"ready": dev}), flush=True)
    for line in sys.stdin:
        try:
            req = json.loads(line)
            res = jtp.predict(req["path"], float(req.get("min", 0.1)))
            print(json.dumps({"ok": res}), flush=True)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"err": str(e) or type(e).__name__}), flush=True)


if __name__ == "__main__":
    main()
