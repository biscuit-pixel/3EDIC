"""Find one person across all photos by face (conference use)."""
from __future__ import annotations

import numpy as np


def find_person(project, faces: list[dict], use_arc: bool = False, top_k: int = 200, min_sim: float | None = None):
    """faces: embeddings from FaceEngine.faces_in_image() for the reference photos of ONE person.
    -> ranked [{photo_id, sim, person_idx}] (best match per photo, highest first)."""
    key = "face_arc" if use_arc else "face"
    refs = [f[key] for f in faces if f.get(key) is not None]
    if not refs:
        return []
    R = np.stack(refs)
    thr = min_sim if min_sim is not None else (0.30 if use_arc else 0.40)
    out = []
    for photo_id, people in project.load_persons().items():
        best, who = -1.0, -1
        for j, p in enumerate(people):
            v = getattr(p, key if use_arc else "face", None)
            if v is None:
                continue
            s = float((R @ v).max())            # best against any reference photo
            if s > best:
                best, who = s, j
        if who >= 0 and best >= thr:
            out.append({"photo_id": photo_id, "sim": best, "person_idx": who})
    out.sort(key=lambda d: -d["sim"])
    return out[:top_k]
