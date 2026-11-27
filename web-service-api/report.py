"""
The sales data and report logic, shared by both services.

The API uses check_request to reject bad input before it starts a run. The
workflow task uses it too, because runs can also start from the Dashboard or
the CLI, and then builds the report one month at a time.
"""

import json
import re
from pathlib import Path

SALES = json.loads((Path(__file__).parent / "sales.json").read_text())

REGIONS = sorted({row["region"] for row in SALES})


def months_in(quarter: str) -> list[str]:
    """The three months of a quarter such as 2026-Q3, as 2026-07, 2026-08, 2026-09."""
    year, q = quarter.split("-Q")
    first = (int(q) - 1) * 3 + 1
    return [f"{year}-{month:02d}" for month in range(first, first + 3)]


# A quarter is reportable when the data has all three of its months.
_MONTHS = {row["month"] for row in SALES}
_CANDIDATES = {f"{m[:4]}-Q{(int(m[5:]) - 1) // 3 + 1}" for m in _MONTHS}
QUARTERS = sorted(q for q in _CANDIDATES if all(m in _MONTHS for m in months_in(q)))


def check_request(region: object, quarter: object) -> str | None:
    """Return why a report request is invalid, or None if it's fine."""
    if not isinstance(region, str) or region not in REGIONS:
        return f"region must be one of {', '.join(REGIONS)}"
    if not isinstance(quarter, str) or not re.fullmatch(r"\d{4}-Q[1-4]", quarter):
        return "quarter must look like 2026-Q3"
    if quarter not in QUARTERS:
        return f"no data for {quarter}; quarters with data: {', '.join(QUARTERS)}"
    return None


def month_totals(region: str, month: str) -> dict:
    """Revenue and units for one region and month, by product."""
    by_product: dict[str, dict] = {}
    for row in SALES:
        if row["region"] == region and row["month"] == month:
            by_product[row["product"]] = {
                "units": row["units"],
                "revenue": row["units"] * row["unit_price"],
            }
    return {
        "month": month,
        "units": sum(p["units"] for p in by_product.values()),
        "revenue": sum(p["revenue"] for p in by_product.values()),
        "by_product": by_product,
    }


def combine(region: str, quarter: str, months: list[dict]) -> dict:
    """Roll monthly totals up into the quarter's report."""
    by_product: dict[str, dict] = {}
    for month in months:
        for product, totals in month["by_product"].items():
            entry = by_product.setdefault(product, {"units": 0, "revenue": 0})
            entry["units"] += totals["units"]
            entry["revenue"] += totals["revenue"]
    return {
        "region": region,
        "quarter": quarter,
        "revenue": sum(m["revenue"] for m in months),
        "units": sum(m["units"] for m in months),
        "top_product": max(by_product, key=lambda p: by_product[p]["revenue"]),
        "by_product": by_product,
        "by_month": [{k: m[k] for k in ("month", "units", "revenue")} for m in months],
    }
