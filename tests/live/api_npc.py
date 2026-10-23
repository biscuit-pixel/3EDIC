"""API tests: guard, images, review flows (NPC fixture) and a live pipeline run on a handful of real photos.
usage: test_api.py FIXTURE_DATA_DIR FIXTURE_PROJECT_ID PHOTOS_FOR_LIVE_RUN_DIR
"""
import io
import shutil
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from edic.api import create_app  # noqa: E402
from edic.engine import Engine  # noqa: E402


def ok(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name, extra)
    assert cond, name


def wait(c, job, timeout=900):
    t = time.time()
    while time.time() - t < timeout:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["state"] != "running":
            return j
        time.sleep(0.5)
    raise TimeoutError


def main(fixture, pid, live_dir):
    eng = Engine(fixture)
    app = create_app(eng, token="t0k")
    c = TestClient(app, base_url="http://127.0.0.1:8737")
    ok("no cookie -> 401", c.get("/api/system").status_code == 401)
    c.cookies.set("edic", "t0k")
    ok("cookie ok", c.get("/api/system").status_code == 200)
    evil = TestClient(app, base_url="http://evil.example")
    evil.cookies.set("edic", "t0k")
    ok("foreign Host -> 403", evil.get("/api/system").status_code == 403)
    s = c.get("/api/system").json()
    ok("system", s["version"] and "providers" in s["hardware"], s["hardware"])

    fs = c.get("/api/fs", params={"path": live_dir}).json()
    ok("folder picker", fs["images"] > 0, f'{fs["images"]} images')

    # ---- fixture project (NPC): review flows
    p = c.get(f"/api/projects/{pid}").json()
    ok("project", p["photos"] == 969 and p["competitors"] == 58, p["stats"])
    photos = c.get(f"/api/projects/{pid}/photos", params={"limit": 5}).json()
    pid0 = photos[0]["id"]
    r = c.get(f"/api/projects/{pid}/photos/{pid0}/image", params={"w": 320})
    im = Image.open(io.BytesIO(r.content))
    ok("thumbnail", r.status_code == 200 and max(im.size) <= 320 and r.headers["content-type"] == "image/jpeg", im.size)
    t = time.time(); c.get(f"/api/projects/{pid}/photos/{pid0}/image", params={"w": 320}); ok("thumbnail cached", time.time() - t < 0.2)
    det = c.get(f"/api/projects/{pid}/photos/{pid0}").json()
    ok("photo detail with persons", "persons" in det and "candidates" in det, f'{len(det["persons"])} persons')
    if det["persons"]:
        b = det["persons"][0]["box"]
        r = c.get(f"/api/projects/{pid}/photos/{pid0}/crop", params={"box": ",".join(map(str, b)), "w": 240})
        ok("crop", r.status_code == 200 and max(Image.open(io.BytesIO(r.content)).size) <= 240)
    ok("bad crop box -> 400", c.get(f"/api/projects/{pid}/photos/{pid0}/crop", params={"box": "a,b"}).status_code == 400)
    ok("unknown project -> 404", c.get("/api/projects/nope").status_code == 404)

    st = c.get(f"/api/projects/{pid}/review/stats").json()
    batches = c.get(f"/api/projects/{pid}/review/batches").json()
    cards = c.get(f"/api/projects/{pid}/review/cards").json()
    unknown = c.get(f"/api/projects/{pid}/review/unknown").json()
    tags = c.get(f"/api/projects/{pid}/review/tags").json()
    album = c.get(f"/api/projects/{pid}/review/album").json()
    ok("review views", all(isinstance(x, (list, dict)) for x in (batches, cards, unknown, tags, album)),
       f"batches={len(batches)} cards={len(cards)} unknown={len(unknown)} tags={len(tags)} album={len(album)}")
    if cards:
        card = cards[0]
        a0 = c.get(f"/api/projects/{pid}/review/stats").json()["accepted_pairs"]
        c.post(f"/api/projects/{pid}/review/accept", json={"photo_id": card["photo_id"], "competitor_id": card["competitor_id"]})
        ok("accept", c.get(f"/api/projects/{pid}/review/stats").json()["accepted_pairs"] == a0 + 1)
        ok("undo", c.post(f"/api/projects/{pid}/review/undo").json()["restored"] is not None
           and c.get(f"/api/projects/{pid}/review/stats").json()["accepted_pairs"] == a0)
        g = c.get(f"/api/projects/{pid}/review/game").json()
        ok("game stats sane", g["total"] >= 0 and g["level"] >= 1 and g["per_minute"] <= 600, g)
    for path in ("accept", "reject", "batch", "photo", "nobody"):
        ok(f"empty body to review/{path} -> 400", c.post(f"/api/projects/{pid}/review/{path}", json={}).status_code == 400)
    ok("garbage list -> 400", c.post(f"/api/projects/{pid}/review/batch", json={"competitor_id": 1, "accepted": ["x"]}).status_code == 400)
    # a real example on a photo that has people: must save, count, and leave the project usable
    phs = c.get(f"/api/projects/{pid}/calibration").json()["suggested_photos"]
    det0 = c.get(f"/api/projects/{pid}/photos/{phs[0]}").json()
    hip = [q for q in det0["persons"] if q["hip"]][0]["hip"]
    ex = c.post(f"/api/projects/{pid}/calibration/example", json={"photo_id": phs[0], "number": None, "box": [hip[0], hip[1], hip[0] + 60, hip[1] + 60]})
    ok("calibration example saves", ex.status_code == 200, ex.text[:80])
    far = c.post(f"/api/projects/{pid}/calibration/example", json={"photo_id": phs[0], "box": [0, 0, 5, 5]})
    ok("example far from any person -> 422", far.status_code == 422, far.text[:80])
    c.delete(f"/api/projects/{pid}/calibration")
    ok("calibration bad box -> 400", c.post(f"/api/projects/{pid}/calibration/example", json={"photo_id": 1, "box": [1, 2]}).status_code == 400)

    # roster import
    cnt = c.post(f"/api/projects/{pid}/competitors/import", json={"text": "901 Test Person\n902;Another\n950-952\njunk line"}).json()
    ok("roster import (2 named + range of 3)", cnt["added"] == 5, cnt)
    cs = {x["number"]: x for x in c.get(f"/api/projects/{pid}/competitors").json()}
    ok("names stored", cs[901]["name"] == "Test Person" and 951 in cs)
    for n in (901, 902, 950, 951, 952):
        c.delete(f"/api/projects/{pid}/competitors/{cs[n]['id']}") if n in cs else None

    plan = c.get(f"/api/projects/{pid}/export/plan").json()
    ok("export plan", plan["files"] > 0 and plan["folders"], f'{plan["files"]} files, {len(plan["folders"])} folders')
    dest = Path(tempfile.mkdtemp(prefix="edic_exp_"))
    j = wait(c, c.post(f"/api/projects/{pid}/export", json={"dest": str(dest), "mode": "symlink"}).json())
    ok("export job", j["state"] == "done" and (dest / "3edic-report.csv").exists(), j["result"])
    inside = c.post(f"/api/projects/{pid}/export", json={"dest": "/home/user/npc_flat/out"})
    ok("export inside source folder refused", inside.status_code == 400)
    shutil.rmtree(dest, ignore_errors=True)

    # ---- live run on real photos, fresh data dir (models are downloaded for real)
    live = Engine(tempfile.mkdtemp(prefix="edic_live_"))
    lc = TestClient(create_app(live, token="t"), base_url="http://127.0.0.1:8737"); lc.cookies.set("edic", "t")
    ok("fresh install: models missing", lc.get("/api/system").json()["ready"] is False)
    pr = lc.post("/api/projects", json={"name": "Live", "photos_dir": live_dir}).json()
    ok("project created", pr["photos"] == 0)
    t = time.time()
    j = wait(lc, lc.post(f"/api/projects/{pr['id']}/process").json())
    ok("process job (download + ingest + extract + score)", j["state"] == "done", f'{time.time()-t:.0f}s {j["result"] or j["error"]}')
    sm = lc.get(f"/api/projects/{pr['id']}").json()
    ok("all photos processed", sm["processed"] == sm["photos"] > 0, sm["stats"])
    ok("models ready after", lc.get("/api/system").json()["ready"])
    sug = lc.get(f"/api/projects/{pr['id']}/roster/suggest").json()
    print("   roster suggestions from OCR:", sug[:5])
    ok("project delete keeps photos", lc.delete(f"/api/projects/{pr['id']}").status_code == 200 and any(Path(live_dir).iterdir()))
    print("all API tests passed")


if __name__ == "__main__":
    main(*sys.argv[1:4])
