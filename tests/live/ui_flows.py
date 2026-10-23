"""Use the real UI like a person would; screenshot each step and fail on console errors.
usage: ui_flows.py BASE_URL OUT_DIR PROJECT_ID [lang]
"""
import sys
import json
import urllib.request
from playwright.sync_api import sync_playwright

base, out, pid = sys.argv[1], sys.argv[2], sys.argv[3]
lang = sys.argv[4] if len(sys.argv) > 4 else "en"
errors = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name, extra)
    if not cond:
        errors.append("FAILED: " + name)


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

    def shot(name):
        settle()
        page.screenshot(path=f"{out}/{lang}-{name}.png")

    page.goto(base + "/")
    api("/api/settings", "PUT", {"language": lang})
    page.reload()
    stats0 = api(f"/api/projects/{pid}/review/stats")

    # ---- grid: untick two thumbnails, confirm the rest
    page.goto(f"{base}/#/e/{pid}/review"); settle(1500)
    cells = page.locator(".grid .cell")
    n = cells.count()
    check("grid shows thumbnails", n > 3, f"{n} cells")
    shot("flow-grid")
    cells.nth(0).click(); cells.nth(2).click()
    check("clicked cells are marked wrong", page.locator('.cell[aria-pressed="true"]').count() == 2)
    shot("flow-grid-marked")
    title_before = page.locator(".grid-title .num").inner_text()
    page.get_by_role("button", name="Confirm").or_(page.get_by_role("button", name="Potvrdiť")).first.click()
    settle(1500)
    st1 = api(f"/api/projects/{pid}/review/stats")
    check("confirm accepted pairs (n-2) and rejected 2", st1["accepted_pairs"] - stats0["accepted_pairs"] == n - 2, f'{stats0["accepted_pairs"]} -> {st1["accepted_pairs"]}')
    check("moved on to another competitor", page.locator(".grid-title .num").count() == 0 or page.locator(".grid-title .num").inner_text() != title_before)
    shot("flow-grid-after")

    # ---- cards: keyboard accept, undo
    page.get_by_role("button", name="Cards", exact=True).or_(page.get_by_role("button", name="Karty", exact=True)).click(); settle(1500)
    check("card view shows a photo and a question", page.locator(".cards-stage .photo-wrap img").count() == 1 and page.locator(".big-q").count() == 1)
    shot("flow-cards")
    a0 = api(f"/api/projects/{pid}/review/stats")["accepted_pairs"]
    page.keyboard.press("y"); settle(1200)
    a1 = api(f"/api/projects/{pid}/review/stats")["accepted_pairs"]
    check("Y accepts", a1 == a0 + 1, f"{a0} -> {a1}")
    page.keyboard.press("z"); settle(1200)
    check("Z undoes", api(f"/api/projects/{pid}/review/stats")["accepted_pairs"] == a0)
    page.keyboard.press("n"); settle(1200)
    check("N rejects (no accept)", api(f"/api/projects/{pid}/review/stats")["accepted_pairs"] == a0)

    # ---- groups
    page.get_by_role("button", name="Groups").or_(page.get_by_role("button", name="Skupiny")).click(); settle(1500)
    boxes = page.locator(".pbox").count()
    check("group view draws person boxes", boxes >= 2, f"{boxes} boxes")
    shot("flow-groups")

    # ---- unknown: type a real number
    page.get_by_role("button", name="Unknown").or_(page.get_by_role("button", name="Neznáme")).click(); settle(1500)
    comps = api(f"/api/projects/{pid}/competitors")
    num = comps[0]["number"]
    shot("flow-unknown")
    u0 = api(f"/api/projects/{pid}/review/stats")["unknown_photos"]
    page.locator("#nums").fill(str(num)); page.locator("#nums").press("Enter"); settle(1200)
    check("typing a number resolves an unknown photo", api(f"/api/projects/{pid}/review/stats")["unknown_photos"] == u0 - 1, f"{u0} -> {u0-1}")
    page.locator("#nums").fill("99999"); page.locator("#nums").press("Enter"); settle(500)
    check("unknown number gives a toast", page.locator(".toast").count() >= 1)

    # ---- album + competitor modal
    page.get_by_role("button", name="Album").click(); settle(1500)
    check("album lists competitors", page.locator(".acard").count() == len(comps), f"{page.locator('.acard').count()} cards")
    shot("flow-album")
    page.locator(".acard").nth(2).click(); settle(1500)
    check("competitor modal opens", page.locator(".modal").count() == 1)
    shot("flow-album-modal")
    page.keyboard.press("Escape")

    # ---- number marking
    page.goto(f"{base}/#/e/{pid}/number"); settle(1500)
    cells = page.locator(".panel .grid .cell")
    cells.nth(0).click(); settle(1800)
    st = page.locator(".mark-stage img")
    check("marker shows hip crop", st.count() == 1)
    bb = st.bounding_box()
    page.mouse.move(bb["x"] + bb["width"] * .3, bb["y"] + bb["height"] * .35)
    page.mouse.down(); page.mouse.move(bb["x"] + bb["width"] * .6, bb["y"] + bb["height"] * .7, steps=6); page.mouse.up()
    check("drawn box visible", page.locator(".mark-stage .box:not(.hidden)").count() == 1)
    page.locator("#bibno").fill("12")
    shot("flow-number")
    page.get_by_role("button", name="Save example").or_(page.get_by_role("button", name="Uložiť príklad")).click(); settle(1500)
    cal = api(f"/api/projects/{pid}/calibration")
    check("example saved", len(cal["examples"]) >= 1, f'{len(cal["examples"])} examples')
    api(f"/api/projects/{pid}/calibration", "DELETE")

    # ---- remaining tabs
    for tab in ("find", "export", "roster"):
        page.goto(f"{base}/#/e/{pid}/{tab}"); shot(f"flow-{tab}")
    page.goto(base + "/#/settings"); shot("flow-settings")
    b.close()

print("PROBLEMS:", len(errors))
for e in errors[:25]:
    print(" ", e)
sys.exit(1 if errors else 0)
