"""
Monte Carlo option pricing on Render Workflows

Prices an Asian call option by simulating millions of price paths with numpy.
price_option (on the default flex plan) splits the paths into chunks and runs
each chunk as its own task run on an 8 CPU / 32 GB compute plan, where it uses
all 8 CPUs through a process pool. It then combines the chunks into a price
with a 95% confidence interval.

price_option_single_run does the same simulation in one flex run, so you can
compare wall-clock times. With the same seed, both return the same price.

The math is in simulation.py.
"""

import asyncio
import logging
import time

from render import Retry, TaskContext, Workflows

from simulation import (
    InputError,
    available_cpus,
    block_count,
    combine,
    parse_request,
    simulate_blocks,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

CHUNK_PLAN = "8c-32g"

app = Workflows()


# simulate_chunk is the heavy task: 8 CPUs and 32 GB for this run alone, billed
# per second while it runs. It's pure and deterministic (same input, same
# output, no writes anywhere), so retrying it is always safe: a retry
# recomputes exactly the same numbers. Two retries cover a lost instance.
@app.task(
    plan=CHUNK_PLAN,
    timeout_seconds=60 * 60,
    retry=Retry(max_retries=2, wait_duration_ms=5_000, backoff_scaling=2),
)
def simulate_chunk(ctx: TaskContext, spec: dict, first_block: int, block_total: int) -> dict:
    """Simulate a contiguous range of blocks on every CPU this run has."""
    started_at = time.time()
    try:
        spec = parse_request(spec)
    except InputError as e:
        return {"error": str(e)}
    blocks = block_count(spec["paths"])
    if not (0 <= first_block and 1 <= block_total and first_block + block_total <= blocks):
        return {"error": f"Blocks {first_block}..{first_block + block_total - 1} are outside 0..{blocks - 1}"}

    cpus = available_cpus()
    logger.info(f"[CHUNK] blocks {first_block}..{first_block + block_total - 1} on {cpus} CPUs")
    partials = simulate_blocks(spec, first_block, block_total, cpus)
    seconds = round(time.time() - started_at, 2)
    logger.info(f"[CHUNK] blocks {first_block}..{first_block + block_total - 1} done in {seconds} s")
    return {
        "first_block": first_block,
        "partials": partials,
        "cpus": cpus,
        "started_at": started_at,
        "compute_seconds": seconds,
    }


# price_option only splits, waits and adds up, so it runs on flex and never
# retries: a retry would rerun every chunk. Its timeout covers chunks that
# queue for compute and the chunks' own retries.
@app.task(
    timeout_seconds=2 * 60 * 60,
    retry=Retry(max_retries=0, wait_duration_ms=1_000, backoff_scaling=1.5),
)
async def price_option(ctx: TaskContext, request: dict | None = None) -> dict:
    """Price the option by fanning the paths out across `chunks` task runs."""
    started_at = time.time()
    try:
        spec = parse_request(request)
    except InputError as e:
        return {"error": str(e)}

    # Split the blocks into `chunks` contiguous ranges of near-equal size.
    blocks = block_count(spec["paths"])
    chunks = min(spec["chunks"], blocks)
    ranges = [(i * blocks // chunks, (i + 1) * blocks // chunks) for i in range(chunks)]
    logger.info(f"[PRICE] {spec['paths']:,} paths, {blocks} blocks, {chunks} chunks on {CHUNK_PLAN}")

    # Each ctx.run is its own task run on its own 8c-32g instance. If a chunk
    # fails all its attempts, gather raises and this run fails: a price
    # missing some paths would be wrong.
    dispatched_at = time.time()
    results = await asyncio.gather(
        *(ctx.run(simulate_chunk, spec, start, end - start) for start, end in ranges)
    )
    for result in results:
        if "error" in result:
            return {"error": result["error"]}

    partials = [p for result in results for p in result["partials"]]
    wall = round(time.time() - started_at, 2)
    logger.info(f"[PRICE] done in {wall} s")
    return {
        **combine(spec, partials),
        "spec": spec,
        "run": {
            "mode": "fan-out",
            "chunks": chunks,
            "chunk_plan": CHUNK_PLAN,
            "wall_seconds": wall,
            "chunk_runs": [
                {
                    "blocks": [start, end - 1],
                    "cpus": result["cpus"],
                    # Time from ctx.run to the chunk's code starting: queueing
                    # for compute plus starting the instance.
                    "start_delay_seconds": round(result["started_at"] - dispatched_at, 2),
                    "compute_seconds": result["compute_seconds"],
                }
                for (start, end), result in zip(ranges, results)
            ],
        },
    }


# The baseline: every path in one run on the default flex plan (up to 1 CPU).
# Pure and deterministic like simulate_chunk, so retries are safe here too.
@app.task(
    timeout_seconds=2 * 60 * 60,
    retry=Retry(max_retries=2, wait_duration_ms=5_000, backoff_scaling=2),
)
def price_option_single_run(ctx: TaskContext, request: dict | None = None) -> dict:
    """Price the option in this one run, for comparison with price_option."""
    started_at = time.time()
    try:
        spec = parse_request(request)
    except InputError as e:
        return {"error": str(e)}

    cpus = available_cpus()
    blocks = block_count(spec["paths"])
    logger.info(f"[SINGLE] {spec['paths']:,} paths, {blocks} blocks on {cpus} CPUs")
    partials = simulate_blocks(spec, 0, blocks, cpus)
    wall = round(time.time() - started_at, 2)
    logger.info(f"[SINGLE] done in {wall} s")
    return {
        **combine(spec, partials),
        "spec": spec,
        "run": {"mode": "single run", "cpus": cpus, "wall_seconds": wall},
    }


if __name__ == "__main__":
    app.start()
