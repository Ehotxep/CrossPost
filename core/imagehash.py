"""The shared 64-bit difference hash used by calendar grouping and pre-send checks."""
from PIL import Image, ImageOps


def difference_hash(path):
    with Image.open(path) as source:
        small = ImageOps.exif_transpose(source).convert('L').resize((9, 8))
        pixels = small.load()
    value = 0
    for y in range(8):
        for x in range(8):
            value = (value << 1) | int(pixels[x + 1, y] > pixels[x, y])
    return value
