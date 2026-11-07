"""Seed tests. The load test upserts the seed rows into DATABASE_URL and deletes nothing."""

import pandas as pd
import pytest
from sqlalchemy import text

from etl.db import apply_schema, get_engine
from etl.load import SEED_PATH, load_dim_security


@pytest.fixture(scope="module")
def seed():
    return pd.read_csv(SEED_PATH, dtype=str, keep_default_na=False)


@pytest.fixture(scope="module")
def engine():
    engine = get_engine()
    apply_schema(engine)
    yield engine
    engine.dispose()


def security_rows(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT * FROM dim_security ORDER BY ticker")).all()


def test_columns(seed):
    assert list(seed.columns) == ["ticker", "company_name", "sector", "industry"]


def test_110_unique_tickers(seed):
    assert len(seed) == 110
    assert seed["ticker"].is_unique


def test_11_sectors_with_10_each(seed):
    counts = seed["sector"].value_counts()
    assert len(counts) == 11
    assert (counts == 10).all()


def test_no_blank_fields(seed):
    assert not seed.apply(lambda column: column.str.strip().eq("")).any().any()


def test_tickers_use_yfinance_format(seed):
    assert not seed["ticker"].str.contains(".", regex=False).any()


def test_load_dim_security_is_idempotent(engine, seed):
    assert load_dim_security(engine) == len(seed)
    first = security_rows(engine)
    assert load_dim_security(engine) == len(seed)
    assert security_rows(engine) == first
