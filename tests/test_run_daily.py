"""run_daily tests with the fetch and load steps replaced, so no network calls are
made and no prices are written. Each test deletes the etl_run_log row it created."""

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import text

from etl import run_daily
from etl.db import apply_schema, get_engine
from tests.test_transform import full_ticker, raw_frame


@pytest.fixture(scope="module")
def engine():
    engine = get_engine()
    apply_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def run_ids(engine, monkeypatch):
    """Capture the run_id of every run so it can be removed afterwards."""
    created = []
    start_run = run_daily.start_run

    def tracked_start_run(run_engine):
        created.append(start_run(run_engine))
        return created[-1]

    monkeypatch.setattr(run_daily, "start_run", tracked_start_run)
    monkeypatch.setattr(run_daily, "get_tickers", lambda run_engine: ["AAA", "BBB"])
    yield created
    with engine.begin() as conn:
        for run_id in created:
            conn.execute(
                text("DELETE FROM etl_run_log WHERE run_id = :run_id"),
                {"run_id": run_id},
            )


def logged_run(engine, run_id):
    query = text("SELECT * FROM etl_run_log WHERE run_id = :run_id")
    with engine.connect() as conn:
        return conn.execute(query, {"run_id": run_id}).mappings().one()


def patch_steps(monkeypatch, failed):
    raw = raw_frame(full_ticker("AAA", 10.0))
    monkeypatch.setattr(run_daily, "fetch_prices", lambda *args: (raw, failed))
    monkeypatch.setattr(run_daily, "upsert_prices", lambda *args: (1, 1))


def test_success_is_logged(engine, run_ids, monkeypatch):
    patch_steps(monkeypatch, failed=[])
    summary = run_daily.run(engine=engine)
    row = logged_run(engine, run_ids[0])
    assert summary["status"] == row["status"] == "success"
    assert (row["rows_fetched"], row["rows_inserted"], row["rows_updated"]) == (2, 1, 1)
    assert row["finished_at"] >= row["started_at"]
    assert row["error_message"] is None


def test_failed_ticker_makes_the_run_partial(engine, run_ids, monkeypatch):
    patch_steps(monkeypatch, failed=["BBB"])
    summary = run_daily.run(engine=engine)
    assert logged_run(engine, run_ids[0])["status"] == "partial"
    assert summary["failed_tickers"] == ["BBB"]


def test_exception_is_logged_as_failed(engine, run_ids, monkeypatch):
    def boom(*args):
        raise ValueError("source unavailable")

    monkeypatch.setattr(run_daily, "fetch_prices", boom)
    with pytest.raises(ValueError):
        run_daily.run(engine=engine)
    row = logged_run(engine, run_ids[0])
    assert row["status"] == "failed"
    assert row["error_message"] == "source unavailable"
    assert row["finished_at"] is not None


def test_no_data_for_any_ticker_is_failed(engine, run_ids, monkeypatch):
    monkeypatch.setattr(
        run_daily, "fetch_prices", lambda *args: (pd.DataFrame(), ["AAA", "BBB"])
    )
    with pytest.raises(RuntimeError):
        run_daily.run(engine=engine)
    assert logged_run(engine, run_ids[0])["status"] == "failed"


def test_default_window_is_the_catch_up_days():
    assert run_daily.date_window(None, date(2026, 11, 6)) == (
        date(2026, 10, 30),
        date(2026, 11, 7),
    )


def test_backfill_window_handles_leap_day():
    assert run_daily.date_window(2, date(2028, 2, 29)) == (
        date(2026, 2, 28),
        date(2028, 3, 1),
    )
