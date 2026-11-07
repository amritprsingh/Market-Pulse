"""upsert_prices tests. These only write rows for tickers outside the real universe
and remove exactly those rows afterwards."""

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import bindparam, text

from etl.db import apply_schema, get_engine
from etl.load import upsert_prices
from etl.transform import OUTPUT_COLUMNS

TEST_TICKERS = ["ZZTEST-A", "ZZTEST-B"]


def delete_test_rows(engine):
    with engine.begin() as conn:
        for table in ("fact_daily_price", "dim_security"):
            conn.execute(
                text(f"DELETE FROM {table} WHERE ticker IN :tickers").bindparams(
                    bindparam("tickers", expanding=True)
                ),
                {"tickers": TEST_TICKERS},
            )


@pytest.fixture(scope="module")
def engine():
    engine = get_engine()
    apply_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def test_securities(engine):
    delete_test_rows(engine)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO dim_security (ticker) VALUES (:ticker)"),
            [{"ticker": ticker} for ticker in TEST_TICKERS],
        )
    yield
    delete_test_rows(engine)


def prices_frame():
    frame = pd.DataFrame(
        [
            ("ZZTEST-A", date(2026, 1, 5), 10.0, 12.0, 9.0, 11.0, 10.5, 1000),
            ("ZZTEST-A", date(2026, 1, 6), 11.0, 13.0, 10.0, 12.0, 11.5, 2000),
            ("ZZTEST-B", date(2026, 1, 5), 20.0, 22.0, 19.0, 21.0, 20.5, 3000),
        ],
        columns=OUTPUT_COLUMNS,
    )
    frame["volume"] = frame["volume"].astype("Int64")
    return frame


def stored_rows(engine):
    query = text("""
        SELECT ticker, trade_date, open, high, low, close, adj_close, volume, loaded_at
        FROM fact_daily_price
        WHERE ticker LIKE 'ZZTEST-%'
        ORDER BY ticker, trade_date
    """)
    with engine.connect() as conn:
        return conn.execute(query).mappings().all()


def test_first_load_inserts_every_row(engine, test_securities):
    assert upsert_prices(prices_frame(), engine) == (3, 0)
    rows = stored_rows(engine)
    assert len(rows) == 3
    assert rows[0]["close"] == Decimal("11.0000")
    assert rows[0]["volume"] == 1000


def test_loading_same_frame_twice_changes_nothing(engine, test_securities):
    upsert_prices(prices_frame(), engine)
    first = stored_rows(engine)
    assert upsert_prices(prices_frame(), engine) == (0, 0)
    assert stored_rows(engine) == first


def test_changed_close_updates_only_that_row(engine, test_securities):
    upsert_prices(prices_frame(), engine)
    before = stored_rows(engine)

    changed = prices_frame()
    changed.loc[1, "close"] = 12.5
    assert upsert_prices(changed, engine) == (0, 1)

    after = stored_rows(engine)
    assert len(after) == 3
    assert after[1]["close"] == Decimal("12.5000")
    assert after[1]["loaded_at"] > before[1]["loaded_at"]
    assert after[0] == before[0]
    assert after[2] == before[2]


def test_new_and_changed_rows_in_one_load(engine, test_securities):
    upsert_prices(prices_frame().iloc[:2], engine)
    changed = prices_frame()
    changed.loc[0, "adj_close"] = 10.25
    assert upsert_prices(changed, engine) == (1, 1)


def test_counts_add_up_across_chunks(engine, test_securities):
    assert upsert_prices(prices_frame(), engine, chunk_size=2) == (3, 0)
    assert len(stored_rows(engine)) == 3


def test_nulls_are_stored_as_null(engine, test_securities):
    frame = prices_frame()
    frame.loc[0, "close"] = np.nan
    frame.loc[0, "volume"] = pd.NA
    upsert_prices(frame, engine)
    first = stored_rows(engine)[0]
    assert first["close"] is None
    assert first["volume"] is None
    assert upsert_prices(frame, engine) == (0, 0)


def test_failed_load_rolls_back_every_chunk(engine, test_securities):
    frame = prices_frame()
    frame.loc[2, "ticker"] = "ZZTEST-NOT-IN-DIM"
    with pytest.raises(Exception):
        upsert_prices(frame, engine, chunk_size=2)
    assert stored_rows(engine) == []


def high_priced_frame():
    """One row priced so a 1e-6 relative move survives NUMERIC(14,4) rounding."""
    frame = prices_frame().iloc[:1].copy()
    frame[["open", "high", "low", "close", "adj_close"]] = 1000.0
    return frame


def reload_with(engine, column, value):
    """Load a row, reload it with one value changed; return counts and both rows."""
    upsert_prices(high_priced_frame(), engine)
    before = stored_rows(engine)[0]
    changed = high_priced_frame()
    changed[column] = changed[column].astype(object)
    changed.loc[0, column] = value
    changed = changed.astype(high_priced_frame().dtypes.to_dict())
    counts = upsert_prices(changed, engine)
    return counts, before, stored_rows(engine)[0]


def test_adj_close_jitter_leaves_the_row_and_loaded_at_alone(engine, test_securities):
    counts, before, after = reload_with(engine, "adj_close", 1000.0 * (1 + 1e-6))
    assert counts == (0, 0)
    assert after == before


def test_real_adj_close_change_updates_the_row(engine, test_securities):
    counts, before, after = reload_with(engine, "adj_close", 1000.0 * (1 + 1e-3))
    assert counts == (0, 1)
    assert after["adj_close"] == Decimal("1001.0000")
    assert after["loaded_at"] > before["loaded_at"]


@pytest.mark.parametrize(
    "column,value",
    [
        ("open", 1000.0001),
        ("high", 1000.0001),
        ("low", 999.9999),
        ("close", 1000.0001),
        ("volume", 1001),
    ],
)
def test_smallest_change_in_other_columns_updates_the_row(
    engine, test_securities, column, value
):
    counts, before, after = reload_with(engine, column, value)
    assert counts == (0, 1)
    assert after[column] != before[column]
    assert after["loaded_at"] > before["loaded_at"]


def test_adj_close_gaining_or_losing_a_value_updates_the_row(engine, test_securities):
    counts, _, after = reload_with(engine, "adj_close", np.nan)
    assert counts == (0, 1)
    assert after["adj_close"] is None
    assert upsert_prices(high_priced_frame(), engine) == (0, 1)
    assert stored_rows(engine)[0]["adj_close"] == Decimal("1000.0000")
