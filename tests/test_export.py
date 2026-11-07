"""Export tests. They read the real views and write only to a temp folder."""

import csv
import re

import pytest
from sqlalchemy import inspect, text

from etl.export_tableau import DECIMALS, PRICE_DECIMALS, VIEWS, export_all

NUMBER = re.compile(r"-?\d+(\.(\d+))?")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
PRICE_COLUMNS = {c for c, places in DECIMALS.items() if places == PRICE_DECIMALS}


@pytest.fixture(scope="module")
def output_dir(engine, tmp_path_factory):
    path = tmp_path_factory.mktemp("tableau")
    export_all(path, engine)
    return path


def read_csv(output_dir, view):
    with (output_dir / f"{view}.csv").open(newline="", encoding="utf-8") as file:
        reader = csv.reader(file)
        header = next(reader)
        return header, list(reader)


def column_values(output_dir, view, column):
    header, rows = read_csv(output_dir, view)
    return [row[header.index(column)] for row in rows]


def scalar(engine, query):
    with engine.connect() as conn:
        return conn.execute(text(query)).scalar_one()


def decimal_places(cell):
    match = NUMBER.fullmatch(cell)
    return len(match.group(2) or "") if match else None


@pytest.mark.parametrize("view", VIEWS)
def test_row_count_matches_view(engine, output_dir, view):
    _, rows = read_csv(output_dir, view)
    assert len(rows) == scalar(engine, f"SELECT count(*) FROM {view}")


@pytest.mark.parametrize("view", VIEWS)
def test_header_matches_view_columns(engine, output_dir, view):
    header, _ = read_csv(output_dir, view)
    assert header == [column["name"] for column in inspect(engine).get_columns(view)]


@pytest.mark.parametrize("view", VIEWS)
def test_no_cell_has_more_than_six_decimals(output_dir, view):
    header, rows = read_csv(output_dir, view)
    for column in set(header) - PRICE_COLUMNS:
        index = header.index(column)
        places = {decimal_places(row[index]) for row in rows} - {None}
        assert not places or max(places) <= 6, column


@pytest.mark.parametrize("view", VIEWS)
def test_rounded_columns_have_their_exact_decimals(output_dir, view):
    header, _ = read_csv(output_dir, view)
    for column in set(header) & set(DECIMALS):
        cells = [cell for cell in column_values(output_dir, view, column) if cell]
        assert {decimal_places(cell) for cell in cells} == {DECIMALS[column]}, column


@pytest.mark.parametrize(
    "view,column",
    [
        ("v_price_daily", "volume"),
        ("v_sector_daily", "ticker_count"),
        ("v_sector_daily", "total_volume"),
        ("v_pipeline_health", "rows_compared"),
        ("v_pipeline_health", "failed_dq_checks"),
    ],
)
def test_whole_numbers_stay_whole_even_beside_nulls(output_dir, view, column):
    cells = [cell for cell in column_values(output_dir, view, column) if cell]
    assert cells
    assert all(cell.lstrip("-").isdigit() for cell in cells)


@pytest.mark.parametrize(
    "view,column",
    [
        ("v_price_daily", "daily_return"),
        ("v_price_daily", "ma_20"),
        ("v_pipeline_health", "failed_dq_checks"),
        ("v_pipeline_health", "discrepancy_pct"),
    ],
)
def test_nulls_are_empty_cells(engine, output_dir, view, column):
    expected = scalar(engine, f"SELECT count(*) FROM {view} WHERE {column} IS NULL")
    assert column_values(output_dir, view, column).count("") == expected


@pytest.mark.parametrize(
    "view,column",
    [
        ("v_price_daily", "trade_date"),
        ("v_sector_daily", "trade_date"),
        ("v_ticker_summary", "latest_trade_date"),
    ],
)
def test_dates_are_iso(output_dir, view, column):
    assert all(DATE.fullmatch(cell) for cell in column_values(output_dir, view, column))


def test_run_timestamps_have_no_offset(output_dir):
    cells = column_values(output_dir, "v_pipeline_health", "started_at")
    assert all(TIMESTAMP.fullmatch(cell) for cell in cells)


def test_exported_value_is_the_view_value_rounded(engine, output_dir):
    header, rows = read_csv(output_dir, "v_sector_daily")
    last = dict(zip(header, rows[-1]))
    expected = scalar(
        engine,
        f"""
        SELECT round(sector_index, 2) FROM v_sector_daily
        WHERE sector = '{last["sector"]}' AND trade_date = '{last["trade_date"]}'
        """,
    )
    assert last["sector_index"] == str(expected)


def test_readme_explains_sector_index(engine, output_dir):
    readme = (output_dir / "README.txt").read_text(encoding="utf-8")
    first_date = scalar(engine, "SELECT min(trade_date) FROM v_sector_daily")
    assert "top-10 most-traded S&P 500 tickers per sector" in readme
    assert f"rebased\nto 100 on the first date ({first_date})" in readme
    assert "equal-weighted" in readme
    assert "not a sector benchmark" in readme
