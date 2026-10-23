"""HTTP API (FastAPI). Local only: bound to 127.0.0.1, protected by a per-run cookie and a Host check."""
from __future__ import annotations

import io
import os
import secrets
import sys
import threading
import time
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

from . import __version__, find as find_mod, export as export_mod
from .engine import Engine
from .review import Review
from .vlm import assist as vlm_assist, providers as vlm_providers

UI_DIR = Path(__file__).parent / "ui"
IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
THUMB_SIZES = (160, 320, 640, 1280, 2400)


def create_app(engine: Engine | None = None, token: str | None = None) -> FastAPI:
    engine = engine or Engine()
    token = token or secrets.token_urlsafe(24)
    app = FastAPI(title="3EDIČ", version=__version__, docs_url=None, redoc_url=None)
    app.state.engine, app.state.token = engine, token

    # ------------------------------------------------------------- security
    def guard(request: Request):
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost", "testserver"):
            raise HTTPException(403, "bad host")
        if request.cookies.get("edic") != token:
            raise HTTPException(401, "missing session")

    api = dict(dependencies=[Depends(guard)])

    def need(body: dict, *keys: str) -> list[int]:
        """Required integer fields of a request body; anything missing or non-numeric is a clean 400."""
        try:
            return [int(body[k]) for k in keys]
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "required: " + ", ".join(keys))

    def int_list(body: dict, key: str) -> list[int]:
        try:
            return [int(x) for x in body.get(key, [])]
        except (TypeError, ValueError):
            raise HTTPException(400, f"{key} must be a list of numbers")

    def proj(pid: str):
        try:
            return engine.project(pid)
        except KeyError:
            raise HTTPException(404, "no such project")

    # --------------------------------------------------------------- system
    @app.get("/api/system", **api)
    def system():
        return {"version": __version__, "hardware": engine.hardware(), "models": engine.models.status(),
                "ready": engine.models.ready(), "settings": engine.settings(), "variant": engine.variant(),
                "jobs": engine.jobs.active(), "vlm_providers": list(vlm_providers.PROVIDERS),
                "vlm_keys": {k: engine.has_api_key(k) for k in vlm_providers.PROVIDERS},
                "vlm_default_models": vlm_providers.DEFAULT_MODELS}

    @app.put("/api/settings", **api)
    def put_settings(changes: dict = Body(...)):
        try:
            return engine.update_settings(changes)
        except KeyError as e:
            raise HTTPException(400, f"unknown setting: {e}")

    @app.put("/api/secrets/{provider}", **api)
    def put_key(provider: str, body: dict = Body(...)):
        if provider not in vlm_providers.PROVIDERS:
            raise HTTPException(404, "unknown provider")
        engine.set_api_key(provider, (body.get("api_key") or "").strip() or None)
        return {"ok": True, "has_key": engine.has_api_key(provider)}

    @app.post("/api/models/install", **api)
    def install_models(body: dict = Body(default={})):
        return engine.install_models_job(body.get("ids"), body.get("accept", [])).to_dict()

    @app.get("/api/jobs", **api)
    def jobs():
        return engine.jobs.recent()

    @app.get("/api/jobs/{job_id}", **api)
    def job(job_id: str):
        j = engine.jobs.get(job_id)
        if not j:
            raise HTTPException(404)
        return j.to_dict()

    @app.post("/api/jobs/{job_id}/cancel", **api)
    def job_cancel(job_id: str):
        engine.jobs.cancel(job_id)
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/events", **api)
    def job_events(job_id: str):
        import json

        def stream():
            while True:
                j = engine.jobs.get(job_id)
                if not j:
                    return
                yield f"data: {json.dumps(j.to_dict())}\n\n"
                if j.state != "running":
                    return
                time.sleep(0.4)
        return StreamingResponse(stream(), media_type="text/event-stream")

    # --------------------------------------------------------------- updates
    @app.get("/api/update", **api)
    def update_info(force: bool = False):
        if not engine.settings()["check_updates"] and not force:
            return {"current": engine.updater.current, "available": False, "disabled": True}
        info = engine.updater.check(force)
        info["dismissed"] = bool(info.get("latest")) and engine.settings()["update_dismissed"] == info["latest"]
        return info

    @app.post("/api/update/install", **api)
    def update_install():
        info = engine.updater.check()
        if not info.get("available") or not info.get("can_install"):
            raise HTTPException(409, "there is no update this app can install by itself")
        dry = os.environ.get("EDIC_UPDATE_DRYRUN") == "1"

        def run(job):
            path = engine.updater.download_and_verify(info, progress=job.progress, cancelled=job.cancelled)
            job.message = "restarting"
            cmd = engine.updater.apply(path, dry_run=dry)
            if not dry:                                   # let the answer reach the UI, then get out of the installer's way
                threading.Timer(1.5, lambda: os._exit(0)).start()
            return {"restarting": not dry, "version": info["latest"], "command": cmd if dry else None}
        return engine.jobs.start("update", run, "Updating to " + info["latest"]).to_dict()

    @app.post("/api/open-release-page", **api)
    def open_release_page():
        """Open the releases page in the default browser (a native window cannot follow external links itself)."""
        import webbrowser
        webbrowser.open(f"https://github.com/{engine.updater.repo}/releases/latest")
        return {"ok": True}

    @app.get("/api/fs", **api)
    def browse(path: str = ""):
        """Folder picker for the UI (a browser cannot hand over a real path)."""
        p = Path(path).expanduser() if path else Path.home()
        try:
            p = p.resolve()
            dirs = sorted((d for d in p.iterdir() if d.is_dir() and not d.name.startswith(".")), key=lambda d: d.name.lower())
            images = sum(1 for f in p.iterdir() if f.suffix.lower() in IMG_EXT)
        except (OSError, PermissionError):
            raise HTTPException(400, "cannot open that folder")
        drives = []
        if sys.platform == "win32":                      # a browser cannot switch drives, so offer them here
            import string
            drives = [f"{c}:\\" for c in string.ascii_uppercase if Path(f"{c}:\\").exists()]
        return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "images": images, "drives": drives,
                "dirs": [{"name": d.name, "path": str(d)} for d in dirs[:500]]}

    # ------------------------------------------------------------- projects
    @app.get("/api/projects", **api)
    def projects():
        return engine.list_projects()

    @app.post("/api/projects", **api)
    def create_project(body: dict = Body(...)):
        try:
            p = engine.create_project(body.get("name", ""), body.get("photos_dir", ""))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return p.summary()

    @app.get("/api/projects/{pid}", **api)
    def project(pid: str):
        p = proj(pid)
        return {**p.summary(), "settings": p.settings(), "stats": Review(p).stats(), "profile": (p.bib_profile().to_dict() if p.bib_profile() else None)}

    @app.delete("/api/projects/{pid}", **api)
    def delete_project(pid: str):
        proj(pid)
        engine.delete_project(pid)
        return {"ok": True}

    @app.put("/api/projects/{pid}/settings", **api)
    def project_settings(pid: str, changes: dict = Body(...)):
        p = proj(pid)
        for k, v in changes.items():
            try:
                p.set_setting(k, v)
            except KeyError:
                raise HTTPException(400, f"unknown setting: {k}")
        return p.settings()

    @app.post("/api/projects/{pid}/process", **api)
    def process(pid: str):
        proj(pid)
        return engine.process_job(pid).to_dict()

    @app.post("/api/projects/{pid}/rescore", **api)
    def rescore(pid: str):
        p = proj(pid)
        return engine.jobs.start("rescore", lambda j: p.rescore(engine.variant()), "Scoring").to_dict()

    # --------------------------------------------------------------- photos
    @app.get("/api/projects/{pid}/photos", **api)
    def photos(pid: str, limit: int = 200, offset: int = 0):
        return proj(pid).photos()[offset:offset + limit]

    @app.get("/api/projects/{pid}/photos/{photo_id}", **api)
    def photo(pid: str, photo_id: int):
        d = Review(proj(pid)).photo_detail(photo_id)
        if not d:
            raise HTTPException(404)
        return d

    def photo_path(pid: str, photo_id: int) -> Path:
        path = proj(pid).photo_path(photo_id)
        if not path or not Path(path).exists():
            raise HTTPException(404, "photo not found (moved or deleted?)")
        return Path(path)

    @app.get("/api/projects/{pid}/photos/{photo_id}/image", **api)
    def photo_image(pid: str, photo_id: int, w: int = 640):
        """Cached JPEG thumbnail, EXIF-rotated like the pose boxes. w snaps to a fixed size to keep the cache small."""
        w = min(THUMB_SIZES, key=lambda s: abs(s - w))
        cache = proj(pid).root / "thumbs" / f"{photo_id}_{w}.jpg"
        if not cache.exists():
            cache.parent.mkdir(exist_ok=True)
            with Image.open(photo_path(pid, photo_id)) as im:
                im.draft("RGB", (w * 2, w * 2))
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((w, w), Image.LANCZOS)
                tmp = cache.with_suffix(f".{secrets.token_hex(4)}.tmp")    # unique: simultaneous requests must not share a temp file
                im.save(tmp, "JPEG", quality=82)
                tmp.replace(cache)
        return FileResponse(cache, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/api/projects/{pid}/photos/{photo_id}/crop", **api)
    def photo_crop(pid: str, photo_id: int, box: str = Query(..., description="x1,y1,x2,y2 in full-res pixels"), w: int = 480):
        try:
            x1, y1, x2, y2 = [float(v) for v in box.split(",")]
        except ValueError:
            raise HTTPException(400, "box must be x1,y1,x2,y2")
        w = min(max(w, 32), 1600)
        cache = proj(pid).root / "crops" / f"{photo_id}_{int(x1)}_{int(y1)}_{int(x2)}_{int(y2)}_{w}.jpg"
        if not cache.exists():
            with Image.open(photo_path(pid, photo_id)) as im:
                raw_w, raw_h = im.size
                rotated = im.getexif().get(0x0112, 1) in (5, 6, 7, 8)
                W, H = (raw_h, raw_w) if rotated else (raw_w, raw_h)           # displayed size, like the pose boxes
                if x2 - x1 < 2 or y2 - y1 < 2:
                    raise HTTPException(400, "empty crop")
                s = min(1.0, w / (x2 - x1))
                im.draft("RGB", (max(1, int(raw_w * s)), max(1, int(raw_h * s))))   # JPEG decodes at 1/2, 1/4, 1/8 size: much faster
                im = ImageOps.exif_transpose(im).convert("RGB")
                f = im.width / W
                c = im.crop((max(0, int(x1 * f)), max(0, int(y1 * f)), min(im.width, int(x2 * f)), min(im.height, int(y2 * f))))
                if c.width < 2 or c.height < 2:
                    raise HTTPException(400, "empty crop")
                c.thumbnail((w, w), Image.LANCZOS)
                cache.parent.mkdir(exist_ok=True)
                tmp = cache.with_suffix(f".{secrets.token_hex(4)}.tmp")    # unique: simultaneous requests must not share a temp file
                c.save(tmp, "JPEG", quality=85)
                tmp.replace(cache)
        return FileResponse(cache, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    # ---------------------------------------------------------- competitors
    @app.get("/api/projects/{pid}/competitors", **api)
    def competitors(pid: str):
        return proj(pid).competitors()

    @app.post("/api/projects/{pid}/competitors", **api)
    def add_competitor(pid: str, body: dict = Body(...)):
        try:
            number = int(body["number"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "number is required")
        if not 1 <= number <= 9999:
            raise HTTPException(400, "number out of range")
        return {"id": proj(pid).add_competitor(number, body.get("name", ""))}

    @app.post("/api/projects/{pid}/competitors/import", **api)
    def import_competitors(pid: str, body: dict = Body(...)):
        """Paste a start list: one competitor per line, 'number name' or 'number;name' or 'number,name'; ranges like 1-40."""
        import re
        added = 0
        p = proj(pid)
        for line in (body.get("text") or "").splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d{1,4})\s*[-–]\s*(\d{1,4})$", line)
            if m and int(m.group(1)) < int(m.group(2)) <= 9999:
                for n in range(int(m.group(1)), int(m.group(2)) + 1):
                    p.add_competitor(n)
                    added += 1
                continue
            m = re.match(r"^(\d{1,4})\s*[;,\t ]?\s*(.*)$", line)
            if m:
                p.add_competitor(int(m.group(1)), m.group(2).strip())
                added += 1
        return {"added": added}

    @app.put("/api/projects/{pid}/competitors/{cid}", **api)
    def rename_competitor(pid: str, cid: int, body: dict = Body(...)):
        p = proj(pid)
        with p.lock:
            p.con.execute("UPDATE competitors SET name=? WHERE id=?", (body.get("name", ""), cid))
        return {"ok": True}

    @app.delete("/api/projects/{pid}/competitors/{cid}", **api)
    def delete_competitor(pid: str, cid: int):
        p = proj(pid)
        with p.lock:
            p.con.execute("DELETE FROM competitors WHERE id=?", (cid,))
        return {"ok": True}

    @app.get("/api/projects/{pid}/roster/suggest", **api)
    def roster_suggest(pid: str):
        return proj(pid).suggest_roster()

    # ---------------------------------------------------------- calibration
    @app.get("/api/projects/{pid}/calibration", **api)
    def calibration(pid: str):
        p = proj(pid)
        with p.lock:
            ex = [dict(r) for r in p.con.execute("SELECT id,photo_id,person_idx,number,rel_box FROM bib_examples")]
            people = p.load_persons()
            cands = []
            for ph in p.photos():                               # front-facing, big, hips visible: where a tag is likely visible
                ps = people.get(ph["id"], [])
                if ph["height"] and ps:
                    big = max(ps, key=lambda q: q.height)
                    if big.face is not None and big.height > 0.5 * ph["height"] and big.kpts[11, 2] > 0.5 and big.kpts[12, 2] > 0.5:
                        cands.append(ph["id"])
        step = max(1, len(cands) // 12)
        return {"examples": ex, "profile": (p.bib_profile().to_dict() if p.bib_profile() else None), "suggested_photos": cands[::step][:12]}

    @app.post("/api/projects/{pid}/calibration/example", **api)
    def calibration_example(pid: str, body: dict = Body(...)):
        (photo_id,) = need(body, "photo_id")
        try:
            box = [float(v) for v in body["box"]]
            assert len(box) == 4
            number = int(body["number"]) if body.get("number") not in (None, "") else None
        except (KeyError, ValueError, TypeError, AssertionError):
            raise HTTPException(400, "required: box [x1,y1,x2,y2] in photo pixels; number optional")
        try:
            return proj(pid).add_bib_example(photo_id, number, box)
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.delete("/api/projects/{pid}/calibration", **api)
    def calibration_clear(pid: str):
        proj(pid).clear_bib_examples()
        return {"ok": True}

    @app.post("/api/projects/{pid}/calibration/apply", **api)
    def calibration_apply(pid: str):
        p = proj(pid)

        def run(job):
            engine.models.ensure(progress=job.progress, cancelled=job.cancelled)
            n = p.reread_bibs(engine.extractor().bib, progress=job.progress, stop=job.cancelled)
            return {"photos": n, **p.rescore(engine.variant())}
        return engine.jobs.start("calibrate", run, "Re-reading numbers").to_dict()

    # --------------------------------------------------------------- review
    def rv(pid):
        return Review(proj(pid))

    @app.get("/api/projects/{pid}/review/stats", **api)
    def r_stats(pid: str):
        return rv(pid).stats()

    @app.get("/api/projects/{pid}/review/batches", **api)
    def r_batches(pid: str, limit: int = 6, per_batch: int = 48):
        return rv(pid).batches(limit, per_batch)

    @app.get("/api/projects/{pid}/review/cards", **api)
    def r_cards(pid: str, limit: int = 20):
        return rv(pid).cards(limit)

    @app.get("/api/projects/{pid}/review/unknown", **api)
    def r_unknown(pid: str, limit: int = 30):
        return rv(pid).unknown(limit)

    @app.get("/api/projects/{pid}/review/tags", **api)
    def r_tags(pid: str, limit: int = 12):
        return rv(pid).tags(limit)

    @app.get("/api/projects/{pid}/review/album", **api)
    def r_album(pid: str):
        return rv(pid).album()

    @app.get("/api/projects/{pid}/review/competitor/{cid}", **api)
    def r_competitor(pid: str, cid: int):
        return rv(pid).competitor_items(cid)

    @app.get("/api/projects/{pid}/review/game", **api)
    def r_game(pid: str):
        """Counters for the review screen. A grid review is one action even though it decides many photos."""
        p = proj(pid)
        with p.lock:
            now = time.time()
            midnight = now - (now % 86400)          # UTC day; good enough for a daily counter
            q = lambda s, *a: p.con.execute(s, a).fetchone()[0]
            total = q("SELECT COUNT(*) FROM decision_log WHERE undone=0")
            today = q("SELECT COUNT(*) FROM decision_log WHERE undone=0 AND at>=?", midnight)
            rows = [r["at"] for r in p.con.execute("SELECT at FROM decision_log WHERE undone=0 ORDER BY id DESC LIMIT 200")]
        actions = []                                  # one entry per UI action (decisions within 1 s belong together)
        for t in rows:
            if not actions or actions[-1] - t > 1.0:
                actions.append(t)
        combo = 0
        for a, b in zip(actions, actions[1:]):        # consecutive actions less than 4 s apart
            if a - b > 4:
                break
            combo += 1
        recent = actions[:20]
        span = recent[0] - recent[-1] if len(recent) > 1 else 0
        pace = min(600.0, (len(recent) - 1) / max(span, 5.0) * 60) if len(recent) > 1 and now - recent[0] < 120 else 0.0
        return {"total": total, "today": today, "combo": combo + 1 if actions and now - actions[0] < 10 else 0,
                "per_minute": round(pace, 1), "level": 1 + total // 100, "to_next": 100 - total % 100}

    @app.post("/api/projects/{pid}/review/accept", **api)
    def r_accept(pid: str, body: dict = Body(...)):
        photo_id, comp_id = need(body, "photo_id", "competitor_id")
        rv(pid).accept(photo_id, comp_id, body.get("person_idx"))
        return {"ok": True}

    @app.post("/api/projects/{pid}/review/reject", **api)
    def r_reject(pid: str, body: dict = Body(...)):
        photo_id, comp_id = need(body, "photo_id", "competitor_id")
        rv(pid).reject(photo_id, comp_id)
        return {"ok": True}

    @app.post("/api/projects/{pid}/review/batch", **api)
    def r_batch(pid: str, body: dict = Body(...)):
        (comp_id,) = need(body, "competitor_id")
        rv(pid).resolve_batch(comp_id, int_list(body, "accepted"), int_list(body, "rejected"))
        return {"ok": True}

    @app.post("/api/projects/{pid}/review/photo", **api)
    def r_photo(pid: str, body: dict = Body(...)):
        (photo_id,) = need(body, "photo_id")
        try:
            pm = {int(k): int(v) for k, v in (body.get("person_map") or {}).items()}
        except (TypeError, ValueError):
            raise HTTPException(400, "person_map must map competitor id to person index")
        rv(pid).set_photo(photo_id, int_list(body, "competitor_ids"), pm)
        return {"ok": True}

    @app.post("/api/projects/{pid}/review/nobody", **api)
    def r_nobody(pid: str, body: dict = Body(...)):
        (photo_id,) = need(body, "photo_id")
        rv(pid).nobody(photo_id)
        return {"ok": True}

    @app.post("/api/projects/{pid}/review/undo", **api)
    def r_undo(pid: str):
        return {"restored": rv(pid).undo()}

    # ----------------------------------------------------------------- find
    @app.post("/api/projects/{pid}/find", **api)
    async def find(pid: str, files: list[UploadFile] = File(...), top_k: int = 200):
        """Upload one or more photos of ONE person; get back the photos they appear in, best match first."""
        import cv2
        import numpy as np
        p = proj(pid)
        if not engine.models.ready():
            raise HTTPException(409, "models are not installed yet")
        faces = []
        fe = engine.extractor().faces
        for f in files[:10]:
            img = cv2.imdecode(np.frombuffer(await f.read(), np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            found = fe.faces_in_image(img)
            if found:
                faces.append(max(found, key=lambda x: (x["box"][2] - x["box"][0]) * (x["box"][3] - x["box"][1])))
        if not faces:
            raise HTTPException(422, "no face found in the uploaded photo(s)")
        use_arc = engine.variant() == "arcface"
        return {"references": len(faces), "matches": find_mod.find_person(p, faces, use_arc=use_arc, top_k=top_k)}

    @app.post("/api/projects/{pid}/find/assign", **api)
    def find_assign(pid: str, body: dict = Body(...)):
        (comp_id,) = need(body, "competitor_id")
        rv(pid).accept_many([(x, comp_id) for x in int_list(body, "photo_ids")])
        return {"ok": True}

    @app.post("/api/projects/{pid}/find/export", **api)
    def find_export(pid: str, body: dict = Body(...)):
        p = proj(pid)
        dest = Path(body.get("dest", "")).expanduser()
        if not str(dest).strip() or str(dest) == ".":
            raise HTTPException(400, "choose a destination folder")
        ids = int_list(body, "photo_ids")
        return engine.jobs.start("export", lambda job: export_mod.export_photos(p, ids, dest, body.get("mode") or "copy", progress=job.progress), "Exporting").to_dict()

    # ------------------------------------------------------------------ vlm
    @app.get("/api/projects/{pid}/vlm/estimate", **api)
    def vlm_estimate(pid: str, provider: str = "anthropic"):
        return vlm_assist.estimate(proj(pid), provider)

    @app.post("/api/projects/{pid}/vlm/run", **api)
    def vlm_run(pid: str, body: dict = Body(default={})):
        p = proj(pid)
        s = engine.settings()
        name = body.get("provider") or s["vlm_provider"]
        if not name:
            raise HTTPException(400, "choose a provider in settings first")
        try:
            provider = vlm_providers.make(name, engine.api_key(name) or "", body.get("model") or s["vlm_model"])
        except vlm_providers.VlmError as e:
            raise HTTPException(400, str(e))

        def run(job):
            r = vlm_assist.run(p, provider, progress=job.progress, stop=job.cancelled, limit=body.get("limit"))
            return {**r, **p.rescore(engine.variant())}
        return engine.jobs.start("vlm", run, "Reading numbers with " + name).to_dict()

    # --------------------------------------------------------------- export
    @app.get("/api/projects/{pid}/export/plan", **api)
    def export_plan(pid: str, assign_mode: str | None = None):
        folders = export_mod.plan(proj(pid), assign_mode)
        return {"folders": [{"name": k, "count": len(v), "auto": sum(1 for _, h in v if h == "auto")} for k, v in sorted(folders.items())],
                "files": sum(len(v) for v in folders.values())}

    @app.post("/api/projects/{pid}/export", **api)
    def export(pid: str, body: dict = Body(...)):
        p = proj(pid)
        dest = Path(body.get("dest", "")).expanduser()
        if not str(dest).strip() or str(dest) == ".":
            raise HTTPException(400, "choose a destination folder")
        if Path(p.get_meta("photos_dir")).resolve() in [dest.resolve(), *dest.resolve().parents]:
            raise HTTPException(400, "the destination cannot be inside the source folder")

        def run(job):
            return export_mod.export(p, dest, body.get("mode"), body.get("assign_mode"), body.get("template"),
                                     body.get("include_unsorted", True), progress=job.progress)
        return engine.jobs.start("export", run, "Exporting").to_dict()

    # ------------------------------------------------------------------- UI
    @app.get("/", include_in_schema=False)
    def index(response: Response):
        resp = FileResponse(UI_DIR / "index.html", headers={"Cache-Control": "no-store"})
        resp.set_cookie("edic", token, httponly=True, samesite="strict")
        return resp

    if UI_DIR.exists():
        app.mount("/", StaticFiles(directory=UI_DIR), name="ui")
    return app
