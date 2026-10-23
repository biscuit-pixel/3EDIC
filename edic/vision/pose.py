"""YOLOv8-pose through ONNX Runtime (no PyTorch needed at run time)."""
import cv2
import numpy as np

from ..types import Person
from . import providers


class PoseModel:
    def __init__(self, path, conf=0.25, iou=0.45, force_cpu=False):
        self.sess = providers.session(path, force_cpu)
        inp = self.sess.get_inputs()[0]
        self.name = inp.name
        self.size = int(inp.shape[2])              # static square input, e.g. 1280
        self.conf, self.iou = conf, iou

    def detect(self, img) -> list[Person]:
        h, w = img.shape[:2]
        s = self.size / max(h, w)
        nh, nw = round(h * s), round(w * s)
        canvas = np.full((self.size, self.size, 3), 114, np.uint8)
        canvas[:nh, :nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
        x = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        out = self.sess.run(None, {self.name: x})[0][0].T          # (N, 56)
        keep = out[:, 4] >= self.conf
        out = out[keep]
        if len(out) == 0:
            return []
        cx, cy, bw, bh = out[:, 0], out[:, 1], out[:, 2], out[:, 3]
        boxes = np.stack([cx - bw / 2, cy - bh / 2, bw, bh], 1)
        idx = cv2.dnn.NMSBoxes(boxes.tolist(), out[:, 4].tolist(), self.conf, self.iou)
        people = []
        for i in np.array(idx).reshape(-1):
            r = out[i]
            kp = r[5:].reshape(17, 3).copy()
            kp[:, :2] /= s
            x1, y1, x2, y2 = (cx[i] - bw[i] / 2) / s, (cy[i] - bh[i] / 2) / s, (cx[i] + bw[i] / 2) / s, (cy[i] + bh[i] / 2) / s
            people.append(Person(box=(float(x1), float(y1), float(x2), float(y2)), kpts=kp))
        return people
