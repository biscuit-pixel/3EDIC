"""Albums (variants, Others), person tagging, queue, context, bulk moves, workflow stages, sampling, migration."""
import os
import sqlite3
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
from test_engine import comp_id, person, project  # noqa: F401,E402  (fixtures)

from edic import scoring  # noqa: E402
from edic.export import export, folder_name, plan  # noqa: E402
from edic.project import Project  # noqa: E402
from edic.review import Review  # noqa: E402


def pid(pr, i):
    return pr.photos()[i]["id"]


# ------------------------------------------------------------------ albums
def test_labels_variants_and_others_are_hidden_by_default(project):
    a = project.add_competitor(56, "Jana")
    b = project.add_competitor(56, "Jana", variant=2)
    assert a != b and project.label(56, 2) == "056_2" and project.label(7) == "007"
    project.others_album()
    assert {c["label"] for c in project.competitors()} >= {"041", "042", "056", "056_2"}
    assert "Others" not in {c["label"] for c in project.competitors()}
    assert "Others" in {c["label"] for c in project.competitors(include_others=True)}
    assert project.summary()["competitors"] == len(project.competitors())


def test_renumber_onto_existing_album_makes_a_variant_or_merges(project):
    rv = Review(project)
    c41, c42 = comp_id(project, 41), comp_id(project, 42)
    rv.accept(pid(project, 1), c41)
    rv.accept(pid(project, 5), c42)
    r = project.update_album(c42, number=41)                          # same number, different person: becomes 41_2
    assert {c["label"] for c in project.competitors()} >= {"041", "041_2"}
    c41_2 = next(c["id"] for c in project.competitors() if c["label"] == "041_2")
    assert r["id"] == c42 == c41_2
    project.merge_albums(c41_2, c41)                                   # ...or merge them
    assert "041_2" not in {c["label"] for c in project.competitors()}
    got = {r["photo_id"] for r in project.con.execute("SELECT photo_id FROM assignments WHERE competitor_id=? AND status='accepted'", (c41,))}
    assert got == {pid(project, 1), pid(project, 5)}
    other = project.add_competitor(90)                                 # renumbering onto another album with "merge" folds into it
    rv.accept(pid(project, 6), other)
    out = project.update_album(c41, number=90, merge=True)
    assert out["merged_into"] == other and "041" not in {c["label"] for c in project.competitors()}
    assert {r["photo_id"] for r in project.con.execute("SELECT photo_id FROM assignments WHERE competitor_id=? AND status='accepted'", (other,))} == {pid(project, i) for i in (1, 5, 6)}


def test_merge_keeps_the_confirmed_pair_on_a_clash(project):
    rv = Review(project)
    c41, c42 = comp_id(project, 41), comp_id(project, 42)
    ph = pid(project, 1)
    project.con.execute("INSERT INTO assignments(photo_id,competitor_id,p,status) VALUES(?,?,0.9,'suggested')", (ph, c41))
    rv.accept(ph, c42)
    project.merge_albums(c42, c41)
    row = project.con.execute("SELECT status FROM assignments WHERE photo_id=? AND competitor_id=?", (ph, c41)).fetchone()
    assert row["status"] == "accepted"


def test_delete_empty_albums_only_removes_empty_ones(project):
    rv = Review(project)
    empty = project.add_competitor(99)
    rv.accept(pid(project, 1), comp_id(project, 41))
    n = project.delete_empty_albums()
    labels = {c["label"] for c in project.competitors()}
    assert n >= 1 and "099" not in labels and "041" in labels and empty not in {c["id"] for c in project.competitors()}


def test_old_databases_are_migrated(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    con = sqlite3.connect(root / "project.db")
    con.executescript("""
        CREATE TABLE competitors (id INTEGER PRIMARY KEY, number INTEGER UNIQUE NOT NULL, name TEXT NOT NULL DEFAULT '');
        CREATE TABLE photos (id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT NOT NULL, taken REAL, width INTEGER, height INTEGER, processed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE assignments (photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE, competitor_id INTEGER NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
            p REAL NOT NULL DEFAULT 0, num_conf REAL NOT NULL DEFAULT 0, person_idx INTEGER, status TEXT NOT NULL DEFAULT 'suggested', source TEXT NOT NULL DEFAULT 'model', updated REAL,
            PRIMARY KEY (photo_id, competitor_id));
        INSERT INTO competitors VALUES (1, 41, 'Old Name');
        INSERT INTO photos(id,path,name,taken) VALUES (1,'/x/a.jpg','a.jpg',1);
        INSERT INTO assignments(photo_id,competitor_id,p,status) VALUES (1,1,0.9,'accepted');
    """)
    con.commit(); con.close()
    pr = Project(root)
    (c,) = pr.competitors()
    assert (c["number"], c["variant"], c["name"], c["kind"], c["label"]) == (41, 1, "Old Name", "competitor", "041")
    assert pr.con.execute("SELECT COUNT(*) FROM assignments").fetchone()[0] == 1         # data and foreign keys survived
    assert pr.add_competitor(41, variant=2) != c["id"]                                    # variants now possible


# ------------------------------------------------------------- tagging
def test_a_group_photo_is_tagged_person_by_person_and_undone_as_one_action(project):
    rv = Review(project)
    group = pid(project, 8)                                    # two people on this photo (41 and 42)
    c41, c42 = comp_id(project, 41), comp_id(project, 42)
    rv.set_person(group, 0, c41)
    rv.set_person(group, 1, c42)                               # second person tagged without leaving the photo
    pairs = {(r["competitor_id"], r["person_idx"]) for r in project.con.execute("SELECT * FROM assignments WHERE photo_id=? AND status='accepted'", (group,))}
    assert pairs == {(c41, 0), (c42, 1)}
    rv.set_person(group, 0, c42)                               # change of mind: person 0 is 42 (and person 1 too, that is fine)
    st = {r["competitor_id"]: r["status"] for r in project.con.execute("SELECT * FROM assignments WHERE photo_id=?", (group,))}
    assert st[c41] == "rejected" and st[c42] == "accepted"
    rv.undo()                                                  # one undo reverts the whole re-tag
    st = {r["competitor_id"]: r["status"] for r in project.con.execute("SELECT * FROM assignments WHERE photo_id=?", (group,))}
    assert st[c41] == "accepted" and st[c42] == "accepted"


def test_new_albums_are_created_when_tagging_an_unseen_number(project):
    rv = Review(project)
    new = project.add_competitor(77)
    rv.set_person(pid(project, 9), 0, new) if project.load_persons().get(pid(project, 9)) else rv.accept(pid(project, 9), new)
    assert "077" in {c["label"] for c in project.competitors()}


def test_queue_holds_unknown_and_below_threshold_photos_in_shooting_order(project):
    project.set_setting("auto_threshold", 0.9999)             # nothing is "sure": everything the model likes goes to 'check'
    project.rescore("basic")
    rv = Review(project)
    q = rv.queue("both")
    ids = [x["id"] for x in q["items"]]
    assert ids == sorted(ids, key=lambda i: next(p["taken"] for p in project.photos() if p["id"] == i))
    assert q["counts"]["unknown"] + q["counts"]["check"] == len(ids) > 0
    done = ids[0]
    rv.nobody(done)
    assert done not in [x["id"] for x in rv.queue("both")["items"]]
    assert set(x["reason"] for x in rv.queue("unknown")["items"]) <= {"unknown"}
    assert rv.stats()["to_tag"] == len(rv.queue("both")["items"])


def test_context_shows_neighbours_and_what_they_are_tagged_as(project):
    rv = Review(project)
    rv.accept(pid(project, 3), comp_id(project, 41))
    ctx = rv.context(pid(project, 4), before=2, after=2)["items"]
    assert [c["id"] for c in ctx] == [pid(project, i) for i in (2, 3, 4, 5, 6)]
    assert [c["current"] for c in ctx] == [False, False, True, False, False]
    assert ctx[1]["state"] == "tagged" and ctx[1]["tags"][0]["label"] == "041"
    assert len(rv.context(pid(project, 0), before=4, after=1)["items"]) == 2          # edges are not padded
    assert rv.context(10**6)["items"] == []


def test_photo_detail_tells_the_ui_where_to_put_the_number_label(project):
    rv = Review(project)
    group = pid(project, 8)
    rv.set_person(group, 0, comp_id(project, 41))
    d = rv.photo_detail(group)
    p0 = d["persons"][0]
    assert p0["anchor"] is not None and [c["label"] for c in p0["assigned"]] == ["041"]
    project.add_bib_example(pid(project, 0), 41, [420.0, 600.0, 470.0, 650.0])
    project.add_bib_example(pid(project, 1), 41, [430.0, 610.0, 480.0, 660.0])
    assert rv.photo_detail(group)["persons"][0]["anchor"] != p0["anchor"]            # learned offset moves the label to the tag


def test_bulk_tag_bulk_unknown_and_moving_photos_out_of_an_album(project):
    rv = Review(project)
    c41, c42 = comp_id(project, 41), comp_id(project, 42)
    items = [{"photo_id": pid(project, i)} for i in (1, 2, 3)]
    rv.bulk_tag(items, c41)
    assert {r["photo_id"] for r in project.con.execute("SELECT photo_id FROM assignments WHERE competitor_id=? AND status='accepted'", (c41,))} == {x["photo_id"] for x in items}
    rv.bulk_to_unknown(items[:1], c41)                                             # not 41 after all: back to manual tagging
    assert items[0]["photo_id"] in [x["id"] for x in rv.queue("unknown")["items"]]
    rv.move_photos(items[1:2], c41, {"type": "others"})
    rv.move_photos(items[2:3], c41, {"type": "album", "number": 88})                # unseen number: the album is created
    st = lambda ph, c: (project.con.execute("SELECT status FROM assignments WHERE photo_id=? AND competitor_id=?", (ph, c)).fetchone() or {"status": None})["status"]
    others = project.others_album()
    c88 = next(c["id"] for c in project.competitors() if c["number"] == 88)
    assert st(items[1]["photo_id"], c41) == "rejected" and st(items[1]["photo_id"], others) == "accepted"
    assert st(items[2]["photo_id"], c41) == "rejected" and st(items[2]["photo_id"], c88) == "accepted"
    rv.move_photos(items[2:3], c88, {"type": "album", "number": 88})               # moving to where it already is changes nothing
    assert st(items[2]["photo_id"], c88) == "accepted"


def test_split_off_a_second_competitor_with_the_same_number(project):
    rv = Review(project)
    c41 = comp_id(project, 41)
    rv.bulk_tag([{"photo_id": pid(project, i)} for i in (1, 2, 3)], c41)
    new = rv.split_variant(c41, [pid(project, 2), pid(project, 3)])
    labels = {c["id"]: c["label"] for c in project.competitors()}
    assert labels[new] == "041_2"
    in_new = {r["photo_id"] for r in project.con.execute("SELECT photo_id FROM assignments WHERE competitor_id=? AND status='accepted'", (new,))}
    in_old = {r["photo_id"] for r in project.con.execute("SELECT photo_id FROM assignments WHERE competitor_id=? AND status='accepted'", (c41,))}
    assert in_new == {pid(project, 2), pid(project, 3)} and in_old == {pid(project, 1)}


def test_others_and_variants_become_export_folders(project, tmp_path):
    rv = Review(project)
    rv.nobody(pid(project, 9))
    c41 = comp_id(project, 41)
    rv.bulk_tag([{"photo_id": pid(project, 1)}, {"photo_id": pid(project, 2)}], c41)
    rv.split_variant(c41, [pid(project, 2)])
    folders = plan(project, "manual")
    assert set(folders) >= {"Others", "041", "041_2"}
    res = export(project, tmp_path / "out", mode="copy", assign_mode="manual")
    assert (tmp_path / "out" / "Others").is_dir() and (tmp_path / "out" / "041_2").is_dir() and res["files"] >= 3
    assert folder_name("{number:03d} {name}", 56, "Jana", 2) == "056 Jana_2" and folder_name("x", 0, "", 1, "others") == "Others"


# ------------------------------------------------------------ workflow
def test_workflow_stages_follow_import_examples_analyse_ai_review(tmp_path):
    photos = tmp_path / "ph"
    photos.mkdir()
    pr = Project.create(tmp_path / "d", "W", photos)
    assert pr.workflow()["stage"] == "import"
    for i in range(3):
        Image.new("RGB", (60, 90), (i, 0, 0)).save(photos / f"{i}.jpg")
    pr.ingest()
    assert pr.workflow()["stage"] == "examples"
    pr.set_meta("examples_skipped", True)
    assert pr.workflow()["stage"] == "analyse"
    pr.con.execute("UPDATE photos SET processed=1")
    assert pr.workflow()["stage"] == "ai"
    pr.set_meta("ai_skipped", True)
    assert pr.workflow()["stage"] == "review"


def test_samples_are_spread_over_the_event_and_do_not_count_as_analysed(tmp_path):
    photos = tmp_path / "ph"
    photos.mkdir()
    for i in range(40):
        f = photos / f"{i:02d}.jpg"
        Image.new("RGB", (60, 90)).save(f)
        os.utime(f, (1_700_000_000 + i,) * 2)
    pr = Project.create(tmp_path / "d", "S", photos)
    pr.ingest()
    ids = pr.sample_photo_ids(8)
    allids = [p["id"] for p in pr.photos()]
    assert len(ids) == 8 and ids == sorted(ids) and allids.index(ids[0]) < 5 and allids.index(ids[-1]) > 34

    class FakePose:
        def detect(self, img):
            return [person(41)]
    assert pr.prepare_samples(FakePose(), 8) == 8
    w = pr.workflow()
    assert w["processed"] == 0 and w["sampled"] == 8 and len(pr.load_persons()) == 8
    assert pr.prepare_samples(FakePose(), 8) == 0                         # already done: nothing is repeated

    class FakeExtractor:
        def extract(self, img, profile=None):
            return [person(42)]
    pr.extract(FakeExtractor())
    assert pr.workflow()["processed"] == 40                              # the real analysis covers the samples too


def test_auto_roster_makes_albums_for_numbers_that_were_read_twice(project):
    for i in (0, 1):                                                     # the number 55 is read on two photos, 66 on one
        ps = project.load_persons()[pid(project, i)]
        ps[0].numbers = {55: 0.8, **({66: 0.8} if i == 0 else {})}
        project.save_persons(pid(project, i), ps)
    assert project.auto_roster() == [55]
    assert "055" in {c["label"] for c in project.competitors()} and "066" not in {c["label"] for c in project.competitors()}
    project.set_setting("roster_mode", "list")
    ps = project.load_persons()[pid(project, 2)]
    ps[0].numbers = {77: 0.8}
    project.save_persons(pid(project, 2), ps)
    ps = project.load_persons()[pid(project, 3)]
    ps[0].numbers = {77: 0.8}
    project.save_persons(pid(project, 3), ps)
    assert project.auto_roster() == []                                    # a typed start list is not extended behind your back


def test_variant_albums_get_no_anchors_from_read_numbers():
    p = person(41)
    p.numbers = {41: 0.9}
    inp = scoring.ScoreInput(persons=[[p]], times=np.array([0.0]), roster=[41, 41], variants=[1, 2], ocr_min_number=10)
    f_num, *_ = scoring.features(inp, scoring.VARIANTS["basic"], "sface")
    assert f_num[0, 0] > 0 and f_num[0, 1] == 0                           # the read number anchors album 41, never 41_2


def test_bulk_tag_of_a_single_subject_drops_the_models_other_guesses(project):
    rv = Review(project)
    ph = pid(project, 1)
    c41, c42 = comp_id(project, 41), comp_id(project, 42)
    project.con.execute("INSERT INTO assignments(photo_id,competitor_id,person_idx,p,status) VALUES(?,?,0,0.4,'suggested')", (ph, c42))
    rv.bulk_tag([{"photo_id": ph}], c41)
    rows = {r["competitor_id"]: r["status"] for r in project.con.execute("SELECT competitor_id,status FROM assignments WHERE photo_id=?", (ph,))}
    assert rows[c41] == "accepted" and rows.get(c42) == "rejected"


def test_group_photo_modes_in_the_export_plan(project):
    rv = Review(project)
    c41, c42 = comp_id(project, 41), comp_id(project, 42)
    ph = pid(project, 8)                                       # the group photo: person 0 is 41, person 1 is 42
    people = project.load_persons()[ph]
    people[1].box = (people[1].box[0], people[1].box[1] + 400, people[1].box[2], people[1].box[3])        # 42 is smaller (further away)
    project.save_persons(ph, people)
    rv.set_person(ph, 0, c41)
    rv.set_person(ph, 1, c42)
    names = lambda mode: {k: [p["name"] for p, _ in v] for k, v in plan(project, "manual", group_mode=mode).items()}
    gname = project.photos()[8]["name"]
    every = names("all")
    assert gname in every["041"] and gname in every["042"] and "_groups" not in every
    main = names("main")
    assert gname in main["041"] and gname not in main.get("042", [])
    sep = names("separate")
    assert gname not in sep.get("041", []) and gname not in sep.get("042", []) and sep["_groups"] == [gname]
    both = names("both")
    assert gname in both["041"] and gname in both["042"] and both["_groups"] == [gname]
    project.set_setting("group_mode", "separate")                # the project's own setting is the default
    assert "_groups" in plan(project, "manual")
