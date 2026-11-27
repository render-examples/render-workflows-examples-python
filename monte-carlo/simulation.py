"""
The Monte Carlo math, with no Render code in it.

Prices an arithmetic-average Asian call: an option that pays
max(average price - strike, 0) at maturity, where the average is taken over
every monitoring date (daily, by default). There's no closed-form price for it,
which is why people simulate it.

The paths are split into fixed-size blocks. Block i always draws from the same
random stream, derived from (seed, i), no matter which task run or process
simulates it. So the same seed gives the same price whether the blocks run in
one task run or spread across many.
"""

import math
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

# Paths per block. Each block is one unit of work for a process, and one entry
# in a chunk's result. Changing it changes which random numbers each path gets,
# so it's a constant rather than an input.
BLOCK_SIZE = 100_000
# numpy works on this many paths at a time inside a block. Like BLOCK_SIZE, it
# decides the order random numbers are drawn in, so it's fixed too.
SUB_BATCH = 10_000

DEFAULTS = {
    "spot": 100.0,  # price of the underlying today
    "strike": 100.0,
    "rate": 0.05,  # risk-free rate, continuously compounded
    "volatility": 0.2,  # annualized
    "maturity_years": 1.0,
    "steps": 252,  # monitoring dates: one per trading day
    "paths": 2_000_000,
    "seed": 42,
    "chunks": 4,  # task runs to split the paths across (price_option only)
}
MAX_PATHS = 1_000_000_000
MAX_STEPS = 10_000
MAX_CHUNKS = 64


class InputError(ValueError):
    """Bad input. A retry won't fix it, so tasks return it instead of raising."""


def parse_request(request: dict | None) -> dict:
    """Fill in defaults and check ranges."""
    request = {} if request is None else request
    if not isinstance(request, dict):
        raise InputError("The request must be an object")
    unknown = set(request) - set(DEFAULTS)
    if unknown:
        raise InputError(f"Unknown fields: {sorted(unknown)}")
    spec = {**DEFAULTS, **request}
    for key in ("spot", "strike", "volatility", "maturity_years"):
        if not isinstance(spec[key], (int, float)) or isinstance(spec[key], bool) or spec[key] <= 0:
            raise InputError(f"{key} must be a positive number")
    if not isinstance(spec["rate"], (int, float)) or isinstance(spec["rate"], bool):
        raise InputError("rate must be a number")
    for key, low, high in (
        ("steps", 1, MAX_STEPS),
        ("paths", 1, MAX_PATHS),
        ("chunks", 1, MAX_CHUNKS),
        ("seed", 0, 2**32 - 1),
    ):
        if not isinstance(spec[key], int) or isinstance(spec[key], bool) or not low <= spec[key] <= high:
            raise InputError(f"{key} must be an integer from {low} to {high}")
    return spec


def block_count(paths: int) -> int:
    return math.ceil(paths / BLOCK_SIZE)


def available_cpus() -> int:
    """CPUs this run may use.

    On Render, os.cpu_count() reports the host's CPUs, not the compute plan's.
    The plan's limit is in the cgroup's cpu.max ("800000 100000" is 8 CPUs).
    """
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            return max(1, int(quota) // int(period))
    except (OSError, ValueError):
        pass
    return os.cpu_count() or 1


def simulate_block(spec: dict, block: int) -> list[float]:
    """Simulate one block of paths. Returns [paths, Asian payoff sum, sum of
    squares, European payoff sum, sum of squares], undiscounted.

    The European payoff max(S_T - strike, 0) comes from the same paths. It has
    a closed-form price (Black-Scholes), so it checks the simulation.
    """
    n = min(BLOCK_SIZE, spec["paths"] - block * BLOCK_SIZE)
    # SeedSequence with a spawn_key gives each block its own independent
    # stream, the same one every time.
    rng = np.random.Generator(np.random.PCG64(np.random.SeedSequence(spec["seed"], spawn_key=(block,))))

    steps = spec["steps"]
    dt = spec["maturity_years"] / steps
    drift = (spec["rate"] - 0.5 * spec["volatility"] ** 2) * dt
    shock = spec["volatility"] * math.sqrt(dt)

    sums = [float(n), 0.0, 0.0, 0.0, 0.0]
    # Work through the block SUB_BATCH paths at a time. Small arrays stay in
    # the CPU cache, which matters when 8 processes share one machine.
    for offset in range(0, n, SUB_BATCH):
        m = min(SUB_BATCH, n - offset)
        # Step through time for m paths at once. log_price holds log(S/spot).
        log_price = np.zeros(m)
        price_sum = np.zeros(m)
        z = np.empty(m)
        price = np.empty(m)
        for _ in range(steps):
            rng.standard_normal(out=z)
            z *= shock
            z += drift
            log_price += z
            np.exp(log_price, out=price)
            price_sum += price

        asian = np.maximum(spec["spot"] * price_sum / steps - spec["strike"], 0.0)
        european = np.maximum(spec["spot"] * price - spec["strike"], 0.0)
        sums[1] += float(asian.sum())
        sums[2] += float(np.dot(asian, asian))
        sums[3] += float(european.sum())
        sums[4] += float(np.dot(european, european))
    return sums


def _simulate_blocks_in_process(spec: dict, blocks: list[int]) -> list[list[float]]:
    return [simulate_block(spec, b) for b in blocks]


def simulate_blocks(spec: dict, first_block: int, block_total: int, cpus: int) -> list[list[float]]:
    """Simulate blocks [first_block, first_block + block_total), using up to
    `cpus` processes. Returns one entry per block, in block order.

    numpy runs this on one CPU, so a bigger compute plan only helps if we start
    more processes. Process i gets blocks i, i + workers, i + 2 * workers, ...
    """
    blocks = list(range(first_block, first_block + block_total))
    workers = min(cpus, len(blocks))
    if workers <= 1:
        return _simulate_blocks_in_process(spec, blocks)

    shares = [blocks[i::workers] for i in range(workers)]
    # "spawn" starts clean interpreters instead of forking the task's process,
    # which has the SDK's threads and event loop running.
    with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(_simulate_blocks_in_process, [spec] * workers, shares))

    # Put the blocks back in order.
    by_block = {}
    for share, partials in zip(shares, results):
        by_block.update(zip(share, partials))
    return [by_block[b] for b in blocks]


def _estimate(total: float, total_sq: float, n: float, discount: float) -> dict:
    mean = total / n
    variance = max(total_sq / n - mean * mean, 0.0) * n / max(n - 1, 1)
    std_error = discount * math.sqrt(variance / n)
    price = discount * mean
    return {
        "price": round(price, 6),
        "std_error": round(std_error, 6),
        # 95% confidence interval: price ± 1.96 standard errors.
        "ci95": [round(price - 1.96 * std_error, 6), round(price + 1.96 * std_error, 6)],
    }


def black_scholes_call(spec: dict) -> float:
    s, k, r = spec["spot"], spec["strike"], spec["rate"]
    sigma, t = spec["volatility"], spec["maturity_years"]
    d1 = (math.log(s / k) + (r + 0.5 * sigma**2) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    cdf = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    return s * cdf(d1) - k * math.exp(-r * t) * cdf(d2)


def combine(spec: dict, partials: list[list[float]]) -> dict:
    """Reduce per-block sums (in block order) to prices with 95% intervals."""
    sums = [0.0] * 5
    for partial in partials:
        for i, value in enumerate(partial):
            sums[i] += value
    n = sums[0]
    discount = math.exp(-spec["rate"] * spec["maturity_years"])
    asian = _estimate(sums[1], sums[2], n, discount)
    european = _estimate(sums[3], sums[4], n, discount)
    exact = black_scholes_call(spec)
    return {
        "asian_call": asian,
        # A sanity check from the same paths: the European call's simulated
        # price should land near Black-Scholes (inside the interval about 95%
        # of the time).
        "european_check": {
            **european,
            "black_scholes": round(exact, 6),
            "within_ci95": european["ci95"][0] <= exact <= european["ci95"][1],
        },
        "paths": int(n),
    }
