"""Plain data types shared across the engine."""
from dataclasses import dataclass, field

import numpy as np

# COCO keypoint indices
NOSE, L_SH, R_SH, L_HIP, R_HIP, L_KNEE, R_KNEE = 0, 5, 6, 11, 12, 13, 14


@dataclass
class Person:
    """One detected person in one photo. All coordinates are full-resolution pixels."""
    box: tuple                                   # x1, y1, x2, y2
    kpts: np.ndarray                             # (17, 3) x, y, confidence
    numbers: dict = field(default_factory=dict)  # bib number -> best OCR confidence
    bibs: list = field(default_factory=list)     # [(number, conf, rel_box)] rel. to hip centre / body height
    colour: np.ndarray | None = None             # trunks colour histogram
    face: np.ndarray | None = None               # SFace embedding (128-d, L2-normalised)
    face_arc: np.ndarray | None = None           # ArcFace embedding (512-d, L2-normalised), optional model
    face_score: float = 0.0

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]
