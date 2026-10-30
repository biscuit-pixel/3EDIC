"""Several people on one photo: tagging one person must not jump to the next photo until everyone is tagged.
Builds a small synthetic event (plain pictures, invented boxes) in a temp folder, serves it, drives the real UI.
usage: ui_v2_multi.py OUT_DIR [lang]
"""
import os
import re
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

from edic.desktop import Server
from edic.engine import Engine
from edic.types import Person

out = sys.argv[1]
lang = sys.argv[2] if len(sys.argv) > 2 else "en"
errors = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name, extra, flush=True)
    if not cond:
        errors.append("FAILED: " + name)


tmp = Path(tempfile.mkdtemp(prefix="edic_multi_"))
photos = tmp / "photos"
photos.mkdir()
(tmp / "data").mkdir()
models = os.environ.get("EDIC_TEST_MODELS", "/tmp/edic_data/models")          # the UI refuses to open until the models are installed
os.symlink(models, tmp / "data" / "models")
eng = Engine(tmp / "data")
rng = np.random.default_rng(5)
layout = [2, 2, 1, 3, 1]                      # people per photo
for i, n in enumerate(layout):
    im = Image.new("RGB", (900, 600), (20 + i * 10, 22, 30))
    d = ImageDraw.Draw(im)
    for k in range(n):
        x = 90 + k * (720 // n)
        d.rectangle([x, 80, x + 150, 560], fill=(150 - k * 30, 90, 70))
    f = photos / f"S{i:02d}.jpg"
    im.save(f)
    os.utime(f, (1_700_000_000 + i * 2,) * 2)
pr = eng.create_project("Multi", str(photos))
pr.ingest()
for ph, n in zip(pr.photos(), layout):
    people = []
    for k in range(n):
        x = 90 + k * (720 // n)
        kp = np.zeros((17, 3), np.float32)
        kp[[11, 12]] = [[x + 30, 360, 1], [x + 120, 360, 1]]
        p = Person(box=(float(x), 80.0, float(x + 150), 560.0), kpts=kp)
        p.face = (lambda v: (v / np.linalg.norm(v)).astype(np.float32))(rng.normal(size=128))
        p.colour = np.abs(rng.normal(size=72)).astype(np.float32)
        people.append(p)
    pr.save_persons(ph["id"], people)
pr.rescore("basic")
srv = Server(str(tmp / "data")).start()
base, pid = srv.url.rstrip("/"), pr.id

with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--no-sandbox"])
    page = b.new_context(viewport={"width": 1440, "height": 1000}).new_page()
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("response", lambda r: errors.append(f"http {r.status}: {r.url[:110]}") if r.status >= 400 else None)

    def api(path, method="GET", body=None):
        return page.evaluate("""async ([p,m,b]) => { const r = await fetch(p,{method:m,headers:{'content-type':'application/json'},body:b?JSON.stringify(b):undefined}); return r.json(); }""", [path, method, body])

    def settle(ms=600):
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(ms)

    def pos():
        return page.locator(".tag-stage .ms-caption").first.inner_text()

    page.goto(base + "/")
    api("/api/settings", "PUT", {"language": lang, "tutorial_seen": True})
    page.reload(); settle(500)
    page.goto(f"{base}/#/e/{pid}/tag"); page.wait_for_selector(".tag-stage .pstage img"); settle(900)
    page.screenshot(path=f"{out}/{lang}-v2-multi-1.png")
    check("starts on the first photo", pos().startswith("1 ") or pos().upper().startswith("1 OF") or "1" in pos().split()[0])
    check("two people are boxed", page.locator(".pstage .pbox").count() == 2)
    first = page.locator(".pstage .pbox.sel").bounding_box()
    page.keyboard.type("12"); page.keyboard.press("Enter"); settle(1000)
    check("tagging the first person keeps the same photo", pos() == pos() and page.locator(".pstage .pbox").count() == 2)
    second = page.locator(".pstage .pbox.sel").bounding_box()
    check("...and selects the other person", abs(second["x"] - first["x"]) > 20, f"{first['x']:.0f} -> {second['x']:.0f}")
    check("the first person now shows 012", "012" in page.locator(".pstage .plabel.done").inner_text())
    page.screenshot(path=f"{out}/{lang}-v2-multi-2.png")
    page.keyboard.type("13"); page.keyboard.press("Enter"); settle(1200)
    check("after the last person the next photo opens", page.locator(".pstage .pbox").count() == 2 and "013" not in page.locator(".pstage").inner_text())
    ph0 = pr.photos()[0]["id"]
    d = api(f"/api/projects/{pid}/photos/{ph0}")
    check("both people of photo 1 are in their own albums", [a["label"] for p_ in d["persons"] for a in p_["assigned"]] == ["012", "013"], str([a["label"] for p_ in d["persons"] for a in p_["assigned"]]))
    page.keyboard.press("Tab"); settle(400)
    check("Tab switches person", page.locator(".pstage .pbox.sel").count() == 1)
    page.keyboard.press("-"); settle(1000)
    others = [a for a in api(f"/api/projects/{pid}/review/album") if a["kind"] == "others"]
    check("- puts the selected person in Others", len(others) == 1 and others[0]["confirmed"] == 1, str(api(f"/api/projects/{pid}/review/album")))
    page.screenshot(path=f"{out}/{lang}-v2-multi-3.png")
    # group photo: every tagged person's album gets the photo
    alb = {a["label"]: a for a in api(f"/api/projects/{pid}/review/album")}
    check("a group photo appears in each person's album", alb["012"]["confirmed"] == 1 and alb["013"]["confirmed"] == 1)
    print("\n".join(errors) if errors else "ALL OK")
    b.close()
srv.stop()
sys.exit(1 if errors else 0)
