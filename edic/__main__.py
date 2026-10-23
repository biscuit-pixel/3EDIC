"""python -m edic  -> starts 3EDIČ and opens it in the browser."""
import argparse
import threading
import webbrowser


def main():
    ap = argparse.ArgumentParser(prog="edic", description="3EDIČ — sort event photos by number, face and order")
    ap.add_argument("--port", type=int, default=8737)
    ap.add_argument("--data-dir", default=None, help="where projects and models are stored")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    import uvicorn

    from .api import create_app
    from .engine import Engine
    app = create_app(Engine(a.data_dir))
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{a.port}/")).start()
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
