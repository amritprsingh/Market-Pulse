"""Build seeds/tickers.csv: 10 validated S&P 500 tickers from each GICS sector.

Run with `python -m etl.seed_tickers`.
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


def fetch_constituents() -> pd.DataFrame:
    response = requests.get(WIKI_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    tables = pd.read_html(StringIO(response.text))
    table = next(t for t in tables if set(SOURCE_COLUMNS) <= set(t.columns))
    constituents = table[list(SOURCE_COLUMNS)].rename(columns=SOURCE_COLUMNS)
    constituents["ticker"] = constituents["ticker"].str.replace(".", "-", regex=False)
    return constituents


def has_price_data(ticker: str) -> bool:
    prices = yf.download(
        ticker, period="1mo", interval="1d", auto_adjust=False, progress=False
    )
    return prices is not None and not prices.dropna(how="all").empty


def select_sector(candidates: pd.DataFrame) -> pd.DataFrame:
    """Take the first tickers in table order that return data."""
    selected = []
    for position, row in candidates.iterrows():
        if has_price_data(row["ticker"]):
            selected.append(position)
        else:
            print(f"No data for {row['ticker']}; trying next in {row['sector']}")
        if len(selected) == TICKERS_PER_SECTOR:
            return candidates.loc[selected]
    sector = candidates["sector"].iloc[0]
    raise RuntimeError(f"Only {len(selected)} valid tickers in {sector}")


def build_universe(constituents: pd.DataFrame) -> pd.DataFrame:
    sectors = constituents["sector"].unique()
    if len(sectors) != SECTOR_COUNT:
        raise RuntimeError(f"Expected {SECTOR_COUNT} sectors, found {len(sectors)}")
    return pd.concat(
        select_sector(constituents[constituents["sector"] == sector])
        for sector in sectors
    )


def main() -> None:
    universe = build_universe(fetch_constituents())
    universe.to_csv(SEED_PATH, index=False)
    print(f"Wrote {len(universe)} tickers to {SEED_PATH}")


if __name__ == "__main__":
    main()
