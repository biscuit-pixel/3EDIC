"""Drive the real UI in headless Chromium, take screenshots and fail on console errors.
usage: ui_shots.py BASE_URL OUT_DIR PROJECT_ID [lang]
"""
import sys
from playwright.sync_api import sync_playwright

base, out, pid = sys.argv[1], sys.argv[2], sys.argv[3]
lang = sys.argv[4] if len(sys.argv) > 4 else "en"
errors = []

with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page = ctx.new_page()
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type in ("error",) else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("requestfailed", lambda r: errors.append(f"requestfailed: {r.url[:120]}"))
    page.on("response", lambda r: errors.append(f"http {r.status}: {r.url[:120]}") if r.status >= 400 else None)

    def shot(name, wait=900):
        page.wait_for_timeout(wait)
        page.screenshot(path=f"{out}/{lang}-{name}.png")
        print("shot", name)

    page.goto(base + "/")
    page.evaluate(f"fetch('/api/settings',{{method:'PUT',headers:{{'content-type':'application/json'}},body:JSON.stringify({{language:'{lang}'}})}})")
    page.goto(base + "/")
    shot("home")
    for tab in ["photos", "roster", "number", "review", "find", "export"]:
        page.goto(f"{base}/#/e/{pid}/{tab}")
        shot(tab, 1500)
    page.goto(base + "/#/settings")
    shot("settings")
    b.close()

print("ERRORS:", len(errors))
for e in errors[:20]:
    print(" ", e)
