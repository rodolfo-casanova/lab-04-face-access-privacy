"""Liveness detection (PAD) with a SIMULATED thermal channel.

There is no real infrared camera in the lab. Synthetic thermal maps are generated for genuine
presentations and for attacks (printed photo, screen, 3D mask), and a simple rule is applied on
the face region. It demonstrates the flow and the APCER / BPCER metrics.
"""
import numpy as np

SIZE = 64


def thermal_map(kind: str, rng: np.random.Generator) -> np.ndarray:
    yy, xx = np.mgrid[:SIZE, :SIZE]
    ambient = rng.uniform(21, 25)
    base = np.full((SIZE, SIZE), ambient, np.float32)
    face = np.exp(-(((xx - 32) / 14.0) ** 2 + ((yy - 30) / 18.0) ** 2))
    if kind == "genuine":
        peak = rng.uniform(33.5, 36.5)
        t = base + face * (peak - ambient)
        t[24:30, 20:44] += rng.uniform(0.5, 1.2)  # warmer periocular zone
    elif kind == "photo":
        t = base + rng.uniform(0, 1.0)
    elif kind == "screen":
        t = base + rng.uniform(6, 9)  # a screen emits heat uniformly
    elif kind == "mask":
        warm = rng.uniform(26, 34)  # heated mask: face shape, without the periocular pattern
        t = base + face * (warm - ambient)
        if rng.random() < 0.15:  # high-quality mask with localized heating
            t[24:30, 20:44] += rng.uniform(0.2, 0.7)
    else:
        raise ValueError(kind)
    return (t + rng.normal(0, 0.3, t.shape)).astype(np.float32)


def is_live(t: np.ndarray) -> tuple[bool, str]:
    center = float(t[18:44, 20:44].mean()); edge = float(np.r_[t[:, :6].ravel(), t[:, -6:].ravel()].mean())
    peri = float(t[24:30, 20:44].mean() - t[30:36, 20:44].mean())
    if center - edge < 4.0:
        return False, "no facial thermal gradient (photo or screen)"
    if not (29.5 <= center <= 38.0):
        return False, "facial temperature out of range"
    if peri < 0.3:
        return False, "periocular thermal pattern missing (possible mask)"
    return True, "live"
