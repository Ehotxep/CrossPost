"""Разметка старых постов теггером: по превью (640 px) узнаём, что на картинке — вид, поза, сеттинг…

Это превращает историю канала в данные «какое содержание заходит», на них учится прогноз.
Размечается один раз; новые посты — при следующем запуске (кнопка в «Статистике» или после импорта).
"""
from datetime import datetime, timezone

from app import db
from app.paths import sub

MIN_PROB = 0.35     # что сохраняем; для выводов дальше берём ещё увереннее
TOP = 60


def _todo() -> list[dict]:
    return db.query("""SELECT p.id, p.preview FROM posts p
                       JOIN channels c ON c.platform=p.platform AND c.chat_id=p.chat_id
                       LEFT JOIN post_tagged t ON t.post_id=p.id
                       WHERE c.selected=1 AND p.preview IS NOT NULL AND t.post_id IS NULL AND p.is_forward=0
                       ORDER BY p.date_utc DESC""")


def status() -> dict:
    from app.tagger import jtp
    done = db.query("""SELECT COUNT(*) AS n FROM post_tagged t JOIN posts p ON p.id=t.post_id
                       JOIN channels c ON c.platform=p.platform AND c.chat_id=p.chat_id WHERE c.selected=1""")[0]["n"]
    with_prev = db.query("""SELECT COUNT(*) AS n FROM posts p JOIN channels c ON c.platform=p.platform AND c.chat_id=p.chat_id
                            WHERE c.selected=1 AND p.preview IS NOT NULL AND p.is_forward=0""")[0]["n"]
    return {"tagged": done, "todo": len(_todo()), "with_preview": with_prev, "tagger_ready": jtp.status()["ready"]}


def run(report):
    from app.tagger import jtp
    if not jtp.status()["ready"]:
        raise RuntimeError("Сначала установите теггер: «Настройки» → e6ai → «Установить теггер»")
    todo = _todo()
    if not todo:
        report(progress=1.0, message="Все посты уже размечены")
        return
    report(progress=0.01, message="Загружаю теггер на видеокарту…")
    jtp.load()
    folder = sub("previews")
    step = 64
    for i in range(0, len(todo), step):
        chunk = todo[i:i + step]
        paths = [folder / r["preview"] for r in chunk]
        exist = [p.exists() for p in paths]
        preds = jtp.predict_batch([p for p, e in zip(paths, exist) if e], MIN_PROB, TOP)
        it = iter(preds)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with db.tx() as c:
            for r, e in zip(chunk, exist):
                tags = next(it) if e else []
                c.executemany("INSERT OR REPLACE INTO post_tags (post_id, tag, prob) VALUES (?,?,?)",
                              [(r["id"], t, p) for t, p in tags])
                c.execute("INSERT OR REPLACE INTO post_tagged (post_id, at) VALUES (?,?)", (r["id"], now))
        n = min(i + step, len(todo))
        report(progress=0.02 + 0.98 * n / len(todo), message=f"Размечаю посты теггером: {n} из {len(todo)}")
    report(progress=1.0, message=f"Размечено постов: {len(todo)}")

