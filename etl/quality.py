"""Data quality checks on fact_daily_price.

Checks only record results in dq_check_result. They never block or change price data.
"""

from datetime import date

from sqlalchemy import Engine, text

from etl.db import get_engine

IN_WINDOW = "trade_date BETWEEN :start_date AND :end_date"

# Each query returns the number of failures for the inclusive date window.
CHECKS = {
    "null_price_fields": f"""
        SELECT count(*) FROM fact_daily_price
        WHERE {IN_WINDOW}
          AND (open IS NULL OR high IS NULL OR low IS NULL
               OR close IS NULL OR adj_close IS NULL)
    """,
    "non_positive_prices": f"""
        SELECT count(*) FROM fact_daily_price
        WHERE {IN_WINDOW}
          AND (open <= 0 OR high <= 0 OR low <= 0 OR close <= 0 OR adj_close <= 0)
    """,
    "high_below_low": f"""
        SELECT count(*) FROM fact_daily_price
        WHERE {IN_WINDOW} AND high < low
    """,
    "price_outside_range": f"""
        SELECT count(*) FROM fact_daily_price
        WHERE {IN_WINDOW}
          AND (open < low OR open > high OR close < low OR close > high)
    """,
    "negative_or_null_volume": f"""
        SELECT count(*) FROM fact_daily_price
        WHERE {IN_WINDOW} AND (volume < 0 OR volume IS NULL)
    """,
    # Counts (ticker, date) gaps. First dates come from all history up to the window
    # end, so a ticker with no rows in the window still counts, and a ticker is only
    # expected from the day it first traded.
    "missing_trading_days": f"""
        WITH first_dates AS (
            SELECT ticker, min(trade_date) AS first_date
            FROM fact_daily_price
            WHERE trade_date <= :end_date
            GROUP BY ticker
        ),
        date_counts AS (
            SELECT trade_date, count(*) AS tickers_with_data
            FROM fact_daily_price
            WHERE {IN_WINDOW}
            GROUP BY trade_date
        ),
        trading_days AS (
            SELECT d.trade_date
            FROM date_counts d
            WHERE d.tickers_with_data * 2 > (
                SELECT count(*) FROM first_dates f WHERE f.first_date <= d.trade_date
            )
        )
        SELECT count(*)
        FROM trading_days t
        JOIN first_dates f ON f.first_date <= t.trade_date
        WHERE NOT EXISTS (
            SELECT 1 FROM fact_daily_price p
            WHERE p.ticker = f.ticker AND p.trade_date = t.trade_date
        )
    """,
    # Looks back past the window start so a ticker with no rows in the window is caught.
    "stale_latest_date": """
        WITH latest AS (
            SELECT ticker, max(trade_date) AS latest_date
            FROM fact_daily_price
            WHERE trade_date <= :end_date
            GROUP BY ticker
        )
        SELECT count(*) FROM latest
        WHERE latest_date < (
            SELECT mode() WITHIN GROUP (ORDER BY latest_date DESC) FROM latest
        )
    """,
}

SAVE_RESULT = text("""
    INSERT INTO dq_check_result (run_id, check_name, rows_failed, passed)
    VALUES (:run_id, :check_name, :rows_failed, :passed)
    ON CONFLICT (run_id, check_name) DO UPDATE SET
        rows_failed = EXCLUDED.rows_failed,
        passed = EXCLUDED.passed
""")


def run_quality_checks(
    run_id: int, start_date: date, end_date: date, engine: Engine | None = None
) -> dict[str, int]:
    """Run every check for the inclusive window; return rows_failed per check."""
    engine = engine or get_engine()
    window = {"start_date": start_date, "end_date": end_date}
    results = {}
    with engine.begin() as conn:
        for check_name, query in CHECKS.items():
            rows_failed = conn.execute(text(query), window).scalar_one()
            results[check_name] = rows_failed
            conn.execute(
                SAVE_RESULT,
                {
                    "run_id": run_id,
                    "check_name": check_name,
                    "rows_failed": rows_failed,
                    "passed": rows_failed == 0,
                },
            )
    return results
