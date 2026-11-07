"""Database connection and setup. Run `python -m etl.db` to apply the schema and views."""

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
SCHEMA_PATH = SQL_DIR / "schema.sql"
VIEWS_PATH = SQL_DIR / "views.sql"


def get_engine() -> Engine:
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set; add it to .env")
    return create_engine(url)


def apply_sql_file(path: Path, engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql(path.read_text())


def apply_schema(engine: Engine | None = None) -> None:
    apply_sql_file(SCHEMA_PATH, engine)


def apply_views(engine: Engine | None = None) -> None:
    apply_sql_file(VIEWS_PATH, engine)


if __name__ == "__main__":
    apply_schema()
    print(f"Applied {SCHEMA_PATH.name}")
    apply_views()
    print(f"Applied {VIEWS_PATH.name}")
