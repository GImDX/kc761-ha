"""Enforce independent branch coverage, not coverage.py's combined percentage."""

import json
from pathlib import Path
import sys

MINIMUM_BRANCH_PERCENT = 95


def check_report(report: dict) -> tuple[int, int]:
    """Fail closed for missing branch data and for coverage below the contract."""
    if report.get("meta", {}).get("branch_coverage") is not True:
        raise ValueError("coverage report must include branch data")
    totals = report.get("totals", {})
    covered, total = totals.get("covered_branches"), totals.get("num_branches")
    if type(covered) is not int or type(total) is not int or not 0 <= covered <= total or total <= 0:
        raise ValueError("coverage report has invalid branch totals")
    if covered * 100 < total * MINIMUM_BRANCH_PERCENT:
        raise ValueError(f"branch coverage {covered}/{total} = {covered / total:.2%}, required >=95%")
    return covered, total


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m tests.check_branch_coverage COVERAGE_JSON", file=sys.stderr)
        return 2
    try:
        covered, total = check_report(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, AttributeError) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Branch coverage: {covered}/{total} = {covered / total:.2%} (required >=95%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
