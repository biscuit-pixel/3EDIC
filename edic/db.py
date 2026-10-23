"""SQLite storage for one project."""
import json
import sqlite3
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
    taken REAL, width INTEGER, height INTEGER,
    processed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS photos_taken ON photos(taken);
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY, photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL, box BLOB, kpts BLOB, numbers TEXT, bibs TEXT,
    colour BLOB, face BLOB, face_arc BLOB, face_score REAL,
    UNIQUE(photo_id, idx)
);
CREATE TABLE IF NOT EXISTS competitors (
    id INTEGER PRIMARY KEY, number INTEGER UNIQUE NOT NULL, name TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS assignments (
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    competitor_id INTEGER NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
    p REAL NOT NULL DEFAULT 0,               -- model probability
    num_conf REAL NOT NULL DEFAULT 0,        -- bib evidence (OCR / VLM) for this pair
    person_idx INTEGER,                      -- which person in the photo (when known)
    status TEXT NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested','accepted','rejected')),
    source TEXT NOT NULL DEFAULT 'model',    -- model | user
    updated REAL,
    PRIMARY KEY (photo_id, competitor_id)
);
CREATE INDEX IF NOT EXISTS assign_comp ON assignments(competitor_id, status);
CREATE TABLE IF NOT EXISTS decision_log (
    id INTEGER PRIMARY KEY, at REAL NOT NULL, photo_id INTEGER NOT NULL, competitor_id INTEGER NOT NULL,
    action TEXT NOT NULL, existed INTEGER NOT NULL, prev_status TEXT, prev_source TEXT, prev_person INTEGER,
    prev_p REAL, prev_num_conf REAL, undone INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bib_examples (
    id INTEGER PRIMARY KEY, photo_id INTEGER NOT NULL, person_idx INTEGER NOT NULL,
    number INTEGER, rel_box TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vlm_reads (
    photo_id INTEGER NOT NULL, person_idx INTEGER NOT NULL, provider TEXT NOT NULL,
    number INTEGER, confidence REAL, PRIMARY KEY (photo_id, person_idx, provider)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    cols = {r["name"] for r in con.execute("PRAGMA table_info(assignments)")}
    if "evidence" not in cols:                    # projects created before evidence was stored
        con.execute("ALTER TABLE assignments ADD COLUMN evidence TEXT")
    return con


def arr(a):
    return None if a is None else np.asarray(a, np.float32).tobytes()


def unarr(b, shape=None):
    if b is None:
        return None
    a = np.frombuffer(b, np.float32).copy()
    return a.reshape(shape) if shape else a


def jdump(o):
    return json.dumps(o, separators=(",", ":"))
