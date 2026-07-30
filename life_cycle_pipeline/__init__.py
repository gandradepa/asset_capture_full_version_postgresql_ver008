"""Life Cycle data pipeline (relocated from the ver005 project root).

Contains the Excel -> PostgreSQL interface used by the Life Cycle Assessment
blueprint's "Update Database" button:

* ``track_assets.build_life_cycle`` - build the life_cycle DataFrame from the
  "UBC - Asset Basic Info.xlsx" workbook (or an uploaded file-like object).
* ``load_life_cycle.load`` - (re)load that DataFrame into the ``life_cycle``
  table plus the ``space_floor`` reference table and FK.

Both modules read their database target from the ``LIFE_CYCLE_SA_DSN``
environment variable (a SQLAlchemy URL); the blueprint derives it from the
portal's ``QR_PG_DSN`` at import time. They default to the local sandbox.
"""
