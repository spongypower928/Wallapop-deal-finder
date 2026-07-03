"""SQLite storage. One row per listing, with first/last-seen history so we can
tell new listings from old ones and keep price-change history later."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "motodeals.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id          TEXT PRIMARY KEY,   -- "{source}:{listing_id}"
    source      TEXT NOT NULL,      -- 'wallapop' | 'milanuncios'
    model       TEXT NOT NULL,      -- search.name, e.g. 'yamaha-mt-07'
    title       TEXT,
    price       REAL,
    url         TEXT,
    location    TEXT,
    -- structured fields we parse out for the price model (may be NULL early on)
    year        INTEGER,
    mileage_km  INTEGER,
    raw         TEXT,               -- full JSON blob from the source
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    -- filled by the local-LLM extraction stage (motodeals extract)
    condition   TEXT,               -- 'new' | 'used' | 'needs_work'
    has_itv     INTEGER,            -- 1 / 0 / NULL(unknown)
    red_flags   TEXT,               -- JSON array of English strings
    extracted_at TEXT
);
"""

# Columns added after the first release; ensure they exist on older DB files.
_EXTRA_COLUMNS = {
    "condition": "TEXT",
    "has_itv": "INTEGER",
    "red_flags": "TEXT",
    "extracted_at": "TEXT",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(listings)")}
    for col, coltype in _EXTRA_COLUMNS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {coltype}")
    conn.commit()


def update_extraction(conn: sqlite3.Connection, listing_id: str, data: dict) -> None:
    """Store one listing's LLM-extracted fields. `data` is the validated dict
    from extract.extract(). LLM mileage/year overwrite the weaker regex guess."""
    conn.execute(
        """
        UPDATE listings SET
            mileage_km   = COALESCE(:mileage, mileage_km),
            condition    = :condition,
            has_itv      = :has_itv,
            red_flags    = :red_flags,
            extracted_at = :ts
        WHERE id = :id
        """,
        {
            "id": listing_id,
            "mileage": data.get("mileage"),
            "condition": data.get("condition"),
            "has_itv": None if data.get("has_itv") is None else int(data["has_itv"]),
            "red_flags": json.dumps(data.get("red_flags", []), ensure_ascii=False),
            "ts": now_iso(),
        },
    )


def upsert(conn: sqlite3.Connection, listing: dict) -> bool:
    """Insert a listing or update last_seen/price if we've seen it before.
    Returns True if this listing is new."""
    ts = now_iso()
    row = conn.execute("SELECT id FROM listings WHERE id = ?", (listing["id"],)).fetchone()
    is_new = row is None
    conn.execute(
        """
        INSERT INTO listings (id, source, model, title, price, url, location,
                              year, mileage_km, raw, first_seen, last_seen)
        VALUES (:id, :source, :model, :title, :price, :url, :location,
                :year, :mileage_km, :raw, :ts, :ts)
        ON CONFLICT(id) DO UPDATE SET
            price      = excluded.price,
            title      = excluded.title,
            year       = COALESCE(excluded.year, listings.year),
            mileage_km = COALESCE(excluded.mileage_km, listings.mileage_km),
            raw        = excluded.raw,
            last_seen  = excluded.last_seen
        """,
        {
            "id": listing["id"],
            "source": listing["source"],
            "model": listing["model"],
            "title": listing.get("title"),
            "price": listing.get("price"),
            "url": listing.get("url"),
            "location": listing.get("location"),
            "year": listing.get("year"),
            "mileage_km": listing.get("mileage_km"),
            "raw": json.dumps(listing.get("raw", {}), ensure_ascii=False),
            "ts": ts,
        },
    )
    return is_new
