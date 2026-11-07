# MarketPulse

Daily financial market data pipeline: Python + Pandas ETL from public APIs into PostgreSQL,
with automated data quality checks, SQL reconciliation, and a Tableau dashboard on top.

## Stack (do not add other technologies without asking)
Python 3.12, Pandas, SQL, PostgreSQL 16, Tableau. Libraries: SQLAlchemy 2.x, psycopg 3,
yfinance, python-dotenv, pytest. Scheduling is a plain Python job run by launchd.

## Environment
- macOS on Apple Silicon. Always use the venv at .venv (Python 3.12).
- Config comes from .env (DATABASE_URL). Never hardcode credentials. Never commit .env.
- Call load_dotenv() from real script files only.

## Design rules
- Loads must be idempotent: upsert on (ticker, trade_date), safe to re-run any day.
- Every run writes to etl_run_log. Every data quality check writes to dq_check_result.
  Every reconciliation writes to reconciliation_result. Metrics come from these tables only.
- The daily job includes a catch-up window so missed days backfill automatically.
- Run the daily job after US market close; the latest day can be partial or revised.
- yfinance returns two-level columns; flatten them in the transform step.
- Keep each module small: extract, transform, load, quality, reconcile, run_daily.
- No invented numbers anywhere. Row counts, ticker counts, and discrepancy % must be
  computed from the database.

## Layout
sql/ (schema.sql, views.sql), etl/, seeds/tickers.csv, tests/, data/ (gitignored), logs/

## Scheduling
- launchd runs scripts/run_daily.sh Monday to Friday at 18:30 local time (catch-up window)
  and scripts/run_weekly.sh Saturday at 10:00 local time (--refresh-history).
- Plists are in launchd/. Install with scripts/install_launchd.sh, remove with
  scripts/uninstall_launchd.sh. Plist paths are absolute; update them if the repo moves.
- Logs: logs/daily.log, logs/weekly.log, logs/etl.log, and launchd's own
  logs/launchd.*.log. All jobs share one lock, logs/.run.lock.
- A Mac asleep at the scheduled time runs the job when it wakes. A Mac that is powered
  off skips it, and the 7-day catch-up window covers the gap.

## Commands
- Run tests: pytest -q
- Apply schema: python -m etl.db
