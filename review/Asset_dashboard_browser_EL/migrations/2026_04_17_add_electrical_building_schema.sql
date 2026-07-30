BEGIN;

CREATE TABLE IF NOT EXISTS electrical_building_schema (
    row_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    "Hierarchy"             TEXT NOT NULL,
    "Supply From"           TEXT,
    "Equipment ID"          TEXT NOT NULL,
    "Building"              TEXT NOT NULL,
    "Amperage Rating"       TEXT,
    "Amperage Rating (UoM)" TEXT,
    "Power Rating"          TEXT,
    "Power Rating (UoM)"    TEXT,
    "Voltage Rating"        TEXT,
    "Voltage Rating (UoM)"  TEXT,
    "ambigous"              TEXT,
    new_draw                TEXT DEFAULT 'TRUE',
    source_pdf              TEXT DEFAULT '',
    created_at              TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_ebs_building_newdraw
    ON electrical_building_schema(Building, new_draw);

CREATE INDEX IF NOT EXISTS idx_ebs_equipment_id
    ON electrical_building_schema("Equipment ID");

COMMIT;
