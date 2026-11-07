"""Report tests. Recorded rows belong to the fake_market run and are removed with it."""

from decimal import Decimal

from sqlalchemy import text

from etl.report import collect_report, format_report


def test_report_includes_recorded_results(engine, fake_market):
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO dq_check_result (run_id, check_name, rows_failed, passed)
                VALUES (:run_id, 'zz_fake_check', 3, false)
            """),
            {"run_id": fake_market},
        )
        conn.execute(
            text("""
                INSERT INTO reconciliation_result
                    (run_id, source_name, rows_compared, rows_mismatched, discrepancy_pct)
                VALUES (:run_id, 'yfinance', 10, 1, 99.5)
            """),
            {"run_id": fake_market},
        )

    report = collect_report(30, engine)

    assert report["runs_by_status"]["test"] == 1
    assert report["max_discrepancy_pct"] == Decimal("99.5000")
    assert report["coverage_days"] >= 1
    failed = {check["check_name"]: check for check in report["failed_checks"]}
    assert failed["zz_fake_check"]["rows_failed"] == 3
    assert failed["zz_fake_check"]["failed_runs"] == 1
    assert "zz_fake_check: 3 rows across 1 runs" in format_report(report)


def test_empty_report_formats_without_values():
    output = format_report(
        {
            "days": 30,
            "runs_by_status": {},
            "total_rows": 0,
            "distinct_tickers": 0,
            "avg_discrepancy_pct": None,
            "max_discrepancy_pct": None,
            "coverage_days": 0,
            "failed_checks": [],
        }
    )
    assert "Average discrepancy: n/a" in output
    assert "Failed DQ checks:\n  none" in output
