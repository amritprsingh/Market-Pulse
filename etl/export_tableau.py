"""Export the reporting views to CSV for Tableau Public, which can't reach Postgres.

Run with `python -m etl.export_tableau`.
"""

import csv
from pathlib import Path

from sqlalchemy import Engine, inspect, text

from etl.db import get_engine

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "tableau"

# Each view with its sort order, so repeat exports are stable and comparable.
VIEWS = {
    "v_price_daily": "ticker, trade_date",
    "v_sector_daily": "sector, trade_date",
    "v_ticker_summary": "ticker",
    "v_pipeline_health": "run_id",
}

PRICE_DECIMALS = 4
RETURN_DECIMALS = 6
DECIMALS = {
    "open": PRICE_DECIMALS,
    "high": PRICE_DECIMALS,
    "low": PRICE_DECIMALS,
    "close": PRICE_DECIMALS,
    "adj_close": PRICE_DECIMALS,
    "latest_close": PRICE_DECIMALS,
    "high_52w": PRICE_DECIMALS,
    "low_52w": PRICE_DECIMALS,
    "daily_return": RETURN_DECIMALS,
    "avg_daily_return": RETURN_DECIMALS,
    "return_1m": RETURN_DECIMALS,
    "return_3m": RETURN_DECIMALS,
    "return_1y": RETURN_DECIMALS,
    "ma_20": RETURN_DECIMALS,
    "ma_50": RETURN_DECIMALS,
    "vol_avg_30": RETURN_DECIMALS,
    "avg_volume_30": RETURN_DECIMALS,
    "sector_index": 2,
    "dollar_volume": 0,
    "total_dollar_volume": 0,
}

# Tableau can misread a timestamp with a bare UTC offset, so export plain market time.
TIMESTAMP_COLUMNS = {
    "started_at": (
        "to_char(started_at AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI:SS')"
    ),
}

README = """MarketPulse export for Tableau Public

Files: one CSV per reporting view, UTF-8, with a header row. Dates are YYYY-MM-DD.
Empty cells are nulls.

sector_index tracks the current top-10 most-traded S&P 500 tickers per sector, rebased
to 100 on the first date ({first_date}), equal-weighted. It is not a sector benchmark:
the tickers were chosen by recent trading activity and then tracked back through
history, so it reflects how today's most-traded names performed, not the sector.

Rounding: prices to 4 decimals, returns and moving averages to 6, sector_index to 2,
dollar volumes to 0.

v_pipeline_health: started_at is US Eastern time. An empty failed_dq_checks means no
checks were recorded for that run, not that they passed.
"""


def view_query(engine: Engine, view: str) -> tuple[list[str], str]:
    """Return a view's columns and a query selecting them, rounded for export."""
    columns = [column["name"] for column in inspect(engine).get_columns(view)]
    selected = []
    for column in columns:
        if column in DECIMALS:
            selected.append(f'ROUND("{column}", {DECIMALS[column]}) AS "{column}"')
        elif column in TIMESTAMP_COLUMNS:
            selected.append(f'{TIMESTAMP_COLUMNS[column]} AS "{column}"')
        else:
            selected.append(f'"{column}"')
    return columns, f"SELECT {', '.join(selected)} FROM {view} ORDER BY {VIEWS[view]}"


def export_all(
    output_dir: Path = OUTPUT_DIR, engine: Engine | None = None
) -> list[tuple[Path, int]]:
    """Write every view and the README; return (path, data rows) for each CSV."""
    engine = engine or get_engine()
    output_dir.mkdir(parents=True, exist_ok=True)
    queries = {view: view_query(engine, view) for view in VIEWS}
    written = []
    # One snapshot, so the files agree even if a scheduled run loads during the export.
    with engine.connect().execution_options(
        isolation_level="REPEATABLE READ", postgresql_readonly=True
    ) as conn:
        for view, (columns, query) in queries.items():
            path = output_dir / f"{view}.csv"
            rows = 0
            with path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file, lineterminator="\n")
                writer.writerow(columns)
                for row in conn.execute(text(query)):
                    writer.writerow(row)
                    rows += 1
            written.append((path, rows))
        first_date = conn.execute(
            text("SELECT min(trade_date) FROM v_sector_daily")
        ).scalar_one()
    (output_dir / "README.txt").write_text(
        README.format(first_date=first_date), encoding="utf-8"
    )
    return written


def main() -> None:
    for path, rows in export_all():
        print(f"{path.name:<24}{rows:>8} rows{path.stat().st_size:>12} bytes")


if __name__ == "__main__":
    main()
