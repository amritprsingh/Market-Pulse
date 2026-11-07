"""Schema tests. These run against DATABASE_URL and only read data, apart from
re-applying the idempotent schema, so existing rows are left untouched."""

import pytest
from sqlalchemy import inspect, text

from etl.db import apply_schema, get_engine

EXPECTED_PRIMARY_KEYS = {
    "dim_security": ["ticker"],
    "fact_daily_price": ["ticker", "trade_date"],
    "etl_run_log": ["run_id"],
    "dq_check_result": ["run_id", "check_name"],
    "reconciliation_result": ["run_id"],
}

EXPECTED_FOREIGN_KEYS = {
    "fact_daily_price": ("ticker", "dim_security"),
    "dq_check_result": ("run_id", "etl_run_log"),
    "reconciliation_result": ("run_id", "etl_run_log"),
}


@pytest.fixture(scope="module")
def engine():
    engine = get_engine()
    apply_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def inspector(engine):
    return inspect(engine)


def row_counts(engine):
    with engine.connect() as conn:
        return {
            table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in EXPECTED_PRIMARY_KEYS
        }


def test_all_tables_exist(inspector):
    assert set(EXPECTED_PRIMARY_KEYS) <= set(inspector.get_table_names())


@pytest.mark.parametrize("table,columns", EXPECTED_PRIMARY_KEYS.items())
def test_primary_keys(inspector, table, columns):
    assert inspector.get_pk_constraint(table)["constrained_columns"] == columns


@pytest.mark.parametrize("table,fk", EXPECTED_FOREIGN_KEYS.items())
def test_foreign_keys(inspector, table, fk):
    column, referred_table = fk
    found = [
        (key["constrained_columns"], key["referred_table"])
        for key in inspector.get_foreign_keys(table)
    ]
    assert ([column], referred_table) in found


def test_price_columns_are_numeric_14_4(inspector):
    columns = {c["name"]: c["type"] for c in inspector.get_columns("fact_daily_price")}
    for name in ("open", "high", "low", "close", "adj_close"):
        assert (columns[name].precision, columns[name].scale) == (14, 4)


def test_trade_date_is_indexed(inspector):
    indexes = inspector.get_indexes("fact_daily_price")
    assert ["trade_date"] in [index["column_names"] for index in indexes]


def test_reapplying_schema_keeps_existing_rows(engine):
    before = row_counts(engine)
    apply_schema(engine)
    assert row_counts(engine) == before
