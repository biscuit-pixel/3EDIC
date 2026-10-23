"""Fit the default combiner weights shipped in edic/data on a labelled, already-extracted event.

Input is a legacy feature cache (pickle of per-photo Person lists from the research phase) plus the
manually sorted folders (<number>_<event>/<photo>.jpg) as ground truth. Reports honest cross-fitted
numbers (fit on one half of the photos, score the other) and writes in-sample weights.

usage: fit_combiner.py DATASET_ROOT LEGACY_CACHE.pkl [--write]
"""
import pickle
import re
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from edic import scoring  # noqa: E402
from edic.exif import read_meta  # noqa: E402
from edic.types import Person  # noqa: E402


def load_legacy(root, cache_path):
    sys.path.insert(0, "/home/user/photosorter")      # the old pickles reference photosorter.features
    cache = pickle.loads(Path(cache_path).read_bytes())
    lab, paths = {}, {}
    for d in Path(root).iterdir():
        m = re.match(r"(\d+)_", d.name)
        if d.is_dir() and m:
            for f in d.glob("*.jpg"):
                lab.setdefault(f.name, set()).add(int(m.group(1)))
                paths[f.name] = f
    names = [n for n in cache if n in lab]
    times = np.array([(read_meta(paths[n])["taken"] or np.nan) for n in names])
    nums = [int(m.group(1)) if (m := re.search(r"-(\d+)\.jpg$", n)) else None for n in names]
    known = [i for i in range(len(names)) if not np.isnan(times[i]) and nums[i] is not None]
    for i in range(len(names)):
        if np.isnan(times[i]) and nums[i] is not None and known:
            times[i] = times[min(known, key=lambda k: abs(nums[k] - nums[i]))]
    order = np.argsort(times)
    names, times = [names[i] for i in order], times[order]
    persons = []
    for n in names:
        persons.append([Person(box=tuple(p.box), kpts=p.kpts, numbers=dict(p.numbers), bibs=list(p.bibs),
                               colour=p.colour, face=p.face, face_arc=getattr(p, "face_arc", None),
                               face_score=getattr(p, "face_score", 0.0)) for p in cache[n]])
    roster = sorted(set().union(*lab.values()))
    R = {c: i for i, c in enumerate(roster)}
    Y = np.zeros((len(names), len(roster)), int)
    for i, n in enumerate(names):
        for c in lab[n]:
            Y[i, R[c]] = 1
    return names, times, persons, roster, Y


def crossfit_P(inp, Y, mods):
    f_num, feats, _ = scoring.features(inp, mods, mods[0])
    n, m = Y.shape
    groups = np.repeat(np.random.default_rng(0).integers(0, 2, n)[:, None], m, axis=1).ravel()
    X1, y = scoring.stage1_matrix(f_num, feats, mods), Y.ravel()
    P1 = np.zeros(len(y))
    for f in (0, 1):
        tr, te = groups != f, groups == f
        P1[te] = LogisticRegression(max_iter=2000).fit(X1[tr], y[tr]).predict_proba(X1[te])[:, 1]
    P1 = P1.reshape(n, m)
    X2 = scoring.stage2_matrix(P1, inp.times)
    P2 = np.zeros(len(y))
    for f in (0, 1):
        tr, te = groups != f, groups == f
        P2[te] = LogisticRegression(max_iter=2000).fit(X2[tr], y[tr]).predict_proba(X2[te])[:, 1]
    return P2.reshape(n, m), f_num, feats


def report(tag, P, Y):
    s = f"{tag:<26} AP={average_precision_score(Y.ravel(), P.ravel()):.3f} |"
    for t in (0.5, 0.7, 0.9):
        a = P >= t
        tp = (a & (Y == 1)).sum()
        s += f"  t={t}: P={tp / max(a.sum(), 1):.2f} R={tp / Y.sum():.2f}"
    print(s)


def main(root, cache_path, write):
    names, times, persons, roster, Y = load_legacy(root, cache_path)
    print(f"photos {len(names)}, competitors {len(roster)}")
    inp = scoring.ScoreInput(persons=persons, times=times, roster=roster)
    for variant, mods in scoring.VARIANTS.items():
        P, f_num, feats = crossfit_P(inp, Y, mods)
        report(f"{variant} (cross-fitted)", P, Y)
        comb = scoring.fit_combiner(f_num, feats, times, Y, mods)
        res = scoring.score(inp, variant, comb)
        report(f"{variant} (in-sample)", res.P, Y)
        if write:
            scoring.save_combiner(comb, variant, Path(__file__).resolve().parent.parent / "edic" / "data")
            print("  wrote", variant)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], "--write" in sys.argv)
