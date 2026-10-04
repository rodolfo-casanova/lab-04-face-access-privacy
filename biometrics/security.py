"""Channel integrity (simulated sensor attestation) and template encryption."""
import base64
import hashlib
import hmac
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SENSOR_KEY = os.getenv("SENSOR_KEY", "demo-sensor-key-change-me").encode()  # key of the attested sensor
TEMPLATE_KEY = hashlib.sha256(os.getenv("TEMPLATE_KEY", "demo-template-key-change-me").encode()).digest()


def sign_frame(data: bytes, key: bytes = SENSOR_KEY) -> str:
    """The camera firmware would do this: sign every frame it captures."""
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def verify_frame(data: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign_frame(data), signature or "")


def encrypt_template(employee_id: str, vector: bytes) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(TEMPLATE_KEY).encrypt(nonce, vector, employee_id.encode())  # the ID is bound as associated data
    return base64.b64encode(nonce + ct).decode()


def decrypt_template(employee_id: str, token: str) -> bytes:
    raw = base64.b64decode(token)
    return AESGCM(TEMPLATE_KEY).decrypt(raw[:12], raw[12:], employee_id.encode())


def credential(employee_id: str, template_token: str) -> str:
    """The employee's "credential": their encrypted template travels with them, not on a central server."""
    return json.dumps({"employee_id": employee_id, "template": template_token})
