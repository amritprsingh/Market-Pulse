"""Reshape raw yfinance data into rows for fact_daily_price.

Suspicious values are kept as they are for the quality checks to flag.
"""

import pandas as pd

PRICE_COLUMNS = ["open", "high", "low", "close", "adj_close"]
OUTPUT_COLUMNS = ["ticker", "trade_date", *PRICE_COLUMNS, "volume"]
SOURCE_COLUMNS = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}


def transform(raw: pd.DataFrame) -> pd.DataFrame:
    """Flatten (field, ticker) columns into one row per ticker and trade date."""
    if raw.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    prices = (
        raw.rename_axis(index="trade_date", columns=["field", "ticker"])
        .stack("ticker", future_stack=True)
        .reset_index()
        .rename(columns=SOURCE_COLUMNS)
        .dropna(subset=PRICE_COLUMNS, how="all")
    )
    # Dropping the timezone keeps the exchange's calendar date.
    trade_dates = pd.to_datetime(prices["trade_date"])
    if trade_dates.dt.tz is not None:
        trade_dates = trade_dates.dt.tz_localize(None)
    prices["trade_date"] = trade_dates.dt.date
    # Nullable integers, so a missing volume stays null instead of becoming 0.
    prices["volume"] = prices["volume"].round().astype("Int64")
    return (
        prices[OUTPUT_COLUMNS]
        .sort_values(["ticker", "trade_date"])
        .reset_index(drop=True)
    )
