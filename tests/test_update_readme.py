"""Tests for the README results updater, on strings only."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "update_readme_results",
    Path(__file__).resolve().parent.parent / "scripts" / "update_readme_results.py",
)
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)

REPORT = "MarketPulse report\n\n30-day window in progress: 3 days complete\nRows: 5\n"


def test_replaces_only_the_results_section():
    readme = f"# Title\n\n{updater.START}\nold\n{updater.END}\n\n## Next\nkeep\n"
    result = updater.replace_results(readme, updater.results_section(REPORT))
    assert result.startswith("# Title\n\n")
    assert result.endswith("\n\n## Next\nkeep\n")
    assert "old" not in result
    assert "**30-day window in progress: 3 days complete**" in result
    assert "Rows: 5" in result


def test_rerunning_gives_the_same_readme():
    readme = f"{updater.START}\n{updater.END}\n"
    once = updater.replace_results(readme, updater.results_section(REPORT))
    assert updater.replace_results(once, updater.results_section(REPORT)) == once


@pytest.mark.parametrize(
    "readme",
    ["no markers", f"{updater.START}\n{updater.END}\n{updater.START}\n{updater.END}"],
)
def test_requires_exactly_one_pair_of_markers(readme):
    with pytest.raises(ValueError):
        updater.replace_results(readme, "section")
