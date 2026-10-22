"""Потоковая обработка выгрузки: посты → недельная статистика по тегам.

На выходе для каждого (тег, неделя, рейтинг): число постов n и сумма log(1 + избранное) sy.
Логарифм гасит редкие вирусные выбросы. Сайт целиком считается так же — это «фон»,
с которым потом сравнивается каждый тег (так снимается эффект возраста постов:
старые посты успели набрать больше избранного, но и фон в те недели выше).
"""
import gzip
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from app.paths import sub

EPOCH = pd.Timestamp("2000-01-03", tz="UTC")  # понедельник: недели считаем от него
WEEKS_BACK = 120                               # ~2.3 года: хватает на сезонность прошлого года
CATEGORIES = {0: "general", 3: "franchise", 4: "character", 5: "species"}  # остальное (авторы, мета) не берём
MIN_TOTAL = {"e6ai": 8, "e621": 20}            # меньше постов за весь период — тег не храним
CHUNK = 150_000
NEED = {"created_at", "rating", "tag_string", "fav_count", "is_deleted"}


def week_of(ts) -> int:
    return int((pd.Timestamp(ts, tz="UTC") - EPOCH).days // 7)


def week_start(w: int) -> datetime:
    return (EPOCH + pd.Timedelta(days=7 * int(w))).to_pydatetime()


def out_dir(source: str) -> Path:
    return sub("trends", source)


def read_tags(path: Path) -> dict[str, str]:
    df = pd.read_csv(path, usecols=lambda c: c in {"name", "category"}, dtype={"name": str}, keep_default_na=False)
    df["category"] = pd.to_numeric(df["category"], errors="coerce")
    df = df[df["category"].isin(list(CATEGORIES))]
    return dict(zip(df["name"], df["category"].map(CATEGORIES)))


def _parse_dates(s: pd.Series) -> pd.Series:
    d = pd.to_datetime(s, utc=True, errors="coerce", format="ISO8601")
    if d.isna().mean() > 0.5:  # на случай другого формата дат
        d = pd.to_datetime(s, utc=True, errors="coerce", format="mixed")
    return d


def _compress(parts: list[pd.DataFrame], keys: list[str]) -> list[pd.DataFrame]:
    if len(parts) <= 1:
        return parts
    return [pd.concat(parts, ignore_index=True).groupby(keys, observed=True, as_index=False)[["n", "sy"]].sum()]


def build(source: str, day: str, posts_path: Path, tags_path: Path, report) -> dict:
    t0 = time.time()
    report(progress=0.6, message="Читаю теги…")
    cats = read_tags(tags_path)
    dump_day = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    start = pd.Timestamp(dump_day - timedelta(weeks=WEEKS_BACK))

    tag_parts, site_parts = [], []
    total_posts = 0
    size = posts_path.stat().st_size
    with open(posts_path, "rb") as raw, gzip.GzipFile(fileobj=raw) as gz:
        reader = pd.read_csv(gz, usecols=lambda c: c in NEED, dtype=str, keep_default_na=False,
                             chunksize=CHUNK, on_bad_lines="skip")
        for i, ch in enumerate(reader):
            created = _parse_dates(ch["created_at"])
            keep = created.notna() & (created >= start)
            if "is_deleted" in ch:
                keep &= ch["is_deleted"].str.lower().ne("t") & ch["is_deleted"].str.lower().ne("true")
            ch = ch[keep]
            if ch.empty:
                report(progress=0.6 + 0.35 * raw.tell() / size, message=f"Обрабатываю посты… {total_posts:,}".replace(",", " "))
                continue
            created = created[keep]
            week = ((created - EPOCH).dt.days // 7).astype("int32")
            y = np.log1p(pd.to_numeric(ch["fav_count"], errors="coerce").fillna(0).clip(lower=0)).astype("float32")
            rating = ch["rating"].str[:1].str.lower()
            base = pd.DataFrame({"week": week.values, "rating": rating.values, "y": y.values})
            total_posts += len(base)

            site_parts.append(base.groupby(["week", "rating"], as_index=False).agg(n=("y", "size"), sy=("y", "sum")))

            tags = ch["tag_string"].str.split()
            ex = pd.DataFrame({"tag": tags.values, "week": week.values, "rating": rating.values, "y": y.values}).explode("tag")
            ex = ex[ex["tag"].isin(cats.keys())]
            tag_parts.append(ex.groupby(["tag", "week", "rating"], as_index=False).agg(n=("y", "size"), sy=("y", "sum")))

            if len(tag_parts) >= 8:
                tag_parts = _compress(tag_parts, ["tag", "week", "rating"])
                site_parts = _compress(site_parts, ["week", "rating"])
            report(progress=0.6 + 0.35 * raw.tell() / size, message=f"Обрабатываю посты… {total_posts:,}".replace(",", " "))

    if not tag_parts:
        raise RuntimeError("В выгрузке не нашлось постов за последние два года")
    tags_df = _compress(tag_parts, ["tag", "week", "rating"])[0]
    site_df = _compress(site_parts, ["week", "rating"])[0]

    totals = tags_df.groupby("tag")["n"].sum()
    keep_tags = totals[totals >= MIN_TOTAL.get(source, 10)].index
    tags_df = tags_df[tags_df["tag"].isin(keep_tags)].copy()
    tags_df["n"] = tags_df["n"].astype("int32")
    tags_df["sy"] = tags_df["sy"].astype("float32")

    report(progress=0.97, message="Сохраняю…")
    od = out_dir(source)
    tags_df.to_pickle(od / "tags_weekly.pkl")
    site_df.to_pickle(od / "site_weekly.pkl")
    meta = {
        "source": source, "dump_date": day, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "posts": int(total_posts), "tags": int(tags_df["tag"].nunique()),
        "categories": {t: cats[t] for t in keep_tags},
        "seconds": round(time.time() - t0, 1),
    }
    (od / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta
