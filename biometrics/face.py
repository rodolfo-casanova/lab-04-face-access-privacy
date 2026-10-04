"""Face model (InsightFace buffalo_l: RetinaFace detector + ArcFace recognition)."""
import os

import cv2
import numpy as np

from common.utils import CACHE_DIR, log

_APP = None


def app():
    global _APP
    if _APP is None:
        from insightface.app import FaceAnalysis

        name = os.getenv("FACE_MODEL", "buffalo_l")
        _APP = FaceAnalysis(name=name, root=str(CACHE_DIR / "insightface"),
                            allowed_modules=["detection", "recognition"], providers=["CPUExecutionProvider"])
        _APP.prepare(ctx_id=-1, det_size=(320, 320))
        log(f"Face model {name} loaded.")
    return _APP


def embed(img_rgb: np.ndarray):
    """Returns (normalized ArcFace vector, keypoints or None). Accepts tight face crops."""
    a = app()
    bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
    pad = int(0.35 * max(bgr.shape[:2]))  # margin: very tight crops make detection harder
    padded = cv2.copyMakeBorder(bgr, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    if max(padded.shape[:2]) < 320:
        s = 320 / max(padded.shape[:2])
        padded = cv2.resize(padded, None, fx=s, fy=s)
    faces = a.get(padded)
    if faces:
        f = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        return f.normed_embedding.astype(np.float32), f.kps
    # fallback: the crop is already the face, so pass it straight to the recognition model
    rec = a.models["recognition"]
    v = rec.get_feat(cv2.resize(bgr, (112, 112))).ravel().astype(np.float32)
    return v / (np.linalg.norm(v) + 1e-9), None


def yaw_proxy(kps):
    """Simple side-turn estimate from 5 keypoints (eyes and nose): 0 = frontal."""
    if kps is None:
        return None
    le, re, nose = kps[0], kps[1], kps[2]
    mid = (le[0] + re[0]) / 2
    return float((nose[0] - mid) / (abs(re[0] - le[0]) + 1e-6))
