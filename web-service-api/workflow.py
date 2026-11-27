"""
Web service API: the workflow service

One task, build_report, turns the bundled sales data into a quarterly report
for one region. It needs no API keys, so you can run the whole example as is.
The API service (api.py) starts it and reads its result.
"""

import asyncio
import logging

from render import Retry, TaskContext, Workflows

import report

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = Workflows()


# build_report only reads, so a retry is safe. In a real app this task would
# query a database or a warehouse, which can fail for a moment; Render retries
# the attempt up to twice.
@app.task(
    retry=Retry(max_retries=2, wait_duration_ms=1000, backoff_scaling=2),
    timeout_seconds=60,
)
async def build_report(ctx: TaskContext, region: str, quarter: str) -> dict:
    """Build the sales report for one region and quarter."""
    # The API checks input before it starts a run, but runs can also start from
    # the Dashboard or the CLI. A retry can't fix bad input, so return the
    # problem as a result instead of raising.
    problem = report.check_request(region, quarter)
    if problem:
        return {"error": problem}

    months = []
    for month in report.months_in(quarter):
        logger.info(f"[REPORT] {region} {month}")
        # Stand-in for a slow query, so a run takes a few seconds and you can
        # watch it go from running to completed.
        await asyncio.sleep(1)
        months.append(report.month_totals(region, month))

    return report.combine(region, quarter, months)


if __name__ == "__main__":
    app.start()
