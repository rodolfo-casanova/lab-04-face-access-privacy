"""Access device: all biometric processing happens here, at the edge.

Endpoints:
  POST /enroll  -> requires consent. Returns the credential with the encrypted template
  POST /verify  -> channel integrity -> liveness -> ArcFace -> 1:1 verification
  POST /pin     -> fallback route without biometrics (delegated to the backend)
"""
import json
import os

import cv2
import numpy as np
import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from biometrics import face, pad, security

BACKEND = os.getenv("BACKEND_URL", "http://bio-backend:8002")
THRESHOLD = float(os.getenv("VERIFY_THRESHOLD", "0.35"))
app = FastAPI(title="Biometric access device (edge)")


def decode(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "invalid image")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def report(employee_id, decision, reason):
    try:
        requests.post(f"{BACKEND}/events", json={"employee_id": employee_id, "method": "biometric",
                                                 "decision": decision, "reason": reason}, timeout=5)
    except Exception:  # noqa: BLE001
        pass


@app.on_event("startup")
def warmup():
    face.app()


@app.post("/enroll")
async def enroll(employee_id: str = Form(...), consent: bool = Form(...), image: UploadFile = File(...)):
    if not consent:
        raise HTTPException(403, "No enrollment without explicit consent. Use the fallback route (credential + PIN).")
    img = decode(await image.read())
    vec, _ = face.embed(img)
    del img  # the image is not kept
    token = security.encrypt_template(employee_id, vec.tobytes())
    return {"credential": security.credential(employee_id, token)}


@app.post("/verify")
async def verify(credential: str = Form(...), signature: str = Form(...), image: UploadFile = File(...),
                 thermal: UploadFile = File(...)):
    cred = json.loads(credential)
    eid = cred["employee_id"]
    data = await image.read()
    # 1. Channel integrity: an injected frame does not carry a valid sensor signature
    if not security.verify_frame(data, signature):
        report(eid, "denied", "invalid sensor signature (possible injection)")
        return {"allowed": False, "reason": "invalid sensor signature (possible injection)"}
    # 2. Liveness detection with the thermal channel
    thermal_map = np.frombuffer(await thermal.read(), np.float32).reshape(pad.SIZE, pad.SIZE)
    live, why = pad.is_live(thermal_map)
    if not live:
        report(eid, "denied", f"PAD: {why}")
        return {"allowed": False, "reason": f"PAD: {why}"}
    # 3. Embedding and 1:1 verification against the template on the credential
    img = decode(data)
    vec, _ = face.embed(img)
    del img, data  # immediate discard of the image
    tpl = np.frombuffer(security.decrypt_template(eid, cred["template"]), np.float32)
    score = float(vec @ tpl)
    ok = score >= THRESHOLD
    report(eid, "allowed" if ok else "denied", f"1:1 verification (threshold {THRESHOLD})")
    return {"allowed": ok, "score": score, "threshold": THRESHOLD, "reason": "match" if ok else "no match"}


@app.post("/pin")
def pin(employee_id: str = Form(...), pin: str = Form(...)):
    r = requests.post(f"{BACKEND}/pin/verify", json={"employee_id": employee_id, "pin": pin}, timeout=10)
    return r.json()


@app.get("/health")
def health():
    return {"ok": True, "threshold": THRESHOLD}
