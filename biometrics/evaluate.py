"""Biometric access evaluation (calls the services over HTTP).

Measures: FMR / FNMR (global and per subgroup), threshold calibration, APCER / BPCER per attack type,
injection rejection, PIN fallback route and a backend privacy check.

Data: LFW (Labeled Faces in the Wild, academic use) or, if present, the folder data/faces/<person>/*.jpg
with photos of people who gave their consent.
"""
import json
import os
import time

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import requests  # noqa: E402

from common.utils import CACHE_DIR, ROOT, log, results_dir, save_json  # noqa: E402
from biometrics import face, pad, security  # noqa: E402

DEVICE = os.getenv("DEVICE_URL", "http://bio-device:8001")
BACKEND = os.getenv("BACKEND_URL", "http://bio-backend:8002")
N_ID = int(os.getenv("BIO_IDENTITIES", "30"))
OUT = results_dir("biometrics")


def wait(url, timeout=1800):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if requests.get(f"{url}/health", timeout=3).ok:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3)
    raise RuntimeError(f"Service unavailable: {url}")


def load_people():
    own = ROOT / "data" / "faces"
    people = {}
    dirs = sorted(p for p in own.iterdir() if p.is_dir()) if own.exists() else []
    if dirs:
        for d in dirs:
            imgs = [cv2.cvtColor(cv2.imread(str(f)), cv2.COLOR_BGR2RGB) for f in sorted(d.glob("*")) if f.suffix.lower() in (".jpg", ".jpeg", ".png")]
            if len(imgs) >= 2:
                people[d.name] = imgs
        log(f"Using own photos: {len(people)} people")
        return people, "own photos with consent"
    from sklearn.datasets import fetch_lfw_people

    log("Downloading/loading LFW (the first run can take several minutes)...")
    lfw = fetch_lfw_people(min_faces_per_person=6, resize=1.0, color=True, data_home=str(CACHE_DIR / "sklearn"))
    imgs = lfw.images
    if imgs.max() <= 1.0:
        imgs = imgs * 255.0
    imgs = imgs.astype(np.uint8)
    rng = np.random.default_rng(0)
    ids = rng.permutation(np.unique(lfw.target))[:N_ID]
    for i in ids:
        idx = np.where(lfw.target == i)[0][:6]
        people[lfw.target_names[i].replace(" ", "_")] = [imgs[j] for j in idx]
    return people, "LFW (Labeled Faces in the Wild)"


def enc(img):
    return cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))[1].tobytes()


def verify(cred, img, thermal, key=security.SENSOR_KEY):
    data = enc(img)
    r = requests.post(f"{DEVICE}/verify", data={"credential": cred, "signature": security.sign_frame(data, key)},
                      files={"image": ("f.png", data), "thermal": ("t.bin", thermal.tobytes())}, timeout=60)
    return r.json()


def main():
    wait(BACKEND); wait(DEVICE)
    people, source = load_people()
    names = list(people)
    rng = np.random.default_rng(1)
    creds = {}

    # ---------------------------------------------------------- Enrollment (with consent) and opt-out
    optout = names[-1]
    for n in names:
        r = requests.post(f"{DEVICE}/enroll", data={"employee_id": n, "consent": str(n != optout).lower()},
                          files={"image": ("e.png", enc(people[n][0]))}, timeout=60)
        if r.status_code == 200:
            creds[n] = r.json()["credential"]
    optout_ok = optout not in creds
    requests.post(f"{BACKEND}/pin/register", json={"employee_id": optout, "pin": "4821"}, timeout=10)
    pin_good = requests.post(f"{DEVICE}/pin", data={"employee_id": optout, "pin": "4821"}, timeout=10).json()
    pin_bad = requests.post(f"{DEVICE}/pin", data={"employee_id": optout, "pin": "0000"}, timeout=10).json()
    log(f"Opt-out rejected at enrollment: {optout_ok} | correct PIN: {pin_good} | wrong PIN: {pin_bad}")

    # ---------------------------------------------------------- Genuine users and impostors (valid channel, live subject)
    enrolled = [n for n in names if n in creds]
    gen, imp = [], []
    for n in enrolled:
        for img in people[n][1:4]:
            r = verify(creds[n], img, pad.thermal_map("genuine", rng))
            _, kps = face.embed(img)
            gen.append({"person": n, "score": r.get("score"), "allowed": r["allowed"], "reason": r["reason"],
                        "yaw": face.yaw_proxy(kps), "luminance": float(cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).mean())})
        others = [m for m in enrolled if m != n]
        for m in rng.choice(others, size=min(3, len(others)), replace=False):
            r = verify(creds[n], people[m][1], pad.thermal_map("genuine", rng))
            imp.append({"target": n, "attacker": m, "score": r.get("score"), "allowed": r["allowed"]})
    thr = requests.get(f"{DEVICE}/health", timeout=5).json()["threshold"]
    g_live = [x for x in gen if x["score"] is not None]
    gs = np.array([x["score"] for x in g_live]); is_ = np.array([x["score"] for x in imp if x["score"] is not None])
    fmr = float(np.mean(is_ >= thr)); fnmr = float(np.mean(gs < thr))
    thr_cal = float(np.quantile(is_, 0.99)) if len(is_) else thr  # threshold that leaves FMR <= 1 %
    log(f"Threshold {thr}: FMR={fmr:.3f} FNMR={fnmr:.3f} | calibrated threshold (FMR<=1 %)={thr_cal:.3f} -> FNMR={np.mean(gs < thr_cal):.3f}")

    # ---------------------------------------------------------- Subgroups (bias audit: method)
    groups = {}
    lum_med = float(np.median([x["luminance"] for x in g_live])) if g_live else 0
    for x in g_live:
        keys = []
        if x["yaw"] is not None:
            keys.append("frontal pose" if abs(x["yaw"]) < 0.15 else "side pose")
        keys.append("low lighting" if x["luminance"] < lum_med else "normal lighting")
        for k in keys:
            groups.setdefault(k, []).append(x["score"] < thr)
    fnmr_groups = {k: {"FNMR": float(np.mean(v)), "n": len(v)} for k, v in groups.items()}
    vals = [v["FNMR"] for v in fnmr_groups.values() if v["n"] >= 5]
    ratio = (max(vals) / max(min(vals), 1e-3)) if len(vals) >= 2 else None

    # ---------------------------------------------------------- PAD: presentation attacks
    attacks = {}
    probes = [(n, people[n][1]) for n in enrolled][:20]
    for kind in ("photo", "screen", "mask"):
        acc = [verify(creds[n], img, pad.thermal_map(kind, rng))["allowed"] for n, img in probes]
        attacks[kind] = {"APCER": float(np.mean(acc)), "attempts": len(acc)}
    bpcer = float(np.mean([x["reason"].startswith("PAD") for x in gen])) if gen else None
    log(f"APCER per attack: {attacks} | BPCER={bpcer}")

    # ---------------------------------------------------------- Injection (frame without a valid sensor signature)
    inj = [verify(creds[n], img, pad.thermal_map("genuine", rng), key=b"attacker-key")["allowed"] for n, img in probes]
    inj_rate = float(np.mean(inj))
    log(f"Injection attacks accepted: {inj_rate:.1%}")

    # ---------------------------------------------------------- Backend privacy
    schema = requests.get(f"{BACKEND}/schema", timeout=5).json()
    forbidden = ("image", "embedding", "vector", "template", "photo", "face")
    cols = [c for t in schema.values() for c in t]
    privacy_ok = not any(any(f in c.lower() for f in forbidden) for c in cols)
    events = requests.get(f"{BACKEND}/events?limit=5", timeout=5).json()

    metrics = {
        "data_source": source, "identities": len(names), "enrolled": len(enrolled),
        "device_threshold": thr, "FMR": fmr, "FNMR": fnmr, "calibrated_threshold_FMR_1pct": thr_cal,
        "FNMR_at_calibrated_threshold": float(np.mean(gs < thr_cal)) if len(gs) else None,
        "FNMR_by_subgroup": fnmr_groups, "FNMR_max_min_ratio": ratio,
        "PAD": {"APCER_by_attack": attacks, "BPCER": bpcer, "note": "simulated thermal channel"},
        "injection_accepted": inj_rate,
        "fallback_route": {"enrollment_without_consent_rejected": optout_ok, "correct_pin": pin_good, "wrong_pin": pin_bad},
        "backend_privacy": {"schema": schema, "no_biometric_data": privacy_ok, "latest_events": events},
        "credential_example": json.loads(next(iter(creds.values())))["template"][:60] + "..." if creds else None,
        "bias_note": ("The subgroups (pose, lighting) demonstrate the audit METHOD. A real demographic audit "
                      "requires data with self-declared attributes and consent. Sensitive attributes are never inferred."),
    }
    save_json(metrics, OUT / "metrics.json")

    fig, ax = plt.subplots(1, 3, figsize=(15, 4))
    ax[0].hist(gs, bins=20, alpha=0.7, label="Genuine"); ax[0].hist(is_, bins=20, alpha=0.7, label="Impostors")
    ax[0].axvline(thr, color="k", ls="--", label=f"Threshold {thr}"); ax[0].axvline(thr_cal, color="r", ls=":", label=f"Calibrated {thr_cal:.2f}")
    ax[0].set_title("1:1 verification scores (ArcFace)"); ax[0].legend(fontsize=8)
    ax[1].bar(list(fnmr_groups), [v["FNMR"] for v in fnmr_groups.values()], color="#d6b656"); ax[1].set_title("FNMR by subgroup")
    ax[1].tick_params(axis="x", rotation=20)
    ax[2].bar([f"APCER {k}" for k in attacks] + ["BPCER", "Injection"], [v["APCER"] for v in attacks.values()] + [bpcer or 0, inj_rate], color="#b85450")
    ax[2].set_title("Liveness detection and channel integrity"); ax[2].tick_params(axis="x", rotation=20)
    plt.tight_layout(); plt.savefig(OUT / "biometrics.png", dpi=110); plt.close()
    log(f"Done. Results in {OUT}")


if __name__ == "__main__":
    main()
