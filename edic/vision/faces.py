"""Face detection (YuNet) and embeddings: SFace (default, Apache-2.0) and ArcFace (optional model)."""
import cv2
import numpy as np

from ..types import Person
from . import providers
from .regions import head_box


def _unit(v):
    return (v / (np.linalg.norm(v) + 1e-9)).astype(np.float32)


class FaceEngine:
    def __init__(self, yunet_path, sface_path, arcface_path=None, force_cpu=False):
        self.det = cv2.FaceDetectorYN.create(str(yunet_path), "", (320, 320), 0.6)
        self.sface = cv2.FaceRecognizerSF.create(str(sface_path), "")
        self.arc = providers.session(arcface_path, force_cpu) if arcface_path else None
        self.arc_input = self.arc.get_inputs()[0].name if self.arc else None

    @property
    def has_arcface(self):
        return self.arc is not None

    def _arc_embed(self, aligned_bgr):
        x = (aligned_bgr[:, :, ::-1].astype(np.float32) - 127.5) / 127.5
        out = self.arc.run(None, {self.arc_input: x.transpose(2, 0, 1)[None]})[0][0]
        return _unit(out)

    def _embed_face(self, crop, row):
        aligned = self.sface.alignCrop(crop, row)
        sf = _unit(self.sface.feature(aligned).flatten())
        return sf, (self._arc_embed(aligned) if self.arc else None)

    def detect(self, crop):
        self.det.setInputSize((crop.shape[1], crop.shape[0]))
        _, faces = self.det.detect(crop)
        return [] if faces is None else list(faces)

    def embed_person(self, img, p: Person):
        """Fill p.face / p.face_arc from the head region."""
        b = head_box(p, img.shape)
        if b is None or min(b[2] - b[0], b[3] - b[1]) < 16:
            return
        x1, y1, x2, y2 = b
        crop = img[y1:y2, x1:x2]
        crop = cv2.resize(crop, None, fx=480 / max(crop.shape[:2]), fy=480 / max(crop.shape[:2]),
                          interpolation=cv2.INTER_CUBIC)
        faces = self.detect(crop)
        if not faces:
            return
        f = max(faces, key=lambda r: r[-1])
        p.face, arc = self._embed_face(crop, f)
        p.face_arc, p.face_score = arc, float(f[-1])

    def faces_in_image(self, img, max_side=1600):
        """Embeddings of every face in a whole image (reference portraits for 'find a person')."""
        s = min(1.0, max_side / max(img.shape[:2]))
        small = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img
        out = []
        for f in self.detect(small):
            sf, arc = self._embed_face(small, f)
            out.append({"face": sf, "face_arc": arc, "score": float(f[-1]),
                        "box": tuple(float(v) / s for v in (f[0], f[1], f[0] + f[2], f[1] + f[3]))})
        return out
