"""Regenerate the Results section of README.md from `python -m etl.report --days 30`.

Only the text between the results markers changes. Run from anywhere with the venv:
    .venv/bin/python scripts/update_readme_results.py
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
START = "<!-- results:start -->"
END = "<!-- results:end -->"
REPORT_COMMAND = ["-m", "etl.report", "--days", "30"]


def results_section(report: str) -> str:
    window = next(line for line in report.splitlines() if "-day window" in line)
    return "\n".join(
        [
            START,
            f"**{window}**",
            "",
            "Generated with `python -m etl.report --days 30`. Every figure below comes",
            "from etl_run_log, dq_check_result, reconciliation_result and",
            "fact_daily_price.",
            "",
            "```",
            report.rstrip(),
            "```",
            END,
        ]
    )


def replace_results(readme: str, section: str) -> str:
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError(f"README.md needs exactly one {START} and one {END}")
    before, rest = readme.split(START)
    _, after = rest.split(END)
    return before + section + after


def main() -> None:
    report = subprocess.run(
        [sys.executable, *REPORT_COMMAND],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    README.write_text(replace_results(README.read_text(), results_section(report)))
    print(f"Updated Results in {README.name}")


if __name__ == "__main__":
    main()
