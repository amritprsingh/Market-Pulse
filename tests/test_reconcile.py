"""Reconciliation tests. The download is replaced, so no network calls are made."""

from datetime import timedelta
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import text

from etl import reconcile as reconcile_module
from etl.load import upsert_prices
from etl.reconcile import (
    ADJ_CLOSE_TOLERANCE,
    CLOSE_TOLERANCE,
    VOLUME_TOLERANCE,
    reconcile,
)
from tests.fake_market import DAYS, clean_prices, raw_from, row_index

START, END = DAYS[0], DAYS[-1]
TICKER = "ZZDQ-A"


@pytest.fixture
def stored(engine, fake_market):
    prices = clean_prices()
    upsert_prices(prices, engine)
    return prices


@pytest.fixture
def source(monkeypatch):
    """Set what the mocked download returns; records the requested windows."""
    frames = {"prices": clean_prices()}
    frames["calls"] = []

    def fetch(tickers, start, end):
        frames["calls"].append((start, end))
        return raw_from(frames["prices"]), []

    monkeypatch.setattr(reconcile_module, "fetch_prices", fetch)
    return frames


def saved_result(engine, run_id):
    query = text("""
        SELECT source_name, rows_compared, rows_mismatched, discrepancy_pct, checked_at
        FROM reconciliation_result WHERE run_id = :run_id
    """)
    with engine.connect() as conn:
        return conn.execute(query, {"run_id": run_id}).mappings().one()


def perturb(source, column, factor):
    index = row_index(source["prices"], TICKER, DAYS[1])
    source["prices"][column] = source["prices"][column].astype("float64")
    source["prices"].loc[index, column] *= factor


def test_identical_data_is_zero_percent(engine, fake_market, stored, source):
    reconcile(fake_market, START, END, engine)
    row = saved_result(engine, fake_market)
    assert row["source_name"] == "yfinance"
    assert row["rows_compared"] == 9
    assert row["rows_mismatched"] == 0
    assert row["discrepancy_pct"] == Decimal("0.0000")
    assert row["checked_at"] is not None


def test_close_beyond_tolerance_is_mismatched(engine, fake_market, stored, source):
    perturb(source, "close", 1 + 2 * CLOSE_TOLERANCE)
    reconcile(fake_market, START, END, engine)
    row = saved_result(engine, fake_market)
    assert (row["rows_compared"], row["rows_mismatched"]) == (9, 1)
    assert row["discrepancy_pct"] == Decimal("11.1111")


def test_close_within_tolerance_matches(engine, fake_market, stored, source):
    perturb(source, "close", 1 + CLOSE_TOLERANCE / 2)
    assert reconcile(fake_market, START, END, engine)["rows_mismatched"] == 0


def test_adj_close_beyond_tolerance_is_mismatched(engine, fake_market, stored, source):
    perturb(source, "adj_close", 1 + 2 * ADJ_CLOSE_TOLERANCE)
    reconcile(fake_market, START, END, engine)
    row = saved_result(engine, fake_market)
    assert (row["rows_compared"], row["rows_mismatched"]) == (9, 1)
    assert row["discrepancy_pct"] == Decimal("11.1111")


def test_adj_close_jitter_matches(engine, fake_market, stored, source):
    perturb(source, "adj_close", 1 + 1e-6)
    assert reconcile(fake_market, START, END, engine)["rows_mismatched"] == 0


def test_adj_close_null_on_one_side_is_mismatched(engine, fake_market, source):
    prices = clean_prices()
    prices.loc[row_index(prices, TICKER, DAYS[1]), "adj_close"] = float("nan")
    upsert_prices(prices, engine)
    assert reconcile(fake_market, START, END, engine)["rows_mismatched"] == 1


def test_volume_beyond_tolerance_is_mismatched(engine, fake_market, stored, source):
    perturb(source, "volume", 1 + 2 * VOLUME_TOLERANCE)
    assert reconcile(fake_market, START, END, engine)["rows_mismatched"] == 1


def test_volume_within_tolerance_matches(engine, fake_market, stored, source):
    perturb(source, "volume", 1 + VOLUME_TOLERANCE / 2)
    assert reconcile(fake_market, START, END, engine)["rows_mismatched"] == 0


def test_row_only_in_database_is_mismatched(engine, fake_market, stored, source):
    source["prices"] = source["prices"].drop(
        row_index(source["prices"], TICKER, DAYS[1])
    )
    reconcile(fake_market, START, END, engine)
    row = saved_result(engine, fake_market)
    assert (row["rows_compared"], row["rows_mismatched"]) == (9, 1)
    assert row["discrepancy_pct"] == Decimal("11.1111")


def test_row_only_in_source_is_mismatched(engine, fake_market, source):
    prices = clean_prices()
    upsert_prices(prices.drop(row_index(prices, TICKER, DAYS[1])), engine)
    reconcile(fake_market, START, END, engine)
    row = saved_result(engine, fake_market)
    assert (row["rows_compared"], row["rows_mismatched"]) == (9, 1)
    assert row["discrepancy_pct"] == Decimal("11.1111")


def test_one_sided_rows_on_both_sides_and_a_bad_close(engine, fake_market, source):
    prices = clean_prices()
    upsert_prices(prices.drop(row_index(prices, "ZZDQ-B", DAYS[0])), engine)
    source["prices"] = source["prices"].drop(
        row_index(source["prices"], "ZZDQ-C", DAYS[0])
    )
    perturb(source, "close", 1.05)
    reconcile(fake_market, START, END, engine)
    row = saved_result(engine, fake_market)
    assert (row["rows_compared"], row["rows_mismatched"]) == (9, 3)
    assert row["discrepancy_pct"] == Decimal("33.3333")


def test_null_on_one_side_is_mismatched(engine, fake_market, source):
    prices = clean_prices()
    prices.loc[row_index(prices, TICKER, DAYS[1]), "volume"] = pd.NA
    upsert_prices(prices, engine)
    assert reconcile(fake_market, START, END, engine)["rows_mismatched"] == 1


def test_empty_download_counts_every_stored_row(engine, fake_market, stored, monkeypatch):
    monkeypatch.setattr(
        reconcile_module, "fetch_prices", lambda *args: (pd.DataFrame(), ["ZZDQ-A"])
    )
    result = reconcile(fake_market, START, END, engine)
    assert (result["rows_compared"], result["rows_mismatched"]) == (9, 9)
    assert result["discrepancy_pct"] == 100.0


def test_nothing_on_either_side_is_recorded(engine, fake_market, monkeypatch):
    monkeypatch.setattr(
        reconcile_module, "fetch_prices", lambda *args: (pd.DataFrame(), [])
    )
    result = reconcile(fake_market, START, END, engine)
    assert (result["rows_compared"], result["discrepancy_pct"]) == (0, 0.0)


def test_download_end_covers_the_last_day(engine, fake_market, stored, source):
    reconcile(fake_market, START, END, engine)
    assert source["calls"] == [(START, END + timedelta(days=1))]


def test_rerun_replaces_the_result(engine, fake_market, stored, source):
    reconcile(fake_market, START, END, engine)
    perturb(source, "close", 1.05)
    reconcile(fake_market, START, END, engine)
    assert saved_result(engine, fake_market)["rows_mismatched"] == 1
