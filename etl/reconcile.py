"""Compare fact_daily_price with a fresh yfinance download of the same window."""

import logging
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import Engine, text

from etl.db import get_engine
from etl.extract import fetch_prices, get_tickers
from etl.transform import transform

CLOSE_TOLERANCE = 0.0001  # 0.01% relative
ADJ_CLOSE_TOLERANCE = 1e-4  # 0.01% relative
VOLUME_TOLERANCE = 0.01  # 1% relative
SOURCE_NAME = "yfinance"
KEY_COLUMNS = ["ticker", "trade_date"]

log = logging.getLogger(__name__)

STORED_PRICES = text("""
    SELECT ticker, trade_date, close, adj_close, volume
    FROM fact_daily_price
    WHERE trade_date BETWEEN :start_date AND :end_date
""")

SAVE_RESULT = text("""
    INSERT INTO reconciliation_result
        (run_id, source_name, rows_compared, rows_mismatched, discrepancy_pct)
    VALUES (:run_id, :source_name, :rows_compared, :rows_mismatched, :discrepancy_pct)
    ON CONFLICT (run_id) DO UPDATE SET
        source_name = EXCLUDED.source_name,
        rows_compared = EXCLUDED.rows_compared,
        rows_mismatched = EXCLUDED.rows_mismatched,
        discrepancy_pct = EXCLUDED.discrepancy_pct,
        checked_at = now()
""")


def differs(stored: pd.Series, source: pd.Series, tolerance: float) -> pd.Series:
    """Flag values outside the relative tolerance, or null on one side only."""
    stored = pd.to_numeric(stored).astype(float)
    source = pd.to_numeric(source).astype(float)
    outside = (stored - source).abs() > tolerance * source.abs()
    return outside | (stored.isna() != source.isna())


def compare(stored: pd.DataFrame, source: pd.DataFrame) -> tuple[int, int]:
    """Return (rows_compared, rows_mismatched) over the union of both sides."""
    merged = stored.merge(
        source[[*KEY_COLUMNS, "close", "adj_close", "volume"]],
        on=KEY_COLUMNS,
        how="outer",
        suffixes=("_stored", "_source"),
        indicator=True,
    )
    mismatched = (
        (merged["_merge"] != "both")
        | differs(merged["close_stored"], merged["close_source"], CLOSE_TOLERANCE)
        | differs(
            merged["adj_close_stored"], merged["adj_close_source"], ADJ_CLOSE_TOLERANCE
        )
        | differs(merged["volume_stored"], merged["volume_source"], VOLUME_TOLERANCE)
    )
    return len(merged), int(mismatched.sum())


def reconcile(
    run_id: int, start_date: date, end_date: date, engine: Engine | None = None
) -> dict:
    """Reconcile the inclusive window, save the result, and return it."""
    engine = engine or get_engine()
    tickers = get_tickers(engine)
    raw, failed = fetch_prices(tickers, start_date, end_date + timedelta(days=1))
    if failed:
        log.warning("Reconciliation download failed for: %s", failed)
    with engine.connect() as conn:
        stored = pd.read_sql(
            STORED_PRICES, conn, params={"start_date": start_date, "end_date": end_date}
        )

    rows_compared, rows_mismatched = compare(stored, transform(raw))
    discrepancy_pct = (
        round(100 * rows_mismatched / rows_compared, 4) if rows_compared else 0.0
    )
    result = {
        "run_id": run_id,
        "source_name": SOURCE_NAME,
        "rows_compared": rows_compared,
        "rows_mismatched": rows_mismatched,
        "discrepancy_pct": discrepancy_pct,
    }
    with engine.begin() as conn:
        conn.execute(SAVE_RESULT, result)
    return result
