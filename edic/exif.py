"""Capture time and size from a photo's EXIF without decoding the image."""
from datetime import datetime
from pathlib import Path

from PIL import Image


def read_meta(path: str | Path) -> dict:
    """-> {"taken": epoch seconds or None, "width": int, "height": int}"""
    with Image.open(path) as im:
        w, h = im.size
        exif = im.getexif()
        sub = exif.get_ifd(0x8769)
        raw = sub.get(0x9003) or exif.get(0x0132)           # DateTimeOriginal, else DateTime
        taken = None
        if raw:
            try:
                taken = datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S").timestamp()
                frac = sub.get(0x9291)                       # SubSecTimeOriginal
                if frac:
                    taken += float("0." + str(frac).strip())
            except ValueError:
                taken = None
        orient = exif.get(0x0112, 1)
    if orient in (5, 6, 7, 8):                               # rotated: report displayed size
        w, h = h, w
    return {"taken": taken, "width": w, "height": h}
