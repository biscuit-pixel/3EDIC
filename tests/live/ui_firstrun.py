"""First run in a real browser: fresh data dir -> setup screen -> download through the UI -> home.
usage: ui_firstrun.py BASE_URL OUT_DIR
"""
import sys
from playwright.sync_api import sync_playwright

base, out = sys.argv[1], sys.argv[2]
problems = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--no-sandbox"])
    page = b.new_context(viewport={"width": 1440, "height": 1000}).new_page()
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("console", lambda m: problems.append(f"console: {m.text}") if m.type == "error" else None)
    page.goto(base + "/")
    page.wait_for_timeout(1500)
    print("url after open:", page.url.split("#")[-1])
    assert page.url.endswith("#/setup"), "fresh install must land on the setup screen"
    page.screenshot(path=f"{out}/firstrun-1-setup.png")
    # ArcFace needs the licence tick before the button can be used
    page.locator(".ms-orbit").click()
    page.wait_for_timeout(1200)
    go = page.get_by_role("button", name="Download and start")
    print("download disabled until licence ticked:", go.is_disabled())
    page.screenshot(path=f"{out}/firstrun-2-arc.png")
    page.locator(".ms-check").click()
    print("download enabled after tick:", not go.is_disabled())
    page.locator(".ms-orbit").click()                 # leave ArcFace off: keep the test to the required models
    page.wait_for_timeout(500)
    go.click()
    page.wait_for_timeout(5000)
    page.screenshot(path=f"{out}/firstrun-3-downloading.png")
    page.wait_for_url("**/#/", timeout=180000)
    page.wait_for_timeout(1500)
    page.screenshot(path=f"{out}/firstrun-4-home.png")
    print("landed on home:", page.url.endswith("#/") or page.url.endswith("/"))
    b.close()
print("PROBLEMS:", problems)
