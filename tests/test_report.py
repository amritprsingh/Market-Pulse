"""Report tests. Recorded rows belong to the fake_market run and are removed with it."""

from decimal import Decimal

import pytest
from sqlalchemy import text

from etl.report import collect_report, format_report, window_status


def record(engine, query, run_id):
    with engine.begin() as conn:
        conn.execute(text(query), {"run_id": run_id})


def test_report_includes_recorded_results(engine, fake_market):
    record(
        engine,
        """
        INSERT INTO dq_check_result (run_id, check_name, rows_failed, passed)
        VALUES (:run_id, 'zz_fake_check', 3, false)
        """,
        fake_market,
    )
    record(
        engine,
        """
        INSERT INTO reconciliation_result
            (run_id, source_name, rows_compared, rows_mismatched, discrepancy_pct)
        VALUES (:run_id, 'yfinance', 10, 1, 99.5)
        """,
        fake_market,
    )

    report = collect_report(30, engine)

    runs = {(r["triggered_by"], r["status"]): r["runs"] for r in report["runs_by_trigger"]}
    assert runs[("manual", "test")] == 1
    assert report["max_discrepancy_pct"] == Decimal("99.5000")
    assert report["coverage_days"] >= 1
    assert report["dq_failed_results"] >= 1
    failed = {check["check_name"]: check for check in report["failed_checks"]}
    assert failed["zz_fake_check"]["rows_failed"] == 3
    assert failed["zz_fake_check"]["failed_runs"] == 1
    assert "zz_fake_check: 3 rows across 1 runs" in format_report(report)


def test_only_completed_launchd_runs_count_as_scheduled_days(engine, fake_market):
    before = collect_report(30, engine)["scheduled_days_completed"]
    record(
        engine,
        "UPDATE etl_run_log SET triggered_by = 'launchd', status = 'failed' "
        "WHERE run_id = :run_id",
        fake_market,
    )
    after_failed = collect_report(30, engine)
    assert after_failed["scheduled_days_completed"] == before
    assert after_failed["scheduled_runs_failed"] >= 1

    record(
        engine,
        "UPDATE etl_run_log SET status = 'success' WHERE run_id = :run_id",
        fake_market,
    )
    assert collect_report(30, engine)["scheduled_days_completed"] in (before, before + 1)


@pytest.mark.parametrize(
    "completed,expected",
    [
        (0, "30-day window in progress: 0 days complete"),
        (29, "30-day window in progress: 29 days complete"),
        (30, "30-day window complete: 30 days of scheduled runs"),
    ],
)
def test_window_is_only_complete_after_enough_scheduled_days(completed, expected):
    assert window_status({"days": 30, "scheduled_days_completed": completed}) == expected


def test_empty_report_formats_without_values():
    output = format_report(
        {
            "days": 30,
            "generated_at": "2026-01-01 00:00 ET",
            "runs_by_trigger": [],
            "scheduled_days_completed": 0,
            "scheduled_runs_failed": 0,
            "first_scheduled_date": None,
            "last_scheduled_date": None,
            "total_rows": 0,
            "distinct_tickers": 0,
            "first_trade_date": None,
            "last_trade_date": None,
            "dq_results": 0,
            "dq_failed_results": 0,
            "dq_runs": 0,
            "reconciliations": 0,
            "avg_discrepancy_pct": None,
            "max_discrepancy_pct": None,
            "coverage_days": 0,
            "failed_checks": [],
        }
    )
    assert "30-day window in progress: 0 days complete" in output
    assert "Average discrepancy: n/a" in output
    assert "Failed DQ checks:\n  none" in output
