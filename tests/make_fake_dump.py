"""Синтетическая выгрузка в формате db_export e621/e6ai — для проверки конвейера трендов.

Заложенные закономерности (их и должен найти анализ):
  - armor: в последние ~4 месяца постов и отклика становится заметно больше (растущий тренд)
  - halloween: каждый год всплеск в конце октября (сезонность)
  - underwater, desert: мало постов, высокий отклик (ниши)
  - simple_background: много постов, низкий отклик
  - fennec_fox, military: стабильно высокий отклик
  - на e621 в последние 3 месяца растут bioluminescence и mecha, на e6ai — ещё нет (опережающий сигнал)

  python tests/make_fake_dump.py OUTDIR e6ai 120000
"""
import csv
import gzip
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

DAY = datetime(2026, 10, 9, tzinfo=timezone.utc)
SPAN_DAYS = 7 * 125

SPECIES = ["fennec_fox", "wolf", "dragon", "protogen", "sergal", "synth", "fox", "cat", "deer", "snow_leopard",
           "tiger", "rabbit", "horse", "hyena", "otter", "bat", "lizard", "shark", "raccoon", "lion"]
GENERAL = ["military", "camouflage", "armor", "desert", "rain", "underwater", "night", "simple_background",
           "halloween", "bioluminescence", "mecha", "cyberpunk", "maid_uniform", "sitting", "standing", "smile",
           "looking_at_viewer", "solo", "duo", "outside", "inside", "clothed", "nude", "gun", "sword", "beach",
           "forest", "city", "snow", "sunset", "glasses", "hat", "scarf", "boots", "gloves", "tail", "wings"]
GENERAL += [f"filler_{i}" for i in range(120)]
CHARACTERS = ["char_a", "char_b", "char_c", "char_d"]
FRANCHISE = ["franchise_x", "franchise_y"]
META = ["hi_res", "absurd_res", "ai_generated"]
DIRECTORS = [f"director_{i}" for i in range(40)]

EFFECT = {"fennec_fox": 1.6, "military": 1.45, "underwater": 1.9, "desert": 1.5, "simple_background": 0.55,
          "camouflage": 1.25, "sergal": 1.4, "synth": 1.3, "nude": 1.2, "sitting": 0.9, "filler_3": 0.7}


def build(outdir: Path, source: str, n_posts: int, seed: int = 1):
    rng = np.random.default_rng(seed + (0 if source == "e6ai" else 100))
    outdir.mkdir(parents=True, exist_ok=True)
    pool = SPECIES + GENERAL + CHARACTERS + FRANCHISE
    zipf = 1.0 / np.arange(1, len(pool) + 1) ** 0.9
    order = rng.permutation(len(pool))
    base_p = np.empty(len(pool)); base_p[order] = zipf
    base_p = base_p / base_p.sum() * 9  # ~9 тегов на пост из пула
    pi = {t: i for i, t in enumerate(pool)}
    for t in ("underwater", "desert"):            # ниши: редкие
        base_p[pi[t]] = 0.004
    base_p[pi["simple_background"]] = 0.30
    base_p[pi["halloween"]] = 0.004
    base_p[pi["bioluminescence"]] = 0.01
    base_p[pi["mecha"]] = 0.02
    base_p[pi["armor"]] = 0.03
    base_p = np.clip(base_p, 0, 0.6)

    ages = rng.uniform(8, SPAN_DAYS, n_posts) if False else rng.uniform(0, SPAN_DAYS, n_posts)
    created = [DAY - timedelta(days=float(a)) for a in ages]
    rows = []
    tag_counts: dict[str, int] = {}
    for k in range(n_posts):
        a = ages[k]
        dt = created[k]
        p = base_p.copy()
        recency = max(0.0, 1 - a / 120)              # 0 → 120 дней назад, 1 → сейчас
        p[pi["armor"]] *= 1 + 3.0 * recency
        if source == "e621":
            p[pi["bioluminescence"]] *= 1 + 6.0 * max(0.0, 1 - a / 90)
            p[pi["mecha"]] *= 1 + 3.0 * max(0.0, 1 - a / 90)
        doy = dt.timetuple().tm_yday
        if 290 <= doy <= 305:                          # конец октября
            p[pi["halloween"]] *= 25
        chosen = [pool[i] for i in np.nonzero(rng.random(len(pool)) < p)[0]]
        if not chosen:
            chosen = ["solo"]
        mult = 1.0
        for t in chosen:
            mult *= EFFECT.get(t, 1.0)
        if "armor" in chosen:
            mult *= 1 + 0.6 * recency
        rating = rng.choice(["s", "q", "e"], p=[0.45, 0.25, 0.30])
        quality = rng.lognormal(mean=np.log(20 if source == "e6ai" else 45), sigma=0.8) * mult
        matured = 1 - np.exp(-a / 6)                   # избранное копится в первые недели
        fav = int(quality * matured)
        tags = chosen + [rng.choice(META), rng.choice(DIRECTORS)]
        for t in tags:
            tag_counts[t] = tag_counts.get(t, 0) + 1
        rows.append([k + 1, 1, dt.strftime("%Y-%m-%d %H:%M:%S.%f"), "md5", "", rating, 1024, 1536,
                     " ".join(tags), "", fav, "png", "", k, "", 123456,
                     int(rng.poisson(2)), 'описание, с запятой\nи переносом "строки"', "",
                     dt.strftime("%Y-%m-%d %H:%M:%S.%f"), "t" if rng.random() < 0.01 else "f", "f", "f",
                     fav // 2, fav // 2 + 1, -1, "f", "f", "f"])
    cols = ["id", "uploader_id", "created_at", "md5", "source", "rating", "image_width", "image_height",
            "tag_string", "locked_tags", "fav_count", "file_ext", "parent_id", "change_seq", "approver_id",
            "file_size", "comment_count", "description", "duration", "updated_at", "is_deleted", "is_pending",
            "is_flagged", "score", "up_score", "down_score", "is_rating_locked", "is_status_locked", "is_note_locked"]
    day = DAY.strftime("%Y-%m-%d")
    with gzip.open(outdir / f"posts-{day}.csv.gz", "wt", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    cat = {**{t: 5 for t in SPECIES}, **{t: 0 for t in GENERAL}, **{t: 4 for t in CHARACTERS},
           **{t: 3 for t in FRANCHISE}, **{t: 7 for t in META}, **{t: 1 for t in DIRECTORS}}
    with gzip.open(outdir / f"tags-{day}.csv.gz", "wt", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "category", "post_count"])
        for i, (t, c) in enumerate(sorted(tag_counts.items())):
            w.writerow([i + 1, t, cat.get(t, 0), c])
    return outdir / f"posts-{day}.csv.gz", outdir / f"tags-{day}.csv.gz"


if __name__ == "__main__":
    out, src, n = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    print(build(out, src, n))
