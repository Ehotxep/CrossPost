"""GIF и видео: ffmpeg (ставится кнопкой), разбор файла и переделка — только когда площадка иначе не примет.

Правило: файл уходит как есть. Переделываем, только если:
  • Telegram (бот): больше 50 МБ или кодек, который Telegram не покажет в ленте (не H.264) → H.264 MP4;
  • Discord: больше лимита сервера или кодек/формат, который Discord не проигрывает → H.264 MP4 под лимит
    (большой GIF тоже становится MP4 — так он в разы легче);
  • e6ai: формат, который сайт не принял (например, MOV, или MP4, если e6ai его не берёт) → WebM (VP9);
  • ВКонтакте видео и GIF принимает как есть.
Отдельно, кнопкой «Изменить» на плитке видео или GIF, — edit(): отрезок, рамка, поворот, разрешение, MP4 или GIF (палитра под ролик, потолок по весу).
Готовые переделки кешируются рядом с файлом (uploads/<id>.conv-*.{mp4,webm}) и удаляются вместе с ним.
"""
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from app.paths import sub

FFMPEG_ZIP = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
TG_PLAYS = {"h264"}
DISCORD_PLAYS = {"h264", "vp8", "vp9"}
NOWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def tools_dir() -> Path:
    return sub("tools", "ffmpeg")


def tool(name: str) -> str | None:
    """Путь к ffmpeg/ffprobe: сначала свой (поставленный кнопкой), потом системный."""
    exe = name + (".exe" if sys.platform == "win32" else "")
    own = tools_dir() / exe
    if own.exists():
        return str(own)
    return shutil.which(name)


def ready() -> bool:
    return bool(tool("ffmpeg") and tool("ffprobe"))


def status() -> dict:
    ff = tool("ffmpeg")
    ver = ""
    if ff:
        try:
            out = subprocess.run([ff, "-hide_banner", "-version"], capture_output=True, text=True, timeout=15, creationflags=NOWIN).stdout
            ver = (out.splitlines() or [""])[0].replace("ffmpeg version ", "")[:60]
        except Exception:
            pass
    return {"ready": ready(), "path": ff or "", "version": ver, "own": bool(ff and Path(ff).parent == tools_dir()),
            "can_install": sys.platform == "win32"}


def install(report) -> dict:
    """Скачать ffmpeg для Windows (сборка BtbN с GitHub, ~150 МБ) и оставить только ffmpeg.exe и ffprobe.exe."""
    if sys.platform != "win32":
        raise RuntimeError("Автоустановка — только для Windows. Поставьте ffmpeg из пакетов системы.")
    import requests
    d = tools_dir()
    z = d / "ffmpeg.zip.part"
    report(progress=0.01, message="Скачиваю ffmpeg с GitHub…")
    with requests.get(FFMPEG_ZIP, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(z, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    report(progress=0.9 * done / total, message=f"Скачиваю ffmpeg: {done / 1e6:.0f} из {total / 1e6:.0f} МБ")
    report(progress=0.92, message="Распаковываю…")
    with zipfile.ZipFile(z) as zf:
        for n in zf.namelist():
            base = n.rsplit("/", 1)[-1]
            if n.split("/")[-2:-1] == ["bin"] and base in ("ffmpeg.exe", "ffprobe.exe"):
                with zf.open(n) as src, open(d / (base + ".tmp"), "wb") as dst:
                    shutil.copyfileobj(src, dst)
                (d / (base + ".tmp")).replace(d / base)
    z.unlink(missing_ok=True)
    if not ready():
        raise RuntimeError("В архиве не нашлось ffmpeg.exe — попробуйте ещё раз позже")
    report(progress=1.0, message="ffmpeg установлен")
    return status()


def _run(args, timeout=1800):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=NOWIN)
    if p.returncode != 0:
        tail = "\n".join((p.stderr or "").strip().splitlines()[-3:])
        raise RuntimeError(f"ffmpeg: {tail[:300]}")
    return p


def probe(path) -> dict:
    """{duration, width, height, vcodec, acodec, fps, frames?} — или {} без ffprobe."""
    fp = tool("ffprobe")
    if not fp:
        return {}
    try:
        p = _run([fp, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)], timeout=60)
        j = json.loads(p.stdout or "{}")
    except Exception:
        return {}
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), {})
    try:
        num, den = (v.get("avg_frame_rate") or "0/1").split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except ValueError:
        fps = 0.0
    dur = float((j.get("format") or {}).get("duration") or v.get("duration") or 0)
    w, h = v.get("width"), v.get("height")
    rot = (v.get("tags") or {}).get("rotate")
    for sd in v.get("side_data_list") or []:
        rot = sd.get("rotation", rot)
    try:
        if rot is not None and int(float(rot)) % 180 != 0:   # снято на телефоне боком: ffmpeg и браузер показывают уже повёрнутым
            w, h = h, w
    except (TypeError, ValueError):
        pass
    return {"duration": dur, "width": w, "height": h, "vcodec": v.get("codec_name"),
            "acodec": a.get("codec_name"), "fps": round(fps, 2), "format": (j.get("format") or {}).get("format_name", "")}


def frame(src, dst) -> bool:
    """Кадр из видео (в начале, но не самый первый — он часто чёрный) — для миниатюры и теггера."""
    ff = tool("ffmpeg")
    if not ff:
        return False
    info = probe(src)
    at = min(1.0, (info.get("duration") or 0) * 0.1)
    try:
        _run([ff, "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1", "-q:v", "3", str(dst)], timeout=120)
        return Path(dst).exists()
    except Exception:
        return False


def _cache(uid: str, key: str, ext: str) -> Path:
    return sub("uploads") / f"{uid}.conv-{key}.{ext}"


def _keep(uid: str, src, out: Path, label: str) -> Path:
    """Копия перекодированного видео — в папку готовых файлов («имя_h264_50MB.mp4»); возвращает сам out."""
    try:
        from app.publish import outputs
        outputs.save("video", out, f"{Path(src).stem}_{label}{out.suffix}", f"{uid}|{out.name}")
    except Exception:   # noqa: BLE001
        pass
    return out


def to_mp4(uid: str, src, max_bytes: int | None = None, log=lambda m: None) -> Path:
    """H.264 + AAC, yuv420p, faststart. С max_bytes — подбираем битрейт (и уменьшаем кадр), чтобы влезть."""
    ff = tool("ffmpeg")
    if not ff:
        raise RuntimeError("нужен ffmpeg — «Настройки» → «Видео и GIF» → «Установить ffmpeg»")
    key = "mp4" + (f"-{max_bytes // 1024 // 1024}m" if max_bytes else "")
    out = _cache(uid, key, "mp4")
    label = "h264" + (f"_{max_bytes // 1024 // 1024}MB" if max_bytes else "")
    if out.exists():
        return _keep(uid, src, out, label)
    info = probe(src)
    dur = max(info.get("duration") or 0, 0.5)
    w = info.get("width") or 0
    tmp = out.with_name(out.name + ".tmp.mp4")
    is_gif = Path(src).suffix.lower() == ".gif" or info.get("vcodec") == "gif"
    audio = [] if is_gif or not info.get("acodec") else ["-c:a", "aac", "-b:a", "128k"]
    base = [ff, "-y", "-v", "error", "-i", str(src), "-map", "0:v:0"] + (["-map", "0:a:0?"] if audio else ["-an"])
    vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    for attempt in range(4):
        if max_bytes:
            total_kbps = max_bytes * 8 / 1000 / dur * (0.93 - 0.05 * attempt)
            v_kbps = max(150, int(total_kbps - (128 if audio else 0)))
            side = None   # слишком низкий битрейт для такого кадра — уменьшаем кадр
            if w and v_kbps < 2500 and w > 1280:
                side = 1280
            if w and v_kbps < 1000 and w > 854:
                side = 854
            if w and v_kbps < 450 and w > 640:
                side = 640
            vf2 = f"scale='min({side},iw)':-2" if side else vf
            rate = ["-b:v", f"{v_kbps}k", "-maxrate", f"{int(v_kbps * 1.3)}k", "-bufsize", f"{v_kbps * 2}k"]
        else:
            vf2, rate = vf, ["-crf", "20"]
        log("перекодирую видео в MP4" + (f" под {max_bytes // 1024 // 1024} МБ" if max_bytes else ""))
        _run(base + ["-vf", vf2, "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p", *rate, *audio,
                     "-movflags", "+faststart", str(tmp)])
        if not max_bytes or tmp.stat().st_size <= max_bytes:
            tmp.replace(out)
            return _keep(uid, src, out, label)
    tmp.unlink(missing_ok=True)
    raise RuntimeError(f"не получилось ужать видео до {max_bytes // 1024 // 1024} МБ — оно слишком длинное")


def to_webm(uid: str, src, log=lambda m: None) -> Path:
    ff = tool("ffmpeg")
    if not ff:
        raise RuntimeError("нужен ffmpeg — «Настройки» → «Видео и GIF» → «Установить ffmpeg»")
    out = _cache(uid, "webm", "webm")
    if out.exists():
        return _keep(uid, src, out, "vp9")
    info = probe(src)
    tmp = out.with_name(out.name + ".tmp.webm")
    audio = ["-c:a", "libopus", "-b:a", "128k"] if info.get("acodec") else ["-an"]
    log("перекодирую видео в WebM для e6ai")
    _run([ff, "-y", "-v", "error", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0",
          "-row-mt", "1", "-pix_fmt", "yuv420p", *audio, str(tmp)], timeout=3600)
    tmp.replace(out)
    return _keep(uid, src, out, "vp9")


GIF_FPS = (8, 10, 12, 15, 20, 24)
GIF_MAX_SECONDS = 60
SHORT_SIDES = (360, 480, 720)   # «разрешение» в редакторе — короткая сторона кадра
BROWSER_PLAYS = {"h264", "vp8", "vp9"}
STRIP_N = 10


def strip(uid: str, src, n: int = STRIP_N, tile_h: int = 72, tile_w: int = 84) -> Path:
    """Лента кадров для полосы времени: n кадров в ряд одной картинкой (кеш рядом с файлом)."""
    out = sub("uploads") / f"{uid}.strip.jpg"
    if out.exists():
        return out
    ff = tool("ffmpeg")
    if not ff:
        raise RuntimeError("нужен ffmpeg — «Настройки» → «Видео и GIF» → «Установить ffmpeg»")
    from PIL import Image, ImageOps
    dur = max(probe(src).get("duration") or 0, 0.1)
    sheet = Image.new("RGB", (tile_w * n, tile_h), (20, 22, 28))
    tmp = out.with_name(out.name + ".f.jpg")
    for i in range(n):
        try:
            _run([ff, "-y", "-v", "error", "-ss", f"{dur * (i + 0.5) / n:.3f}", "-i", str(src), "-frames:v", "1", "-q:v", "4", "-f", "image2", str(tmp)], timeout=60)
            with Image.open(tmp) as im:
                sheet.paste(ImageOps.fit(im.convert("RGB"), (tile_w, tile_h)), (i * tile_w, 0))
        except Exception:   # noqa: BLE001 — один кадр не вышел: остаётся тёмная плитка
            pass
    tmp.unlink(missing_ok=True)
    sheet.save(out, "JPEG", quality=80)
    return out


def preview(uid: str, src) -> Path:
    """Файл для проигрывания в окне редактора: сам файл, если браузер его играет, иначе лёгкий беззвучный MP4 (кеш)."""
    info = probe(src)
    if Path(src).suffix.lower() in (".mp4", ".webm", ".m4v") and info.get("vcodec") in BROWSER_PLAYS:
        return Path(src)
    out = _cache(uid, "preview", "mp4")
    if out.exists():
        return out
    ff = tool("ffmpeg")
    if not ff:
        raise RuntimeError("нужен ffmpeg — «Настройки» → «Видео и GIF» → «Установить ffmpeg»")
    tmp = out.with_name(out.name + ".tmp.mp4")
    _run([ff, "-y", "-v", "error", "-i", str(src), "-map", "0:v:0", "-an", "-vf", "scale='min(960,iw)':-2:flags=bicubic,fps=30",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp)], timeout=1800)
    tmp.replace(out)
    return out


def _even(x: float) -> int:
    return max(2, int(x) // 2 * 2)


LOOP_MODES = ("none", "cross", "pingpong", "fade")
XFADES = {"fade": "Плавное растворение", "dissolve": "Растворение точками", "fadeblack": "Через чёрное", "slideleft": "Сдвиг влево", "wipeleft": "Шторка"}
LOOP_MAX_D = 3.0
REVERSE_MAX_BYTES = 1_200_000_000   # пинг-понг держит весь отрезок в памяти


def loop_params(loop: dict | None, length: float) -> dict:
    """Проверенные параметры зацикливания: {mode, d, xf, color}. d — длительность перехода, не больше трети отрезка."""
    loop = loop or {}
    mode = loop.get("mode") or "none"
    if mode not in LOOP_MODES:
        raise RuntimeError("Неизвестный режим зацикливания")
    xf = loop.get("xf") or "fade"
    if xf not in XFADES:
        raise RuntimeError("Неизвестный переход")
    color = "white" if loop.get("color") == "white" else "black"
    d = max(0.2, min(float(loop.get("d") or 1.0), LOOP_MAX_D, length / 3))
    if mode in ("cross", "fade") and length < 0.9:
        raise RuntimeError("Для перехода нужен отрезок хотя бы около секунды")
    if mode == "pingpong" and length < 0.4:
        raise RuntimeError("Для пинг-понга нужен отрезок хотя бы 0,4 секунды")
    return {"mode": mode, "d": round(d, 2), "xf": xf, "color": color}


def loop_graph(lp: dict, pre: list, length: float, fps: float, gif: bool, size: tuple, audio: bool) -> dict:
    """Граф ffmpeg для зацикливания. pre — фильтры до эффекта (поворот, рамка, масштаб).
    Возвращает {"v": строка графа с выходом [o], "a": граф звука с выходом [ao] или None, "len": длина результата, с}.
    Звуку кроссфейда нужен второй вход (то же видео, первые d+0.3 с) — см. edit()."""
    mode, d, col = lp["mode"], lp["d"], lp["color"]
    n = max(3, round(length * fps))
    head = ["setpts=PTS-STARTPTS", f"fps={fps:g}"] + pre
    if not gif:
        head.append("format=yuv420p")
    elif mode == "cross":
        head.append("format=gbrp")   # xfade не работает с палитровыми кадрами
    v = "[0:v:0]" + ",".join(head) + "[v];"
    a = None
    if mode == "fade":
        v += f"[v]fade=t=in:st=0:d={d}:color={col},fade=t=out:st={length - d:.3f}:d={d}:color={col}[o]"
        if audio:
            a = f"[0:a:0]afade=t=in:st=0:d={d},afade=t=out:st={length - d:.3f}:d={d}[ao]"
        out = length
    elif mode == "pingpong":
        mem = size[0] * size[1] * (3 if gif else 1.5) * fps * length
        if mem > REVERSE_MAX_BYTES:
            raise RuntimeError("Для пинг-понга отрезок слишком длинный для такого разрешения: возьмите отрезок покороче, кадр меньше или меньше кадров в секунду")
        v += f"[v]split[pa][pb];[pb]reverse,trim=start_frame=1:end_frame={n - 1},setpts=PTS-STARTPTS[pr];[pa][pr]concat=n=2:v=1:a=0[o]"
        out = length + (n - 2) / fps   # крайние кадры не повторяются — шов без «залипания»
    else:   # cross: хвост (последние d с) плавно переходит в начало (первые d с); ролик короче на d
        v += (f"[v]split[m0][h0];[m0]trim=start={d},setpts=PTS-STARTPTS[m];[h0]trim=end={d},setpts=PTS-STARTPTS[h];"
              f"[m][h]xfade=transition={lp['xf']}:duration={d}:offset={length - 2 * d:.3f}[o]")
        if audio:   # звук: начало берём вторым входом (asplit + acrossfade в ffmpeg зависает без кадров)
            a = (f"[0:a:0]atrim=start={d},asetpts=PTS-STARTPTS[am];[1:a:0]asetpts=PTS-STARTPTS[ah];"
                 f"[am][ah]acrossfade=d={d},atrim=end={length - d:.3f},asetpts=PTS-STARTPTS[ao]")
        out = length - d
    return {"v": v, "a": a, "len": out}


def plan_edit(info: dict, o: dict) -> dict:
    """Что получится: размеры кадра после поворота, обрезки и масштаба. o — параметры правки (см. edit)."""
    w0, h0 = int(info.get("width") or 0), int(info.get("height") or 0)
    rot = int(o.get("rotate") or 0) % 360
    if rot in (90, 270):
        w0, h0 = h0, w0
    cr = o.get("crop") or {}
    cx, cy = max(0.0, min(float(cr.get("x") or 0), 0.95)), max(0.0, min(float(cr.get("y") or 0), 0.95))
    cw, ch = float(cr.get("w") or 1), float(cr.get("h") or 1)
    cw, ch = max(0.05, min(cw, 1 - cx)), max(0.05, min(ch, 1 - cy))
    px, py = _even(w0 * cx) if cx else 0, _even(h0 * cy) if cy else 0
    pw, ph = _even(w0 * cw), _even(h0 * ch)
    pw, ph = min(pw, w0 - px) // 2 * 2 or 2, min(ph, h0 - py) // 2 * 2 or 2
    full = (px, py, pw, ph) == (0, 0, w0, h0) or not w0
    ow, oh = pw, ph
    short = int(o.get("short") or 0)
    if short and min(ow, oh) > short:
        k = short / min(ow, oh)
        ow, oh = _even(ow * k), _even(oh * k)
    return {"rot": rot, "flip": bool(o.get("flip")), "crop": None if full else (pw, ph, px, py), "out": (ow, oh), "scale": (ow, oh) != (pw, ph)}


def edit(src, dst, o: dict, report=lambda **kw: None) -> dict:
    """Правка видео или GIF: отрезок по времени, поворот, отражение, рамка обрезки, разрешение, зацикливание — на выходе MP4 или GIF.
    o: {start, end (с), rotate (0/90/180/270), flip, crop {x,y,w,h в долях кадра после поворота}, short (360/480/720 или 0),
        gif (bool), fps (для GIF), max_bytes (для GIF — иначе уменьшаем кадр и частоту, до 4 попыток),
        loop {mode: none/cross/pingpong/fade, d, xf, color}, preview (быстрый беззвучный WebM до 360p — посмотреть петлю)}.
    Возвращает {"size", "width", "height", "fps", "length", "attempts", "over"}."""
    ff = tool("ffmpeg")
    if not ff:
        raise RuntimeError("нужен ffmpeg — «Настройки» → «Видео и GIF» → «Установить ffmpeg»")
    info = probe(src)
    total = float(info.get("duration") or 0)
    start = max(0.0, float(o.get("start") or 0))
    if total and start >= total:
        raise RuntimeError(f"начало ({start:g} с) за пределами ролика — он длится {total:.1f} с")
    end = float(o.get("end") or 0) or total
    if total:
        end = min(end, total)
    length = (end - start) if end else GIF_MAX_SECONDS
    gif = bool(o.get("gif"))
    if gif:
        length = min(length, GIF_MAX_SECONDS)
    if length < 0.2:
        raise RuntimeError("отрезок слишком короткий")
    lp = loop_params(o.get("loop"), length)
    preview = bool(o.get("preview"))
    if preview:
        if lp["mode"] == "none":
            raise RuntimeError("Выберите режим зацикливания, чтобы посмотреть петлю")
        gif = False
        o = dict(o, short=min(int(o.get("short") or 360), 360))
    pl = plan_edit(info, o)
    chain = []
    if pl["rot"] == 90:
        chain.append("transpose=1")
    elif pl["rot"] == 180:
        chain.append("transpose=1,transpose=1")
    elif pl["rot"] == 270:
        chain.append("transpose=2")
    if pl["flip"]:
        chain.append("hflip")
    if pl["crop"]:
        pw, ph, px, py = pl["crop"]
        chain.append(f"crop={pw}:{ph}:{px}:{py}")
    dst = Path(dst)
    tmp = dst.with_name(dst.name + ".tmp." + ("gif" if gif else "webm" if preview else "mp4"))
    err_f = dst.with_name(dst.name + ".err")
    max_bytes = int(o.get("max_bytes") or 0) if gif else 0
    f = int(o.get("fps") or 15)
    scale_w = pl["out"][0] if pl["scale"] else 0
    ow, oh = pl["out"]
    has_a = bool(info.get("acodec")) and Path(src).suffix.lower() != ".gif" and not preview
    palette = ",split[a][b];[a]palettegen=max_colors=256:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle"
    src_args = ["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(src)]
    base = [ff, "-y", "-v", "error", "-nostats", "-progress", "pipe:1"]
    out_len = length
    for attempt in range(1, 5 if max_bytes else 2):
        ch = list(chain)
        if lp["mode"] != "none":   # зацикливание: общий граф фильтров
            fps_out = float(f) if gif else (15.0 if preview else float(round(info.get("fps") or 30) or 30))
            if gif:
                if scale_w:
                    ch.append(f"scale={ow}:{oh}:flags=lanczos")
            else:
                ch.append(f"scale={ow}:{oh}:flags=lanczos" if pl["scale"] else "scale=trunc(iw/2)*2:trunc(ih/2)*2")
            g = loop_graph(lp, ch, length, fps_out, gif, (ow, oh), has_a)
            out_len = g["len"]
            if gif:
                cmd = base + src_args + ["-filter_complex", g["v"] + ";[o]" + palette.lstrip(",") + "[g]",
                                         "-map", "[g]", "-an", "-loop", "0", "-f", "gif", str(tmp)]
            else:
                enc = (["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1", "-b:v", "0", "-crf", "38", "-pix_fmt", "yuv420p", "-f", "webm", str(tmp)]
                       if preview else   # просмотр — WebM: его играет любой движок, и он собирается быстро
                       ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-f", "mp4", str(tmp)])
                if g["a"]:
                    two = ["-ss", f"{start:.3f}", "-t", f"{lp['d'] + 0.3:.3f}", "-i", str(src)] if lp["mode"] == "cross" else []
                    cmd = base + src_args + two + ["-filter_complex", g["v"] + ";" + g["a"], "-map", "[o]", "-map", "[ao]", "-c:a", "aac", "-b:a", "128k"] + enc
                else:
                    cmd = base + src_args + ["-filter_complex", g["v"], "-map", "[o]", "-an"] + enc
        elif gif:
            ch.insert(0, f"fps={f}")
            if scale_w:
                ch.append(f"scale={ow}:{oh}:flags=lanczos")
            cmd = base + src_args + ["-filter_complex", "[0:v:0]" + ",".join(ch) + palette, "-an", "-loop", "0", "-f", "gif", str(tmp)]
        else:
            ch.append(f"scale={ow}:{oh}:flags=lanczos" if pl["scale"] else "scale=trunc(iw/2)*2:trunc(ih/2)*2")
            cmd = base + src_args + ["-map", "0:v:0"] + (["-map", "0:a:0?", "-c:a", "aac", "-b:a", "128k"] if has_a else ["-an"]) + \
                  ["-vf", ",".join(ch), "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-f", "mp4", str(tmp)]
        with open(err_f, "w", encoding="utf-8", errors="replace") as ef:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=ef, text=True, creationflags=NOWIN)
            try:
                for line in p.stdout:
                    if line.startswith(("out_time_us=", "out_time_ms=")):
                        try:
                            done = int(line.split("=", 1)[1]) / 1e6
                        except ValueError:
                            continue
                        frac = max(0.0, min(done / max(out_len, 0.1), 1.0))
                        report(progress=(((attempt - 1) + frac) / 4 if max_bytes else frac) * 0.98,
                               message=("Делаю GIF… " if gif else "Обрабатываю видео… ") + f"{int(frac * 100)}%" + (f" (попытка {attempt})" if attempt > 1 else ""))
                code = p.wait()
            finally:   # ошибка в отчёте о ходе или отмена — ffmpeg не остаётся висеть в фоне
                if p.poll() is None:
                    p.kill()
                    p.wait()
        if code != 0:
            tail = "\n".join(err_f.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:])
            tmp.unlink(missing_ok=True)
            err_f.unlink(missing_ok=True)
            raise RuntimeError(f"ffmpeg: {tail[:300] or 'не получилось обработать файл'}")
        err_f.unlink(missing_ok=True)
        size = tmp.stat().st_size
        if not max_bytes or size <= max_bytes or attempt == 4:
            tmp.replace(dst)
            return {"size": size, "width": ow, "height": oh, "fps": f if gif else info.get("fps"), "length": round(out_len, 2),
                    "attempts": attempt, "over": bool(max_bytes and size > max_bytes)}
        k = min((max_bytes / size) ** 0.5 * 0.92, 0.85)   # слишком тяжело: кадр меньше, кадры реже
        ow, oh = max(2, _even(ow * k)), max(2, _even(oh * k))
        scale_w = ow
        f = max(8, int(f * 0.8))
    raise RuntimeError("не получилось обработать файл")


def remove(uid: str):
    for f in sub("uploads").glob(f"{uid}.conv-*"):
        f.unlink(missing_ok=True)
    (sub("uploads") / f"{uid}.frame.jpg").unlink(missing_ok=True)


# --- что нужно каждой площадке -------------------------------------------------------------

def need_for(platform: str, f: dict, info: dict, limit: int | None = None) -> str | None:
    """Почему файл не уйдёт как есть (коротко, для «Куда уйдёт»), или None — уходит как есть.
    f — описание файла (kind, size, name), info — probe() (может быть пустым без ffprobe)."""
    ext = Path(f["name"]).suffix.lower()
    vcodec = info.get("vcodec")
    if f["kind"] == "gif" and platform == "bluesky":
        return "GIF уйдёт видео (Bluesky не принимает GIF)"
    if f["kind"] == "gif":
        if limit and f["size"] > limit:
            return "GIF тяжелее лимита — станет MP4"
        return None
    # видео
    if platform == "telegram":
        if limit and f["size"] > limit:
            return f"больше {limit // 1024 // 1024} МБ — ужму"
        if ext != ".mp4" or (vcodec and vcodec not in TG_PLAYS):
            return "перекодирую в H.264 MP4"
        return None
    if platform == "discord":
        if limit and f["size"] > limit:
            return f"больше {limit // 1024 // 1024} МБ — ужму"
        if ext not in (".mp4", ".webm") or (vcodec and vcodec not in DISCORD_PLAYS):
            return "перекодирую в H.264 MP4"
        return None
    if platform == "bluesky":
        if f["kind"] == "gif":
            return "GIF уйдёт видео (Bluesky не принимает GIF)"
        if limit and f["size"] > limit:
            return f"больше {limit // 1024 // 1024} МБ — ужму"
        if ext != ".mp4" or (vcodec and vcodec not in TG_PLAYS):
            return "перекодирую в H.264 MP4"
        return None
    if platform == "e6ai":
        if ext not in (".mp4", ".webm") or (vcodec and vcodec not in DISCORD_PLAYS | {"av1"}):
            return "перекодирую в WebM"
        return None
    return None
