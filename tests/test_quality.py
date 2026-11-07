"""Quality check tests on hand-built rows in the fake 1990 window."""

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text

from etl.load import upsert_prices
from etl.quality import CHECKS, run_quality_checks
from tests.fake_market import DAYS, clean_prices, row_index

START, END = DAYS[0], DAYS[-1]
TICKER_A, TICKER_B, TICKER_C = "ZZDQ-A", "ZZDQ-B", "ZZDQ-C"


def set_value(column, value):
    def mutate(prices):
        prices.loc[row_index(prices, TICKER_A, DAYS[1]), column] = value
        return prices

    return mutate


def drop_row(ticker, day):
    def mutate(prices):
        return prices.drop(row_index(prices, ticker, day))

    return mutate


def saved_results(engine, run_id):
    query = text("""
        SELECT check_name, rows_failed, passed FROM dq_check_result
        WHERE run_id = :run_id
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"run_id": run_id}).all()
    return {name: (rows_failed, passed) for name, rows_failed, passed in rows}


def test_clean_data_passes_every_check(engine, fake_market):
    upsert_prices(clean_prices(), engine)
    results = run_quality_checks(fake_market, START, END, engine)
    assert set(results) == set(CHECKS)
    assert len(CHECKS) == 7
    assert saved_results(engine, fake_market) == {name: (0, True) for name in CHECKS}


@pytest.mark.parametrize(
    "check_name,mutate",
    [
        ("null_price_fields", set_value("open", np.nan)),
        ("null_price_fields", set_value("adj_close", np.nan)),
        ("non_positive_prices", set_value("close", 0.0)),
        ("non_positive_prices", set_value("adj_close", -1.0)),
        ("high_below_low", set_value("high", 8.0)),
        ("price_outside_range", set_value("open", 13.0)),
        ("price_outside_range", set_value("open", 8.0)),
        ("price_outside_range", set_value("close", 13.0)),
        ("price_outside_range", set_value("close", 8.0)),
        ("negative_or_null_volume", set_value("volume", -1)),
        ("negative_or_null_volume", set_value("volume", pd.NA)),
        ("missing_trading_days", drop_row(TICKER_A, DAYS[1])),
        ("stale_latest_date", drop_row(TICKER_A, DAYS[2])),
    ],
)
def test_check_fires_on_one_bad_row(engine, fake_market, check_name, mutate):
    upsert_prices(mutate(clean_prices()), engine)
    results = run_quality_checks(fake_market, START, END, engine)
    assert results[check_name] == 1
    assert saved_results(engine, fake_market)[check_name] == (1, False)


def test_ticker_starting_after_window_start_is_not_missing(engine, fake_market):
    prices = drop_row(TICKER_C, DAYS[0])(clean_prices())
    upsert_prices(prices, engine)
    results = run_quality_checks(fake_market, START, END, engine)
    assert results["missing_trading_days"] == 0


def test_gap_after_the_first_traded_day_is_missing(engine, fake_market):
    upsert_prices(drop_row(TICKER_C, DAYS[1])(clean_prices()), engine)
    results = run_quality_checks(fake_market, START, END, engine)
    assert results["missing_trading_days"] == 1


def test_date_without_a_majority_is_not_a_trading_day(engine, fake_market):
    prices = clean_prices()
    prices = prices.drop(
        [row_index(prices, TICKER_A, DAYS[1]), row_index(prices, TICKER_B, DAYS[1])]
    )
    upsert_prices(prices, engine)
    assert run_quality_checks(fake_market, START, END, engine)["missing_trading_days"] == 0


def test_ticker_with_no_rows_in_window_is_stale_and_missing(engine, fake_market):
    prices = clean_prices()
    prices = prices.drop(
        [row_index(prices, TICKER_C, DAYS[1]), row_index(prices, TICKER_C, DAYS[2])]
    )
    upsert_prices(prices, engine)
    results = run_quality_checks(fake_market, DAYS[1], END, engine)
    assert results["stale_latest_date"] == 1
    assert results["missing_trading_days"] == 2


def test_bad_row_outside_window_is_ignored(engine, fake_market):
    prices = clean_prices()
    prices.loc[row_index(prices, TICKER_A, DAYS[0]), "high"] = 1.0
    upsert_prices(prices, engine)
    results = run_quality_checks(fake_market, DAYS[1], END, engine)
    assert results["high_below_low"] == 0


def test_checks_do_not_change_price_rows_and_can_rerun(engine, fake_market):
    prices = set_value("high", 8.0)(clean_prices())
    upsert_prices(prices, engine)
    query = text("SELECT * FROM fact_daily_price WHERE ticker LIKE 'ZZDQ-%' ORDER BY 1, 2")
    with engine.connect() as conn:
        before = conn.execute(query).all()
    run_quality_checks(fake_market, START, END, engine)
    run_quality_checks(fake_market, START, END, engine)
    with engine.connect() as conn:
        assert conn.execute(query).all() == before
    assert len(saved_results(engine, fake_market)) == len(CHECKS)
