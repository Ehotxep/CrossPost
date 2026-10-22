#!/usr/bin/env python3
"""Кросспостинг картинок в Discord, Telegram, VK, X, DeviantArt и Reddit одной командой.

Примеры:
  python cli/crosspost.py art.png -t "Новый арт!" --title "Закат" --tags sunset,digitalart
  python cli/crosspost.py a.png b.jpg -t "Скетчи недели" --only telegram,discord
  python cli/crosspost.py art.png -t "Тест" --dry-run
  python cli/crosspost.py spicy.png -t "Для взрослых" --nsfw   # уйдёт только туда, где разрешено 18+
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.common import Post, PlatformError, apply_rating, load_config  # noqa: E402
from core.images import prepare  # noqa: E402
from platforms import deviantart, discord, reddit, telegram, vk, x  # noqa: E402

PLATFORMS = {"discord": discord, "telegram": telegram, "vk": vk, "x": x,
             "deviantart": deviantart, "reddit": reddit}


def main() -> int:
    ap = argparse.ArgumentParser(description="Опубликовать картинки сразу на нескольких площадках.")
    ap.add_argument("images", nargs="+", type=Path, help="файлы картинок")
    ap.add_argument("-t", "--text", default="", help="текст поста")
    ap.add_argument("--text-file", type=Path, help="взять текст из файла (удобно для длинных и многострочных)")
    ap.add_argument("--title", default="", help="название работы (обязательно для DeviantArt)")
    ap.add_argument("--tags", default="", help="теги для DeviantArt через запятую")
    rating = ap.add_mutually_exclusive_group()
    rating.add_argument("--nsfw", "--mature", dest="nsfw", action="store_const", const=True,
                        help="контент 18+: только в места с rating nsfw/all, с пометками 18+")
    rating.add_argument("--sfw", dest="nsfw", action="store_const", const=False,
                        help="обычный контент: только в места с rating sfw/all")
    ap.add_argument("--only", default="", help="только эти площадки: discord,telegram,vk,x,deviantart,reddit")
    ap.add_argument("--skip", default="", help="пропустить эти площадки")
    ap.add_argument("--dry-run", action="store_true", help="ничего не публиковать, только показать, что будет")
    ap.add_argument("-y", "--yes", action="store_true", help="не спрашивать подтверждение")
    ap.add_argument("--config", type=Path, help="путь к config.toml")
    args = ap.parse_args()

    cfg = load_config(args.config)
    for img in args.images:
        if not img.exists():
            raise SystemExit(f"Файл не найден: {img}")

    nsfw = args.nsfw
    if nsfw is None:
        if not cfg.get("general", {}).get("require_rating", True):
            nsfw = False
        elif args.yes or not sys.stdin.isatty():
            raise SystemExit("Укажите --sfw или --nsfw (без подтверждения скрипт не угадывает рейтинг).")
        else:
            ans = input("Это контент 18+ (NSFW)? [y/n] ").strip().lower()
            if ans not in ("y", "yes", "д", "да", "n", "no", "н", "нет"):
                raise SystemExit("Нужно ответить y или n. Отменено.")
            nsfw = ans in ("y", "yes", "д", "да")

    text = args.text_file.read_text(encoding="utf-8").strip() if args.text_file else args.text
    p = Post(text=text, title=args.title,
             tags=[t.strip() for t in args.tags.split(",") if t.strip()], mature=nsfw)

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    unknown = (only | skip) - PLATFORMS.keys()
    if unknown:
        raise SystemExit(f"Неизвестные площадки: {', '.join(unknown)}")
    targets = [n for n in PLATFORMS
               if cfg.get(n, {}).get("enabled") and (not only or n in only) and n not in skip]
    if not targets:
        raise SystemExit("Нет включённых площадок. Проверьте enabled = true в config.toml и флаги --only/--skip.")

    # Подготовка картинок под каждую площадку
    plan = {}
    print(f"Контент: {'NSFW (18+)' if nsfw else 'SFW'}\n")
    print("Подготовка:")
    for name in targets:
        mod = PLATFORMS[name]
        c, dests, skipped_dests = apply_rating(name, cfg[name], nsfw)
        if c is None:
            print(f"  – {mod.NAME}: пропуск — {', '.join(skipped_dests)}")
            continue
        imgs = args.images
        if len(imgs) > mod.MAX_IMAGES:
            print(f"  ! {mod.NAME}: максимум {mod.MAX_IMAGES} картинок, лишние не попадут")
            imgs = imgs[:mod.MAX_IMAGES]
        c = dict(c, _light=cfg.get("light", {}))
        try:
            if hasattr(mod, "prepare_images"):  # площадка готовит несколько версий сама (Telegram)
                prepared = mod.prepare_images(c, imgs)
            else:
                prepared = [prepare(i, **mod.limits(c)) for i in imgs]
        except (ValueError, OSError) as e:
            print(f"  ✗ {mod.NAME}: не удалось подготовить картинку: {e}")
            continue
        except PlatformError as e:
            print(f"  ✗ {e}")
            continue
        plan[name] = (c, prepared)
        if hasattr(prepared, "describe_lines"):
            print(f"  {mod.NAME}:")
            for line in prepared.describe_lines():
                print(f"    {line}")
        else:
            print(f"  {mod.NAME}: " + "; ".join(pi.describe() for pi in prepared))
        if dests:
            print(f"    куда: {', '.join(dests)}")
        if skipped_dests:
            print(f"    пропущено по рейтингу: {', '.join(skipped_dests)}")
        if hasattr(mod, "plan_lines"):
            try:
                for line in mod.plan_lines(c, p):
                    print(f"    {line}")
            except PlatformError as e:
                print(f"  ✗ {e}")
                del plan[name]
                continue
        if name == "x":
            xt, stripped = x.prepare_text(c, p)
            if stripped:
                print("    (ссылки из текста для X убраны: пост со ссылкой стоит ~$0.20 вместо ~$0.02)")
            if xt != p.text:
                print(f"    текст для X: {xt!r}")
        if name == "deviantart" and not p.title and not c.get("default_title"):
            print("    ! у работы не будет названия — добавьте --title")
        if name == "reddit":
            print(f"    заголовок: {reddit.make_title(c, p)!r}")

    if args.dry_run:
        print("\nПробный запуск — ничего не опубликовано.")
        return 0
    if not plan:
        print("\nНекуда публиковать: все площадки пропущены.")
        return 1
    if not args.yes:
        ans = input(f"\nОпубликовать в: {', '.join(PLATFORMS[n].NAME for n in plan)}? [y/N] ").strip().lower()
        if ans not in ("y", "yes", "д", "да"):
            print("Отменено.")
            return 1

    print("\nПубликация:")
    failed = []
    for name, (c, prepared) in plan.items():
        mod = PLATFORMS[name]
        try:
            links = mod.post(c, prepared, p)
            print(f"  ✓ {mod.NAME}: " + ", ".join(map(str, links)))
        except PlatformError as e:
            print(f"  ✗ {e}")
            failed.append(name)
        except Exception as e:  # сеть, неожиданный ответ и т.п. — не роняем остальные площадки
            print(f"  ✗ {mod.NAME}: {type(e).__name__}: {e}")
            failed.append(name)

    if failed:
        print(f"\nНе получилось: {', '.join(failed)}. Повторить только их:\n"
              f"  python cli/crosspost.py ... --only {','.join(failed)}")
        return 1
    print("\nГотово!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
