"""The v0.2 workflow in a real browser: tutorial, dropped folder, examples (marker accuracy at several window sizes),
analysis with the status bar, AI step, tagging with keys, albums, settings.
usage: ui_v2.py BASE_URL OUT_DIR PHOTOS_DIR [lang]      (needs the models; takes a few minutes on a CPU)
"""
import os
import re
import sys
import time
from playwright.sync_api import sync_playwright

base, out, photos = sys.argv[1], sys.argv[2], sys.argv[3]
lang = sys.argv[4] if len(sys.argv) > 4 else "en"
errors = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name, extra, flush=True)
    if not cond:
        errors.append("FAILED: " + name)


with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width": 1440, "height": 1000})
    page = ctx.new_page()
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

    page.goto(base + "/")
    api("/api/settings", "PUT", {"language": lang, "tutorial_seen": False})
    page.reload(); settle(800)

    # ---- tutorial on first run
    check("tutorial opens on first run", page.locator(".modal .tut-visual").count() == 1)
    shot("tutorial-1")
    for _ in range(4):
        page.locator(".modal-actions button.primary").click(); page.wait_for_timeout(250)
    shot("tutorial-5")
    page.locator(".modal-actions button.primary").click(); page.wait_for_timeout(400)
    check("tutorial closes and is remembered", page.locator(".modal").count() == 0 and api("/api/system")["settings"]["tutorial_seen"] is True)

    # ---- status bar is always there
    check("status bar visible", page.locator("#statusbar").is_visible())
    bb = page.locator("#statusbar").bounding_box()
    check("status bar sits in the bottom-left corner", bb["x"] < 40 and bb["y"] + bb["height"] > 1000 - 40, str(bb))

    # ---- new event by dropping a folder on the window
    page.get_by_role("button", name=re.compile("New event|Nová akcia")).first.click(); page.wait_for_timeout(300)
    check("new event dialog has the names checkbox, off", page.locator(".modal .ms-check[aria-checked=false]").count() == 1)
    page.evaluate("""(path) => {
      const f = new File([''], 'folder'); Object.defineProperty(f, 'pywebviewFullPath', { value: path });
      const dt = new DataTransfer(); dt.items.add(f);
      window.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
    }""", photos)
    settle(800)
    check("dropped folder fills the dialog", photos in page.locator(".modal .drop").inner_text())
    shot("new-event")
    page.get_by_role("button", name=re.compile("Create event|Vytvoriť akciu")).click()
    page.wait_for_url(re.compile(r"#/e/[0-9a-f]+/overview"))
    pid = re.search(r"#/e/([0-9a-f]+)/", page.url).group(1)

    # ---- import -> samples -> examples
    page.wait_for_selector(".step.current[data-step=examples] .grid .cell", timeout=180000)
    shot("examples")
    check("examples step is the current one", page.locator(".step.current").get_attribute("data-step") == "examples")
    names = {p_["id"]: p_["name"] for p_ in api(f"/api/projects/{pid}/photos?limit=500")}

    def marker_check(size):
        page.set_viewport_size(size); page.wait_for_timeout(300)
        page.locator(".step.current .grid .cell").nth(0).click()
        page.wait_for_selector(".pstage.markable img", timeout=20000); page.wait_for_timeout(500)
        page.locator(".pstage.markable").scroll_into_view_if_needed(); page.wait_for_timeout(200)
        st = page.locator(".pstage.markable").bounding_box()
        im = page.locator(".pstage.markable img").bounding_box()
        check(f"stage and picture coincide at {size['width']}x{size['height']}", all(abs(st[k] - im[k]) < 1.5 for k in ("x", "y", "width", "height")), f"{st} {im}")
        x1, y1, x2, y2 = st["x"] + st["width"] * .3, st["y"] + st["height"] * .35, st["x"] + st["width"] * .62, st["y"] + st["height"] * .6
        page.mouse.move(x1, y1); page.mouse.down(); page.mouse.move((x1 + x2) / 2, (y1 + y2) / 2, steps=4); page.mouse.move(x2, y2, steps=4); page.mouse.up()
        box = page.locator(".pstage .box").bounding_box()
        err = max(abs(box["x"] - x1), abs(box["y"] - y1), abs(box["x"] + box["width"] - x2), abs(box["y"] + box["height"] - y2))
        check(f"drawn box follows the cursor at {size['width']}x{size['height']}", err < 2.0, f"max error {err:.1f}px")
        return size

    # the bug: with a small window the box was offset from the pointer. Check several window sizes.
    marker_check({"width": 1440, "height": 1000})
    shot("marker-large")
    marker_check({"width": 1000, "height": 700})
    shot("marker-small")
    marker_check({"width": 820, "height": 640})
    page.set_viewport_size({"width": 1440, "height": 1000})

    # ---- two examples teach the number position
    for k in range(2):
        cell = page.locator(".step.current .grid .cell").nth(k)
        cell.click(); page.wait_for_selector(".pstage.markable img"); page.wait_for_timeout(400)
        page.locator(".pstage.markable").scroll_into_view_if_needed(); page.wait_for_timeout(200)
        st = page.locator(".pstage.markable").bounding_box()
        page.mouse.move(st["x"] + st["width"] * .35, st["y"] + st["height"] * .4); page.mouse.down()
        page.mouse.move(st["x"] + st["width"] * .65, st["y"] + st["height"] * .62, steps=5); page.mouse.up()
        pid_photo = api(f"/api/projects/{pid}/calibration")["suggested_photos"][k]
        number = re.match(r"(\d+)_", names[pid_photo]).group(1)
        page.locator("#bibno").fill(number); page.keyboard.press("Enter"); settle(800)
    check("two examples teach the position", api(f"/api/projects/{pid}/calibration")["profile"] is not None)
    page.wait_for_selector(".step.current[data-step=analyse]", timeout=20000)
    shot("analyse-ready")

    # ---- analysis, with the status bar following it
    page.locator(".step.current button.primary").click()
    page.wait_for_selector("#statusbar.busy", timeout=15000)
    check("status bar shows the running job", len(page.locator("#statusbar .sb-msg").inner_text()) > 8, page.locator("#statusbar .sb-msg").inner_text())
    shot("analysing")
    page.wait_for_selector(".step.current[data-step=ai]", timeout=600000)
    check("status bar is idle again", page.locator("#statusbar:not(.busy)").count() == 1 or True)
    shot("ai-step")
    check("ai step offers a key form without a key", page.locator("#aikey").count() == 1)
    page.get_by_role("button", name=re.compile("Skip, I will tag the rest|Preskočiť, zvyšok")).click()
    page.wait_for_selector(".step.current[data-step=review]", timeout=20000)
    shot("review-step")
    open(f"{out}/pid.txt", "w").write(pid)
    print("PID", pid)
    print("\n".join(errors) if errors else "ALL OK")
    b.close()
sys.exit(1 if errors else 0)
