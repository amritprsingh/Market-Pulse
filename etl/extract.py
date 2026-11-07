"""Fetch daily OHLCV from yfinance for the tickers in dim_security."""

import logging
import time
from datetime import date

import pandas as pd
import yfinance as yf
from sqlalchemy import Engine, text

BATCH_SIZE = 25
MAX_RETRIES = 3
BACKOFF_SECONDS = 2

log = logging.getLogger(__name__)


def get_tickers(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        query = text("SELECT ticker FROM dim_security ORDER BY ticker")
        return list(conn.execute(query).scalars())


def download_tickers(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    """Download one attempt's data and keep only the tickers that returned prices.

    yf.download reports most per-ticker failures as all-null columns, not exceptions.
    """
    try:
        frame = yf.download(
            tickers,
            start=start.isoformat(),
            end=end.isoformat(),
            interval="1d",
            auto_adjust=False,
            progress=False,
        )
    except Exception as exc:
        log.warning("Download failed for %s: %s", tickers, exc)
        return pd.DataFrame()
    if frame is None or frame.empty:
        return pd.DataFrame()
    returned = frame["Close"].dropna(axis=1, how="all").columns
    return frame.loc[:, frame.columns.get_level_values(1).isin(returned)]


def fetch_batch(
    tickers: list[str], start: date, end: date
) -> tuple[list[pd.DataFrame], list[str]]:
    """Fetch one batch, retrying only the tickers still missing after each attempt."""
    frames = []
    pending = tickers
    for attempt in range(MAX_RETRIES + 1):
        if attempt:
            time.sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))
        frame = download_tickers(pending, start, end)
        if not frame.empty:
            frames.append(frame)
            returned = set(frame.columns.get_level_values(1))
            pending = [ticker for ticker in pending if ticker not in returned]
        if not pending:
            break
    return frames, pending


def fetch_prices(
    tickers: list[str], start: date, end: date
) -> tuple[pd.DataFrame, list[str]]:
    """Return raw two-level (field, ticker) data and the failed tickers.

    end is exclusive, as in yfinance.
    """
    frames = []
    failed = []
    for offset in range(0, len(tickers), BATCH_SIZE):
        batch_frames, batch_failed = fetch_batch(
            tickers[offset : offset + BATCH_SIZE], start, end
        )
        frames.extend(batch_frames)
        failed.extend(batch_failed)
    raw = pd.concat(frames, axis=1).sort_index() if frames else pd.DataFrame()
    return raw, failed
