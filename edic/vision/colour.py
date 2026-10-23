"""Trunks / swimwear colour descriptor."""
import cv2
import numpy as np

from ..types import Person
from .regions import trunks_box


def colour_hist(img, p: Person):
    b = trunks_box(p, img.shape)
    if b is None:
        return None
    x1, y1, x2, y2 = b
    crop = img[y1:y2, x1:x2]
    if crop.shape[0] < 8 or crop.shape[1] < 8:
        return None
    hsv = cv2.cvtColor(cv2.resize(crop, (48, 48)), cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [18, 4], [0, 180, 0, 256]).flatten()
    hist /= hist.sum() + 1e-9
    return hist.astype(np.float32)
