"""Decisions and the review queue: what the user still has to look at, and how answers are stored."""
from __future__ import annotations

import functools
import json
import time


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        with self.pr.lock:
            return fn(self, *a, **kw)
    return wrapper


class Review:
    def __init__(self, project):
        self.pr = project
        self.con = project.con

    # ------------------------------------------------------------ decisions
    def _log(self, photo_id, comp_id, action):
        r = self.con.execute("SELECT * FROM assignments WHERE photo_id=? AND competitor_id=?", (photo_id, comp_id)).fetchone()
        self.con.execute(
            "INSERT INTO decision_log(at,photo_id,competitor_id,action,existed,prev_status,prev_source,prev_person,prev_p,prev_num_conf) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (time.time(), photo_id, comp_id, action, 1 if r else 0, r and r["status"], r and r["source"],
             r and r["person_idx"], r and r["p"], r and r["num_conf"]))

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
        """The photo shows none of the competitors (audience, host, empty stage)."""
        self.set_photo(photo_id, [])
        self.con.execute("INSERT INTO meta(key,value) VALUES(?,'1') ON CONFLICT(key) DO UPDATE SET value='1'", (f"nobody:{photo_id}",))

    @_locked
    def undo(self):
        """Revert the most recent decision. -> the restored (photo_id, competitor_id) or None."""
        r = self.con.execute("SELECT * FROM decision_log WHERE undone=0 ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            return None
        self.con.execute("BEGIN")
        if r["existed"]:
            self.con.execute("UPDATE assignments SET status=?,source=?,person_idx=?,p=?,num_conf=? WHERE photo_id=? AND competitor_id=?",
                             (r["prev_status"], r["prev_source"], r["prev_person"], r["prev_p"], r["prev_num_conf"],
                              r["photo_id"], r["competitor_id"]))
        else:
            self.con.execute("DELETE FROM assignments WHERE photo_id=? AND competitor_id=?", (r["photo_id"], r["competitor_id"]))
        self.con.execute("UPDATE decision_log SET undone=1 WHERE id=?", (r["id"],))
        self.con.execute("COMMIT")
        return r["photo_id"], r["competitor_id"]

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
        return {"photos": total, "resolved_photos": min(total, resolved + nobody), "accepted_pairs": accepted,
                "auto_pairs": q("SELECT COUNT(*) FROM assignments WHERE status='suggested' AND p>=?", auto),
                "to_check_pairs": check, "unknown_photos": unknown,
                "progress": (min(total, resolved + nobody) / total) if total else 0.0}

    def _enrich(self, row):
        """Item for the UI: pair + the person's box (so a thumbnail can show just them) + evidence."""
        d = dict(row)
        people = self.pr.load_persons().get(d["photo_id"], [])
        j = d.get("person_idx")
        d["box"] = [float(v) for v in people[j].box] if j is not None and 0 <= j < len(people) else None
        ph = self.con.execute("SELECT width,height FROM photos WHERE id=?", (d["photo_id"],)).fetchone()
        d["width"], d["height"] = (ph["width"], ph["height"]) if ph else (None, None)
        ev = d.get("evidence")
        d["evidence"] = json.loads(ev) if ev else None
        return d

    def _comp_map(self):
        return {c["id"]: c for c in self.pr.competitors()}

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
        brief["persons"] = [{"idx": j, "box": [float(v) for v in p.box], "has_face": p.face is not None,
                             "hip": list(hip_box(p, shape)) if hip_box(p, shape) else None,
                             "numbers": {str(k): v for k, v in p.numbers.items()}} for j, p in enumerate(people)]
        brief["candidates"] = cands
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
        comps = self.pr.competitors()
        out = []
        for c in comps:
            q = lambda s, *a: self.con.execute(s, (c["id"], *a)).fetchone()[0]
            confirmed = q("SELECT COUNT(*) FROM assignments WHERE competitor_id=? AND status='accepted'")
            auto_n = q("SELECT COUNT(*) FROM assignments WHERE competitor_id=? AND status='suggested' AND p>=?", auto)
            check = q("SELECT COUNT(*) FROM assignments WHERE competitor_id=? AND status='suggested' AND p>=? AND p<?", low, auto)
            cover = self.con.execute("SELECT photo_id FROM assignments WHERE competitor_id=? AND status<>'rejected' ORDER BY (status='accepted') DESC, p DESC LIMIT 1", (c["id"],)).fetchone()
            out.append({**c, "confirmed": confirmed, "auto": auto_n, "check": check, "cover_photo": cover["photo_id"] if cover else None,
                        "complete": check == 0 and confirmed + auto_n > 0})
        return out
