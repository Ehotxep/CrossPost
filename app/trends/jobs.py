"""Задачи трендов для отдельного процесса (см. app/subproc.py): скачать и разобрать выгрузку."""
from pathlib import Path


def update(report, source: str, ua: str) -> dict:
    from app.trends import aggregate, dumps
    day, posts, tags = dumps.ensure_latest(source, ua, report)
    meta = aggregate.build(source, day, posts, tags, report)
    from app import e6tags
    if source == e6tags.site():   # заодно — база тегов для формы того сайта, куда загружаем (переименования и связи — маленькие файлы)
        try:
            e6tags.update(lambda **k: report(**{**k, "progress": None} if "progress" in k else k), ua, tags, source)
        except Exception as e:  # noqa: BLE001
            report(message=f"Тренды готовы; базу тегов обновить не удалось: {e}")
    return {"day": day, "posts": meta.get("posts")}


def import_files(report, source: str, posts_path: str, tags_path: str) -> dict:
    from app.trends import aggregate, dumps
    report(progress=0.05, message="Копирую файлы…")
    day, posts, tags = dumps.import_files(source, posts_path, tags_path)
    meta = aggregate.build(source, day, Path(posts), Path(tags), report)
    return {"day": day, "posts": meta.get("posts")}


def e6db(report, ua: str, tags_file: str | None = None, site: str | None = None) -> dict:
    from app import e6tags
    return e6tags.update(report, ua, Path(tags_file) if tags_file else None, site)


def history_tags(report) -> None:
    from app.insights import history_tags as H
    H.run(report)
