"""Pipeline health report, built only from the pipeline's tables.

Run with `python -m etl.report --days 30`.
"""

import argparse

from sqlalchemy import Engine, text

from etl.db import get_engine

CUTOFF = "now() - make_interval(days => :days)"
MARKET_TIME = "AT TIME ZONE 'America/New_York'"

GENERATED_AT = text(f"SELECT to_char(now() {MARKET_TIME}, 'YYYY-MM-DD HH24:MI') || ' ET'")

RUNS_BY_TRIGGER = text(f"""
    SELECT triggered_by, status, count(*) AS runs
    FROM etl_run_log
    WHERE started_at >= {CUTOFF}
    GROUP BY triggered_by, status
    ORDER BY triggered_by, status
""")

# All time, not the report window: the 30-day milestone counts every scheduled day.
SCHEDULED = text(f"""
    SELECT
        count(DISTINCT (started_at {MARKET_TIME})::date)
            FILTER (WHERE status IN ('success', 'partial')) AS scheduled_days_completed,
        count(*) FILTER (WHERE status = 'failed') AS scheduled_runs_failed,
        min((started_at {MARKET_TIME})::date) AS first_scheduled_date,
        max((started_at {MARKET_TIME})::date) AS last_scheduled_date
    FROM etl_run_log
    WHERE triggered_by = 'launchd'
""")

PRICE_TOTALS = text("""
    SELECT count(*) AS total_rows,
           count(DISTINCT ticker) AS distinct_tickers,
           min(trade_date) AS first_trade_date,
           max(trade_date) AS last_trade_date
    FROM fact_daily_price
""")

DQ_TOTALS = text(f"""
    SELECT count(*) AS dq_results,
           count(*) FILTER (WHERE NOT d.passed) AS dq_failed_results,
           count(DISTINCT d.run_id) AS dq_runs
    FROM dq_check_result d
    JOIN etl_run_log r USING (run_id)
    WHERE r.started_at >= {CUTOFF}
""")

RECONCILIATION = text(f"""
    SELECT avg(discrepancy_pct) AS avg_discrepancy_pct,
           max(discrepancy_pct) AS max_discrepancy_pct,
           count(*) AS reconciliations,
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
            "generated_at": conn.execute(GENERATED_AT).scalar_one(),
            "runs_by_trigger": conn.execute(RUNS_BY_TRIGGER, params).mappings().all(),
            **conn.execute(SCHEDULED).mappings().one(),
            **conn.execute(PRICE_TOTALS).mappings().one(),
            **conn.execute(DQ_TOTALS, params).mappings().one(),
            **conn.execute(RECONCILIATION, params).mappings().one(),
            "failed_checks": conn.execute(FAILED_CHECKS, params).mappings().all(),
        }


def format_pct(value) -> str:
    return "n/a" if value is None else f"{value:.4f}%"


def window_status(report: dict) -> str:
    """Only claim the window once that many days of scheduled runs have completed."""
    days, completed = report["days"], report["scheduled_days_completed"]
    if completed >= days:
        return f"{days}-day window complete: {completed} days of scheduled runs"
    return f"{days}-day window in progress: {completed} days complete"


def format_report(report: dict) -> str:
    days = report["days"]
    lines = [
        f"MarketPulse report, last {days} days (generated {report['generated_at']})",
        "",
        window_status(report),
        f"Scheduled runs: first {report['first_scheduled_date'] or 'none'}, "
        f"last {report['last_scheduled_date'] or 'none'}, "
        f"failed {report['scheduled_runs_failed']}",
        "",
        "Runs by trigger and status:",
    ]
    lines += [
        f"  {run['triggered_by']} {run['status']}: {run['runs']}"
        for run in report["runs_by_trigger"]
    ] or ["  none"]
    lines += [
        "",
        f"Rows in fact_daily_price: {report['total_rows']}",
        f"Distinct tickers: {report['distinct_tickers']}",
        f"Trade dates: {report['first_trade_date']} to {report['last_trade_date']}",
        "",
        f"DQ check results: {report['dq_results']} across {report['dq_runs']} runs, "
        f"{report['dq_failed_results']} failed",
        "Failed DQ checks:",
    ]
    lines += [
        f"  {check['check_name']}: {check['rows_failed']} rows "
        f"across {check['failed_runs']} runs"
        for check in report["failed_checks"]
    ] or ["  none"]
    lines += [
        "",
        f"Reconciliations: {report['reconciliations']}",
        f"Reconciliation coverage: {report['coverage_days']} days",
        f"Average discrepancy: {format_pct(report['avg_discrepancy_pct'])}",
        f"Maximum discrepancy: {format_pct(report['max_discrepancy_pct'])}",
    ]
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
