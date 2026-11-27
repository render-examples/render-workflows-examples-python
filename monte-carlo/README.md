# Monte Carlo on Render Workflows

Price an option by simulating tens of millions of price paths with numpy, split across task runs on 8 CPU / 32 GB compute plans and combined into one price with a 95% confidence interval.

## What it shows

- A CPU-heavy job fanned out across big compute plans: `simulate_chunk` runs on `8c-32g`, the orchestrator `price_option` on the default `flex`
- Using every CPU in a task run: numpy runs on one CPU, so each chunk starts a process pool sized from the plan's real CPU count
- Reproducible parallel randomness: the same seed gives the same price, however the paths are split
- A map-reduce over task runs: chunks return per-block sums, the parent combines them into a price, standard error and confidence interval
- Safe retries: the chunk task is pure and deterministic, so a retry recomputes exactly the same numbers
- A measured comparison, on Render, of one `flex` run against the fan-out

## The scenario

An **Asian call option** pays `max(average price − strike, 0)` at maturity, where the average is taken over every trading day. Unlike a plain (European) call, it has no closed-form price, so banks price it by simulating many possible price paths and averaging the payoffs. More paths give a tighter answer: the error shrinks with the square root of the path count, so 100x the paths buys 10x the precision.

Each path simulates 252 daily prices (geometric Brownian motion). With the defaults (spot 100, strike 100, 5% rate, 20% volatility, 1 year), the price is about **5.78**.

As a check, the same paths also price a European call, which does have a closed form (Black-Scholes, 10.4506 with the defaults). The result reports both and whether Black-Scholes falls inside the simulation's 95% interval.

## Task tree

```
price_option                (flex, no retries, 2 h timeout)
  ├── split the paths into blocks of 100,000, and the blocks into `chunks` ranges
  ├── simulate_chunk × chunks   (8c-32g, 2 retries, 1 h timeout per attempt)
  │     └── process pool, one process per CPU → per-block payoff sums
  └── combine()             → price, standard error, 95% CI, European check

price_option_single_run     (flex, 2 retries, 2 h timeout)
  └── every block in this one run, for comparison
```

## How the randomness stays reproducible

The paths are cut into fixed blocks of 100,000. Block `i` always draws from its own random stream, `SeedSequence(seed, spawn_key=(i,))` with numpy's PCG64 generator, no matter which task run or process simulates it. Each block returns five sums (paths, payoff sum and sum of squares for the Asian and European payoffs), and `combine()` adds them up in block order.

So `price_option` with 4 chunks, 16 chunks, or `price_option_single_run` on one CPU all return the **same price to the last digit** for the same seed. That makes the fan-out easy to trust and to test: compare it with a single run.

## Measured on Render

50 million paths × 252 steps, seed 42, Oregon, November 2026. Wall-clock is the parent task's own timer, from start to result.

| Run | Compute | Wall-clock | Price (95% CI) |
|---|---|---|---|
| `price_option_single_run` | 1 × `flex` (1 CPU) | **198.2 s** | 5.781362 (5.779145 to 5.783580) |
| `price_option`, 4 chunks | 4 × `8c-32g` (32 CPUs) | **13.4 s** (14.8x) | 5.781362 (5.779145 to 5.783580) |
| `price_option`, 8 chunks | 8 × `8c-32g` (64 CPUs) | 12.9 s | 5.781362 |
| `price_option`, 16 chunks | 16 × `8c-32g` (128 CPUs) | 7.5 s | 5.781362 |

The European check landed inside its interval in every run: 10.447539 simulated against 10.450584 from Black-Scholes.

Why not 32x on 32 CPUs: with 4 chunks, each chunk's code started 1 to 3 seconds after `price_option` dispatched it, then computed for 7 to 10 seconds (that includes starting its 8 worker processes). A perfect split of 198 s over 32 CPUs would be 6.2 s. Fixed costs like these matter less the bigger the job.

What it cost at list prices: the four `8c-32g` chunk runs lasted 46.6 s in total by the API's timestamps, about **$0.03** at $2.50 per hour. The single `flex` run is billed for the CPU and memory it used, at most $0.40 per hour, so **at most $0.02**. The fan-out bought a 15x faster answer for a little more money. See [compute plans](https://render.com/docs/workflows-limits#compute-plans).

## Prerequisites

- Python 3.12+ (numpy 2.5 needs it; Render's default Python is newer)
- The [Render CLI](https://render.com/docs/cli)

No API keys or environment variables are required.

## Run locally

```bash
pip install -r requirements.txt
render workflows dev -- python main.py
```

In another terminal, price the option with 2 million paths in 4 chunks. Task input is a JSON array of the task's arguments:

```bash
render workflows tasks runs start price_option --local --input='[{"paths": 2000000, "seed": 42, "chunks": 4}]'
```

`sample-input.json` holds the same input, so `--input-file sample-input.json` works too. Run the same input through `price_option_single_run` and compare: the price is identical.

Every field is optional:

| Field | Default | Meaning |
|---|---|---|
| `spot` | 100 | Price of the underlying today |
| `strike` | 100 | Strike price |
| `rate` | 0.05 | Risk-free rate, continuously compounded |
| `volatility` | 0.2 | Annualized volatility |
| `maturity_years` | 1 | Time to maturity in years |
| `steps` | 252 | Averaging dates (one per trading day), up to 10,000 |
| `paths` | 2,000,000 | Simulated paths, up to 1 billion |
| `seed` | 42 | Random seed, 0 to 2³²−1 |
| `chunks` | 4 | Task runs to split the paths across, up to 64 (`price_option` only) |

Locally, the plan setting is ignored: every chunk runs on your machine and sizes its process pool from your CPU count.

## Deploy to Render

1. Push this folder to a Git repository Render can access.
2. In the Render Dashboard, click **New > Workflow** and connect the repository.
3. Set these fields:

   | Field | Value |
   |---|---|
   | Language | Python |
   | Root Directory | `monte-carlo` (the folder that holds `main.py`). Leave it empty if this folder is the repository root. |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `python main.py` |
   | Region | Any |
   | Environment variables | None |

4. Create the workflow. Once the first version is ready, run `price_option` from the Dashboard with `[{"paths": 50000000, "chunks": 4}]` as its input, then `price_option_single_run` with the same input to compare.

## Trigger from another service

`client.py` runs both tasks with the same input and prints the prices, the timings and the speedup:

```bash
export RENDER_API_KEY=your-render-api-key
export WORKFLOW_SLUG=your-workflow-slug   # defaults to monte-carlo
python client.py 50000000
```

The core of it:

```python
from render import Render

render = Render()  # reads RENDER_API_KEY

run = render.workflows.run_task(
    f"{SLUG}/price_option", [{"paths": 50_000_000, "seed": 42, "chunks": 4}]
)
result = run.results[0]  # results is a list holding the return value
print(result["asian_call"]["price"], result["asian_call"]["ci95"])
print(result["run"]["wall_seconds"], "s")
```

`run_task` waits for the run to finish. To start a run and check on it later, use `start_task` instead.

## Limits and gotchas

- **A bigger plan doesn't parallelize your code.** numpy runs this loop on one CPU. Each chunk starts a `ProcessPoolExecutor` with one process per CPU of its plan. The docs say the same about [using multiple CPUs](https://render.com/docs/workflows-defining-flows).
- **Count CPUs from the cgroup, not `os.cpu_count()`.** Inside a task run, `os.cpu_count()` reports the host machine's CPUs. `available_cpus()` reads the plan's quota from `/sys/fs/cgroup/cpu.max` (8 on `8c-32g`, 1 on `flex`).
- **Start worker processes with `spawn`.** Forking the task's process would copy the SDK's running threads and event loop. `spawn` starts clean interpreters; they re-import `main.py` but don't start the task server, because `app.start()` sits behind `if __name__ == "__main__"`.
- **Keep numpy's arrays small.** Each block is simulated 10,000 paths at a time so the arrays stay in CPU cache. With 100,000 at a time, 8 processes on one `8c-32g` instance spent most of their time waiting on memory: the same 50 million paths took 23 to 25 s per chunk instead of 7 to 10.
- **Size the fan-out for new-compute limits.** Each workflow service can start new runs totaling 32 CPUs per minute on Pro workspaces (16 on Hobby); Render queues runs above that rather than failing them. See [compute limits](https://render.com/docs/workflows-limits#compute-limits). The default of 4 chunks on `8c-32g` is exactly 32 CPUs. On our workspace, 16 chunks (128 CPUs) all started within 4 seconds, so we didn't see queueing, but plan for the documented limit.
- **Retrying a chunk is safe.** `simulate_chunk` is pure: same input, same output, and it writes nothing anywhere. A retry after a lost instance or an out-of-memory kill recomputes exactly the same sums. We killed a worker process mid-chunk locally: the chunk failed, retried, and the final price matched a clean run to the last digit. `price_option` never retries, because a retry would rerun every chunk. See [retries](https://render.com/docs/workflows-defining-tasks#retry-settings).
- **A chunk that fails every attempt fails the price.** `price_option` uses a plain `asyncio.gather`, so a price is never computed from a partial set of paths.
- **Bad input is returned, not raised.** An unknown field, a negative volatility or too many paths comes back as `{"error": ...}`, because a retry can't fix it.
- **Results stay small.** A chunk returns five numbers per block of 100,000 paths, so even a billion paths is about 10,000 short lists. Task arguments are capped at 4 MB in total, and Render keeps run inputs and results for 30 days. See [limits](https://render.com/docs/workflows-limits#additional-limits).
- **Timeouts apply per attempt.** Each chunk attempt gets an hour, and each retry a fresh hour. `price_option` has 2 hours to cover queued chunks and their retries. See [timeouts](https://render.com/docs/workflows-defining-tasks#timeout).

## Links

- [Render Workflows](https://render.com/docs/workflows)
- [Defining tasks](https://render.com/docs/workflows-defining-tasks)
- [Workflows limits](https://render.com/docs/workflows-limits)
- [Triggering runs](https://render.com/docs/workflows-running)
- [numpy random: parallel generation with SeedSequence](https://numpy.org/doc/stable/reference/random/parallel.html)
