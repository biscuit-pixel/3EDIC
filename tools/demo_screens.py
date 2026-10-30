"""Builds a synthetic demo event (generated figures and invented numbers, no real people) and takes the README screenshots of the real UI.
usage: python tools/demo_screens.py OUT_DIR        (needs the models installed in $EDIC_TEST_MODELS, only so the UI opens)
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from playwright.sync_api import sync_playwright

from edic.desktop import Server
from edic.engine import Engine
from edic.types import Person

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
W, H = 600, 900
rng = np.random.default_rng(11)
NUMBERS = [12, 17, 23, 31, 42, 56, 64, 78]
SKIN = [(176, 112, 78), (152, 94, 66), (196, 132, 96), (140, 88, 62), (184, 120, 84), (160, 100, 70), (200, 140, 104), (146, 92, 64)]
TRUNKS = [(20, 20, 24), (170, 20, 30), (20, 60, 150), (30, 120, 90), (220, 150, 20), (110, 40, 140), (30, 30, 30), (200, 80, 120)]
font = ImageFont.load_default(size=34)


def figure(img, cx, scale, skin, trunks, number, back):
    d = ImageDraw.Draw(img)
    s = lambda v: v * scale
    top = H - 80 - 760 * scale
    P = lambda x, y: (cx + s(x), top + s(y))
    shade = tuple(int(c * 0.78) for c in skin)
    d.ellipse([*P(-38, 40), *P(38, 116)], fill=skin)                                        # head
    d.rectangle([*P(-16, 110), *P(16, 150)], fill=shade)                                      # neck
    d.polygon([P(-105, 160), P(105, 160), P(62, 400), P(-62, 400)], fill=skin)                # torso
    d.polygon([P(-105, 160), P(-170, 230), P(-150, 340), P(-118, 330), P(-120, 240), P(-80, 200)], fill=shade)   # arms
    d.polygon([P(105, 160), P(170, 230), P(150, 340), P(118, 330), P(120, 240), P(80, 200)], fill=shade)
    d.polygon([P(-66, 390), P(66, 390), P(70, 470), P(-70, 470)], fill=trunks)                # trunks
    d.polygon([P(-70, 470), P(-2, 470), P(-12, 760), P(-52, 760)], fill=skin)                 # legs
    d.polygon([P(2, 470), P(70, 470), P(52, 760), P(12, 760)], fill=skin)
    d.rectangle([*P(-58, 748), *P(-8, 770)], fill=(40, 40, 44)); d.rectangle([*P(8, 748), *P(58, 770)], fill=(40, 40, 44))
    if not back:
        d.ellipse([*P(-18, 70), *P(-8, 78)], fill=(30, 20, 16)); d.ellipse([*P(8, 70), *P(18, 78)], fill=(30, 20, 16))
        d.arc([*P(-16, 84), *P(16, 104)], 20, 160, fill=(60, 30, 24), width=3)
    else:
        d.pieslice([*P(-40, 36), *P(40, 100)], 180, 360, fill=(70, 50, 36))                   # hair seen from behind
    if number is not None and not back:                                                       # the number plate on the hip
        c = P(50, 440)
        r = 30 * scale
        d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=(245, 245, 240))
        t = str(number)
        tw = d.textlength(t, font=font)
        d.text((c[0] - tw / 2, c[1] - 19 * scale), t, fill=(10, 10, 10), font=ImageFont.load_default(size=int(34 * scale)))
    hips = [(cx + s(-45), top + s(450), 1), (cx + s(45), top + s(450), 1)]
    box = (cx - s(175), top + s(40), cx + s(175), top + s(770))
    return box, hips


def background(i):
    bg = Image.new("RGB", (W, H), (10, 10, 13))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse([W * 0.1, H * 0.05, W * 0.9, H * 0.95], fill=(48 + i % 3 * 6, 44, 60))
    return Image.blend(bg, glow.filter(ImageFilter.GaussianBlur(120)), 0.9)


# who is in which photo (competitor index, or a tuple for a group), in shooting order; None = back view with no readable number
plan = []
for k, n in enumerate(NUMBERS):
    plan += [((k,), False), ((k,), True), ((k,), False), ((k,), True)]
plan += [((1, 2), False), ((3, 4), False), ((5, 6), False)]
order = np.random.default_rng(4).permutation(len(plan))              # competitors meet on stage in a jumble, as at a real event
plan = [plan[i] for i in order]

tmp = Path(tempfile.mkdtemp(prefix="edic_demo_"))
(tmp / "data").mkdir()
show = Path("/tmp/Pictures")                                      # what the header will show as the photo folder
shutil.rmtree(show, ignore_errors=True)
os.symlink(os.environ.get("EDIC_TEST_MODELS", "/tmp/edic_data/models"), tmp / "data" / "models")
eng = Engine(tmp / "data")
eng.update_settings({"check_updates": False, "tutorial_seen": True, "language": "en"})
FACE = {k: (lambda v: v / np.linalg.norm(v))(rng.normal(size=128)).astype(np.float32) for k in range(len(NUMBERS))}
COLOUR = {k: np.abs(rng.normal(size=72)).astype(np.float32) for k in range(len(NUMBERS))}


def build(name, folder, processed, rosters=True):
    photos = show / folder
    photos.mkdir(parents=True)
    spec = []
    for i, (who, back) in enumerate(plan):
        img = background(i)
        people = []
        for slot, k in enumerate(who):
            cx = W / 2 if len(who) == 1 else (W * (0.3 + 0.4 * slot))
            sc = 0.95 if len(who) == 1 else 0.78
            number = NUMBERS[k] if (not (back or i % 4 == 3) and rng.random() < 0.3) else None
            box, hips = figure(img, cx, sc, SKIN[k], TRUNKS[k], NUMBERS[k], back or i % 4 == 3)
            people.append((k, box, hips, number, back or i % 4 == 3))
        f = photos / f"DSC_{1000 + i}.jpg"
        img.save(f, quality=92)
        os.utime(f, (1_780_000_000 + i * 3,) * 2)
        spec.append(people)
    pr = eng.create_project(name, str(photos))
    pr.ingest()
    owners = {ph["id"]: people[0][0] for ph, people in zip(pr.photos(), spec)}
    for ph, people in zip(pr.photos(), spec):
        ps = []
        for k, box, hips, number, back in people:
            kp = np.zeros((17, 3), np.float32)
            kp[11], kp[12] = hips[0], hips[1]
            p = Person(box=tuple(float(v) for v in box), kpts=kp)
            if not back:
                p.face = (FACE[k] + 0.22 * rng.normal(size=128)).astype(np.float32); p.face /= np.linalg.norm(p.face)
            p.colour = np.abs(COLOUR[k] * (1 + 0.9 * rng.normal(size=72)) + 0.5 * rng.random(72)).astype(np.float32)
            if number is not None:
                p.numbers = {number: 0.85}
            ps.append(p)
        pr.save_persons(ph["id"], ps, mark_processed=processed)
    return pr, owners, {ph['id']: [q[0] for q in people] for ph, people in zip(pr.photos(), spec)}


ex, bib_owner, _ = build("Spring Open 2026", "Spring Open 2026 · examples", processed=False)       # stays at the "show a few numbers" step
rv, _, truth = build("Spring Open 2026 · Saturday", "Spring Open 2026 · Saturday", processed=True)
rv.set_meta("examples_skipped", True); rv.set_meta("ai_skipped", True)
NAMES = {12: "Anna Vesela", 17: "Martin Novak", 23: "Eva Horvat", 31: "Peter Kral", 42: "Lucia Mala", 56: "Tomas Bielik", 64: "Katarina Zelena", 78: "Jakub Cerny"}
rv.set_roster([(n, NAMES[n]) for n in NUMBERS])
# A synthetic event is not a fair test of the real scoring (every embedding is invented), so write the outcome a real run
# would roughly have: most photos placed with confidence, some to check, some nobody could place.
pick = np.random.default_rng(9)
ids = [ph["id"] for ph in rv.photos()]
groups = [i for i in ids if len(truth[i]) == 2]
singles = [i for i in ids if len(truth[i]) == 1]
unknown = set(groups[:1] + [int(x) for x in pick.choice(singles, 5, replace=False)])
rest = [i for i in singles if i not in unknown]
check = set(groups[1:2] + [int(x) for x in pick.choice(rest, 8, replace=False)])
comp = {c["number"]: c["id"] for c in rv.competitors()}
with rv.lock:
    for n, i in enumerate(ids):
        if i in unknown:
            continue
        for j, k in enumerate(truth[i]):
            sure = i not in check
            p_ = 0.9 + 0.09 * pick.random() if sure else 0.35 + 0.4 * pick.random()
            confirmed = sure and n % 3 == 0
            rv.con.execute("INSERT OR REPLACE INTO assignments(photo_id,competitor_id,p,num_conf,person_idx,status,source) VALUES(?,?,?,?,?,?,?)",
                           (i, comp[NUMBERS[k]], p_, 0.8 if n % 4 == 0 else 0.0, j, "accepted" if confirmed else "suggested", "user" if confirmed else "model"))
            if not sure:                                                                   # a runner-up guess, as the model would have
                other = comp[NUMBERS[(k + 1) % len(NUMBERS)]]
                rv.con.execute("INSERT OR IGNORE INTO assignments(photo_id,competitor_id,p,person_idx,status) VALUES(?,?,?,?,'suggested')", (i, other, 0.3 + 0.1 * pick.random(), j))
from edic.review import Review
print("stats:", {k: v for k, v in Review(rv).stats().items() if k in ("photos", "resolved_photos", "to_check_pairs", "to_tag")})
srv = Server(str(tmp / "data")).start()
base = srv.url.rstrip("/")

with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--no-sandbox"])
    page = b.new_context(viewport={"width": 1280, "height": 1000}).new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))

    def shot(name, wait=900, top=True):
        page.wait_for_load_state("networkidle"); page.wait_for_timeout(wait)
        if top:
            page.evaluate("window.scrollTo(0, 0)"); page.wait_for_timeout(200)
        page.screenshot(path=str(out / f"{name}.png"))

    page.goto(base + "/"); page.wait_for_timeout(800)
    # 1. examples: drag a box around a number
    page.goto(f"{base}/#/e/{ex.id}/overview"); page.wait_for_selector(".step.current .grid .cell", timeout=30000)
    first_id = page.evaluate("fetch('/api/projects/%s/calibration').then(r=>r.json()).then(j=>j.suggested_photos[0])" % ex.id)
    first_bib = NUMBERS[bib_owner[first_id]]
    page.locator(".step.current .grid .cell").nth(0).click(); page.wait_for_selector(".pstage.markable img"); page.wait_for_timeout(600)
    st = page.locator(".pstage.markable").bounding_box()
    page.evaluate("document.querySelector('.step.current').scrollIntoView()"); page.wait_for_timeout(300)
    st = page.locator(".pstage.markable").bounding_box()
    x1, y1, x2, y2 = st["x"] + st["width"] * .55, st["y"] + st["height"] * .36, st["x"] + st["width"] * .79, st["y"] + st["height"] * .57
    page.mouse.move(x1, y1); page.mouse.down(); page.mouse.move(x2, y2, steps=6); page.mouse.up()
    page.locator("#bibno").fill(str(first_bib))
    shot("1-show-a-few-numbers", top=False)
    # 2. overview of a finished analysis
    page.goto(f"{base}/#/e/{rv.id}/overview"); page.wait_for_selector(".step.current[data-step=review]"); shot("2-overview")
    # 3. check grid
    page.goto(f"{base}/#/e/{rv.id}/check"); page.wait_for_timeout(1200)
    if page.locator(".grid .cell").count() > 1:
        page.locator(".grid .cell").nth(1).click()
    shot("3-check")
    # 4. tag: prefer a group photo
    page.goto(f"{base}/#/e/{rv.id}/tag"); page.wait_for_selector(".tag-stage .pstage img"); page.wait_for_timeout(1000)
    for _ in range(40):
        if page.locator(".pstage .pbox").count() >= 2:
            break
        page.keyboard.press("ArrowRight"); page.wait_for_timeout(500)
    shot("4-tag")
    # 5. albums
    page.goto(f"{base}/#/e/{rv.id}/albums"); page.wait_for_selector(".acard"); shot("5-albums")
    page.locator(".acard").nth(2).click(); page.wait_for_selector(".modal .grid .cell"); shot("6-album")
    print("errors:", errs)
    b.close()
srv.stop()
