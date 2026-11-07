"""Daily pipeline job, run after US market close.

`python -m etl.run_daily` fetches the catch-up window.
`python -m etl.run_daily --backfill-years 2` fetches that many years of history.
`python -m etl.run_daily --refresh-history` is a shortcut for --backfill-years 2.
"""

import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import Engine, text

from etl.db import get_engine
from etl.extract import fetch_prices, get_tickers
from etl.load import upsert_prices
from etl.quality import run_quality_checks
from etl.reconcile import reconcile
from etl.transform import transform

CATCH_UP_DAYS = 7
REFRESH_HISTORY_YEARS = 2
MARKET_TIMEZONE = ZoneInfo("America/New_York")
LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "etl.log"

log = logging.getLogger(__name__)


def date_window(backfill_years: int | None, today: date) -> tuple[date, date]:
    """Return the fetch window; end is exclusive, so today is included."""
    if backfill_years is None:
        start = today - timedelta(days=CATCH_UP_DAYS)
    else:
        start = (pd.Timestamp(today) - pd.DateOffset(years=backfill_years)).date()
    return start, today + timedelta(days=1)


def start_run(engine: Engine) -> int:
    with engine.begin() as conn:
        query = text("INSERT INTO etl_run_log (status) VALUES ('running') RETURNING run_id")
        return conn.execute(query).scalar_one()


def finish_run(engine: Engine, run_id: int, **fields) -> None:
    statement = text("""
        UPDATE etl_run_log SET
            finished_at = now(),
            status = :status,
            rows_fetched = :rows_fetched,
            rows_inserted = :rows_inserted,
            rows_updated = :rows_updated,
            error_message = :error_message
        WHERE run_id = :run_id
    """)
    with engine.begin() as conn:
        conn.execute(statement, {"run_id": run_id, **fields})


def run(backfill_years: int | None = None, engine: Engine | None = None) -> dict:
    """Run the pipeline, record it in etl_run_log, and return the run's summary."""
    engine = engine or get_engine()
    run_id = start_run(engine)
    summary = {
        "status": "failed",
        "rows_fetched": None,
        "rows_inserted": None,
        "rows_updated": None,
        "error_message": None,
    }
    failed = []
    try:
        start, end = date_window(backfill_years, datetime.now(MARKET_TIMEZONE).date())
        log.info("Run %s fetching %s to %s", run_id, start, end)
        raw, failed = fetch_prices(get_tickers(engine), start, end)
        prices = transform(raw)
        if prices.empty:
            raise RuntimeError("No price data returned for any ticker")
        summary["rows_fetched"] = len(prices)
        rows_inserted, rows_updated = upsert_prices(prices, engine)
        summary["rows_inserted"] = rows_inserted
        summary["rows_updated"] = rows_updated
        # Failed checks and mismatches are recorded and don't change the run status.
        last_day = end - timedelta(days=1)
        dq_results = run_quality_checks(run_id, start, last_day, engine)
        failed_checks = {name: rows for name, rows in dq_results.items() if rows}
        if failed_checks:
            log.warning("Run %s failed DQ checks: %s", run_id, failed_checks)
        reconciliation = reconcile(run_id, start, last_day, engine)
        log.info("Run %s reconciliation: %s", run_id, reconciliation)
        summary["status"] = "partial" if failed else "success"
    except Exception as exc:
        summary["error_message"] = str(exc)
        log.exception("Run %s failed", run_id)
        raise
    finally:
        if failed:
            log.warning("Run %s failed tickers: %s", run_id, failed)
        finish_run(engine, run_id, **summary)
    log.info("Run %s finished: %s", run_id, summary)
    return {"run_id": run_id, "failed_tickers": failed, **summary}


def configure_logging() -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
    )


def positive_int(value: str) -> int:
    years = int(value)
    if years < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return years


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    history = parser.add_mutually_exclusive_group()
    history.add_argument("--backfill-years", type=positive_int)
    history.add_argument(
        "--refresh-history",
        action="store_const",
        const=REFRESH_HISTORY_YEARS,
        dest="backfill_years",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    configure_logging()
    run(args.backfill_years)


if __name__ == "__main__":
    main()
