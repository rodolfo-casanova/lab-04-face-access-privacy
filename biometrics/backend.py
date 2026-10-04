"""Access backend: receives ONLY decisions. Never images or face vectors."""
import hashlib
import os
import sqlite3
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from common.utils import results_dir

DB = results_dir("biometrics") / "backend.db"
app = FastAPI(title="Access backend (no biometrics)")


def db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
                   employee_id TEXT, method TEXT, decision TEXT, reason TEXT)""")
    con.execute("CREATE TABLE IF NOT EXISTS pins (employee_id TEXT PRIMARY KEY, salt TEXT, hash TEXT)")
    return con


class Event(BaseModel):
    employee_id: str
    method: str  # biometric | pin
    decision: str  # allowed | denied
    reason: str


class Pin(BaseModel):
    employee_id: str
    pin: str


@app.post("/events")
def add_event(e: Event):
    with db() as con:
        con.execute("INSERT INTO events(ts, employee_id, method, decision, reason) VALUES (?,?,?,?,?)",
                    (time.time(), e.employee_id, e.method, e.decision, e.reason))
    return {"ok": True}


@app.get("/events")
def list_events(limit: int = 50):
    with db() as con:
        rows = con.execute("SELECT id, ts, employee_id, method, decision, reason FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(zip(["id", "ts", "employee_id", "method", "decision", "reason"], r)) for r in rows]


@app.get("/schema")
def schema():
    """Privacy proof: shows every column the backend stores."""
    with db() as con:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: [c[1] for c in con.execute(f"PRAGMA table_info({t})")] for t in tables}


@app.post("/pin/register")
def pin_register(p: Pin):
    salt = os.urandom(8).hex()
    h = hashlib.pbkdf2_hmac("sha256", p.pin.encode(), bytes.fromhex(salt), 100_000).hex()
    with db() as con:
        con.execute("INSERT OR REPLACE INTO pins VALUES (?,?,?)", (p.employee_id, salt, h))
    return {"ok": True}


@app.post("/pin/verify")
def pin_verify(p: Pin):
    with db() as con:
        row = con.execute("SELECT salt, hash FROM pins WHERE employee_id=?", (p.employee_id,)).fetchone()
    if not row:
        raise HTTPException(404, "employee has no registered PIN")
    ok = hashlib.pbkdf2_hmac("sha256", p.pin.encode(), bytes.fromhex(row[0]), 100_000).hex() == row[1]
    add_event(Event(employee_id=p.employee_id, method="pin", decision="allowed" if ok else "denied",
                    reason="correct PIN" if ok else "wrong PIN"))
    return {"allowed": ok}


@app.get("/health")
def health():
    return {"ok": True}
