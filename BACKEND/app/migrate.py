"""Tiny additive migration helper.

`Base.metadata.create_all()` creates missing tables but never adds columns to existing ones,
so databases created before a model gained a column would break. This adds any missing
nullable columns (works on both SQLite and PostgreSQL). For anything more complex, use Alembic.
"""
import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

# table -> {column: SQL type}. Columns must be nullable or have a DEFAULT.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "donor_profiles": {
        "latitude": "FLOAT",
        "longitude": "FLOAT",
        "verified": "BOOLEAN NOT NULL DEFAULT FALSE",
        "suspended": "BOOLEAN NOT NULL DEFAULT FALSE",
    },
    "hospital_profiles": {"latitude": "FLOAT", "longitude": "FLOAT"},
    "requests": {"latitude": "FLOAT", "longitude": "FLOAT"},
}


def ensure_columns(engine: Engine) -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, columns in ADDED_COLUMNS.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    log.info("migrate: adding %s.%s", table, name)
                    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {ddl}'))
