"""Load steps. All writes are upserts, so every load is safe to re-run."""

from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, text

from etl.db import get_engine

SEED_PATH = Path(__file__).resolve().parent.parent / "seeds" / "tickers.csv"

UPSERT_SECURITY = text("""
    INSERT INTO dim_security (ticker, company_name, sector, industry)
    VALUES (:ticker, :company_name, :sector, :industry)
    ON CONFLICT (ticker) DO UPDATE SET
        company_name = EXCLUDED.company_name,
        sector = EXCLUDED.sector,
        industry = EXCLUDED.industry
""")


def load_dim_security(engine: Engine | None = None) -> int:
    """Upsert seeds/tickers.csv into dim_security and return the rows processed."""
    engine = engine or get_engine()
    securities = pd.read_csv(SEED_PATH, dtype=str, keep_default_na=False)
    rows = securities.to_dict("records")
    with engine.begin() as conn:
        conn.execute(UPSERT_SECURITY, rows)
    return len(rows)
