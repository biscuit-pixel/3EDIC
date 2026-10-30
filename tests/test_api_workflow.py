"""The v0.2 HTTP surface: roster mode, workflow steps, queue/context, per-person tagging, bulk, album ops, status, working folder."""
import os
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from edic.api import create_app
from edic.engine import Engine
from edic.types import Person


def wait(c, job, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["state"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("EDIC_DATA", raising=False)
    eng = Engine(tmp_path / "data")
    photos = tmp_path / "photos"
    photos.mkdir()
    for i in range(8):
        f = photos / f"P{i:02d}.jpg"
        Image.new("RGB", (400, 600), (i * 20, 80, 120)).save(f)
        os.utime(f, (1_700_000_000 + i,) * 2)
    c = TestClient(create_app(eng, token="t"), base_url="http://127.0.0.1:8737")
    c.cookies.set("edic", "t")
    return eng, c, photos


def make_people(pr, two=()):
    rng = np.random.default_rng(2)
    for ph in pr.photos():
        people = []
        for k in range(2 if ph["id"] in two else 1):
            kp = np.zeros((17, 3), np.float32)
            kp[[11, 12]] = [[150 + 60 * k, 350, 1], [250 + 60 * k, 350, 1]]
            p = Person(box=(100.0 + 60 * k, 50.0, 300.0 + 60 * k, 550.0), kpts=kp)
            p.face = (lambda v: (v / np.linalg.norm(v)).astype(np.float32))(rng.normal(size=128))
            p.colour = np.abs(rng.normal(size=72)).astype(np.float32)
            people.append(p)
        pr.save_persons(ph["id"], people)


def test_create_ingest_and_workflow_stages(env):
    eng, c, photos = env
    r = c.post("/api/projects", json={"name": "", "photos_dir": str(photos / "P00.jpg"), "roster_mode": "list"})   # a dropped file means its folder
    assert r.status_code == 200
    pid = r.json()["id"]
    assert c.get(f"/api/projects/{pid}").json()["settings"]["roster_mode"] == "list"
    assert c.post("/api/projects", json={"photos_dir": str(photos), "roster_mode": "x"}).status_code == 400
    assert c.get(f"/api/projects/{pid}/workflow").json()["stage"] == "import"
    assert wait(c, c.post(f"/api/projects/{pid}/ingest").json())["state"] == "done"
    assert c.get(f"/api/projects/{pid}/workflow").json()["stage"] == "examples"
    w = c.post(f"/api/projects/{pid}/workflow/skip", json={"step": "examples"}).json()
    assert w["stage"] == "analyse"
    assert c.post(f"/api/projects/{pid}/workflow/skip", json={"step": "nope"}).status_code == 400
    info = c.post("/api/resolve-folder", json={"path": str(photos / "P01.jpg")}).json()
    assert info["path"] == str(photos) and info["images"] == 8
    assert c.post("/api/resolve-folder", json={"path": str(photos / "missing")}).status_code == 400


def test_queue_context_person_and_bulk(env):
    eng, c, photos = env
    pr = eng.create_project("T", str(photos))
    pr.ingest()
    ids = [p["id"] for p in pr.photos()]
    make_people(pr, two={ids[2]})
    pid = pr.id
    q = c.get(f"/api/projects/{pid}/review/queue").json()
    assert q["counts"]["unknown"] == 8 and [i["id"] for i in q["items"]] == ids         # no albums yet: everything is unknown, in shooting order
    ctx = c.get(f"/api/projects/{pid}/review/context/{ids[4]}?before=2&after=1").json()["items"]
    assert [i["id"] for i in ctx] == ids[2:6] and [i["id"] for i in ctx if i["current"]] == [ids[4]]
    eng.update_settings({"context_frames": 3})
    assert len(c.get(f"/api/projects/{pid}/review/context/{ids[4]}").json()["items"]) == 7

    # two people on one photo: tagging one does not decide the other
    r = c.post(f"/api/projects/{pid}/review/person", json={"photo_id": ids[2], "person_idx": 0, "number": 56}).json()
    assert r["ok"]
    d = c.get(f"/api/projects/{pid}/photos/{ids[2]}").json()
    assert [a["label"] for a in d["persons"][0]["assigned"]] == ["056"] and d["persons"][1]["assigned"] == []
    c.post(f"/api/projects/{pid}/review/person", json={"photo_id": ids[2], "person_idx": 1, "number": 57})
    d = c.get(f"/api/projects/{pid}/photos/{ids[2]}").json()
    assert [a["label"] for a in d["persons"][1]["assigned"]] == ["057"]
    assert c.post(f"/api/projects/{pid}/review/person", json={"photo_id": ids[2], "person_idx": 1, "number": 0}).status_code == 400
    c.post(f"/api/projects/{pid}/review/person", json={"photo_id": ids[2], "person_idx": 1, "others": True})
    d = c.get(f"/api/projects/{pid}/photos/{ids[2]}").json()
    assert [a["label"] for a in d["persons"][1]["assigned"]] == ["Others"]

    # bulk tag with a brand-new number creates its album
    b = c.post(f"/api/projects/{pid}/review/bulk-tag", json={"photo_ids": ids[3:6], "number": 77}).json()
    assert b["count"] == 3
    labels = {a["label"]: a for a in c.get(f"/api/projects/{pid}/review/album").json()}
    assert labels["077"]["confirmed"] == 3 and labels["Others"]["confirmed"] == 1
    # "does not match" sends photos back to the queue
    c.post(f"/api/projects/{pid}/review/bulk-unknown", json={"competitor_id": b["competitor_id"], "photo_ids": ids[3:5]})
    q = c.get(f"/api/projects/{pid}/review/queue?kind=unknown").json()
    assert {ids[3], ids[4]} <= {i["id"] for i in q["items"]}
    assert c.post(f"/api/projects/{pid}/review/bulk-tag", json={"photo_ids": [], "number": 5}).status_code == 400


def test_album_operations(env):
    eng, c, photos = env
    pr = eng.create_project("T", str(photos))
    pr.ingest()
    ids = [p["id"] for p in pr.photos()]
    make_people(pr)
    pid = pr.id
    a = c.post(f"/api/projects/{pid}/review/bulk-tag", json={"photo_ids": ids[:4], "number": 56}).json()["competitor_id"]
    b = c.post(f"/api/projects/{pid}/review/bulk-tag", json={"photo_ids": ids[4:6], "number": 60}).json()["competitor_id"]
    assert c.put(f"/api/projects/{pid}/competitors/{a}", json={"name": "Jana"}).status_code == 200
    # renumber onto an existing album -> becomes 60_2 (another category); merge=true folds in instead
    out = c.put(f"/api/projects/{pid}/competitors/{a}", json={"number": 60}).json()
    assert out["id"] == a
    assert {x["label"] for x in c.get(f"/api/projects/{pid}/competitors").json()} == {"060", "060_2"}
    n = c.post(f"/api/projects/{pid}/competitors/{a}/split", json={"photo_ids": ids[:1]}).json()["id"]
    assert any(x["id"] == n and x["label"] == "060_3" for x in c.get(f"/api/projects/{pid}/competitors").json())
    # removing wrong photos asks where they go
    assert c.post(f"/api/projects/{pid}/competitors/{a}/remove-photos", json={"photo_ids": ids[1:2], "destination": {"type": "x"}}).status_code == 400
    c.post(f"/api/projects/{pid}/competitors/{a}/remove-photos", json={"photo_ids": ids[1:2], "destination": {"type": "unknown"}})
    c.post(f"/api/projects/{pid}/competitors/{a}/remove-photos", json={"photo_ids": ids[2:3], "destination": {"type": "others"}})
    c.post(f"/api/projects/{pid}/competitors/{a}/remove-photos", json={"photo_ids": ids[3:4], "destination": {"type": "album", "number": 99}})
    labels = {x["label"]: x for x in c.get(f"/api/projects/{pid}/review/album").json()}
    assert labels["Others"]["confirmed"] == 1 and labels["099"]["confirmed"] == 1
    assert ids[1] in {i["id"] for i in c.get(f"/api/projects/{pid}/review/queue?kind=unknown").json()["items"]}
    # empty albums can be deleted in one go; merge needs a different target
    assert c.post(f"/api/projects/{pid}/competitors/{b}/merge", json={"into": b}).status_code == 400
    c.post(f"/api/projects/{pid}/competitors/{b}/merge", json={"into": n})
    assert c.delete(f"/api/projects/{pid}/albums/empty").json()["deleted"] >= 1
    assert c.put(f"/api/projects/{pid}/competitors/9999", json={"name": "x"}).status_code == 404


def test_status_device_and_working_folder(env, tmp_path):
    eng, c, _ = env
    s = c.get("/api/status").json()
    assert s["active"] == [] and s["preference"] == "auto" and s["device"]
    assert c.put("/api/settings", json={"device": "cpu"}).json()["device"] == "cpu"
    assert c.get("/api/status").json()["device"] == "CPU"
    assert c.get("/api/system").json()["hardware"]["gpu_available"] in (True, False)
    assert c.put("/api/settings", json={"device": "tpu"}).status_code == 400
    c.put("/api/settings", json={"device": "auto"})
    # moving the working folder copies projects and settings, remembers the new place, and never deletes the old one
    pr = eng.create_project("Keep", str(env[2]))
    dest = tmp_path / "elsewhere"
    assert c.post("/api/data-dir", json={"path": str(eng.data_dir / "inside")}).status_code == 400
    job = wait(c, c.post("/api/data-dir", json={"path": str(dest)}).json())
    assert job["state"] == "done" and job["result"]["restart"]
    assert (dest / "projects" / pr.id / "project.db").exists() and (dest / "settings.json").exists()
    assert (eng.data_dir / "projects" / pr.id).exists()
    from edic.engine import default_data_dir
    assert default_data_dir() == dest.resolve()
    assert c.get("/api/data-dir").json()["current"] == str(eng.data_dir)
