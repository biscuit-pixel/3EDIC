"""Bib number reading (OCR) and the per-event calibration profile."""
import re
from dataclasses import asdict, dataclass

import cv2
import numpy as np

from ..types import Person
from .regions import clip, hip_box, hip_centre


@dataclass
class BibProfile:
    """Where this event's number sits relative to the hips, learned from a few user examples.
    Values are in units of body height, relative to the hip centre."""
    dx: float
    dy: float
    w: float
    h: float
    spread: tuple = (0.05, 0.03)

    @classmethod
    def from_examples(cls, rels):
        a = np.array(rels)                                   # (n, 4): x, y, w, h of the number's text box
        cx, cy = a[:, 0] + a[:, 2] / 2, a[:, 1] + a[:, 3] / 2
        return cls(float(np.median(cx)), float(np.median(cy)), float(np.median(a[:, 2])), float(np.median(a[:, 3])),
                   (float(max(np.std(cx), 0.02)), float(max(np.std(cy), 0.015))))

    def box(self, p: Person, img_shape, margin=2.5):
        c = hip_centre(p)
        if c is None:
            return None
        unit = max(p.height, 1.0)
        cx, cy = c[0] + self.dx * unit, c[1] + self.dy * unit
        hw = max(self.w * margin, 3 * self.spread[0]) * unit / 2 + self.spread[0] * 2 * unit
        hh = max(self.h * margin, 3 * self.spread[1]) * unit / 2 + self.spread[1] * 2 * unit
        return clip(cx - hw, cy - hh, cx + hw, cy + hh, img_shape)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(d["dx"], d["dy"], d["w"], d["h"], tuple(d.get("spread", (0.05, 0.03))))


def _variants(crop, scale):
    """Upscaled plus contrast-boosted versions: bibs are small and often low contrast."""
    up = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    yield up
    gray = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4)).apply(gray)
    yield cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR)
    yield cv2.cvtColor(255 - clahe, cv2.COLOR_GRAY2BGR)


class BibReader:
    def __init__(self, min_digits=1, max_digits=3):
        from rapidocr_onnxruntime import RapidOCR
        self.ocr = RapidOCR()
        self.min_digits, self.max_digits = min_digits, max_digits

    def read(self, img, p: Person, profile: BibProfile | None = None):
        """-> ({number: confidence}, [(number, conf, rel_box)])"""
        b = (profile.box(p, img.shape) if profile else None) or hip_box(p, img.shape)
        if b is None:
            return {}, []
        x1, y1, x2, y2 = b
        crop = img[y1:y2, x1:x2]
        if crop.size == 0:
            return {}, []
        scale = max(1.0, 320 / max(crop.shape[0], 1))
        found, bibs = {}, []
        centre, unit = hip_centre(p), max(p.height, 1.0)
        for variant in _variants(crop, scale):
            res, _ = self.ocr(variant)
            for quad, text, conf in res or []:
                d = re.sub(r"\D", "", text)
                if not self.min_digits <= len(d) <= self.max_digits:
                    continue
                n, conf = int(d), float(conf)
                found[n] = max(found.get(n, 0), conf)
                pts = np.array(quad) / scale + (x1, y1)
                bx1, by1 = pts.min(0)
                bx2, by2 = pts.max(0)
                if centre is not None:
                    bibs.append((n, conf, ((bx1 - centre[0]) / unit, (by1 - centre[1]) / unit,
                                           (bx2 - bx1) / unit, (by2 - by1) / unit)))
        return found, bibs
