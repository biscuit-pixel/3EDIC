"""Build a 3EDIČ project from a manually sorted dataset + a legacy feature cache (development fixture).

usage: import_legacy.py DATASET_ROOT LEGACY_CACHE.pkl DATA_DIR FLAT_DIR
Truth for evaluation is written to DATA_DIR/truth.json  ({photo name: [numbers]}).
"""
import json
import os
import pickle
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from edic.project import Project  # noqa: E402
from edic.types import Person  # noqa: E402


def main(root, cache_path, data_dir, flat_dir):
    sys.path.insert(0, "/home/user/photosorter")
    cache = pickle.loads(Path(cache_path).read_bytes())
    flat = Path(flat_dir)
    flat.mkdir(parents=True, exist_ok=True)
    truth = {}
    for d in sorted(Path(root).iterdir()):
        m = re.match(r"(\d+)_", d.name)
        if not (d.is_dir() and m):
            continue
        for f in d.glob("*.jpg"):
            truth.setdefault(f.name, []).append(int(m.group(1)))
            link = flat / f.name
            if not link.exists():
                os.symlink(f, link)
    pr = Project.create(data_dir, "NPC 365 · 3.5.", flat)
    print("ingest:", pr.ingest(), "photos")
    ids = {p["name"]: p["id"] for p in pr.photos()}
    for name, people in cache.items():
        if name in ids:
            pr.save_persons(ids[name], [Person(box=tuple(p.box), kpts=p.kpts, numbers=dict(p.numbers), bibs=list(p.bibs),
                                               colour=p.colour, face=p.face, face_arc=getattr(p, "face_arc", None),
                                               face_score=getattr(p, "face_score", 0.0)) for p in people])
    pr.set_roster(sorted({n for v in truth.values() for n in v}))
    Path(data_dir, "truth.json").write_text(json.dumps(truth))
    print("project", pr.id, pr.summary())


if __name__ == "__main__":
    main(*sys.argv[1:5])
