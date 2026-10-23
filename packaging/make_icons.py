"""Build the platform icons from the master PNG (packaging/icon-1024.png). To change the app icon, replace that file.
usage: make_icons.py  -> packaging/build/icon.ico, icon.icns, icon.png
"""
from pathlib import Path

from PIL import Image

here = Path(__file__).resolve().parent
out = here / "build"
out.mkdir(exist_ok=True)
master = Image.open(here / "icon-1024.png").convert("RGBA")
master.resize((512, 512), Image.LANCZOS).save(out / "icon.png")
master.save(out / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
master.save(out / "icon.icns")
print("icons:", sorted(p.name for p in out.iterdir()))
