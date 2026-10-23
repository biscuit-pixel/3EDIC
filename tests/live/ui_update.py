"""Update banner in a real browser against a fake GitHub release server (install step in dry-run mode).
usage: ui_update.py OUT_DIR
"""
import hashlib
import http.server
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["EDIC_UPDATE_DRYRUN"] = "1"
from playwright.sync_api import sync_playwright

from edic.desktop import Server
from edic.updater import Updater

out = sys.argv[1]
EXE = b"MZ" + b"fake installer " * 50_000
SUMS = f"{hashlib.sha256(EXE).hexdigest()}  3EDIC-Setup-9.9.9.exe\n".encode()
problems = []


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        base = f"http://127.0.0.1:{fake.server_port}"
        if self.path.endswith("/latest"):
            body = json.dumps({"tag_name": "v9.9.9", "prerelease": False, "draft": False, "html_url": base + "/r", "body": "## What's new\n- Faster grid\n- Fixes",
                               "assets": [{"name": n, "size": len(d), "browser_download_url": f"{base}/dl/{n}"} for n, d in
                                          (("3EDIC-Setup-9.9.9.exe", EXE), ("SHA256SUMS.txt", SUMS))]}).encode()
        elif self.path.endswith("SHA256SUMS.txt"):
            body = SUMS
        elif self.path.endswith(".exe"):
            body = EXE
        else:
            self.send_error(404); return
        self.send_response(200); self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)


fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=fake.serve_forever, daemon=True).start()
data = tempfile.mkdtemp(prefix="edic_upd_")
if os.environ.get("EDIC_TEST_MODELS"):                    # reuse downloaded models so the app does not start on the setup screen
    os.symlink(os.environ["EDIC_TEST_MODELS"], Path(data) / "models")
srv = Server(data)
srv.engine.updater = Updater(data, current="0.1.0", plat="win32", frozen=True, api_url=f"http://127.0.0.1:{fake.server_port}/latest")
srv.start()


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name, extra)
    if not cond:
        problems.append(name)


with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--no-sandbox"])
    page = b.new_context(viewport={"width": 1300, "height": 800}).new_page()
    page.on("pageerror", lambda e: problems.append(f"pageerror {e}"))
    page.goto(srv.url)
    page.goto(srv.url + "#/settings")
    page.wait_for_timeout(2500)
    check("banner shows the new version", page.locator("#banner .update-bar").count() == 1 and "9.9.9" in page.locator("#banner").inner_text())
    page.screenshot(path=f"{out}/update-banner.png")
    page.get_by_role("button", name="What’s new").click(); page.wait_for_timeout(400)
    check("release notes open", "Faster grid" in page.locator(".modal").inner_text())
    page.keyboard.press("Escape")
    page.get_by_role("button", name="Update now").click(); page.wait_for_timeout(3000)
    check("update downloaded and verified", (Path(data) / "updates" / "3EDIC-Setup-9.9.9.exe").exists())
    check("UI reports completion", "Done" in page.locator("#banner").inner_text() or "restart" in page.locator("#banner").inner_text().lower(), page.locator("#banner").inner_text().replace("\n", " | ")[:90])
    page.screenshot(path=f"{out}/update-done.png")
    # Settings: version + check now
    page.get_by_role("button", name="Check now").click(); page.wait_for_timeout(1500)
    check("settings says an update is available", "9.9.9" in page.locator("main").inner_text())
    page.screenshot(path=f"{out}/update-settings.png")
    # Later hides the banner and it stays hidden after a reload
    page.get_by_role("button", name="Later").click(); page.wait_for_timeout(500)
    check("Later hides the banner", page.locator("#banner .update-bar").count() == 0)
    page.reload(); page.wait_for_timeout(2500)
    check("dismissal survives a reload", page.locator("#banner .update-bar").count() == 0)
    b.close()
srv.stop(); fake.shutdown()
print("PROBLEMS:", problems)
sys.exit(1 if problems else 0)
