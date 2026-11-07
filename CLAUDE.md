# MarketPulse

Daily financial market data pipeline: Python + Pandas ETL from public APIs into PostgreSQL,
with automated data quality checks, SQL reconciliation, and a Tableau dashboard on top.

## Stack (do not add other technologies without asking)
Python 3.12, Pandas, SQL, PostgreSQL 16, Tableau. Libraries: SQLAlchemy 2.x, psycopg 3,
yfinance, python-dotenv, pytest. Scheduling is a plain Python job (launchd or cron later).

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

## Commands
- Run tests: pytest -q
- Apply schema: python -m etl.db
