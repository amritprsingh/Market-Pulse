"""Extract tests with yf.download replaced, so no network calls are made."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from etl import extract

FIELDS = ["Adj Close", "Close", "High", "Low", "Open", "Volume"]
START, END = date(2026, 1, 5), date(2026, 1, 7)


def fake_download(tickers, missing=()):
    columns = pd.MultiIndex.from_product([FIELDS, tickers], names=["Price", "Ticker"])
    index = pd.to_datetime(["2026-01-05", "2026-01-06"])
    frame = pd.DataFrame(1.0, index=index, columns=columns)
    frame.loc[:, frame.columns.get_level_values(1).isin(missing)] = np.nan
    return frame


@pytest.fixture
def sleeps(monkeypatch):
    delays = []
    monkeypatch.setattr(extract.time, "sleep", delays.append)
    return delays


def test_requests_unadjusted_prices(monkeypatch, sleeps):
    calls = []

    def download(tickers, **kwargs):
        calls.append(kwargs)
        return fake_download(tickers)

    monkeypatch.setattr(extract.yf, "download", download)
    extract.fetch_prices(["AAA"], START, END)
    assert calls[0]["auto_adjust"] is False


def test_batches_tickers(monkeypatch, sleeps):
    sizes = []

    def download(tickers, **kwargs):
        sizes.append(len(tickers))
        return fake_download(tickers)

    monkeypatch.setattr(extract.yf, "download", download)
    tickers = [f"T{number}" for number in range(30)]
    raw, failed = extract.fetch_prices(tickers, START, END)
    assert sizes == [25, 5]
    assert failed == []
    assert set(raw.columns.get_level_values(1)) == set(tickers)


def test_retries_only_missing_tickers_then_recovers(monkeypatch, sleeps):
    calls = []

    def download(tickers, **kwargs):
        calls.append(list(tickers))
        return fake_download(tickers, missing=["BBB"] if len(calls) == 1 else [])

    monkeypatch.setattr(extract.yf, "download", download)
    raw, failed = extract.fetch_prices(["AAA", "BBB"], START, END)
    assert calls == [["AAA", "BBB"], ["BBB"]]
    assert failed == []
    assert raw["Close"].notna().all().all()


def test_ticker_missing_after_retries_is_failed(monkeypatch, sleeps):
    calls = []

    def download(tickers, **kwargs):
        calls.append(list(tickers))
        return fake_download(tickers, missing=["BBB"])

    monkeypatch.setattr(extract.yf, "download", download)
    raw, failed = extract.fetch_prices(["AAA", "BBB"], START, END)
    assert len(calls) == 1 + extract.MAX_RETRIES
    assert sleeps == [2, 4, 8]
    assert failed == ["BBB"]
    assert set(raw.columns.get_level_values(1)) == {"AAA"}


def test_download_exception_is_retried_then_failed(monkeypatch, sleeps):
    def download(tickers, **kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr(extract.yf, "download", download)
    raw, failed = extract.fetch_prices(["AAA"], START, END)
    assert raw.empty
    assert failed == ["AAA"]
