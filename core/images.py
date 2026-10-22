"""Подготовка картинок под лимиты конкретной площадки.

Если файл и так подходит (формат, вес, размеры) — отправляется оригинал без изменений.
Иначе картинка уменьшается и/или пережимается, пока не влезет в лимит.
"""
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageCms, ImageFile, ImageFilter, ImageOps

Image.MAX_IMAGE_PIXELS = None  # большие арты — это нормально
ImageFile.MAXBLOCK = 64 * 1024 * 1024  # иначе optimize=True падает на больших JPEG в памяти

MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp"}
EXT = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp"}


@dataclass
class Prepared:
    name: str
    data: bytes
    mime: str
    width: int
    height: int
    changed: bool  # True, если пришлось пережимать
    label: str = ""  # как назвать версию в отчёте («лёгкая», «оригинал»…)

    def describe(self) -> str:
        mb = len(self.data) / 1024 / 1024
        note = self.label or ("пережата" if self.changed else "оригинал")
        return f"{self.name} {self.width}x{self.height}, {mb:.1f} МБ ({note})"


def _dims_ok(w, h, max_side, max_sum):
    return (not max_side or max(w, h) <= max_side) and (not max_sum or w + h <= max_sum)


def _fit_dims(im, max_side, max_sum):
    w, h = im.size
    scale = 1.0
    if max_side and max(w, h) > max_side:
        scale = min(scale, max_side / max(w, h))
    if max_sum and w + h > max_sum:
        scale = min(scale, max_sum / (w + h))
    if scale < 1.0:
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    return im


def _encode(im, fmt, **kw) -> bytes:
    buf = BytesIO()
    im.save(buf, fmt, **kw)
    return buf.getvalue()


def prepare(path: Path, max_bytes: int, formats=("JPEG", "PNG"),
            max_side: int | None = None, max_sum: int | None = None) -> Prepared:
    raw = path.read_bytes()
    with Image.open(path) as im:
        fmt = im.format
        w, h = im.size
        animated = getattr(im, "is_animated", False)

    if fmt in formats and len(raw) <= max_bytes and _dims_ok(w, h, max_side, max_sum):
        return Prepared(path.name, raw, MIME.get(fmt, "application/octet-stream"), w, h, False)

    if animated:
        raise ValueError(
            f"{path.name}: анимацию нельзя пережать без потерь, а в лимиты площадки она не влезает"
        )

    stem = path.stem
    with Image.open(path) as src:
        im = ImageOps.exif_transpose(src)
        im.load()
    im = _fit_dims(im, max_side, max_sum)

    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    if has_alpha and "PNG" in formats:
        data = _encode(im, "PNG", optimize=True)
        if len(data) <= max_bytes:
            return Prepared(stem + ".png", data, "image/png", *im.size, True)

    # Дальше — JPEG. Прозрачность заливаем белым.
    if has_alpha:
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")

    while True:
        for q in (95, 92, 88, 84, 80):
            data = _encode(im, "JPEG", quality=q, optimize=True,
                           subsampling=0 if q >= 92 else 2)
            if len(data) <= max_bytes:
                return Prepared(stem + ".jpg", data, "image/jpeg", *im.size, True)
        w, h = im.size
        if min(w, h) < 300:
            raise ValueError(f"{path.name}: не удалось ужать до {max_bytes // 1024 // 1024} МБ")
        im = im.resize((int(w * 0.85), int(h * 0.85)), Image.LANCZOS)


# --- Лёгкая версия и оригинал -------------------------------------------------

LIGHT_DEFAULTS = {
    "scale": 0.5,          # доля от размера оригинала (0.5 = 50%)
    "quality": 88,         # качество JPEG
    "subsampling": "4:4:4",  # "4:4:4" — чистые цветные края, "4:2:0" — файл меньше
    "sharpen": False,      # лёгкая резкость после уменьшения
    "background": "#FFFFFF",  # чем заливать прозрачность
}
_SUBSAMPLING = {"4:4:4": 0, "4:2:2": 1, "4:2:0": 2}


def _to_srgb(im: Image.Image) -> Image.Image:
    """Приводит картинку с встроенным цветовым профилем к sRGB (иначе цвета на площадках «уедут»)."""
    icc = im.info.get("icc_profile")
    if not icc:
        return im
    try:
        src = ImageCms.ImageCmsProfile(BytesIO(icc))
        if "srgb" in (ImageCms.getProfileDescription(src) or "").lower():
            return im
        mode = "RGBA" if "A" in im.getbands() else "RGB"
        return ImageCms.profileToProfile(im, src, ImageCms.createProfile("sRGB"), outputMode=mode)
    except Exception:
        return im  # битый профиль — оставляем как есть


def make_light(path: Path, recipe: dict | None = None, max_bytes: int | None = None,
               max_sum: int | None = None, max_side: int | None = None) -> Prepared:
    """Лёгкий JPEG по рецепту (по умолчанию 50%, q88, 4:4:4). Служебные данные не сохраняются.

    Если результат не влезает в лимиты площадки, дополнительно уменьшается только для неё.
    """
    r = {**LIGHT_DEFAULTS, **(recipe or {})}
    with Image.open(path) as src:
        im = ImageOps.exif_transpose(src)
        im.load()
    im = _to_srgb(im)

    scale = float(r["scale"])
    if 0 < scale < 1:
        w, h = im.size
        im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    im = _fit_dims(im, max_side, max_sum)

    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, r["background"])
        bg.paste(rgba, mask=rgba.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    if r["sharpen"]:
        im = im.filter(ImageFilter.UnsharpMask(radius=0.8, percent=40, threshold=2))

    q = int(r["quality"])
    sub = _SUBSAMPLING.get(str(r["subsampling"]), 0)
    while True:
        data = _encode(im, "JPEG", quality=q, subsampling=sub, optimize=True)
        if not max_bytes or len(data) <= max_bytes:
            return Prepared(path.stem + ".jpg", data, "image/jpeg", *im.size, True, "лёгкая")
        w, h = im.size  # не влезло в лимит — уменьшаем ещё
        if min(w, h) < 300:
            raise ValueError(f"{path.name}: лёгкая версия не влезает в {max_bytes // 1024 // 1024} МБ")
        im = im.resize((int(w * 0.85), int(h * 0.85)), Image.LANCZOS)


def load_original(path: Path, max_bytes: int | None = None) -> Prepared:
    """Оригинал как есть, байт в байт. Если он больше лимита и это PNG — пробуем пережать без потерь."""
    raw = path.read_bytes()
    with Image.open(path) as im:
        fmt, (w, h) = im.format, im.size
    if not max_bytes or len(raw) <= max_bytes:
        return Prepared(path.name, raw, MIME.get(fmt, "application/octet-stream"), w, h, False, "оригинал")
    if fmt == "PNG":
        with Image.open(path) as im:
            im.load()
            data = _encode(im, "PNG", optimize=True, compress_level=9)
        if len(data) <= max_bytes:
            return Prepared(path.name, data, "image/png", w, h, True, "оригинал, пережат без потерь")
    mb = len(raw) / 1024 / 1024
    raise ValueError(f"{path.name}: оригинал {mb:.0f} МБ больше лимита {max_bytes // 1024 // 1024} МБ")
