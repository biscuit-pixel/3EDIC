"""3EDIČ as a desktop app: starts the local server on a free port and shows it in a native window.

  edic-desktop                    open the app window
  edic-desktop --browser          use the default browser instead of a native window
  edic-desktop --smoke            headless self-test (used by the installer build): exit 0 only if everything works
  edic-desktop --smoke --with-models   ...and also download the models and run the vision pipeline once
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from http.cookiejar import CookieJar
from pathlib import Path


class Server:
    """uvicorn in a background thread, on a free localhost port."""

    def __init__(self, data_dir: str | None = None, port: int = 0):
        import uvicorn

        from .api import create_app
        from .engine import Engine
        self.engine = Engine(data_dir)
        self.port = port or free_port()
        self.url = f"http://127.0.0.1:{self.port}/"
        cfg = uvicorn.Config(create_app(self.engine), host="127.0.0.1", port=self.port, log_level="warning", log_config=None)
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True, name="edic-server")

    def start(self, timeout: float = 30.0):
        self.thread.start()
        t0 = time.time()
        while not self.server.started:
            if not self.thread.is_alive():
                raise RuntimeError("the server stopped while starting")
            if time.time() - t0 > timeout:
                raise TimeoutError("the server did not start")
            time.sleep(0.05)
        return self

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def open_window(url: str) -> bool:
    """Native window via pywebview. Returns False when it cannot be used (missing runtime etc.)."""
    try:
        import webview
    except Exception as e:                                   # noqa: BLE001
        print(f"native window unavailable ({e}); using the browser", file=sys.stderr)
        return False
    try:
        webview.create_window("3EDIČ", url, width=1440, height=920, min_size=(1000, 700), background_color="#060607")
        webview.start()
        return True
    except Exception as e:                                   # noqa: BLE001
        print(f"native window failed ({e}); using the browser", file=sys.stderr)
        return False


def smoke(with_models: bool) -> int:
    """Everything an installed copy needs, checked without a display. Prints one line per check."""
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        ok &= bool(cond)
        print(("ok    " if cond else "FAIL  ") + name, extra, flush=True)

    tmp = tempfile.mkdtemp(prefix="edic_smoke_")
    srv = Server(tmp).start()
    jar = CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        page = op.open(srv.url).read().decode("utf-8")
        check("serves the UI", "3EDI" in page and any(c.name == "edic" for c in jar))
        for path in ("js/app.js", "js/review.js", "css/tokens.css", "css/app.css", "fonts/bitcount-prop-single-latin.woff2", "fonts/bitcount-prop-single-latin-ext.woff2"):
            check("static " + path, op.open(srv.url + path).status == 200)
        sysinfo = json.loads(op.open(srv.url + "api/system").read())
        check("api/system", sysinfo["version"] and sysinfo["hardware"]["providers"], f'{sysinfo["version"]} {sysinfo["hardware"]["providers"]}')
        check("model registry bundled", {m["id"] for m in sysinfo["models"]} >= {"pose", "yunet", "sface", "arcface"})
        folder = json.loads(op.open(srv.url + "api/fs?path=" + urllib.request.quote(tmp)).read())
        check("folder picker", folder["path"])
    finally:
        srv.stop()

    from . import scoring
    for variant in scoring.VARIANTS:
        check(f"combiner weights ({variant})", len(scoring.load_combiner(variant)["stage1"]["coef"]) > 3)

    import numpy as np
    from PIL import Image, ImageDraw
    from .vision.bib import BibReader
    reader = BibReader()
    img = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(img).text((120, 70), "41", fill="black")
    reader.ocr(np.array(img)[:, :, ::-1])
    check("OCR models bundled and loadable", True)

    if with_models:
        from .engine import Engine
        eng = Engine(tmp)
        t0 = time.time()
        eng.models.ensure()
        check("models downloaded and verified", eng.models.ready(), f"{time.time() - t0:.0f}s")
        ex = eng.extractor()
        canvas = np.full((900, 700, 3), 40, np.uint8)
        people = ex.extract(canvas)                          # nobody on a grey canvas: proves the ONNX sessions run
        check("vision pipeline runs", isinstance(people, list), f"providers={eng.hardware()['providers']}")
    print("SMOKE OK" if ok else "SMOKE FAILED", flush=True)
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="edic-desktop")
    ap.add_argument("--data-dir")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--browser", action="store_true", help="use the default browser instead of a native window")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--with-models", action="store_true")
    a = ap.parse_args(argv)
    if a.smoke:
        raise SystemExit(smoke(a.with_models))
    srv = Server(a.data_dir, a.port).start()
    try:
        if a.browser or not open_window(srv.url):
            webbrowser.open(srv.url)
            print(f"3EDIČ is running at {srv.url}  (Ctrl+C to quit)")
            while srv.thread.is_alive():
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()


if __name__ == "__main__":
    main()
