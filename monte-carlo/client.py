"""
Price the option from another service or script, fanned out and in one run,
and compare.

Requires RENDER_API_KEY. Set WORKFLOW_SLUG to your workflow service's slug
(it defaults to monte-carlo).

    python client.py              # 2 million paths
    python client.py 100000000    # 100 million paths
"""

import os
import sys

from render import Render

SLUG = os.getenv("WORKFLOW_SLUG", "monte-carlo")
paths = int(sys.argv[1]) if len(sys.argv) > 1 else 2_000_000
request = {"paths": paths, "seed": 42, "chunks": 4}

# Render() reads RENDER_API_KEY from the environment.
render = Render()

results = {}
for task in ("price_option", "price_option_single_run"):
    print(f"Running {task} with {paths:,} paths...")
    # Task input is a list of the task's arguments. run_task waits for the run
    # to finish and returns its details.
    run = render.workflows.run_task(f"{SLUG}/{task}", [request])
    if run.status != "completed":
        raise SystemExit(f"Run {run.id} ended as {run.status}: {run.error}")
    # results is a list holding the task's return value.
    result = run.results[0]
    if "error" in result:
        raise SystemExit(f"{task}: {result['error']}")
    results[task] = result

for task, result in results.items():
    asian, run = result["asian_call"], result["run"]
    print(f"\n{task}: {run['wall_seconds']} s")
    print(f"  Asian call: {asian['price']:.4f}  (95% CI {asian['ci95'][0]:.4f} to {asian['ci95'][1]:.4f})")
    check = result["european_check"]
    print(f"  European check: {check['price']:.4f} vs Black-Scholes {check['black_scholes']:.4f}")
    for chunk in run.get("chunk_runs", []):
        print(f"  chunk {chunk['blocks']}: {chunk['cpus']} CPUs, started after "
              f"{chunk['start_delay_seconds']} s, computed {chunk['compute_seconds']} s")

fan_out, single = results["price_option"], results["price_option_single_run"]
same = fan_out["asian_call"] == single["asian_call"]
print(f"\nSame price from both: {same}")
print(f"Speedup: {single['run']['wall_seconds'] / fan_out['run']['wall_seconds']:.1f}x")
