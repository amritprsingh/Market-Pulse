"""View tests against the real data in DATABASE_URL. These only read, apart from
re-applying the idempotent schema and views."""

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text

from etl.db import apply_schema, apply_views

VIEWS = ["v_price_daily", "v_sector_daily", "v_ticker_summary", "v_pipeline_health"]
TOLERANCE = 1e-9


@pytest.fixture(scope="module", autouse=True)
def views(engine):
    apply_schema(engine)
    apply_views(engine)


@pytest.fixture(scope="module")
def ticker(engine):
    """A real ticker; the alphabetically first one with full history."""
    query = text("""
        SELECT ticker FROM fact_daily_price
        GROUP BY ticker
        HAVING count(*) = (SELECT count(DISTINCT trade_date) FROM fact_daily_price)
        ORDER BY ticker LIMIT 1
    """)
    with engine.connect() as conn:
        return conn.execute(query).scalar_one()


def scalar(engine, query, **params):
    with engine.connect() as conn:
        return conn.execute(text(query), params).scalar_one()


def fact_prices(engine, ticker):
    query = text("""
        SELECT trade_date, close, adj_close, volume FROM fact_daily_price
        WHERE ticker = :ticker ORDER BY trade_date
    """)
    with engine.connect() as conn:
        frame = pd.read_sql(query, conn, params={"ticker": ticker})
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    return frame.set_index("trade_date").astype(float)


def view_column(engine, column, ticker):
    query = text(f"""
        SELECT {column} FROM v_price_daily WHERE ticker = :ticker ORDER BY trade_date
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"ticker": ticker}).scalars().all()
    return np.array([np.nan if value is None else float(value) for value in rows])


@pytest.mark.parametrize("view", VIEWS)
def test_view_returns_rows(engine, view):
    assert scalar(engine, f"SELECT count(*) FROM {view}") > 0


def test_price_view_has_one_row_per_fact_row(engine):
    assert scalar(engine, "SELECT count(*) FROM v_price_daily") == scalar(
        engine, "SELECT count(*) FROM fact_daily_price"
    )


def test_daily_return_is_null_only_on_each_tickers_first_day(engine):
    mismatches = scalar(
        engine,
        """
        SELECT count(*) FROM (
            SELECT daily_return, trade_date,
                   min(trade_date) OVER (PARTITION BY ticker) AS first_date
            FROM v_price_daily
        ) t
        WHERE (daily_return IS NULL) <> (trade_date = first_date)
        """,
    )
    assert mismatches == 0


def test_daily_return_matches_pandas(engine, ticker):
    expected = fact_prices(engine, ticker)["adj_close"].pct_change().to_numpy()
    actual = view_column(engine, "daily_return", ticker)
    np.testing.assert_allclose(actual, expected, rtol=TOLERANCE, atol=TOLERANCE)


def test_sector_view_has_one_row_per_sector_and_date(engine):
    assert scalar(engine, "SELECT count(*) FROM v_sector_daily") == scalar(
        engine,
        "SELECT count(*) FROM (SELECT DISTINCT sector, trade_date FROM v_price_daily) t",
    )


def test_sector_index_is_100_on_the_first_date(engine):
    query = text("""
        SELECT sector_index FROM v_sector_daily
        WHERE trade_date = (SELECT min(trade_date) FROM v_sector_daily)
    """)
    with engine.connect() as conn:
        values = [float(value) for value in conn.execute(query).scalars()]
    assert len(values) == scalar(engine, "SELECT count(DISTINCT sector) FROM dim_security")
    assert values == pytest.approx([100.0] * len(values), abs=TOLERANCE)


def test_sector_index_matches_compounded_returns(engine):
    query = text("""
        SELECT sector, trade_date, daily_return FROM v_price_daily
    """)
    with engine.connect() as conn:
        prices = pd.read_sql(query, conn)
        indexes = pd.read_sql(
            text("""
                SELECT DISTINCT ON (sector) sector, sector_index FROM v_sector_daily
                ORDER BY sector, trade_date DESC
            """),
            conn,
        ).set_index("sector")["sector_index"]
    prices["daily_return"] = prices["daily_return"].astype(float)
    sector_returns = prices.groupby(["sector", "trade_date"])["daily_return"].mean()
    expected = (1 + sector_returns.fillna(0)).groupby("sector").prod() * 100
    pd.testing.assert_series_equal(
        indexes.astype(float).sort_index(),
        expected.sort_index(),
        check_names=False,
        rtol=TOLERANCE,
    )


@pytest.mark.parametrize("window", [20, 50])
def test_moving_average_matches_pandas(engine, ticker, window):
    prices = fact_prices(engine, ticker)["adj_close"]
    expected = prices.rolling(window).mean().to_numpy()
    actual = view_column(engine, f"ma_{window}", ticker)
    assert np.isnan(actual[: window - 1]).all()
    np.testing.assert_allclose(actual, expected, rtol=TOLERANCE)


@pytest.mark.parametrize(
    "column,offset",
    [
        ("return_1m", pd.DateOffset(months=1)),
        ("return_3m", pd.DateOffset(months=3)),
        ("return_1y", pd.DateOffset(years=1)),
    ],
)
def test_ticker_return_matches_pandas(engine, ticker, column, offset):
    prices = fact_prices(engine, ticker)["adj_close"]
    base = prices[prices.index <= prices.index[-1] - offset].iloc[-1]
    actual = scalar(
        engine, f"SELECT {column} FROM v_ticker_summary WHERE ticker = :t", t=ticker
    )
    assert float(actual) == pytest.approx(prices.iloc[-1] / base - 1, rel=TOLERANCE)


def test_ticker_summary_volume_and_range_match_pandas(engine, ticker):
    prices = fact_prices(engine, ticker)
    last_year = prices[prices.index > prices.index[-1] - pd.Timedelta(weeks=52)]
    query = text("""
        SELECT latest_close, avg_volume_30, high_52w, low_52w
        FROM v_ticker_summary WHERE ticker = :ticker
    """)
    with engine.connect() as conn:
        row = conn.execute(query, {"ticker": ticker}).one()
    assert [float(value) for value in row] == pytest.approx(
        [
            prices["close"].iloc[-1],
            prices["volume"].tail(30).mean(),
            last_year["close"].max(),
            last_year["close"].min(),
        ],
        rel=TOLERANCE,
    )


def test_ticker_summary_has_one_row_per_ticker(engine):
    assert scalar(engine, "SELECT count(*) FROM v_ticker_summary") == scalar(
        engine, "SELECT count(DISTINCT ticker) FROM fact_daily_price"
    )


def test_pipeline_health_has_one_row_per_run(engine):
    assert scalar(engine, "SELECT count(*) FROM v_pipeline_health") == scalar(
        engine, "SELECT count(*) FROM etl_run_log"
    )


def test_run_without_checks_is_null_not_zero(engine):
    mismatches = scalar(
        engine,
        """
        SELECT count(*) FROM v_pipeline_health h
        WHERE (h.failed_dq_checks IS NULL) <> NOT EXISTS (
            SELECT 1 FROM dq_check_result d WHERE d.run_id = h.run_id
        )
        """,
    )
    assert mismatches == 0
