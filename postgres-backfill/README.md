# Resumable Postgres Backfill on Render Workflows

Fill a new column across a large Postgres table in keyset chunks, as a Render Workflows flow where every chunk is a checkpointed step. A failed chunk retries on its own, and a run that stops partway picks up where it left off instead of starting over.

## What it shows

- A flow (`@app.flow`) with one `ctx.step` per chunk, so each chunk's result is recorded as it finishes
- Retries per step: a chunk that fails (a dropped connection, a lock timeout) retries without rerunning the chunks before it
- Idempotent chunk writes: keyset pagination on the primary key, and an `UPDATE` that only fills rows that are still `NULL`
- Progress you can read while the run goes: a `backfill_runs` row updated in the same transaction as each chunk, and a `backfill_status` task that reads it
- Resuming a run that ended early (canceled, out of retries, timed out) from its last committed chunk
- Starting and following the flow from another service with the Render SDK client

Other examples in this repo move data between systems (`data-pipeline`, `etl-job`). This one changes a table in place, and the point is what happens when the run is interrupted.

## Flow and tasks

```
seed_orders (task)              creates orders + backfill_runs, inserts N rows with amount_usd_cents NULL

backfill_orders (flow)          one run = one pass over the table
  ├── step "plan"               counts rows, picks the start id (0, or where resume_from stopped)
  ├── step "chunk-0"            ids 1..5000:      SELECT, convert, UPDATE ... WHERE amount_usd_cents IS NULL,
  ├── step "chunk-1"            ids 5001..10000   and record progress, all in one transaction
  ├── ...                       (each chunk retries up to 3 times on its own)
  └── step "finish"             counts rows still NULL, marks the run finished

backfill_status (task)          reads a run's progress row
```

The backfill converts each order's amount to US cents (`orders.amount_usd_cents`) from its currency and minor-unit amount. The conversion is the stand-in for whatever your backfill computes.

## Prerequisites

- Python 3.11+
- The [Render CLI](https://render.com/docs/cli), version 2.37 or later (earlier versions can't run flows locally)
- A local Postgres (for example `brew install postgresql`)
- For `client.py`: a [Render API key](https://render.com/docs/api-keys)

No other API keys. `seed_orders` drops and recreates the `orders` and `backfill_runs` tables, so point `DATABASE_URL` at a database used only for this example.

## Run locally

```bash
pip install -r requirements.txt
createdb postgres_backfill
cp .env.example .env
# Edit .env if your Postgres isn't at postgresql://localhost:5432/postgres_backfill.
```

Start the local task server. It loads `.env` when it starts:

```bash
render workflows dev -- python main.py
```

In another terminal, create the table with 200,000 orders. Input is a JSON array of the task's arguments:

```bash
render workflows tasks runs start seed_orders --local --input='[200000]'
```

Run the backfill in chunks of 5,000. The local server starts flows with the same command as tasks:

```bash
render workflows tasks runs start backfill_orders --local --input='[5000]'
```

### Watch a chunk fail and retry

The third argument, `fail_chunk`, makes that chunk fail once after its writes commit:

```bash
render workflows tasks runs start seed_orders --local --input='[200000]'
render workflows tasks runs start backfill_orders --local --input='[5000, null, 10]'
```

The task server logs `Step Retrying [11 (chunk-10) attempt=0]`, then `chunk-10` again with `"updated":0`: its rows were already filled, so the retry writes nothing. Chunks 0 to 9 don't run again. The run ends with `"remaining_nulls": 0`.

### Stop a run and resume it

Seed a bigger table and start a run with small chunks:

```bash
render workflows tasks runs start seed_orders --local --input='[1000000]'
render workflows tasks runs start backfill_orders --local --input='[1000]'
```

Cancel it partway with the run ID it printed, then check how far it got:

```bash
render workflows tasks runs cancel <run-id> --local
render workflows tasks runs start backfill_status --local --input='["<run-id>"]'
```

Start a new run with the old run's ID as `resume_from`. It starts after the last chunk the old run committed:

```bash
render workflows tasks runs start backfill_orders --local --input='[1000, "<run-id>"]'
```

## Deploy to Render

Create two resources in the Render Dashboard, in the same region:

1. **Postgres**: **New > Postgres**. Copy its **Internal Database URL**.
2. **Workflow service**: **New > Workflow**, connected to your repository.

   | Field | Value |
   |---|---|
   | Language | Python |
   | Root Directory | `postgres-backfill` (the folder that holds `main.py`). Leave it empty if this folder is the repository root. |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `python main.py` |
   | Region | The database's region |
   | Environment variables | `DATABASE_URL` (the internal URL) |

Task and flow runs reach the database over the private network, like your other services in the same region.

Once the first version is ready, seed the table and start a run. The slug is your workflow service's slug:

```bash
render workflows tasks runs start <workflow-slug>/seed_orders --input='[1000000]'
render workflows flows runs start <workflow-slug>/backfill_orders --input='[10000, null, 20]'
```

The Dashboard shows each step of the flow run, and its attempts: `chunk-20` has a failed attempt and a successful one. From the CLI:

```bash
render workflows flows runs steps list <flow-run-id>
render workflows flows runs steps attempts list <flow-run-id> 21
```

To stop a run and resume it on Render:

```bash
render workflows flows runs cancel <flow-run-id>
render workflows flows runs start <workflow-slug>/backfill_orders --input='[10000, "<flow-run-id>"]'
```

## Trigger from another service

`client.py` seeds the table, starts the flow, and prints progress until the run ends:

```bash
export RENDER_API_KEY=your-render-api-key
export WORKFLOW_SLUG=your-workflow-slug   # defaults to postgres-backfill
python client.py
```

Set `FAIL_CHUNK=20` to watch a chunk retry, or `RESUME_FROM=<flow-run-id>` to resume a run that didn't finish (it skips the seed). `ROW_COUNT` and `CHUNK_SIZE` set the table size and chunk size.

The core of it:

```python
from render import Render

render = Render()  # reads RENDER_API_KEY

run = render.workflows.start_flow(
    f"{SLUG}/backfill_orders",
    [10000, None, None],  # chunk_size, resume_from, fail_chunk
    idempotency_key="backfill-orders-2026-11-27",
)

details = render.workflows.get_flow_run(run.id)  # poll until succeeded, failed or canceled
progress = render.workflows.run_task(f"{SLUG}/backfill_status", [run.id])
```

Your own app can read the `backfill_runs` table directly instead of calling `backfill_status`.

## Limits and gotchas

- **Render replays a flow when the instance running it fails.** The replay reruns the flow function from the top. Steps that finished return their recorded results, and the run continues from the first step that didn't. See [flow replay](https://render.com/docs/workflows-defining-flows#flow-replay).
- **Other failures end the run.** An uncaught error, a timeout, running out of memory, or a step that fails all its attempts fails the run with no replay, and a canceled run stays canceled. In tests on Render, code that exits its own process (a non-zero exit, a kill signal, or exiting without returning) also failed the run. That's what `resume_from` is for: the new run starts from the last chunk the old one committed.
- **Writes must be safe to repeat.** A chunk whose writes committed can still run again: when its attempt fails before returning, or after a replay. The `WHERE amount_usd_cents IS NULL` guard makes the second run a no-op. `rows_updated` counts what each chunk's recorded attempt wrote, so a chunk that ran twice counts 0 the second time; check `remaining_nulls` for completeness.
- **Flow code must be deterministic.** The flow makes the same `ctx.step` calls in the same order on every replay, and computes each step's inputs only from earlier step results. Database reads, clocks and random values go inside steps. See [determinism](https://render.com/docs/workflows-defining-flows#determinism-and-side-effects).
- **Up to 10,000 steps per run, and 4 MB per step result.** The flow checks the chunk count before it starts, and each chunk returns counts and ids, not rows. For bigger tables, raise `chunk_size`. See [limits](https://render.com/docs/workflows-limits).
- **Timeouts apply per attempt.** `timeout_seconds=7200` bounds each attempt of the flow; the maximum is 24 hours. A run that times out fails without replay: resume it with `resume_from`.
- **Go easy on the database.** Steps run one at a time, so the backfill holds one connection. Each chunk sets `lock_timeout = '5s'` so it doesn't queue behind app traffic; a lock timeout raises and the chunk retries. Pick a `chunk_size` that keeps each transaction short.
- **Write the new column from your app first.** The backfill fills rows that exist when it reaches them. Deploy the code that sets `amount_usd_cents` on new and changed orders before you backfill, then add `NOT NULL` once `remaining_nulls` is 0.
- **Run one backfill at a time.** Two runs at once won't corrupt data (the writes are idempotent), but they repeat each other's work. Start runs with an idempotency key so a retried request doesn't start a second one. See [idempotency keys](https://render.com/docs/workflows-running#idempotency-keys).

## Links

- [Defining flows](https://render.com/docs/workflows-defining-flows)
- [Limits and pricing](https://render.com/docs/workflows-limits)
- [Triggering runs](https://render.com/docs/workflows-running)
- [Local development](https://render.com/docs/workflows-local-development)
- [Workflows SDK for Python](https://render.com/docs/workflows-sdk-python)
- [Render Postgres](https://render.com/docs/postgresql)
