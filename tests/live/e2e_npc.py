"""End-to-end check on the NPC fixture: rescore, review stats, simulated review session, export.
usage: e2e_npc.py DATA_DIR PROJECT_ID VARIANT
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from edic.export import export  # noqa: E402
from edic.project import Project  # noqa: E402
from edic.review import Review  # noqa: E402


def truth_pairs(pr, truth):
    pid = {p["name"]: p["id"] for p in pr.photos()}
    cid = {c["number"]: c["id"] for c in pr.competitors()}
    return {(pid[n], cid[c]) for n, cs in truth.items() for c in cs}


def accuracy(pr, rv, truth, mode):
    eff = {(p, c) for p, c, _ in rv.effective(mode)}
    tp = len(eff & truth)
    return len(eff), tp / max(len(eff), 1), tp / len(truth)


def main(data_dir, pid, variant):
    pr = Project(Path(data_dir) / "projects" / pid)
    truth = truth_pairs(pr, json.loads(Path(data_dir, "truth.json").read_text()))
    t = time.time()
    print("rescore:", pr.rescore(variant), f"{time.time()-t:.1f}s")
    rv = Review(pr)
    print("stats:", rv.stats())
    for mode in ("smart", "numbers_only", "all", "manual"):
        n, p, r = accuracy(pr, rv, truth, mode)
        print(f"  mode {mode:<13} exported pairs={n:4d} precision={p:.2f} recall={r:.2f}")

    # simulated user: walks the grid batches, ticks off wrong ones (truth), accepts the rest
    clicks = 0
    for b in rv.batches(limit=1000, per_batch=10**6):
        cid = b["competitor"]["id"]
        good = [i["photo_id"] for i in b["items"] if (i["photo_id"], cid) in truth]
        bad = [i["photo_id"] for i in b["items"] if (i["photo_id"], cid) not in truth]
        rv.resolve_batch(cid, good, bad)
        clicks += len(bad)                       # the user only clicks the wrong ones
    print(f"after grid review: {clicks} wrong thumbnails clicked;", {k: v for k, v in rv.stats().items() if k != 'photos'})
    n, p, r = accuracy(pr, rv, truth, "smart")
    print(f"  smart  pairs={n} precision={p:.2f} recall={r:.2f}")
    print("rescore with user confirmations:", pr.rescore(variant))
    n, p, r = accuracy(pr, rv, truth, "smart")
    print(f"  smart  pairs={n} precision={p:.2f} recall={r:.2f}", rv.stats())
    print("undo ->", rv.undo())

    out = Path("/tmp/edic_export")
    import shutil
    shutil.rmtree(out, ignore_errors=True)
    print("export:", export(pr, out, mode="symlink"))
    print(sorted(p.name for p in out.iterdir())[:8], "...")


if __name__ == "__main__":
    main(*sys.argv[1:4])
