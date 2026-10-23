"""Body regions derived from pose keypoints."""
import numpy as np

from ..types import L_HIP, L_KNEE, R_HIP, R_KNEE, Person


def clip(x1, y1, x2, y2, shape):
    h, w = shape[:2]
    return (max(0, int(x1)), max(0, int(y1)), min(w, int(x2)), min(h, int(y2)))


def hip_centre(p: Person, conf=0.3):
    hips = [p.kpts[i, :2] for i in (L_HIP, R_HIP) if p.kpts[i, 2] >= conf]
    return np.mean(hips, 0) if hips else None


def hip_box(p: Person, img_shape, conf=0.3):
    """Waistband / hip area where the number is pinned."""
    k = p.kpts
    hips = np.array([k[i, :2] for i in (L_HIP, R_HIP) if k[i, 2] >= conf])
    if len(hips) == 0:
        return None
    centre = hips.mean(0)
    body_h = p.height
    hip_w = np.ptp(hips[:, 0]) if len(hips) > 1 else 0.0
    half_w = max(hip_w * 0.9, body_h * 0.12)
    half_h = body_h * 0.10
    return clip(centre[0] - half_w, centre[1] - half_h, centre[0] + half_w, centre[1] + half_h * 1.2, img_shape)


def trunks_box(p: Person, img_shape, conf=0.3):
    """Hips to mid-thigh, central part: trunks colour / pattern."""
    k = p.kpts
    hips = [k[i, :2] for i in (L_HIP, R_HIP) if k[i, 2] >= conf]
    if not hips:
        return None
    hips = np.array(hips)
    knees = [k[i, :2] for i in (L_KNEE, R_KNEE) if k[i, 2] >= conf]
    hip_y = hips[:, 1].mean()
    bottom = np.mean([kn[1] for kn in knees]) if knees else hip_y + p.height * 0.2
    bottom = hip_y + (bottom - hip_y) * 0.55
    cx = hips[:, 0].mean()
    half_w = max(np.ptp(hips[:, 0]) * 0.5 if len(hips) > 1 else 0, p.height * 0.06)
    return clip(cx - half_w, hip_y, cx + half_w, bottom, img_shape)


def head_box(p: Person, img_shape, conf=0.3):
    """Square around nose/eyes/ears, padded for the face detector."""
    pts = p.kpts[:5]
    pts = pts[pts[:, 2] >= conf][:, :2]
    if len(pts) == 0:
        return None
    size = max(np.ptp(pts[:, 0]) * 2.4, np.ptp(pts[:, 1]) * 2.4, p.height * 0.12) * 1.6
    c = pts.mean(0)
    return clip(c[0] - size / 2, c[1] - size / 2, c[0] + size / 2, c[1] + size / 2, img_shape)
