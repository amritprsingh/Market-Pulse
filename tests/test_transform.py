"""Transform tests on hand-built frames shaped like yf.download output."""

from datetime import date

import numpy as np
import pandas as pd

from etl.transform import OUTPUT_COLUMNS, transform


def raw_frame(values):
    """Build a frame from {(field, ticker): [value for each of two dates]}."""
    frame = pd.DataFrame(values, index=pd.to_datetime(["2026-01-05", "2026-01-06"]))
    frame.index.name = "Date"
    frame.columns = pd.MultiIndex.from_tuples(frame.columns, names=["Price", "Ticker"])
    return frame


def full_ticker(ticker, base):
    return {
        ("Open", ticker): [base, base + 1],
        ("High", ticker): [base + 2, base + 3],
        ("Low", ticker): [base - 2, base - 1],
        ("Close", ticker): [base + 0.5, base + 1.5],
        ("Adj Close", ticker): [base + 0.25, base + 1.25],
        ("Volume", ticker): [1000.0, 2000.0],
    }


def test_flattens_two_level_columns():
    result = transform(raw_frame(full_ticker("AAA", 10.0)))
    assert list(result.columns) == OUTPUT_COLUMNS
    assert result.columns.nlevels == 1
    first = result.iloc[0]
    assert (first["ticker"], first["trade_date"]) == ("AAA", date(2026, 1, 5))
    assert first["open"] == 10.0
    assert first["high"] == 12.0
    assert first["low"] == 8.0
    assert first["close"] == 10.5
    assert first["adj_close"] == 10.25
    assert first["volume"] == 1000


def test_sorts_by_ticker_then_date():
    raw = raw_frame({**full_ticker("BBB", 20.0), **full_ticker("AAA", 10.0)})
    result = transform(raw.iloc[::-1])
    assert result["ticker"].tolist() == ["AAA", "AAA", "BBB", "BBB"]
    assert result["trade_date"].tolist() == [
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 5),
        date(2026, 1, 6),
    ]


def test_drops_rows_where_every_price_is_null():
    values = full_ticker("AAA", 10.0)
    for field in ("Open", "High", "Low", "Close", "Adj Close"):
        values[(field, "AAA")][0] = np.nan
    result = transform(raw_frame(values))
    assert result["trade_date"].tolist() == [date(2026, 1, 6)]


def test_keeps_rows_with_only_some_prices_null():
    values = full_ticker("AAA", 10.0)
    values[("Close", "AAA")][0] = np.nan
    result = transform(raw_frame(values))
    assert len(result) == 2
    assert pd.isna(result.loc[0, "close"])


def test_dtypes():
    result = transform(raw_frame(full_ticker("AAA", 10.0)))
    assert isinstance(result.loc[0, "trade_date"], date)
    assert result["volume"].dtype == "Int64"
    for column in ("open", "high", "low", "close", "adj_close"):
        assert pd.api.types.is_float_dtype(result[column])


def test_timezone_is_removed_without_shifting_the_date():
    raw = raw_frame(full_ticker("AAA", 10.0))
    raw.index = raw.index.tz_localize("America/New_York")
    assert transform(raw)["trade_date"].tolist() == [
        date(2026, 1, 5),
        date(2026, 1, 6),
    ]


def test_null_volume_stays_null():
    values = full_ticker("AAA", 10.0)
    values[("Volume", "AAA")][0] = np.nan
    result = transform(raw_frame(values))
    assert pd.isna(result.loc[0, "volume"])
    assert result.loc[1, "volume"] == 2000


def test_suspicious_values_are_not_changed():
    values = full_ticker("AAA", 10.0)
    values[("High", "AAA")][0] = 1.0  # high below low
    values[("Close", "AAA")][0] = -5.0
    values[("Volume", "AAA")][0] = 0.0
    first = transform(raw_frame(values)).iloc[0]
    assert (first["high"], first["close"], first["volume"]) == (1.0, -5.0, 0)


def test_empty_input_gives_empty_output():
    result = transform(pd.DataFrame())
    assert result.empty
    assert list(result.columns) == OUTPUT_COLUMNS


def test_large_volume_is_exact():
    values = full_ticker("AAA", 10.0)
    values[("Volume", "AAA")][0] = 1234567890123.0
    assert transform(raw_frame(values)).loc[0, "volume"] == 1234567890123
