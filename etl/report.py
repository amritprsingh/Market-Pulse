"""Pipeline health report, built only from the pipeline's tables.

Run with `python -m etl.report --days 30`.
"""

import argparse

from sqlalchemy import Engine, text

from etl.db import get_engine

CUTOFF = "now() - make_interval(days => :days)"

RUNS_BY_STATUS = text(f"""
    SELECT status, count(*) AS runs
    FROM etl_run_log
    WHERE started_at >= {CUTOFF}
    GROUP BY status
    ORDER BY status
""")

PRICE_TOTALS = text("""
    SELECT count(*) AS total_rows, count(DISTINCT ticker) AS distinct_tickers
    FROM fact_daily_price
""")

RECONCILIATION = text(f"""
    SELECT avg(discrepancy_pct) AS avg_discrepancy_pct,
           max(discrepancy_pct) AS max_discrepancy_pct,
           count(DISTINCT checked_at::date) AS coverage_days
    FROM reconciliation_result
    WHERE checked_at >= {CUTOFF}
""")

FAILED_CHECKS = text(f"""
    SELECT d.check_name, count(*) AS failed_runs, sum(d.rows_failed) AS rows_failed
    FROM dq_check_result d
    JOIN etl_run_log r USING (run_id)
    WHERE NOT d.passed AND r.started_at >= {CUTOFF}
    GROUP BY d.check_name
    ORDER BY d.check_name
""")


def collect_report(days: int, engine: Engine | None = None) -> dict:
    engine = engine or get_engine()
    params = {"days": days}
    with engine.connect() as conn:
        return {
            "days": days,
            "runs_by_status": dict(conn.execute(RUNS_BY_STATUS, params).all()),
            **conn.execute(PRICE_TOTALS).mappings().one(),
            **conn.execute(RECONCILIATION, params).mappings().one(),
            "failed_checks": conn.execute(FAILED_CHECKS, params).mappings().all(),
        }


def format_pct(value) -> str:
    return "n/a" if value is None else f"{value:.4f}%"


def format_report(report: dict) -> str:
    lines = [f"MarketPulse report, last {report['days']} days", "", "Runs by status:"]
    runs = report["runs_by_status"]
    lines += [f"  {status}: {count}" for status, count in runs.items()] or ["  none"]
    lines += [
        "",
        f"Rows in fact_daily_price: {report['total_rows']}",
        f"Distinct tickers: {report['distinct_tickers']}",
        "",
        f"Average discrepancy: {format_pct(report['avg_discrepancy_pct'])}",
        f"Maximum discrepancy: {format_pct(report['max_discrepancy_pct'])}",
        f"Reconciliation coverage: {report['coverage_days']} days",
        "",
        "Failed DQ checks:",
    ]
    lines += [
        f"  {check['check_name']}: {check['rows_failed']} rows "
        f"across {check['failed_runs']} runs"
        for check in report["failed_checks"]
    ] or ["  none"]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be at least 1")
    print(format_report(collect_report(args.days)))


if __name__ == "__main__":
    main()
