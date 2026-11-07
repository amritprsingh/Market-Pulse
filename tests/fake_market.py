"""Hand-built market data for tests.

Dates sit in 1990, before any real data, and tickers are fake, so checks scoped to this
window only ever see these rows.
"""

from datetime import date

import pandas as pd

from etl.transform import OUTPUT_COLUMNS, SOURCE_COLUMNS

FAKE_TICKERS = ["ZZDQ-A", "ZZDQ-B", "ZZDQ-C"]
DAYS = [date(1990, 1, 2), date(1990, 1, 3), date(1990, 1, 4)]


def clean_prices() -> pd.DataFrame:
    """One valid row per fake ticker and day, sorted as transform() sorts them."""
    frame = pd.DataFrame(
        [
            (ticker, day, 10.0, 12.0, 9.0, 11.0, 10.5, 1000)
            for ticker in FAKE_TICKERS
            for day in DAYS
        ],
        columns=OUTPUT_COLUMNS,
    )
    frame["volume"] = frame["volume"].astype("Int64")
    return frame


def row_index(frame: pd.DataFrame, ticker: str, day: date) -> int:
    match = frame[(frame["ticker"] == ticker) & (frame["trade_date"] == day)]
    return match.index[0]


def raw_from(prices: pd.DataFrame) -> pd.DataFrame:
    """Rebuild the two-level frame yf.download returns from long-format prices."""
    prices = prices.astype({"volume": "float64"})
    raw = pd.concat(
        {
            field: prices.pivot(index="trade_date", columns="ticker", values=column)
            for field, column in SOURCE_COLUMNS.items()
        },
        axis=1,
        names=["Price", "Ticker"],
    )
    raw.index = pd.to_datetime(raw.index)
    return raw
