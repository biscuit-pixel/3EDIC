"""Scoring: turn per-person signals into P(competitor appears in photo).

Pipeline (same as the offline evaluation):
  1. anchors  - persons whose bib number was read (OCR / VLM) or that the user confirmed
  2. galleries - per competitor, face / colour embeddings of the anchored persons (cleaned by consensus)
  3. features - per (photo, competitor): bib evidence + best similarity to the gallery + margin over rivals
  4. combiner - logistic regression (default weights ship with the app, see data/combiner_*.json)
  5. sequence - neighbouring photos (by shooting time) vote, second logistic regression
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import numpy as np

from .types import Person

OCR_MIN_CONF = 0.5
VLM_CONF = 0.9
TAU_SECONDS = 6.0           # decay of the neighbour vote

# modality -> (Person attribute, transform, gallery-cleaning threshold or None)
MODALITIES = {
    "sface": ("face", None, 0.45),
    "arcface": ("face_arc", None, 0.25),
    "colour": ("colour", np.sqrt, None),
}
VARIANTS = {
    "basic": ("sface", "colour"),                      # Apache-2.0 models only
    "arcface": ("arcface", "sface", "colour"),         # optional ArcFace model installed
}


@dataclass
class Seed:
    """A person the user (or a confirmed decision) tied to a competitor."""
    photo: int                 # photo index
    comp: int                  # competitor index (into roster)
    person: int | None = None  # person index within the photo; None -> the photo's main face


@dataclass
class ScoreInput:
    persons: list[list[Person]]                 # per photo, in shooting order
    times: np.ndarray                           # seconds, per photo
    roster: list[int]                           # competitor numbers
    seeds: list[Seed] = field(default_factory=list)
    extra_numbers: dict = field(default_factory=dict)   # (photo, person) -> {number: conf} from the VLM
    ocr_min_number: int = 10                    # single digits from OCR are mostly fragments


@dataclass
class ScoreResult:
    P: np.ndarray                  # (photos, competitors) final probability
    P1: np.ndarray                 # before sequence stage
    f_num: np.ndarray              # bib evidence
    feats: dict                    # modality -> (best similarity, margin)
    variant: str
    best_person: np.ndarray        # (photos, competitors) index of the person that matched best, -1 if none


# ------------------------------------------------------------------ galleries
def _anchors(inp: ScoreInput, comp_index: dict):
    """-> list of (photo, comp, person_idx) from number reads and user seeds."""
    out = []
    for i, ps in enumerate(inp.persons):
        for j, p in enumerate(ps):
            for c, conf in p.numbers.items():
                if c in comp_index and conf >= OCR_MIN_CONF and c >= inp.ocr_min_number:
                    out.append((i, comp_index[c], j, conf))
            for c, conf in inp.extra_numbers.get((i, j), {}).items():
                if c in comp_index:
                    out.append((i, comp_index[c], j, conf))
    for s in inp.seeds:
        j = s.person if s.person is not None else _main_face(inp.persons[s.photo])
        if j is not None:
            out.append((s.photo, s.comp, j, 1.0))
    return out


def _main_face(ps):
    cand = [(p.height, j) for j, p in enumerate(ps) if p.face is not None or p.face_arc is not None]
    return max(cand)[1] if cand else None


def _clean(gal, thr):
    """Drop gallery items that disagree with the rest (wrong reads). -> (kept, kept photo ids or None)."""
    if thr is None or len(gal) < 3:
        return gal, None
    E = np.stack([g[1] for g in gal])
    S = E @ E.T
    np.fill_diagonal(S, np.nan)
    score = np.nanmedian(S, axis=1)
    kept = [g for g, sc in zip(gal, score) if sc >= thr]
    return (kept, {g[0] for g in kept}) if kept else (gal, None)


def _margin(M):
    top2 = np.sort(M, axis=1)[:, -2:]
    other = np.where(M == top2[:, [1]], top2[:, [0]], top2[:, [1]])
    return M - other


def features(inp: ScoreInput, mods: tuple, clean_by: str):
    n, m = len(inp.persons), len(inp.roster)
    comp_index = {c: k for k, c in enumerate(inp.roster)}
    f_num = np.zeros((n, m))
    gal = {k: [[] for _ in range(m)] for k in mods}
    for i, c, j, conf in _anchors(inp, comp_index):
        f_num[i, c] = max(f_num[i, c], conf)
        p = inp.persons[i][j]
        for k in mods:
            attr, tf, _ = MODALITIES[k]
            v = getattr(p, attr, None)
            if v is not None:
                gal[k][c].append((i, tf(v) if tf else v))
    thr = MODALITIES[clean_by][2]
    for c in range(m):
        gal[clean_by][c], keep = _clean(gal[clean_by][c], thr)
        if keep is not None:
            for k in mods:
                if k != clean_by:
                    gal[k][c] = [g for g in gal[k][c] if g[0] in keep]

    # stack every person's embedding once: rows -> (photo, person)
    out, best_person = {}, np.full((n, m), -1)
    for k in mods:
        attr, tf, _ = MODALITIES[k]
        rows, owner, who = [], [], []
        for i, ps in enumerate(inp.persons):
            for j, p in enumerate(ps):
                v = getattr(p, attr, None)
                if v is not None:
                    rows.append(tf(v) if tf else v)
                    owner.append(i)
                    who.append(j)
        F = np.zeros((n, m))
        if rows:
            X, owner, who = np.stack(rows), np.array(owner), np.array(who)
            for c in range(m):
                if not gal[k][c]:
                    continue
                ids = np.array([g[0] for g in gal[k][c]])
                S = X @ np.stack([g[1] for g in gal[k][c]]).T            # (persons, gallery)
                S[owner[:, None] == ids[None, :]] = -1                    # never match a photo against itself
                best = S.max(1)
                agg = np.full(n, -2.0)
                np.maximum.at(agg, owner, best)
                F[:, c] = np.where(agg > -2, np.maximum(agg, -1), 0)
                if k == clean_by:
                    for i in np.unique(owner):
                        sel = np.where(owner == i)[0]
                        best_person[i, c] = who[sel[np.argmax(best[sel])]]
        out[k] = (F, _margin(F))
    return f_num, out, best_person


# ------------------------------------------------------------------ combiner
def load_combiner(variant: str) -> dict:
    p = resources.files("edic") / "data" / f"combiner_{variant}.json"
    return json.loads(p.read_text())


def _sigmoid(z):
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def stage1_matrix(f_num, feats, mods):
    cols = [f_num]
    for k in mods:
        F, M = feats[k]
        cols += [F, M]
    return np.stack([c.ravel() for c in cols], 1)


def neighbour_features(P, times, tau=TAU_SECONDS):
    n, _ = P.shape
    out = []
    for d in (-2, -1, 1, 2):
        Q = np.zeros_like(P)
        for i in range(n):
            j = i + d
            if 0 <= j < n:
                Q[i] = P[j] * np.exp(-abs(times[j] - times[i]) / tau)
        out.append(Q)
    return out


def stage2_matrix(P1, times):
    Q = neighbour_features(P1, times)
    lg = np.log(P1 / (1 - P1 + 1e-9) + 1e-9)
    return np.stack([x.ravel() for x in (lg, *Q, np.maximum.reduce(Q))], 1)


def score(inp: ScoreInput, variant: str = "basic", combiner: dict | None = None) -> ScoreResult:
    mods = VARIANTS[variant]
    comb = combiner or load_combiner(variant)
    f_num, feats, best_person = features(inp, mods, clean_by=mods[0])
    n, m = len(inp.persons), len(inp.roster)
    X1 = stage1_matrix(f_num, feats, mods)
    P1 = _sigmoid(X1 @ np.array(comb["stage1"]["coef"]) + comb["stage1"]["intercept"]).reshape(n, m)
    X2 = stage2_matrix(P1, inp.times)
    P = _sigmoid(X2 @ np.array(comb["stage2"]["coef"]) + comb["stage2"]["intercept"]).reshape(n, m)
    return ScoreResult(P=P, P1=P1, f_num=f_num, feats=feats, variant=variant, best_person=best_person)


def fit_combiner(f_num, feats, times, Y, mods, C=1.0) -> dict:
    """Fit both stages on labelled data (developer tool; see tools/fit_combiner.py)."""
    from sklearn.linear_model import LogisticRegression
    n, m = Y.shape
    X1 = stage1_matrix(f_num, feats, mods)
    s1 = LogisticRegression(C=C, max_iter=2000).fit(X1, Y.ravel())
    P1 = s1.predict_proba(X1)[:, 1].reshape(n, m)
    s2 = LogisticRegression(C=C, max_iter=2000).fit(stage2_matrix(P1, times), Y.ravel())
    return {"mods": list(mods),
            "stage1": {"coef": s1.coef_[0].tolist(), "intercept": float(s1.intercept_[0])},
            "stage2": {"coef": s2.coef_[0].tolist(), "intercept": float(s2.intercept_[0])}}


def save_combiner(comb: dict, variant: str, root: Path):
    (Path(root) / f"combiner_{variant}.json").write_text(json.dumps(comb, indent=1))
