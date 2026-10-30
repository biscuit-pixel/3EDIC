"""Second half of the v0.2 workflow on an analysed event: check grid, tagging with the keyboard, bulk tagging, albums, settings.
usage: ui_v2_tag.py BASE_URL OUT_DIR PROJECT_ID [lang]
"""
import re
import sys
from playwright.sync_api import sync_playwright

base, out, pid = sys.argv[1], sys.argv[2], sys.argv[3]
lang = sys.argv[4] if len(sys.argv) > 4 else "en"
errors = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name, extra, flush=True)
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

    def settle(ms=500):
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(ms)

    def shot(name):
        settle()
        leaked = page.evaluate("document.body.innerText.includes('[object ') || document.body.innerText.includes('undefined') || /(^|\\n)(null|false|NaN)(\\n|$)/.test(document.body.innerText)")
        check(f"no '[object' or 'undefined' text on screen ({name})", not leaked)
        page.screenshot(path=f"{out}/{lang}-v2-{name}.png")

    def to_tag():
        return api(f"/api/projects/{pid}/review/stats")["to_tag"]

    page.goto(base + "/")
    api("/api/settings", "PUT", {"language": lang, "tutorial_seen": True})
    page.reload(); settle(800)

    # ---- check grid: untick two, confirm; the two go to the tagging list
    t0 = to_tag()
    page.goto(f"{base}/#/e/{pid}/check"); settle(1500)
    cells = page.locator(".grid .cell")
    n = cells.count()
    if n:
        shot("check")
        cells.nth(0).click(); cells.nth(1).click()
        page.locator(".ms-key.primary").first.click(); settle(1200)
        check("rejected photos return to the tagging list", to_tag() >= t0, f"{t0} -> {to_tag()}")
    else:
        print("note: nothing to check")

    # ---- tag: one by one
    q = api(f"/api/projects/{pid}/review/queue")
    check("there are photos to tag", len(q["items"]) > 3, str(q["counts"]))
    page.goto(f"{base}/#/e/{pid}/tag"); page.wait_for_selector(".tag-stage .pstage img", timeout=20000); settle(800)
    shot("tag")
    frames = page.locator(".filmstrip .frame").count()
    check("neighbouring photos are shown", frames >= 5, f"{frames} frames")
    check("current frame is marked", page.locator(".filmstrip .frame.cur").count() == 1)
    src = page.locator(".tag-stage .pstage > img").get_attribute("src")
    check("sharp preview is requested", re.search(r"w=(1280|2400)", src) is not None, src)
    pb, lb = page.locator(".pstage .pbox.sel").bounding_box(), page.locator(".pstage .plabel.sel").bounding_box()
    cx, cy = lb["x"] + lb["width"] / 2, lb["y"] + lb["height"] / 2
    check("the label sits on the person, where the number is", pb["x"] <= cx <= pb["x"] + pb["width"] and pb["y"] <= cy <= pb["y"] + pb["height"], f"{pb} {lb}")
    check("number field is focused so typing just works", page.evaluate("document.activeElement && document.activeElement.id") == "whoNo")

    pos0 = page.locator(".tag-stage .ms-caption").first.inner_text()
    page.keyboard.press("ArrowRight"); settle(900)
    pos1 = page.locator(".tag-stage .ms-caption").first.inner_text()
    check("→ moves to the next photo", pos0 != pos1, f"{pos0} -> {pos1}")
    page.keyboard.press("ArrowLeft"); settle(900)
    check("← goes back", page.locator(".tag-stage .ms-caption").first.inner_text() == pos0)

    t1 = to_tag()
    page.keyboard.type("31"); page.keyboard.press("Enter"); settle(1200)
    check("number + Enter tags the photo", to_tag() == t1 - 1, f"{t1} -> {to_tag()}")
    check("...and creates an album when there was none", any(c["number"] == 31 for c in api(f"/api/projects/{pid}/competitors")))
    t2 = to_tag()
    page.keyboard.press("-"); settle(1200)
    check("- puts the photo in Others", to_tag() == t2 - 1 and any(a["kind"] == "others" for a in api(f"/api/projects/{pid}/review/album")), f"{t2} -> {to_tag()}")
    page.keyboard.press("Control+z"); settle(1200)
    check("Ctrl+Z undoes it and shows the photo again", to_tag() == t2)
    shot("tag-after-undo")

    # ---- tag: many at once
    page.get_by_role("button", name=re.compile("Many at once|Viac naraz")).click(); page.wait_for_selector(".grid.big .cell"); settle(800)
    shot("tag-grid")
    cells = page.locator(".grid.big .cell")
    cells.nth(0).click(); cells.nth(3).click(modifiers=["Shift"])
    check("shift-click selects a range", page.locator(".grid.big .cell.sel").count() == 4, str(page.locator(".grid.big .cell.sel").count()))
    t3 = to_tag()
    before = {a["label"]: a for a in api(f"/api/projects/{pid}/review/album")}.get("077", {}).get("confirmed", 0)
    page.locator("#bulkNo").fill("77"); page.keyboard.press("Enter"); settle(1500)
    alb = {a["label"]: a for a in api(f"/api/projects/{pid}/review/album")}
    check("one Enter tags the whole selection into album 077", alb.get("077", {}).get("confirmed") == before + 4, f"{before} -> {alb.get('077', {}).get('confirmed')}")
    check("tagged photos leave the list", to_tag() < t3, f"{t3} -> {to_tag()}")

    # ---- albums
    page.goto(f"{base}/#/e/{pid}/albums"); page.wait_for_selector(".acard"); settle(1000)
    shot("albums")
    page.locator(".acard", has_text="077").click(); page.wait_for_selector(".modal .grid .cell"); settle(800)
    shot("album-open")
    page.locator(".modal .grid .cell").nth(0).click(); page.locator(".modal .grid .cell").nth(1).click()
    page.get_by_role("button", name=re.compile("Take out of this album|Vybrať z tohto albumu")).click(); page.wait_for_timeout(400)
    shot("album-remove")
    page.locator(".modal .choice").nth(1).click()
    page.locator(".modal-actions .ms-key.primary").last.click(); settle(1500)
    check("removed photos landed in Others", next(a for a in api(f"/api/projects/{pid}/review/album") if a["kind"] == "others")["confirmed"] >= 2)
    # renumber onto an existing number -> asks
    page.locator(".acard", has_text="077").click(); page.wait_for_selector(".modal #albNo"); settle(500)
    page.locator("#albNo").fill("31"); page.locator(".modal .ms-key.primary").first.click(); page.wait_for_timeout(600)
    check("renumbering onto an existing number asks what to do", page.locator(".modal .choice").count() == 2)
    shot("album-clash")
    page.locator(".modal .choice").nth(1).click(); settle(1500)
    labels = {c["label"] for c in api(f"/api/projects/{pid}/competitors")}
    check("keeping both makes 031_2", "031_2" in labels, str(sorted(labels)))

    # ---- settings
    page.goto(f"{base}/#/settings"); settle(1200)
    shot("settings")
    page.get_by_role("button", name=re.compile("^Processor$|^Procesor$")).click(); settle(600)
    check("device preference saved", api("/api/system")["settings"]["device"] == "cpu")
    page.get_by_role("button", name=re.compile("^Automatic$|^Automaticky$")).click(); settle(400)
    page.get_by_role("button", name=re.compile("Show the tutorial again|Ukázať návod znova")).click(); page.wait_for_timeout(500)
    check("tutorial can be replayed", page.locator(".modal .tut-visual").count() == 1)
    page.keyboard.press("Escape")
    page.goto(f"{base}/#/e/{pid}/overview"); settle(1200)
    shot("overview-done")
    print("\n".join(errors) if errors else "ALL OK")
    b.close()
sys.exit(1 if errors else 0)
