-- MarketPulse schema. Idempotent: safe to apply repeatedly, never drops data.

CREATE TABLE IF NOT EXISTS dim_security (
    ticker       TEXT PRIMARY KEY,
    company_name TEXT,
    sector       TEXT,
    industry     TEXT,
    exchange     TEXT
);

-- Price columns stay nullable so bad source rows can land and be reported by
-- the data quality checks rather than failing the load.
CREATE TABLE IF NOT EXISTS fact_daily_price (
    ticker     TEXT NOT NULL REFERENCES dim_security (ticker),
    trade_date DATE NOT NULL,
    open       NUMERIC(14,4),
    high       NUMERIC(14,4),
    low        NUMERIC(14,4),
    close      NUMERIC(14,4),
    adj_close  NUMERIC(14,4),
    volume     BIGINT,
    loaded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_fact_daily_price_trade_date
    ON fact_daily_price (trade_date);

CREATE TABLE IF NOT EXISTS etl_run_log (
    run_id        SERIAL PRIMARY KEY,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    status        TEXT NOT NULL,
    rows_fetched  INTEGER,
    rows_inserted INTEGER,
    rows_updated  INTEGER,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS dq_check_result (
    run_id      INTEGER NOT NULL REFERENCES etl_run_log (run_id),
    check_name  TEXT NOT NULL,
    rows_failed INTEGER NOT NULL,
    passed      BOOLEAN NOT NULL,
    PRIMARY KEY (run_id, check_name)
);

CREATE TABLE IF NOT EXISTS reconciliation_result (
    run_id          INTEGER PRIMARY KEY REFERENCES etl_run_log (run_id),
    source_name     TEXT NOT NULL,
    rows_compared   INTEGER NOT NULL,
    rows_mismatched INTEGER NOT NULL,
    discrepancy_pct NUMERIC(7,4) NOT NULL,
    checked_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
