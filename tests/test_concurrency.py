"""The API serves many image requests while the user clicks: no request may fail (regression: shared SQLite connection).
Run: pytest -q tests/test_concurrency.py
"""
import os
import random
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from edic.api import create_app
from edic.engine import Engine
from edic.types import Person


def make(tmp_path):
    eng = Engine(tmp_path / "data")
    photos = tmp_path / "photos"
    photos.mkdir()
    for i in range(12):
        Image.new("RGB", (400, 600), (i * 15, 80, 120)).save(photos / f"P{i:02d}.jpg")
        os.utime(photos / f"P{i:02d}.jpg", (1_700_000_000 + i,) * 2)
    pr = eng.create_project("Stress", str(photos))
    pr.ingest()
    rng = np.random.default_rng(1)
    for ph in pr.photos():
        kp = np.zeros((17, 3), np.float32)
        kp[[11, 12]] = [[150, 350, 1], [250, 350, 1]]
        p = Person(box=(100.0, 50.0, 300.0, 550.0), kpts=kp)
        p.face = (lambda v: (v / np.linalg.norm(v)).astype(np.float32))(rng.normal(size=128))
        p.colour = np.abs(rng.normal(size=72)).astype(np.float32)
        pr.save_persons(ph["id"], [p])
    pr.set_roster([21, 22, 23])
    pr.rescore("basic")
    return eng, pr


def test_parallel_images_and_review_actions_never_fail(tmp_path):
    eng, pr = make(tmp_path)
    c = TestClient(create_app(eng, token="t"), base_url="http://127.0.0.1:8737")
    c.cookies.set("edic", "t")
    ids = [p["id"] for p in pr.photos()]
    comps = [x["id"] for x in pr.competitors()]
    rnd = random.Random(3)

    def hit(i):
        kind = i % 7
        pid, ph, cp = pr.id, rnd.choice(ids), rnd.choice(comps)
        if kind == 0:
            r = c.get(f"/api/projects/{pid}/photos/{ph}/image?w={rnd.choice([160, 320, 640])}")
        elif kind == 1:
            r = c.get(f"/api/projects/{pid}/photos/{ph}/crop?box=100,50,300,550&w=200")
        elif kind == 2:
            r = c.post(f"/api/projects/{pid}/review/accept", json={"photo_id": ph, "competitor_id": cp})
        elif kind == 3:
            r = c.post(f"/api/projects/{pid}/review/reject", json={"photo_id": ph, "competitor_id": cp})
        elif kind == 4:
            r = c.post(f"/api/projects/{pid}/review/undo")
        elif kind == 5:
            r = c.get(f"/api/projects/{pid}/review/stats")
        else:
            r = c.get(f"/api/projects/{pid}/photos/{ph}")
        return kind, r.status_code

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(hit, range(300)))
    bad = [(k, s) for k, s in results if s >= 400]
    assert not bad, bad[:5]
