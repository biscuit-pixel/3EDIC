"""A 3EDIČ project: one event's photos, competitors, signals and decisions."""
from __future__ import annotations

import functools
import json
import threading
import time
import uuid
from pathlib import Path

import cv2
import numpy as np

from . import db, scoring
from .exif import read_meta
from .types import Person
from .vision.bib import BibProfile
from .vision.regions import hip_centre

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}

DEFAULTS = {
    "auto_threshold": 0.8,      # at/above: assigned without asking
    "review_threshold": 0.25,   # between this and auto_threshold: shown for checking
    "assign_mode": "smart",     # smart | numbers_only | all | manual
    "folder_template": "{number:03d}",   # also {name}
    "export_mode": "copy",      # copy | hardlink | symlink
    "ocr_min_number": 10,
}


def locked(fn):
    """Serialise access to the project's single SQLite connection (API threads + background jobs)."""
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        with self.lock:
            return fn(self, *a, **kw)
    return wrapper


class Project:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.con = db.connect(self.root / "project.db")
        self._persons_cache = None

    # ------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, data_dir: Path, name: str, photos_dir: str | Path) -> "Project":
        pid = uuid.uuid4().hex[:10]
        root = Path(data_dir) / "projects" / pid
        root.mkdir(parents=True, exist_ok=True)
        p = cls(root)
        p.set_meta("id", pid)
        p.set_meta("name", name)
        p.set_meta("photos_dir", str(Path(photos_dir).resolve()))
        p.set_meta("created", time.time())
        return p

    @property
    def id(self):
        return self.get_meta("id")

    @locked
    def get_meta(self, key, default=None):
        r = self.con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    @locked
    def set_meta(self, key, value):
        self.con.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, json.dumps(value)))

    @locked
    def setting(self, key):
        return self.get_meta("setting:" + key, DEFAULTS[key])

    @locked
    def settings(self):
        return {k: self.setting(k) for k in DEFAULTS}

    @locked
    def set_setting(self, key, value):
        if key not in DEFAULTS:
            raise KeyError(key)
        self.set_meta("setting:" + key, value)

    @locked
    def summary(self):
        q = lambda s: self.con.execute(s).fetchone()[0]
        return {"id": self.id, "name": self.get_meta("name"), "photos_dir": self.get_meta("photos_dir"),
                "photos": q("SELECT COUNT(*) FROM photos"), "processed": q("SELECT COUNT(*) FROM photos WHERE processed=1"),
                "competitors": q("SELECT COUNT(*) FROM competitors"),
                "accepted": q("SELECT COUNT(*) FROM assignments WHERE status='accepted'"),
                "created": self.get_meta("created")}

    # --------------------------------------------------------------- ingest
    def ingest(self, progress=None, stop=lambda: False) -> int:
        """Scan the photo folder (recursively) and add new photos. -> number added.
        EXIF is read outside the lock and rows are committed in batches, so the UI stays responsive."""
        folder = Path(self.get_meta("photos_dir"))
        files = sorted(f for f in folder.rglob("*") if f.suffix.lower() in PHOTO_EXT and not f.name.startswith("."))
        with self.lock:
            known = {r["path"] for r in self.con.execute("SELECT path FROM photos")}
        added, batch = 0, []

        def flush():
            nonlocal batch
            with self.lock:
                self.con.execute("BEGIN")
                self.con.executemany("INSERT INTO photos(path,name,taken,width,height) VALUES(?,?,?,?,?)", batch)
                self.con.execute("COMMIT")
                self._persons_cache = None
            batch = []

        for i, f in enumerate(files):
            if stop():
                break
            if str(f) not in known:
                try:
                    meta = read_meta(f)
                except Exception:
                    meta = None                              # unreadable file: skip, never abort the import
                if meta:
                    taken = meta["taken"] if meta["taken"] is not None else f.stat().st_mtime
                    batch.append((str(f), f.name, taken, meta["width"], meta["height"]))
                    added += 1
            if len(batch) >= 100:
                flush()
            if progress and i % 25 == 0:
                progress(i + 1, len(files), f.name)
        if batch:
            flush()
        if progress:
            progress(len(files), len(files), "")
        return added

    @locked
    def photo_path(self, photo_id: int) -> str | None:
        r = self.con.execute("SELECT path FROM photos WHERE id=?", (photo_id,)).fetchone()
        return r["path"] if r else None

    @locked
    def photos(self):
        return [dict(r) for r in self.con.execute("SELECT * FROM photos ORDER BY taken, name")]

    # ----------------------------------------------------------- competitors
    @locked
    def add_competitor(self, number: int, name: str = "") -> int:
        self.con.execute("INSERT INTO competitors(number,name) VALUES(?,?) ON CONFLICT(number) DO UPDATE SET name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE name END",
                         (number, name))
        return self.con.execute("SELECT id FROM competitors WHERE number=?", (number,)).fetchone()["id"]

    @locked
    def set_roster(self, entries):
        """entries: iterable of numbers or (number, name) pairs; existing competitors are kept."""
        for e in entries:
            n, name = (e, "") if isinstance(e, int) else (int(e[0]), e[1])
            self.add_competitor(n, name)

    @locked
    def competitors(self):
        return [dict(r) for r in self.con.execute("SELECT * FROM competitors ORDER BY number")]

    @locked
    def suggest_roster(self, min_photos=2, min_conf=0.5):
        """Numbers the OCR/VLM read in several photos but that are not in the roster yet."""
        counts = {}
        for ps in self.load_persons().values():
            seen = set()
            for p in ps:
                for n, c in p.numbers.items():
                    if c >= min_conf and n >= int(self.setting("ocr_min_number")):
                        seen.add(n)
            for n in seen:
                counts[n] = counts.get(n, 0) + 1
        have = {c["number"] for c in self.competitors()}
        return sorted(({"number": n, "photos": k} for n, k in counts.items() if k >= min_photos and n not in have),
                      key=lambda d: -d["photos"])

    # ------------------------------------------------------------- persons
    @locked
    def save_persons(self, photo_id: int, people: list[Person]):
        self.con.execute("BEGIN")
        self.con.execute("DELETE FROM persons WHERE photo_id=?", (photo_id,))
        for idx, p in enumerate(people):
            self.con.execute(
                "INSERT INTO persons(photo_id,idx,box,kpts,numbers,bibs,colour,face,face_arc,face_score) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (photo_id, idx, db.arr(p.box), db.arr(p.kpts), db.jdump({str(k): v for k, v in p.numbers.items()}),
                 db.jdump([(n, c, list(r)) for n, c, r in p.bibs]), db.arr(p.colour), db.arr(p.face),
                 db.arr(p.face_arc), p.face_score))
        self.con.execute("UPDATE photos SET processed=1 WHERE id=?", (photo_id,))
        self.con.execute("COMMIT")
        self._persons_cache = None

    @locked
    def load_persons(self) -> dict[int, list[Person]]:
        if self._persons_cache is None:
            out = {}
            for r in self.con.execute("SELECT * FROM persons ORDER BY photo_id, idx"):
                out.setdefault(r["photo_id"], []).append(Person(
                    box=tuple(db.unarr(r["box"])), kpts=db.unarr(r["kpts"], (17, 3)),
                    numbers={int(k): v for k, v in json.loads(r["numbers"] or "{}").items()},
                    bibs=[(n, c, tuple(rb)) for n, c, rb in json.loads(r["bibs"] or "[]")],
                    colour=db.unarr(r["colour"]), face=db.unarr(r["face"]), face_arc=db.unarr(r["face_arc"]),
                    face_score=r["face_score"] or 0.0))
            self._persons_cache = out
        return self._persons_cache

    # --------------------------------------------------------- calibration
    @locked
    def add_bib_example(self, photo_id: int, number: int | None, box_xyxy) -> dict:
        """The user marked where the number is on a photo (box in full-res pixels).
        The person whose hips are nearest to the box becomes the example."""
        people = self.load_persons().get(photo_id) or []
        cx, cy = (box_xyxy[0] + box_xyxy[2]) / 2, (box_xyxy[1] + box_xyxy[3]) / 2
        best, best_d = None, 1e18
        for j, p in enumerate(people):
            hc = hip_centre(p)
            if hc is None:
                continue
            d = np.hypot(hc[0] - cx, hc[1] - cy) / max(p.height, 1)
            if d < best_d:
                best, best_d = j, d
        if best is None or best_d > 0.5:
            raise ValueError("no person near that spot")
        p, hc = people[best], hip_centre(people[best])
        unit = max(p.height, 1.0)
        rel = [float(v) for v in ((box_xyxy[0] - hc[0]) / unit, (box_xyxy[1] - hc[1]) / unit,
                                  (box_xyxy[2] - box_xyxy[0]) / unit, (box_xyxy[3] - box_xyxy[1]) / unit)]
        self.con.execute("INSERT INTO bib_examples(photo_id,person_idx,number,rel_box) VALUES(?,?,?,?)",
                         (photo_id, best, number, db.jdump(rel)))
        if number is not None:                       # the example also confirms who is on this photo
            from .review import Review
            Review(self).accept(photo_id, self.add_competitor(number), best)
        return {"person_idx": best, "rel_box": rel, "profile": (self.bib_profile() or BibProfile(0, 0, 0, 0)).to_dict()}

    @locked
    def bib_profile(self) -> BibProfile | None:
        rels = [json.loads(r["rel_box"]) for r in self.con.execute("SELECT rel_box FROM bib_examples")]
        return BibProfile.from_examples(rels) if len(rels) >= 2 else None

    @locked
    def clear_bib_examples(self):
        self.con.execute("DELETE FROM bib_examples")

    # ----------------------------------------------------------- extraction
    def extract(self, extractor, progress=None, stop=lambda: False, only_new=True) -> int:
        """Run the vision pipeline on unprocessed photos (resumable). -> photos processed."""
        profile = self.bib_profile()
        with self.lock:
            rows = self.con.execute("SELECT id,path FROM photos" + (" WHERE processed=0" if only_new else "") + " ORDER BY taken").fetchall()
        done = 0
        for i, r in enumerate(rows):
            if stop():
                break
            img = cv2.imread(r["path"], cv2.IMREAD_COLOR)
            if img is None:
                with self.lock:
                    self.con.execute("UPDATE photos SET processed=1 WHERE id=?", (r["id"],))
                continue
            self.save_persons(r["id"], extractor.extract(img, profile))
            done += 1
            if progress:
                progress(i + 1, len(rows), Path(r["path"]).name)
        return done

    def backfill_arc(self, faces, progress=None, stop=lambda: False) -> int:
        """After ArcFace was installed: add its embeddings to persons that were processed without it."""
        with self.lock:
            people = self.load_persons()
            paths = {r["id"]: r["path"] for r in self.con.execute("SELECT id,path FROM photos")}
        todo = [pid for pid, ps in people.items() if any(p.face is not None and p.face_arc is None for p in ps)]
        for i, pid in enumerate(todo):
            if stop():
                break
            img = cv2.imread(paths[pid], cv2.IMREAD_COLOR)
            if img is not None:
                for p in people[pid]:
                    if p.face is not None and p.face_arc is None:
                        faces.embed_person(img, p)
                self.save_persons(pid, people[pid])
            if progress:
                progress(i + 1, len(todo), Path(paths[pid]).name)
        return len(todo)

    def reread_bibs(self, bib_reader, progress=None, stop=lambda: False) -> int:
        """Re-run only the number OCR with the calibration profile, keeping poses/faces."""
        profile = self.bib_profile()
        people = self.load_persons()
        ids = sorted(people)
        with self.lock:
            paths = {r["id"]: r["path"] for r in self.con.execute("SELECT id,path FROM photos")}
        for i, pid in enumerate(ids):
            if stop():
                break
            img = cv2.imread(paths[pid], cv2.IMREAD_COLOR)
            if img is None:
                continue
            for p in people[pid]:
                p.numbers, p.bibs = bib_reader.read(img, p, profile)
            self.save_persons(pid, people[pid])
            if progress:
                progress(i + 1, len(ids), Path(paths[pid]).name)
        return len(ids)

    # -------------------------------------------------------------- scoring
    def _score_input(self, variant):
        photos = [p for p in self.photos() if p["processed"]]
        persons = self.load_persons()
        comps = self.competitors()
        pindex = {p["id"]: i for i, p in enumerate(photos)}
        cindex = {c["id"]: k for k, c in enumerate(comps)}
        seeds = []
        for r in self.con.execute("SELECT photo_id,competitor_id,person_idx FROM assignments WHERE status='accepted' AND source='user'"):
            if r["photo_id"] in pindex and r["competitor_id"] in cindex:
                seeds.append(scoring.Seed(pindex[r["photo_id"]], cindex[r["competitor_id"]], r["person_idx"]))
        extra = {}
        for r in self.con.execute("SELECT photo_id,person_idx,number,confidence FROM vlm_reads WHERE number IS NOT NULL"):
            if r["photo_id"] in pindex:
                extra.setdefault((pindex[r["photo_id"]], r["person_idx"]), {})[r["number"]] = max(r["confidence"] or 0, scoring.VLM_CONF)
        inp = scoring.ScoreInput(persons=[persons.get(p["id"], []) for p in photos],
                                 times=np.array([p["taken"] or 0.0 for p in photos]), roster=[c["number"] for c in comps],
                                 seeds=seeds, extra_numbers=extra, ocr_min_number=int(self.setting("ocr_min_number")))
        return inp, photos, comps

    @locked
    def rescore(self, variant: str = "basic", floor: float = 0.05) -> dict:
        """Recompute model suggestions. Accepted/rejected decisions are never touched."""
        inp, photos, comps = self._score_input(variant)
        if not photos or not comps:
            return {"pairs": 0}
        res = scoring.score(inp, variant)
        now = time.time()
        self.con.execute("BEGIN")
        self.con.execute("DELETE FROM assignments WHERE status='suggested'")
        n = 0
        for i, ph in enumerate(photos):
            for k, c in enumerate(comps):
                p = float(res.P[i, k])
                if p < floor and res.f_num[i, k] == 0:
                    continue
                bp = int(res.best_person[i, k])
                face_key = scoring.VARIANTS[variant][0]
                ev = {"num": round(float(res.f_num[i, k]), 2), "face": round(float(res.feats[face_key][0][i, k]), 2),
                      "colour": round(float(res.feats["colour"][0][i, k]), 2),
                      "seq": bool(res.P[i, k] - res.P1[i, k] > 0.1)}
                self.con.execute(
                    "INSERT INTO assignments(photo_id,competitor_id,p,num_conf,person_idx,status,source,updated,evidence) VALUES(?,?,?,?,?,'suggested','model',?,?) "
                    "ON CONFLICT(photo_id,competitor_id) DO UPDATE SET p=excluded.p,num_conf=excluded.num_conf,"
                    "person_idx=COALESCE(assignments.person_idx,excluded.person_idx),updated=excluded.updated,evidence=excluded.evidence",
                    (ph["id"], c["id"], p, float(res.f_num[i, k]), bp if bp >= 0 else None, now, db.jdump(ev)))
                n += 1
        self.con.execute("COMMIT")
        self.set_meta("last_scored", {"at": now, "variant": variant, "pairs": n})
        return {"pairs": n, "variant": variant}
