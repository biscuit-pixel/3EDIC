"""Use a vision LLM to read bib numbers the local OCR could not."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from ..vision.regions import hip_box, clip
from . import providers

MIN_PERSON_PX = 400          # smaller people are too small for a legible tag
CROP_LONG_SIDE = 512
TOKENS_PER_CALL = 450        # ~350 image tokens + prompt + reply; used for the cost estimate only


def candidates(project, ocr_conf=0.5):
    """Persons that look worth asking about: big enough, hips visible, no number read yet, not asked before."""
    asked = {(r["photo_id"], r["person_idx"]) for r in project.con.execute("SELECT photo_id,person_idx FROM vlm_reads")}
    out = []
    for photo_id, people in project.load_persons().items():
        for j, p in enumerate(people):
            if (photo_id, j) in asked or p.height < MIN_PERSON_PX:
                continue
            if p.kpts[11, 2] < 0.3 and p.kpts[12, 2] < 0.3:
                continue
            if any(c >= ocr_conf for c in p.numbers.values()):
                continue
            out.append((photo_id, j))
    return out


def estimate(project, provider_name: str, ocr_conf=0.5) -> dict:
    n = len(candidates(project, ocr_conf))
    tokens = n * TOKENS_PER_CALL
    return {"crops": n, "input_tokens": tokens, "approx_cost_usd": round(tokens / 1e6 * providers.PRICE_PER_MTOK.get(provider_name, 1.0), 3)}


def crop_jpeg(img, person) -> bytes | None:
    b = hip_box(person, img.shape)
    if b is None:
        return None
    x1, y1, x2, y2 = b
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    hw, hh = (x2 - x1) * 0.75, (y2 - y1) * 0.75                 # a little context around the hip box
    x1, y1, x2, y2 = clip(cx - hw, cy - hh, cx + hw, cy + hh, img.shape)
    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    s = CROP_LONG_SIDE / max(crop.shape[:2])
    if s < 1:
        crop = cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes() if ok else None


def run(project, provider: providers.Provider, progress=None, stop=lambda: False, workers=4, limit=None) -> dict:
    """Ask the provider about every candidate; results (including 'no number') are cached in the project."""
    todo = candidates(project)[:limit]
    paths = {r["id"]: r["path"] for r in project.con.execute("SELECT id,path FROM photos")}
    people = project.load_persons()
    by_photo: dict[int, list[int]] = {}
    for photo_id, j in todo:
        by_photo.setdefault(photo_id, []).append(j)
    found = asked = 0
    done = 0

    def one(item):
        photo_id, j, jpeg = item
        return photo_id, j, provider.read_number(jpeg)

    def jobs():
        for photo_id, js in by_photo.items():
            img = cv2.imread(paths[photo_id], cv2.IMREAD_COLOR)
            if img is None:
                continue
            for j in js:
                jpeg = crop_jpeg(img, people[photo_id][j])
                if jpeg:
                    yield photo_id, j, jpeg

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for photo_id, j, (number, conf) in pool.map(one, jobs()):
            project.con.execute("INSERT OR REPLACE INTO vlm_reads(photo_id,person_idx,provider,number,confidence) VALUES(?,?,?,?,?)",
                                (photo_id, j, provider.name, number, conf))
            asked += 1
            found += number is not None
            done += 1
            if progress:
                progress(done, len(todo), f"{found} numbers read")
            if stop():
                break
    return {"asked": asked, "numbers_found": found}
