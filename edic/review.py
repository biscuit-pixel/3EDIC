"""Decisions and the review queue: what the user still has to look at, and how answers are stored."""
from __future__ import annotations

import functools
import json
import time


def _locked(fn):
    """Lock the project, and make everything one public call changes a single undoable action."""
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        with self.pr.lock:
            if self._depth == 0:
                self._grp = time.time_ns()
            self._depth += 1
            try:
                return fn(self, *a, **kw)
            finally:
                self._depth -= 1
    return wrapper


class Review:
    def __init__(self, project):
        self.pr = project
        self.con = project.con
        self._depth, self._grp = 0, None

    # ------------------------------------------------------------ decisions
    def _log(self, photo_id, comp_id, action):
        r = self.con.execute("SELECT * FROM assignments WHERE photo_id=? AND competitor_id=?", (photo_id, comp_id)).fetchone()
        self.con.execute(
            "INSERT INTO decision_log(at,photo_id,competitor_id,action,existed,prev_status,prev_source,prev_person,prev_p,prev_num_conf,grp) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), photo_id, comp_id, action, 1 if r else 0, r and r["status"], r and r["source"],
             r and r["person_idx"], r and r["p"], r and r["num_conf"], self._grp))

    def _set(self, photo_id, comp_id, status, person_idx, action):
        self._log(photo_id, comp_id, action)
        self.con.execute(
            "INSERT INTO assignments(photo_id,competitor_id,status,source,person_idx,updated) VALUES(?,?,?,'user',?,?) "
            "ON CONFLICT(photo_id,competitor_id) DO UPDATE SET status=excluded.status,source='user',"
            "person_idx=COALESCE(excluded.person_idx,assignments.person_idx),updated=excluded.updated",
            (photo_id, comp_id, status, person_idx, time.time()))

    @_locked
    def accept(self, photo_id, comp_id, person_idx=None):
        self._set(photo_id, comp_id, "accepted", person_idx, "accept")

    @_locked
    def reject(self, photo_id, comp_id):
        self._set(photo_id, comp_id, "rejected", None, "reject")

    @_locked
    def accept_many(self, pairs):
        self.con.execute("BEGIN")
        for photo_id, comp_id in pairs:
            self.accept(photo_id, comp_id)
        self.con.execute("COMMIT")

    @_locked
    def resolve_batch(self, competitor_id, accepted_photo_ids, rejected_photo_ids):
        """Grid review: everything ticked off as wrong is rejected, the rest shown is accepted."""
        self.con.execute("BEGIN")
        for pid in accepted_photo_ids:
            self.accept(pid, competitor_id)
        for pid in rejected_photo_ids:
            self.reject(pid, competitor_id)
        self.con.execute("COMMIT")

    @_locked
    def set_photo(self, photo_id, competitor_ids, person_map=None):
        """Manual tagging: exactly these competitors are on the photo (persons optional)."""
        person_map = person_map or {}
        self.con.execute("BEGIN")
        for cid in competitor_ids:
            self.accept(photo_id, cid, person_map.get(cid))
        for r in self.con.execute("SELECT competitor_id FROM assignments WHERE photo_id=? AND status<>'rejected'", (photo_id,)).fetchall():
            if r["competitor_id"] not in competitor_ids:
                self.reject(photo_id, r["competitor_id"])
        self.con.execute("COMMIT")

    @_locked
    def nobody(self, photo_id):
        """The photo shows no competitor (organiser, audience, empty stage): it goes to the Others album."""
        self.set_photo(photo_id, [self.pr.others_album()])

    @_locked
    def set_person(self, photo_id, person_idx, comp_id):
        """This person on this photo is that competitor. Whatever the model suggested for the same person is dropped;
        other people on the photo are left alone, so a group shot can be tagged one person after another."""
        for r in self.con.execute("SELECT competitor_id FROM assignments WHERE photo_id=? AND person_idx=? AND competitor_id<>? AND status IN ('suggested','accepted')",
                                  (photo_id, person_idx, comp_id)).fetchall():
            self._set(photo_id, r["competitor_id"], "rejected", None, "reject")
        self.accept(photo_id, comp_id, person_idx)

    @_locked
    def reject_person(self, photo_id, person_idx):
        """'Not who the model thinks': drop its suggestions for this person (the photo may still need tagging)."""
        for r in self.con.execute("SELECT competitor_id FROM assignments WHERE photo_id=? AND person_idx=? AND status='suggested'", (photo_id, person_idx)).fetchall():
            self._set(photo_id, r["competitor_id"], "rejected", None, "reject")

    def _sole_person(self, photo_id):
        """The one person who is clearly the subject of this photo (others are far away or cut off), if there is exactly one."""
        ph = self.con.execute("SELECT height FROM photos WHERE id=?", (photo_id,)).fetchone()
        people = self.pr.load_persons().get(photo_id, [])
        big = [i for i, p in enumerate(people) if ph and ph["height"] and p.height >= 0.12 * ph["height"]]
        return big[0] if len(big) == 1 else None

    @_locked
    def bulk_tag(self, items, comp_id):
        """Tag many thumbnails at once. items: [{photo_id, person_idx?}]. Without a person, a photo with a single clear
        subject tags that person (so the model's other guesses for them are dropped); a group photo is tagged as a whole."""
        for it in items:
            idx = it.get("person_idx")
            if idx is None:
                idx = self._sole_person(it["photo_id"])
            if idx is not None:
                self.set_person(it["photo_id"], idx, comp_id)
            else:
                self.accept(it["photo_id"], comp_id)

    @_locked
    def bulk_to_unknown(self, items, comp_id):
        """'These are not that competitor': rejected pairs, so the photos reappear for manual tagging."""
        for it in items:
            self.reject(it["photo_id"], comp_id)

    @_locked
    def move_photos(self, items, from_comp, destination):
        """Take photos out of an album and say where they go. destination: {type: unknown|others|album, number?}"""
        kind = destination.get("type")
        target = self.pr.others_album() if kind == "others" else self.pr.add_competitor(int(destination["number"])) if kind == "album" else None
        for it in items:
            row = self.con.execute("SELECT person_idx FROM assignments WHERE photo_id=? AND competitor_id=?", (it["photo_id"], from_comp)).fetchone()
            person = it.get("person_idx", row["person_idx"] if row else None)
            if target == from_comp:
                continue
            self.reject(it["photo_id"], from_comp)
            if target is not None:
                self.accept(it["photo_id"], target, person)

    @_locked
    def split_variant(self, comp_id, photo_ids):
        """Some photos in this album are a different competitor with the same number (another category): move them
        to a new album 56_2, 56_3, ... -> its id."""
        row = self.con.execute("SELECT number,name FROM competitors WHERE id=? AND kind='competitor'", (comp_id,)).fetchone()
        if not row:
            raise KeyError(comp_id)
        new = self.pr.add_competitor(row["number"], row["name"], self.pr.next_variant(row["number"]))
        for pid in photo_ids:
            old = self.con.execute("SELECT person_idx FROM assignments WHERE photo_id=? AND competitor_id=?", (pid, comp_id)).fetchone()
            self._set(pid, comp_id, "rejected", None, "reject")
            self.accept(pid, new, old["person_idx"] if old else None)
        return new

    @_locked
    def undo(self):
        """Revert the most recent action (everything it changed). -> the (photo_id, competitor_id) of its first change, or None."""
        last = self.con.execute("SELECT * FROM decision_log WHERE undone=0 ORDER BY id DESC LIMIT 1").fetchone()
        if not last:
            return None
        rows = (self.con.execute("SELECT * FROM decision_log WHERE undone=0 AND grp=? ORDER BY id DESC", (last["grp"],)).fetchall()
                if last["grp"] is not None else [last])
        self.con.execute("BEGIN")
        for r in rows:
            if r["existed"]:
                self.con.execute("UPDATE assignments SET status=?,source=?,person_idx=?,p=?,num_conf=? WHERE photo_id=? AND competitor_id=?",
                                 (r["prev_status"], r["prev_source"], r["prev_person"], r["prev_p"], r["prev_num_conf"], r["photo_id"], r["competitor_id"]))
            else:
                self.con.execute("DELETE FROM assignments WHERE photo_id=? AND competitor_id=?", (r["photo_id"], r["competitor_id"]))
            self.con.execute("UPDATE decision_log SET undone=1 WHERE id=?", (r["id"],))
        self.con.execute("COMMIT")
        return rows[-1]["photo_id"], rows[-1]["competitor_id"]

    # ---------------------------------------------------------------- views
    def _thr(self):
        return float(self.pr.setting("auto_threshold")), float(self.pr.setting("review_threshold"))

    @_locked
    def effective(self, mode: str | None = None):
        """(photo_id, competitor_id, how) pairs that would be exported under the assignment mode.
        how: 'user' (confirmed), 'auto' (model)."""
        mode = mode or self.pr.setting("assign_mode")
        auto, low = self._thr()
        out = []
        for r in self.con.execute("SELECT * FROM assignments WHERE status<>'rejected'"):
            if r["status"] == "accepted":
                out.append((r["photo_id"], r["competitor_id"], "user"))
            elif mode == "smart" and r["p"] >= auto:
                out.append((r["photo_id"], r["competitor_id"], "auto"))
            elif mode == "numbers_only" and r["num_conf"] > 0 and r["p"] >= low:
                out.append((r["photo_id"], r["competitor_id"], "auto"))
            elif mode == "all" and r["p"] >= low:
                out.append((r["photo_id"], r["competitor_id"], "auto"))
        return out

    @_locked
    def stats(self):
        auto, low = self._thr()
        q = lambda s, *a: self.con.execute(s, a).fetchone()[0]
        total = q("SELECT COUNT(*) FROM photos WHERE processed=1")
        resolved = q("SELECT COUNT(DISTINCT photo_id) FROM assignments WHERE status='accepted' OR (status='suggested' AND p>=?)", auto)
        nobody = q("SELECT COUNT(*) FROM meta WHERE key LIKE 'nobody:%'")
        accepted = q("SELECT COUNT(*) FROM assignments WHERE status='accepted'")
        check = q("SELECT COUNT(*) FROM assignments WHERE status='suggested' AND p>=? AND p<?", low, auto)
        unknown = len(self.unknown(limit=10**9, count_only=True))
        to_tag = len(self.queue("both", limit=10**9)["items"])
        return {"to_tag": to_tag, "photos": total, "resolved_photos": min(total, resolved + nobody), "accepted_pairs": accepted,
                "auto_pairs": q("SELECT COUNT(*) FROM assignments WHERE status='suggested' AND p>=?", auto),
                "to_check_pairs": check, "unknown_photos": unknown,
                "progress": (min(total, resolved + nobody) / total) if total else 0.0}

    def _anchor(self, person):
        """Where the number sits on this person: where a label should be drawn so the eye does not have to travel."""
        from .vision.regions import hip_centre
        hc = hip_centre(person)
        if hc is None:
            return None
        prof = self.pr.bib_profile()
        u = max(person.height, 1.0)
        return [float(hc[0] + (prof.dx * u if prof else 0)), float(hc[1] + (prof.dy * u if prof else 0))]

    def _enrich(self, row):
        """Item for the UI: pair + the person's box (so a thumbnail can show just them) + evidence."""
        d = dict(row)
        people = self.pr.load_persons().get(d["photo_id"], [])
        j = d.get("person_idx")
        ok = j is not None and 0 <= j < len(people)
        d["box"] = [float(v) for v in people[j].box] if ok else None
        d["anchor"] = self._anchor(people[j]) if ok else None
        ph = self.con.execute("SELECT width,height FROM photos WHERE id=?", (d["photo_id"],)).fetchone()
        d["width"], d["height"] = (ph["width"], ph["height"]) if ph else (None, None)
        ev = d.get("evidence")
        d["evidence"] = json.loads(ev) if ev else None
        return d

    def _comp_map(self):
        return {c["id"]: c for c in self.pr.competitors(include_others=True)}

    @_locked
    def batches(self, limit=10, per_batch=60):
        """Grid review: for each competitor the photos that probably (but not certainly) show them."""
        auto, low = self._thr()
        rows = self.con.execute(
            "SELECT photo_id,competitor_id,p,num_conf,person_idx,evidence FROM assignments "
            "WHERE status='suggested' AND p>=? AND p<? ORDER BY competitor_id, p DESC", (low, auto)).fetchall()
        by = {}
        for r in rows:
            by.setdefault(r["competitor_id"], []).append(r)
        comps = self._comp_map()
        out = [{"competitor": comps[cid], "items": items[:per_batch], "total": len(items)} for cid, items in by.items()]
        out.sort(key=lambda b: -sum(i["p"] for i in b["items"]))
        out = out[:limit]
        for b in out:
            b["items"] = [self._enrich(r) for r in b["items"]]
        return out

    @_locked
    def cards(self, limit=20):
        """One suggestion at a time, most likely first."""
        auto, low = self._thr()
        rows = self.con.execute(
            "SELECT photo_id,competitor_id,p,num_conf,person_idx,evidence FROM assignments "
            "WHERE status='suggested' AND p>=? AND p<? ORDER BY p DESC LIMIT ?", (low, auto, limit)).fetchall()
        comps = self._comp_map()
        return [{**self._enrich(r), "competitor": comps[r["competitor_id"]]} for r in rows]

    @_locked
    def unknown(self, limit=50, count_only=False):
        """Processed photos nobody could place: no accepted pair and no suggestion worth checking."""
        auto, low = self._thr()
        rows = self.con.execute(
            "SELECT id FROM photos WHERE processed=1 AND id NOT IN (SELECT photo_id FROM assignments WHERE status='accepted' OR (status='suggested' AND p>=?)) "
            "AND ('nobody:'||id) NOT IN (SELECT key FROM meta) ORDER BY taken LIMIT ?", (low, limit)).fetchall()
        return [r["id"] for r in rows] if count_only else [self.photo_brief(r["id"]) for r in rows]

    @_locked
    def tags(self, limit=20):
        """Group shots: several plausible competitors, at least one still to check."""
        auto, low = self._thr()
        rows = self.con.execute(
            "SELECT photo_id, COUNT(*) AS n, SUM(CASE WHEN status='suggested' AND p<? THEN 1 ELSE 0 END) AS pending "
            "FROM assignments WHERE status='accepted' OR (status='suggested' AND p>=?) GROUP BY photo_id "
            "HAVING n>=2 AND pending>=1 ORDER BY pending DESC, n DESC LIMIT ?", (auto, low, limit)).fetchall()
        return [self.photo_detail(r["photo_id"]) for r in rows]

    @_locked
    def queue(self, kind: str = "both", limit: int = 500):
        """Photos that need a person: those nobody could place ('unknown') and those with only a below-threshold
        suggestion ('check'), in shooting order. kind: both | unknown | check"""
        auto, low = self._thr()
        reasons = {}
        if kind in ("both", "unknown"):
            for pid in self.unknown(limit=10**9, count_only=True):
                reasons[pid] = "unknown"
        if kind in ("both", "check"):
            for r in self.con.execute("SELECT DISTINCT photo_id FROM assignments WHERE status='suggested' AND p>=? AND p<?", (low, auto)):
                reasons.setdefault(r["photo_id"], "check")
        order = [r["id"] for r in self.con.execute("SELECT id FROM photos ORDER BY taken, name") if r["id"] in reasons]
        counts = {"unknown": sum(1 for v in reasons.values() if v == "unknown"), "check": sum(1 for v in reasons.values() if v == "check")}
        return {"items": [{"id": i, "reason": reasons[i]} for i in order[:limit]], "counts": counts}

    @_locked
    def context(self, photo_id: int, before: int = 4, after: int = 4):
        """The photos shot just before and after this one, with what they are tagged as: a competitor seen from
        behind is often obvious one frame earlier."""
        auto, low = self._thr()
        ids = [r["id"] for r in self.con.execute("SELECT id FROM photos ORDER BY taken, name")]
        if photo_id not in ids:
            return {"items": []}
        i = ids.index(photo_id)
        window = ids[max(0, i - before): i + after + 1]
        comps = self._comp_map()
        out = []
        for pid in window:
            tags = []
            state = "unknown"
            for r in self.con.execute("SELECT competitor_id,status,p FROM assignments WHERE photo_id=? AND status<>'rejected' ORDER BY (status='accepted') DESC, p DESC", (pid,)):
                c = comps.get(r["competitor_id"])
                if not c:
                    continue
                if r["status"] == "accepted":
                    tags.append({"label": c["label"], "status": "accepted"})
                    state = "tagged"
                elif r["p"] >= auto:
                    tags.append({"label": c["label"], "status": "auto"})
                    state = "tagged" if state == "unknown" else state
                elif r["p"] >= low:
                    tags.append({"label": c["label"], "status": "check"})
                    state = "check" if state == "unknown" else state
            out.append({"id": pid, "current": pid == photo_id, "state": state, "tags": tags[:4]})
        return {"items": out}

    @_locked
    def photo_brief(self, photo_id):
        r = self.con.execute("SELECT id,name,width,height,taken FROM photos WHERE id=?", (photo_id,)).fetchone()
        return dict(r) if r else None

    @_locked
    def photo_detail(self, photo_id):
        """Photo + its persons (boxes) + every candidate pair, for the tagging view."""
        brief = self.photo_brief(photo_id)
        people = self.pr.load_persons().get(photo_id, [])
        comps = self._comp_map()
        cands = [dict(r) for r in self.con.execute(
            "SELECT competitor_id,p,num_conf,person_idx,status,source,evidence FROM assignments WHERE photo_id=? ORDER BY p DESC", (photo_id,))]
        for c in cands:
            c["competitor"] = comps.get(c["competitor_id"])
            c["evidence"] = json.loads(c["evidence"]) if c.get("evidence") else None
        from .vision.regions import hip_box
        shape = (brief["height"] or 0, brief["width"] or 0)
        auto, low = self._thr()

        persons = []
        for j, p in enumerate(people):
            mine = [c for c in cands if c["person_idx"] == j and c["status"] != "rejected"]
            persons.append({"idx": j, "box": [float(v) for v in p.box], "has_face": p.face is not None,
                            "hip": list(hip_box(p, shape)) if hip_box(p, shape) else None, "anchor": self._anchor(p),
                            "numbers": {str(k): v for k, v in p.numbers.items()},
                            "assigned": [c["competitor"] for c in mine if c["status"] == "accepted" and c["competitor"]],
                            "suggested": [{"competitor": c["competitor"], "p": c["p"], "evidence": c["evidence"]} for c in mine
                                          if c["status"] == "suggested" and c["p"] >= low and c["competitor"]]})
        brief["persons"], brief["candidates"] = persons, cands
        return brief

    @_locked
    def competitor_items(self, comp_id, limit=400):
        """Everything currently assigned to one competitor (confirmed first), for the album view."""
        auto, low = self._thr()
        rows = self.con.execute(
            "SELECT photo_id,competitor_id,p,num_conf,person_idx,evidence,status FROM assignments "
            "WHERE competitor_id=? AND status<>'rejected' AND (status='accepted' OR p>=?) "
            "ORDER BY (status='accepted') DESC, p DESC LIMIT ?", (comp_id, low, limit)).fetchall()
        return [{**self._enrich(r), "status": r["status"]} for r in rows]

    @_locked
    def album(self):
        """Per competitor progress, for the collection view: photos confirmed / suggested / to check."""
        auto, low = self._thr()
        comps = self.pr.competitors() + [c for c in self.pr.competitors(include_others=True) if c["kind"] == "others"]
        out = []
        for c in comps:
            q = lambda s, *a: self.con.execute(s, (c["id"], *a)).fetchone()[0]
            confirmed = q("SELECT COUNT(*) FROM assignments WHERE competitor_id=? AND status='accepted'")
            auto_n = q("SELECT COUNT(*) FROM assignments WHERE competitor_id=? AND status='suggested' AND p>=?", auto)
            check = q("SELECT COUNT(*) FROM assignments WHERE competitor_id=? AND status='suggested' AND p>=? AND p<?", low, auto)
            cover = self.con.execute("SELECT photo_id FROM assignments WHERE competitor_id=? AND status<>'rejected' ORDER BY (status='accepted') DESC, p DESC LIMIT 1", (c["id"],)).fetchone()
            if c["kind"] == "others" and confirmed == 0:
                continue                                          # the Others album exists only once something is in it
            out.append({**c, "confirmed": confirmed, "auto": auto_n, "check": check, "cover_photo": cover["photo_id"] if cover else None,
                        "photos": confirmed + auto_n + check, "complete": check == 0 and confirmed + auto_n > 0})
        return out
