"""
load_life_cycle.py

Load the ``life_cycle`` DataFrame (built in track_assets.py) into the
PostgreSQL database ``qr_code_db_sandbox`` as a table called ``life_cycle``.

Every column is stored as TEXT because the DataFrame is entirely string-typed
(the date column is an MM/DD/YYYY string); NaN values become SQL NULL.

It also (re)builds a deduplicated ``space_floor`` reference table from SpaceUID
and adds an enforced composite foreign key
``life_cycle (Property Code, Space Number) -> space_floor`` so the relationship
shows up in the DBeaver diagram. This is rebuilt on every load because
``to_sql(if_exists="replace")`` drops life_cycle (and its FK) each run.
"""

import os
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.types import Float, Integer, Text

# Database target as a SQLAlchemy URL. Read from LIFE_CYCLE_SA_DSN so the
# blueprint can point the loader at the same database the portal uses
# (derived from QR_PG_DSN in production). Defaults to the local dev sandbox
# (trust auth on localhost, user "postgres").
DB_URL = os.environ.get(
    "LIFE_CYCLE_SA_DSN",
    "postgresql+psycopg2://postgres@localhost:5432/qr_code_db_sandbox",
)
TABLE_NAME = "life_cycle"
SPACE_REF_TABLE = "space_floor"
FK_NAME = "life_cycle_space_floor_fkey"

# Tiny key/value table that survives the to_sql(if_exists="replace") on
# life_cycle (which only drops life_cycle). We stamp it with the load time so
# the dashboard can show when the data was last updated.
META_TABLE = "life_cycle_meta"

# Derived age columns: "years" is a float (2 decimals), "months" an integer;
# everything else is TEXT.
FLOAT_COLUMNS = ["years"]
INT_COLUMNS = ["months"]


def _build_space_floor_and_fk(conn) -> None:
    """(Re)build the deduped space_floor reference table from SpaceUID and add a
    composite FK from life_cycle to it.

    SpaceUID's (Property.Property code, Space number) key is not unique, so
    DISTINCT ON collapses it to one Floor Name per key, letting space_floor have
    a real PRIMARY KEY. The FK uses MATCH SIMPLE (default), so life_cycle rows
    with a NULL Space Number are not enforced (the 61 unmatched rows)."""
    conn.execute(text(f'DROP TABLE IF EXISTS "{SPACE_REF_TABLE}" CASCADE'))
    conn.execute(text(
        f'CREATE TABLE "{SPACE_REF_TABLE}" ('
        ' "Property Code" text NOT NULL,'
        ' "Space Number"  text NOT NULL,'
        ' "Floor Name"    text,'
        ' PRIMARY KEY ("Property Code", "Space Number"))'
    ))
    conn.execute(text(
        f'INSERT INTO "{SPACE_REF_TABLE}" ("Property Code", "Space Number", "Floor Name") '
        'SELECT DISTINCT ON ("Property.Property code", "Space number") '
        '       "Property.Property code", "Space number", "Floor Name" '
        'FROM "SpaceUID" '
        'WHERE "Property.Property code" IS NOT NULL AND "Space number" IS NOT NULL '
        'ORDER BY "Property.Property code", "Space number", "Floor Name"'
    ))
    conn.execute(text(
        f'ALTER TABLE "{TABLE_NAME}" ADD CONSTRAINT "{FK_NAME}" '
        'FOREIGN KEY ("Property Code", "Space Number") '
        f'REFERENCES "{SPACE_REF_TABLE}" ("Property Code", "Space Number")'
    ))


def _stamp_last_loaded(conn) -> str:
    """Record the current local date/time as the data's "last loaded" stamp and
    return it (``YYYY-MM-DD HH:MM:SS``). Stored in a standalone key/value table
    so it persists across the life_cycle rebuild."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(text(
        f'CREATE TABLE IF NOT EXISTS "{META_TABLE}" '
        '("key" text PRIMARY KEY, "value" text)'
    ))
    conn.execute(
        text(
            f'INSERT INTO "{META_TABLE}" ("key", "value") VALUES (:k, :v) '
            'ON CONFLICT ("key") DO UPDATE SET "value" = EXCLUDED."value"'
        ),
        {"k": "last_loaded", "v": ts},
    )
    return ts


def load(df=None, source=None) -> dict:
    """Load the ``life_cycle`` DataFrame into PostgreSQL and return a summary.

    Pass an already-built ``df`` to load it directly; otherwise the frame is
    built from ``source`` (a path or file-like Excel workbook, defaulting to the
    workbook next to track_assets.py) via ``track_assets.build_life_cycle``.
    The returned dict reports what landed: row/column counts, the reference
    table size and whether the FK is in place.
    """
    if df is None:
        from track_assets import build_life_cycle
        df = build_life_cycle(source)

    engine = create_engine(DB_URL)

    # Strings -> TEXT; "years" -> float; "months" -> integer.
    def _sql_type(col: str):
        if col in FLOAT_COLUMNS:
            return Float()
        if col in INT_COLUMNS:
            return Integer()
        return Text()

    dtype = {col: _sql_type(col) for col in df.columns}

    with engine.begin() as conn:
        df.to_sql(
            TABLE_NAME,
            con=conn,
            if_exists="replace",  # (re)create a fresh table each run
            index=False,
            dtype=dtype,
        )

    # Build the reference table and the enforced FK (in its own transaction),
    # and stamp the load time so the dashboard can show "last updated".
    with engine.begin() as conn:
        _build_space_floor_and_fk(conn)
        last_loaded = _stamp_last_loaded(conn)

    # Report what landed in the database.
    with engine.connect() as conn:
        rows = conn.execute(
            text(f'SELECT count(*) FROM "{TABLE_NAME}"')
        ).scalar_one()
        cols = conn.execute(
            text(
                "SELECT column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_name = :t ORDER BY ordinal_position"
            ),
            {"t": TABLE_NAME},
        ).all()
        ref_rows = conn.execute(
            text(f'SELECT count(*) FROM "{SPACE_REF_TABLE}"')
        ).scalar_one()
        fk_ok = conn.execute(
            text("SELECT 1 FROM pg_constraint WHERE conname = :n"),
            {"n": FK_NAME},
        ).first() is not None

    print(f'Loaded "{TABLE_NAME}": {rows} rows, {len(cols)} columns')
    for name, dt in cols:
        print(f"  {name}: {dt}")
    print(f'Reference table "{SPACE_REF_TABLE}": {ref_rows} rows; FK "{FK_NAME}" present: {fk_ok}')
    print(f"Last loaded: {last_loaded}")

    engine.dispose()

    return {
        "rows": rows,
        "columns": len(cols),
        "ref_rows": ref_rows,
        "fk_ok": fk_ok,
        "last_loaded": last_loaded,
    }


if __name__ == "__main__":
    load()
