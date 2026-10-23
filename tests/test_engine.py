"""Self-contained tests on synthetic data (no models, no dataset): scoring, review flow, export, find-person.
Run: pytest -q tests/test_engine.py
"""
import csv
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from edic import scoring
from edic.export import export, folder_name, plan
from edic.find import find_person
from edic.project import Project
from edic.review import Review
from edic.types import Person

RNG = np.random.default_rng(7)


def unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


FACE = {41: unit(RNG.normal(size=128)), 42: unit(RNG.normal(size=128))}      # two "people"
COLOUR = {41: unit(np.abs(RNG.normal(size=72))), 42: unit(np.abs(RNG.normal(size=72)))}


def person(comp=None, number=None, noise=0.02, y=0, h=1000):
    """A synthetic person: face/colour of `comp` (plus noise) and optionally a read bib `number`."""
    kp = np.zeros((17, 3), np.float32)
    kp[[11, 12]] = [[400, 600, 1], [500, 600, 1]]
    p = Person(box=(300.0, float(y), 600.0, float(y + h)), kpts=kp)
    if comp is not None:
        p.face = unit(FACE[comp] + noise * RNG.normal(size=128))      # noise norm ~0.23 vs 1.0: same person, small variation
        p.colour = (COLOUR[comp] ** 2).astype(np.float32)
    if number is not None:
        p.numbers = {number: 0.7}
    return p


@pytest.fixture
def project(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    pr = Project.create(tmp_path / "data", "Test", photos)
    # 10 photos, 2 s apart: 41 x4 (first one has a readable bib), 42 x4 (first has bib), one group, one empty stage
    plan_ = [([41], 41), ([41], None), ([41], None), ([41], None), ([42], 42), ([42], None), ([42], None), ([42], None),
             ([41, 42], None), ([], None)]
    for i, (comps, bib) in enumerate(plan_):
        f = photos / f"IMG_{i:03d}.jpg"
        Image.new("RGB", (200, 300), (i * 20, 50, 90)).save(f)
        os.utime(f, (1_700_000_000 + i * 2,) * 2)
    pr.ingest()
    for ph, (comps, bib) in zip(pr.photos(), plan_):
        people = [person(c, bib if c == comps[0] else None) for c in comps]
        pr.save_persons(ph["id"], people)
    pr.set_roster([41, 42])
    return pr


def comp_id(pr, number):
    return next(c["id"] for c in pr.competitors() if c["number"] == number)


def test_ingest_orders_by_time_and_is_idempotent(project):
    assert [p["name"] for p in project.photos()] == [f"IMG_{i:03d}.jpg" for i in range(10)]
    assert project.ingest() == 0                         # nothing new the second time


def test_scoring_places_people_and_ignores_single_digit_reads(project):
    project.rescore("basic")
    rv = Review(project)
    p41, p42 = comp_id(project, 41), comp_id(project, 42)
    got = {(r["name"], r["c"]) for r in project.con.execute("SELECT photos.name AS name, competitor_id AS c FROM assignments JOIN photos ON photos.id=photo_id WHERE p>=0.5")}
    # the bib-anchored photos and their face/colour twins are found, and the wrong person is not suggested
    assert ("IMG_001.jpg", p41) in got and ("IMG_005.jpg", p42) in got
    assert ("IMG_001.jpg", p42) not in got and ("IMG_005.jpg", p41) not in got
    assert rv.stats()["photos"] == 10

    # a single-digit OCR read is usually a fragment of another number: it must not become an anchor
    inp, _, _ = project._score_input("basic")
    inp.roster, inp.ocr_min_number = [4, 5], 10
    inp.persons[0][0].numbers = {4: 0.9}
    f_num, *_ = scoring.features(inp, scoring.VARIANTS["basic"], "sface")
    assert f_num.sum() == 0
    inp.ocr_min_number = 1                                # ...unless the event really uses single digits
    f_num, *_ = scoring.features(inp, scoring.VARIANTS["basic"], "sface")
    assert f_num[0, 0] > 0


def test_user_decisions_survive_rescoring_and_undo_restores(project):
    project.rescore("basic")
    rv = Review(project)
    ph = project.photos()[3]["id"]
    c42 = comp_id(project, 42)
    rv.accept(ph, c42)
    project.rescore("basic")
    row = project.con.execute("SELECT status,source FROM assignments WHERE photo_id=? AND competitor_id=?", (ph, c42)).fetchone()
    assert (row["status"], row["source"]) == ("accepted", "user")
    assert rv.undo() == (ph, c42)
    after = project.con.execute("SELECT status FROM assignments WHERE photo_id=? AND competitor_id=?", (ph, c42)).fetchone()
    assert after is None or after["status"] == "suggested"
    assert rv.undo() is None                              # nothing left to undo


def test_rejected_pairs_are_never_exported(project):
    project.rescore("basic")
    rv = Review(project)
    p41 = comp_id(project, 41)
    first = project.photos()[1]["id"]
    rv.reject(first, p41)
    assert not any(p == first and c == p41 for p, c, _ in rv.effective("all"))
    project.rescore("basic")
    assert not any(p == first and c == p41 for p, c, _ in rv.effective("all"))


def test_nobody_and_manual_tagging(project):
    rv = Review(project)
    empty = project.photos()[9]["id"]
    assert empty in {x["id"] for x in rv.unknown(limit=50)}
    rv.nobody(empty)
    assert empty not in {x["id"] for x in rv.unknown(limit=50)}
    group = project.photos()[8]["id"]
    rv.set_photo(group, [comp_id(project, 41), comp_id(project, 42)])
    assert {c for p, c, how in rv.effective("manual") if p == group} == {comp_id(project, 41), comp_id(project, 42)}


def test_assign_modes_differ_as_documented(project):
    project.rescore("basic")
    rv = Review(project)
    n = {m: len(rv.effective(m)) for m in ("manual", "numbers_only", "smart", "all")}
    assert n["manual"] == 0
    assert n["numbers_only"] <= n["smart"] <= n["all"]


def test_batches_and_resolve(project):
    project.set_setting("auto_threshold", 0.999)         # push everything into "check"
    project.rescore("basic")
    rv = Review(project)
    batches = rv.batches(limit=5)
    assert batches and all(0 <= i["p"] < 0.999 for b in batches for i in b["items"])
    b = batches[0]
    ok_ids = [i["photo_id"] for i in b["items"][:-1]]
    bad_ids = [b["items"][-1]["photo_id"]]
    rv.resolve_batch(b["competitor"]["id"], ok_ids, bad_ids)
    states = {r["photo_id"]: r["status"] for r in project.con.execute("SELECT photo_id,status FROM assignments WHERE competitor_id=?", (b["competitor"]["id"],))}
    assert all(states[i] == "accepted" for i in ok_ids) and states[bad_ids[0]] == "rejected"


@pytest.mark.parametrize("mode", ["copy", "hardlink", "symlink"])
def test_export_writes_folders_report_and_handles_name_clashes(project, tmp_path, mode):
    project.rescore("basic")
    rv = Review(project)
    rv.set_photo(project.photos()[8]["id"], [comp_id(project, 41), comp_id(project, 42)])
    # a second card with the same file name must not overwrite the first
    other = tmp_path / "card2"
    other.mkdir()
    Image.new("RGB", (200, 300), (1, 2, 3)).save(other / "IMG_001.jpg")
    dest = tmp_path / f"out_{mode}"
    project.con.execute("UPDATE photos SET path=? WHERE name='IMG_000.jpg'", (str(other / "IMG_001.jpg"),))
    project.con.execute("UPDATE photos SET name='IMG_001.jpg' WHERE name='IMG_000.jpg'")
    res = export(project, dest, mode=mode, assign_mode="all")
    assert res["files"] > 0 and (dest / "3edic-report.csv").exists()
    rows = list(csv.DictReader(open(dest / "3edic-report.csv", encoding="utf-8")))
    assert len(rows) == res["files"]
    names_in_041 = sorted(p.name for p in (dest / "041").iterdir())
    assert len(names_in_041) == len(set(names_in_041))
    if mode == "symlink":
        assert all((dest / "041" / n).is_symlink() for n in names_in_041)


def test_export_planning_does_not_touch_disk_and_folder_templates(project, tmp_path):
    project.rescore("basic")
    folders = plan(project, "all")
    assert "041" in folders or any(k.startswith("041") for k in folders)
    assert not (tmp_path / "out").exists()
    assert folder_name("{number:03d} {name}", 7, "Jana/Nováková?") == "007 Jana_Nováková_"
    assert folder_name("{name}", 7, "   ") == "007"       # empty template result falls back to the number


def test_find_person_ranks_the_right_face_first(project):
    refs = [{"face": unit(FACE[42] + 0.02 * RNG.normal(size=128)), "face_arc": None, "score": 1.0}]
    hits = find_person(project, refs)
    ids = {p["id"]: p["name"] for p in project.photos()}
    top = [ids[h["photo_id"]] for h in hits[:5]]
    assert set(top) <= {f"IMG_{i:03d}.jpg" for i in (4, 5, 6, 7, 8)}
    assert hits[0]["sim"] > 0.7 and find_person(project, [{"face": None}]) == []


def test_calibration_profile_needs_two_examples_and_roundtrips(project):
    ph = project.photos()[0]["id"]
    ex = project.add_bib_example(ph, 41, [420.0, 600.0, 470.0, 650.0])
    assert project.bib_profile() is None                  # one example is not enough
    project.add_bib_example(project.photos()[1]["id"], 41, [430.0, 610.0, 480.0, 660.0])
    prof = project.bib_profile()
    assert prof is not None and prof.w > 0
    from edic.vision.bib import BibProfile
    assert BibProfile.from_dict(prof.to_dict()) == prof
    with pytest.raises(ValueError):
        project.add_bib_example(ph, None, [5.0, 5.0, 9.0, 9.0])      # nobody near that spot
    assert any(r["status"] == "accepted" for r in project.con.execute("SELECT status FROM assignments WHERE photo_id=?", (ph,)))
