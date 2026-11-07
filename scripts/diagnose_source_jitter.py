"""Read-only diagnostic: why does a full refresh update rows minutes after the last one?

Pulls the 2-year window twice and compares the pulls with each other and with
fact_daily_price. Nothing is written: the database session is read-only.

Run from the repo root with `python -m scripts.diagnose_source_jitter`.
"""

import time
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
from sqlalchemy import Engine, text

from etl.db import get_engine
from etl.extract import fetch_prices, get_tickers
from etl.run_daily import MARKET_TIMEZONE, REFRESH_HISTORY_YEARS, date_window
from etl.transform import PRICE_COLUMNS, transform

WAIT_SECONDS = 60
STORAGE_STEP = 0.0001
TOP_N = 10
COMPARE_COLUMNS = [*PRICE_COLUMNS, "volume"]
KEY_COLUMNS = ["ticker", "trade_date"]
STEP = Decimal("0.0001")

STORED_PRICES = text("""
    SELECT ticker, trade_date, open, high, low, close, adj_close, volume
    FROM fact_daily_price
    WHERE trade_date BETWEEN :start_date AND :end_date
""")


def storage_value(value: float) -> float:
    """Round a float as Postgres does for float8 into NUMERIC(14,4).

    Postgres keeps 15 significant digits, then rounds half away from zero.
    """
    if pd.isna(value):
        return value
    return float(Decimal(f"{value:.15g}").quantize(STEP, rounding=ROUND_HALF_UP))


def as_stored(prices: pd.DataFrame) -> pd.DataFrame:
    rounded = prices.copy()
    for column in PRICE_COLUMNS:
        rounded[column] = rounded[column].map(storage_value)
    return rounded


def pull(tickers: list[str], start, end, label: str) -> pd.DataFrame:
    raw, failed = fetch_prices(tickers, start, end)
    prices = transform(raw)
    print(f"{label}: {len(prices)} rows, {len(failed)} failed tickers {failed or ''}")
    return prices


def align(left: pd.DataFrame, right: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Join on the key; return shared rows and the counts found on one side only."""
    merged = left.merge(
        right, on=KEY_COLUMNS, how="outer", suffixes=("_a", "_b"), indicator=True
    )
    left_only = int((merged["_merge"] == "left_only").sum())
    right_only = int((merged["_merge"] == "right_only").sum())
    return merged[merged["_merge"] == "both"].reset_index(drop=True), left_only, right_only


def column_values(shared: pd.DataFrame, column: str) -> tuple[pd.Series, pd.Series]:
    return (
        pd.to_numeric(shared[f"{column}_a"]).astype(float),
        pd.to_numeric(shared[f"{column}_b"]).astype(float),
    )


def differing_masks(shared: pd.DataFrame) -> pd.DataFrame:
    """One boolean column per compared field, true where the two sides differ."""
    masks = {}
    for column in COMPARE_COLUMNS:
        a, b = column_values(shared, column)
        masks[column] = (a != b) & ~(a.isna() & b.isna())
    return pd.DataFrame(masks)


def print_comparison(title: str, left: pd.DataFrame, right: pd.DataFrame) -> dict:
    shared, left_only, right_only = align(left, right)
    masks = differing_masks(shared)
    any_differs = masks.any(axis=1)

    print(f"\n=== {title} ===")
    print(
        f"Shared rows: {len(shared)} | one side only: {left_only} + {right_only} | "
        f"rows differing in any column: {int(any_differs.sum())}"
    )
    print(
        f"{'column':<10}{'differing':>11}{'max abs':>16}{'max rel':>13}"
        f"{'abs < 0.0001':>14}"
    )
    for column in COMPARE_COLUMNS:
        a, b = column_values(shared, column)
        differs = masks[column]
        absolute = (a - b).abs()[differs]
        relative = (absolute / b.abs()[differs]).replace(float("inf"), float("nan"))
        max_abs = f"{absolute.max():.6f}" if absolute.notna().any() else "-"
        max_rel = f"{relative.max():.3e}" if relative.notna().any() else "-"
        # Rounding first stops float noise counting a full 0.0001 step as smaller.
        small = int((absolute.round(10) < STORAGE_STEP).sum())
        print(f"{column:<10}{int(differs.sum()):>11}{max_abs:>16}{max_rel:>13}{small:>14}")
    return {"shared": shared, "masks": masks, "any": any_differs}


def print_concentration(title: str, comparison: dict) -> None:
    shared, any_differs = comparison["shared"], comparison["any"]
    print(f"\n--- {title}: where the differences are ---")
    if not any_differs.any():
        print("No differing rows.")
        return

    per_ticker = pd.DataFrame(
        {
            "differing": any_differs.groupby(shared["ticker"]).sum(),
            "rows": shared.groupby("ticker").size(),
        }
    )
    per_ticker["share"] = per_ticker["differing"] / per_ticker["rows"]
    affected = int((per_ticker["differing"] > 0).sum())
    print(f"Tickers affected: {affected} of {len(per_ticker)}")
    print(f"{'ticker':<10}{'differing':>11}{'rows':>7}{'share':>9}")
    top = per_ticker.sort_values(["differing", "share"], ascending=False).head(TOP_N)
    for ticker, row in top.iterrows():
        print(
            f"{ticker:<10}{int(row['differing']):>11}{int(row['rows']):>7}"
            f"{row['share']:>9.1%}"
        )

    per_date = any_differs.groupby(shared["trade_date"]).sum()
    print(f"Dates affected: {int((per_date > 0).sum())} of {len(per_date)}")
    print(f"{'date':<12}{'differing':>11}")
    for trade_date, count in per_date.sort_values(ascending=False).head(TOP_N).items():
        print(f"{trade_date!s:<12}{int(count):>11}")


def print_adjustment_split(comparison: dict) -> None:
    """Split differing rows by whether adj_close carries any adjustment at all."""
    shared, any_differs = comparison["shared"], comparison["any"]
    close, adj_close = column_values(shared, "close")[0], column_values(shared, "adj_close")[0]
    adjusted = (close - adj_close).abs() >= STORAGE_STEP

    print("\n--- Pull 1 vs pull 2, raw floats: adjusted vs unadjusted rows ---")
    print(f"{'rows':<30}{'total':>8}{'differing':>11}")
    for label, rows in (
        ("adj_close differs from close", adjusted),
        ("adj_close equals close", ~adjusted),
    ):
        print(f"{label:<30}{int(rows.sum()):>8}{int(any_differs[rows].sum()):>11}")

    tickers = pd.DataFrame(
        {
            "adjusted": adjusted.groupby(shared["ticker"]).any(),
            "differs": any_differs.groupby(shared["ticker"]).any(),
        }
    )
    print(
        f"Tickers with no adjusted rows: {int((~tickers['adjusted']).sum())}, "
        f"of which differ: {int((~tickers['adjusted'] & tickers['differs']).sum())}"
    )


def responsible_columns(comparison: dict) -> str:
    counts = comparison["masks"].sum()
    counts = counts[counts > 0].sort_values(ascending=False)
    return ", ".join(f"{name} ({count})" for name, count in counts.items()) or "none"


def print_conclusion(raw_pulls: dict, rounded_pulls: dict, rounded_stored: dict) -> None:
    raw_rows = int(raw_pulls["any"].sum())
    rounded_rows = int(rounded_pulls["any"].sum())
    stored_rows = int(rounded_stored["any"].sum())

    print("\n=== Conclusion ===")
    if raw_rows:
        print(
            f"Source jitter: yes. Two pulls {WAIT_SECONDS} seconds apart differ on "
            f"{raw_rows} rows before any rounding. Columns: "
            f"{responsible_columns(raw_pulls)}."
        )
    else:
        print("Source jitter: no. The two pulls are identical on every shared row.")
    if rounded_rows:
        print(
            f"The jitter survives rounding to 4 decimals on {rounded_rows} rows, so it "
            f"changes stored values. Columns: {responsible_columns(rounded_pulls)}."
        )
    elif raw_rows:
        print("The jitter disappears after rounding to 4 decimals.")
    print(
        f"A refresh loaded right now would update {stored_rows} rows. Columns: "
        f"{responsible_columns(rounded_stored)}."
    )
    if stored_rows and not rounded_rows:
        print(
            "The pulls agree with each other but not with the database, so those rows "
            "hold earlier data."
        )


def main() -> None:
    engine: Engine = get_engine().execution_options(postgresql_readonly=True)
    today = datetime.now(MARKET_TIMEZONE).date()
    start, end = date_window(REFRESH_HISTORY_YEARS, today)
    last_day = end - timedelta(days=1)
    tickers = get_tickers(engine)
    print(f"Window {start} to {last_day}, {len(tickers)} tickers")

    first = pull(tickers, start, end, "Pull 1")
    print(f"Waiting {WAIT_SECONDS} seconds")
    time.sleep(WAIT_SECONDS)
    second = pull(tickers, start, end, "Pull 2")

    with engine.connect() as conn:
        assert conn.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        stored = pd.read_sql(
            STORED_PRICES, conn, params={"start_date": start, "end_date": last_day}
        )
    print(f"Stored: {len(stored)} rows (read-only session)")

    first_rounded, second_rounded = as_stored(first), as_stored(second)
    raw_pulls = print_comparison("Pull 1 vs pull 2, raw floats", first, second)
    rounded_pulls = print_comparison(
        "Pull 1 vs pull 2, rounded as storage would", first_rounded, second_rounded
    )
    print_comparison("Pull 1 vs database, raw floats", first, stored)
    print_comparison("Pull 2 vs database, raw floats", second, stored)
    print_comparison(
        "Pull 1 vs database, rounded (rows a refresh would update)",
        first_rounded,
        stored,
    )
    rounded_stored = print_comparison(
        "Pull 2 vs database, rounded (rows a refresh would update)",
        second_rounded,
        stored,
    )

    print_concentration("Pull 1 vs pull 2, raw floats", raw_pulls)
    print_adjustment_split(raw_pulls)
    print_concentration("Pull 2 vs database, rounded", rounded_stored)
    print_conclusion(raw_pulls, rounded_pulls, rounded_stored)


if __name__ == "__main__":
    main()
