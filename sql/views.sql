-- MarketPulse reporting views for Tableau. Read-only over the base tables.
-- CREATE OR REPLACE can add columns at the end of a view, but not rename, reorder or
-- retype them.

CREATE OR REPLACE VIEW v_price_daily AS
SELECT
    p.ticker,
    s.company_name,
    s.sector,
    s.industry,
    p.trade_date,
    p.open,
    p.high,
    p.low,
    p.close,
    p.adj_close,
    p.volume,
    p.adj_close / NULLIF(LAG(p.adj_close) OVER ticker_days, 0) - 1 AS daily_return,
    p.close * p.volume AS dollar_volume,
    CASE WHEN COUNT(p.adj_close) OVER (ticker_days ROWS 19 PRECEDING) = 20
         THEN AVG(p.adj_close) OVER (ticker_days ROWS 19 PRECEDING)
    END AS ma_20,
    CASE WHEN COUNT(p.adj_close) OVER (ticker_days ROWS 49 PRECEDING) = 50
         THEN AVG(p.adj_close) OVER (ticker_days ROWS 49 PRECEDING)
    END AS ma_50,
    AVG(p.volume) OVER (ticker_days ROWS 29 PRECEDING) AS vol_avg_30
FROM fact_daily_price p
JOIN dim_security s USING (ticker)
WINDOW ticker_days AS (PARTITION BY p.ticker ORDER BY p.trade_date);

-- The first date has no return, so it contributes ln(1) = 0 and the index starts at 100.
CREATE OR REPLACE VIEW v_sector_daily AS
WITH sector_days AS (
    SELECT
        sector,
        trade_date,
        COUNT(*) AS ticker_count,
        AVG(daily_return) AS avg_daily_return,
        SUM(volume) AS total_volume,
        SUM(dollar_volume) AS total_dollar_volume
    FROM v_price_daily
    GROUP BY sector, trade_date
)
SELECT
    sector,
    trade_date,
    ticker_count,
    avg_daily_return,
    total_volume,
    total_dollar_volume,
    100 * EXP(SUM(COALESCE(LN(1 + avg_daily_return), 0)) OVER (
        PARTITION BY sector ORDER BY trade_date
    )) AS sector_index
FROM sector_days;

-- Each return's base is the last trading day on or before the calendar date that far
-- back, so a ticker without that much history gets null.
CREATE OR REPLACE VIEW v_ticker_summary AS
WITH latest AS (
    SELECT DISTINCT ON (ticker)
        ticker,
        trade_date AS latest_trade_date,
        close AS latest_close,
        adj_close AS latest_adj_close
    FROM fact_daily_price
    ORDER BY ticker, trade_date DESC
)
SELECT
    l.ticker,
    l.latest_trade_date,
    l.latest_close,
    l.latest_adj_close / NULLIF(m1.adj_close, 0) - 1 AS return_1m,
    l.latest_adj_close / NULLIF(m3.adj_close, 0) - 1 AS return_3m,
    l.latest_adj_close / NULLIF(y1.adj_close, 0) - 1 AS return_1y,
    v.avg_volume_30,
    r.high_52w,
    r.low_52w
FROM latest l
LEFT JOIN LATERAL (
    SELECT adj_close FROM fact_daily_price p
    WHERE p.ticker = l.ticker
      AND p.trade_date <= (l.latest_trade_date - INTERVAL '1 month')::date
    ORDER BY p.trade_date DESC LIMIT 1
) m1 ON true
LEFT JOIN LATERAL (
    SELECT adj_close FROM fact_daily_price p
    WHERE p.ticker = l.ticker
      AND p.trade_date <= (l.latest_trade_date - INTERVAL '3 months')::date
    ORDER BY p.trade_date DESC LIMIT 1
) m3 ON true
LEFT JOIN LATERAL (
    SELECT adj_close FROM fact_daily_price p
    WHERE p.ticker = l.ticker
      AND p.trade_date <= (l.latest_trade_date - INTERVAL '1 year')::date
    ORDER BY p.trade_date DESC LIMIT 1
) y1 ON true
CROSS JOIN LATERAL (
    SELECT AVG(volume) AS avg_volume_30
    FROM (
        SELECT volume FROM fact_daily_price p
        WHERE p.ticker = l.ticker
        ORDER BY p.trade_date DESC LIMIT 30
    ) last_30
) v
CROSS JOIN LATERAL (
    SELECT MAX(close) AS high_52w, MIN(close) AS low_52w
    FROM fact_daily_price p
    WHERE p.ticker = l.ticker
      AND p.trade_date > (l.latest_trade_date - INTERVAL '52 weeks')::date
) r;

-- failed_dq_checks is null, not 0, for a run with no recorded checks.
CREATE OR REPLACE VIEW v_pipeline_health AS
SELECT
    r.run_id,
    r.started_at,
    r.status,
    r.rows_fetched,
    r.rows_inserted,
    r.rows_updated,
    d.failed_dq_checks,
    x.rows_compared,
    x.rows_mismatched,
    x.discrepancy_pct
FROM etl_run_log r
LEFT JOIN (
    SELECT run_id, COUNT(*) FILTER (WHERE NOT passed) AS failed_dq_checks
    FROM dq_check_result
    GROUP BY run_id
) d USING (run_id)
LEFT JOIN reconciliation_result x USING (run_id);
