"""Build seeds/tickers.csv: the 10 most liquid S&P 500 tickers in each GICS sector.

Liquidity is average daily dollar volume (close * volume) over the last 3 months.

seeds/tickers.csv is a frozen snapshot generated on 2026-11-06. Commit it; do not
regenerate it on every run. Rankings shift daily, so re-running changes the universe.

Run with `python -m etl.seed_tickers` only to deliberately take a new snapshot.
"""

from io import StringIO

import pandas as pd
import requests
import yfinance as yf

from etl.load import SEED_PATH

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
SOURCE_COLUMNS = {
    "Symbol": "ticker",
    "Security": "company_name",
    "GICS Sector": "sector",
    "GICS Sub-Industry": "industry",
}
TICKERS_PER_SECTOR = 10
SECTOR_COUNT = 11
BATCH_SIZE = 50


def fetch_constituents() -> pd.DataFrame:
    response = requests.get(WIKI_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    tables = pd.read_html(StringIO(response.text))
    table = next(t for t in tables if set(SOURCE_COLUMNS) <= set(t.columns))
    constituents = table[list(SOURCE_COLUMNS)].rename(columns=SOURCE_COLUMNS)
    constituents["ticker"] = constituents["ticker"].str.replace(".", "-", regex=False)
    return constituents


def average_dollar_volume(tickers: list[str]) -> pd.Series:
    """Average daily close * volume per ticker; tickers with no data are left out."""
    averages = []
    for start in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[start : start + BATCH_SIZE]
        prices = yf.download(
            batch, period="3mo", interval="1d", auto_adjust=False, progress=False
        )
        averages.append((prices["Close"] * prices["Volume"]).mean())
    return pd.concat(averages).dropna()


def build_universe(constituents: pd.DataFrame) -> pd.DataFrame:
    """Keep the top tickers by dollar volume in each sector, most liquid first."""
    sector_count = constituents["sector"].nunique()
    if sector_count != SECTOR_COUNT:
        raise RuntimeError(f"Expected {SECTOR_COUNT} sectors, found {sector_count}")

    dollar_volume = average_dollar_volume(constituents["ticker"].tolist())
    skipped = sorted(set(constituents["ticker"]) - set(dollar_volume.index))
    if skipped:
        print(f"Skipped {len(skipped)} tickers with no data: {skipped}")

    ranked = constituents.assign(
        dollar_volume=constituents["ticker"].map(dollar_volume)
    ).dropna(subset=["dollar_volume"])
    ranked = ranked.sort_values(["sector", "dollar_volume"], ascending=[True, False])
    universe = ranked.groupby("sector").head(TICKERS_PER_SECTOR)

    counts = universe["sector"].value_counts()
    short = counts[counts < TICKERS_PER_SECTOR]
    if not short.empty:
        raise RuntimeError(f"Too few tickers with data: {short.to_dict()}")
    return universe.drop(columns="dollar_volume")


def main() -> None:
    universe = build_universe(fetch_constituents())
    universe.to_csv(SEED_PATH, index=False)
    print(f"Wrote {len(universe)} tickers to {SEED_PATH}")


if __name__ == "__main__":
    main()
