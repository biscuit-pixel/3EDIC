"""Render an SVG to a PNG with headless Chromium. usage: render_png.py in.svg out.png [size]"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

src, dst = Path(sys.argv[1]), sys.argv[2]
size = int(sys.argv[3]) if len(sys.argv) > 3 else 1024
svg = src.read_text(encoding="utf-8").replace("<svg ", f'<svg style="width:{size}px;height:{size}px;display:block" ', 1)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": size, "height": size})
    pg.set_content(f'<body style="margin:0;background:transparent">{svg}')
    pg.wait_for_timeout(300)
    pg.screenshot(path=dst, omit_background=True)
    b.close()
