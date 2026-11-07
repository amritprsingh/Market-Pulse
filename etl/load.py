"""Load steps. All writes are upserts, so every load is safe to re-run."""

from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, MetaData, Table, func, literal_column, or_, text
from sqlalchemy.dialects.postgresql import insert

from etl.db import get_engine

SEED_PATH = Path(__file__).resolve().parent.parent / "seeds" / "tickers.csv"
PRICE_FIELDS = ["open", "high", "low", "close", "adj_close", "volume"]
CHUNK_SIZE = 1000

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


def upsert_prices(
    df: pd.DataFrame, engine: Engine | None = None, chunk_size: int = CHUNK_SIZE
) -> tuple[int, int]:
    """Upsert prices in one transaction and return (rows_inserted, rows_updated).

    Rows whose values already match are left alone, so rows_updated and loaded_at
    reflect real revisions rather than every row in the catch-up window.
    """
    engine = engine or get_engine()
    table = Table("fact_daily_price", MetaData(), autoload_with=engine)
    # Null must be None here: a float NaN would be stored as a numeric 'NaN'.
    rows = df.astype(object).where(df.notna(), None).to_dict("records")

    rows_inserted = rows_updated = 0
    with engine.begin() as conn:
        for offset in range(0, len(rows), chunk_size):
            statement = insert(table).values(rows[offset : offset + chunk_size])
            statement = statement.on_conflict_do_update(
                index_elements=["ticker", "trade_date"],
                set_={
                    **{field: statement.excluded[field] for field in PRICE_FIELDS},
                    "loaded_at": func.now(),
                },
                where=or_(
                    *(
                        table.c[field].is_distinct_from(statement.excluded[field])
                        for field in PRICE_FIELDS
                    )
                ),
            ).returning(literal_column("xmax = 0").label("inserted"))
            # xmax is 0 only on rows this statement newly inserted.
            for inserted in conn.execute(statement).scalars():
                rows_inserted += inserted
                rows_updated += not inserted
    return rows_inserted, rows_updated
