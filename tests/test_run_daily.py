"""run_daily tests with the download replaced, so no network calls are made. Each
test deletes the etl_run_log row it created and anything recorded against it. Prices
are only written for the fake tickers in tests/fake_market.py."""

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import text

from etl import reconcile, run_daily
from etl.db import apply_schema, get_engine
from tests.fake_market import DAYS, FAKE_TICKERS, clean_prices, raw_from
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
            for table in ("dq_check_result", "reconciliation_result", "etl_run_log"):
                conn.execute(
                    text(f"DELETE FROM {table} WHERE run_id = :run_id"),
                    {"run_id": run_id},
                )


def logged_run(engine, run_id):
    query = text("SELECT * FROM etl_run_log WHERE run_id = :run_id")
    with engine.connect() as conn:
        return conn.execute(query, {"run_id": run_id}).mappings().one()


def patch_steps(monkeypatch, failed):
    """Replace every step that touches the network or real data; return check calls."""
    raw = raw_frame(full_ticker("AAA", 10.0))
    calls = []

    def record(name, result):
        def step(*args):
            calls.append((name, args))
            return result

        return step

    monkeypatch.setattr(run_daily, "fetch_prices", lambda *args: (raw, failed))
    monkeypatch.setattr(run_daily, "upsert_prices", lambda *args: (1, 1))
    monkeypatch.setattr(run_daily, "run_quality_checks", record("quality", {}))
    monkeypatch.setattr(run_daily, "reconcile", record("reconcile", {}))
    return calls


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


def test_checks_use_the_run_window_with_an_inclusive_end(engine, run_ids, monkeypatch):
    calls = patch_steps(monkeypatch, failed=[])
    window = (date(2026, 10, 30), date(2026, 11, 7))
    monkeypatch.setattr(run_daily, "date_window", lambda *args: window)
    run_daily.run(engine=engine)
    expected = (run_ids[0], date(2026, 10, 30), date(2026, 11, 6), engine)
    assert calls == [("quality", expected), ("reconcile", expected)]


def test_exception_in_checks_keeps_load_counts_in_the_log(engine, run_ids, monkeypatch):
    patch_steps(monkeypatch, failed=[])

    def boom(*args):
        raise ValueError("check crashed")

    monkeypatch.setattr(run_daily, "run_quality_checks", boom)
    with pytest.raises(ValueError):
        run_daily.run(engine=engine)
    row = logged_run(engine, run_ids[0])
    assert row["status"] == "failed"
    assert (row["rows_fetched"], row["rows_inserted"]) == (2, 1)


def test_dq_failure_and_mismatch_are_recorded_without_failing_the_run(
    engine, fake_market, run_ids, monkeypatch
):
    loaded = clean_prices()
    loaded.loc[0, "high"] = 1.0  # high below low
    source = loaded.copy()
    source.loc[1, "close"] = 99.0  # beyond the close tolerance

    monkeypatch.setattr(run_daily, "get_tickers", lambda run_engine: FAKE_TICKERS)
    monkeypatch.setattr(reconcile, "get_tickers", lambda run_engine: FAKE_TICKERS)
    monkeypatch.setattr(
        run_daily, "date_window", lambda *args: (DAYS[0], date(1990, 1, 5))
    )
    monkeypatch.setattr(run_daily, "fetch_prices", lambda *a: (raw_from(loaded), []))
    monkeypatch.setattr(reconcile, "fetch_prices", lambda *a: (raw_from(source), []))

    summary = run_daily.run(engine=engine)

    assert summary["status"] == logged_run(engine, run_ids[0])["status"] == "success"
    with engine.connect() as conn:
        params = {"run_id": run_ids[0]}
        dq = conn.execute(
            text(
                "SELECT rows_failed, passed FROM dq_check_result "
                "WHERE run_id = :run_id AND check_name = 'high_below_low'"
            ),
            params,
        ).one()
        mismatched = conn.execute(
            text(
                "SELECT rows_mismatched FROM reconciliation_result "
                "WHERE run_id = :run_id"
            ),
            params,
        ).scalar_one()
    assert tuple(dq) == (1, False)
    assert mismatched == 1


def test_refresh_history_is_a_two_year_backfill():
    assert run_daily.parse_args(["--refresh-history"]).backfill_years == 2


def test_no_arguments_is_the_catch_up_run():
    assert run_daily.parse_args([]).backfill_years is None


def test_refresh_history_and_backfill_years_conflict():
    with pytest.raises(SystemExit):
        run_daily.parse_args(["--refresh-history", "--backfill-years", "1"])
