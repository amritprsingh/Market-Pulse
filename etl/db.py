"""Database connection and schema setup. Run `python -m etl.db` to apply the schema."""

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


def get_engine() -> Engine:
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set; add it to .env")
    return create_engine(url)


def apply_schema(engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql(SCHEMA_PATH.read_text())


if __name__ == "__main__":
    apply_schema()
    print(f"Applied {SCHEMA_PATH.name}")
