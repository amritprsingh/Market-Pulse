"""Shared fixtures. fake_market writes only fake tickers and removes exactly those rows
and its own etl_run_log row afterwards."""

import pytest
from sqlalchemy import text

from etl.db import apply_schema, get_engine
from tests.fake_market import FAKE_TICKERS


@pytest.fixture(scope="session")
def engine():
    engine = get_engine()
    apply_schema(engine)
    yield engine
    engine.dispose()


def delete_fake_securities(engine):
    with engine.begin() as conn:
        for table in ("fact_daily_price", "dim_security"):
            conn.execute(
                text(f"DELETE FROM {table} WHERE ticker = ANY(:tickers)"),
                {"tickers": FAKE_TICKERS},
            )


@pytest.fixture
def fake_market(engine):
    """Create the fake securities and an etl_run_log row; yield its run_id."""
    delete_fake_securities(engine)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO dim_security (ticker) VALUES (:ticker)"),
            [{"ticker": ticker} for ticker in FAKE_TICKERS],
        )
        run_id = conn.execute(
            text("INSERT INTO etl_run_log (status) VALUES ('test') RETURNING run_id")
        ).scalar_one()
    yield run_id
    with engine.begin() as conn:
        for table in ("dq_check_result", "reconciliation_result", "etl_run_log"):
            conn.execute(
                text(f"DELETE FROM {table} WHERE run_id = :run_id"), {"run_id": run_id}
            )
    delete_fake_securities(engine)
