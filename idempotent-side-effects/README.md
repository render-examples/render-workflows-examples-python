# Idempotent Side Effects on Render Workflows

Retry an LLM call as often as you need without repeating a paid side effect: a refund flow drafts its decision with OpenAI, then issues a refund credit exactly once, even when the task that issues it fails and Render retries it.

## What it shows

- Splitting a job into tasks so the LLM call and the side effect retry separately
- An idempotency key derived from the business input (account and order), not from the run or attempt
- Recording the side effect in Postgres with `INSERT ... ON CONFLICT DO NOTHING` on that key
- A switch that fails the side-effect task once, right after the write, so you can watch the retry skip it
- Passing the same key to a provider that accepts one, such as Stripe's `Idempotency-Key` header
- An idempotency key on the task run itself, when another service triggers it
- Returning errors a retry can't fix (missing API key, unknown model, bad input) as results instead of raising

## Task tree

```
handle_refund (orchestrates, no retries)
  ├── draft_refund    (retries twice)  ← calls OpenAI, only reads
  └── issue_credit    (retries twice)  ← writes the credit, once per account and order
        INSERT INTO refund_credits ... ON CONFLICT (idempotency_key) DO NOTHING
```

## Why a retry doesn't repeat the credit

Render retries a task when an attempt raises or times out, and each retry runs the task again from the start. Code that writes something and then fails writes it again on the retry. Three things stop that here:

1. **Separate tasks.** `draft_refund` and `issue_credit` are their own task runs, started with `ctx.run`. A failed credit retries `issue_credit` alone, so the model isn't called again, and a failed model call retries `draft_refund` alone, so the credit isn't issued again. `handle_refund` only orchestrates and has retries off, because retrying it would rerun both.
2. **A key from the business input.** `issue_credit` builds its key as `sha256("<account>:refund:<order>")`. Every attempt, and every later run for the same order, gets the same key. A key built from the run ID, the attempt, a timestamp, or the model's output would change between attempts and stop nothing.
3. **A write that checks the key.** The key is the table's primary key, and the insert uses `ON CONFLICT (idempotency_key) DO NOTHING`. Only the attempt that wrote the credit inserts a row. Any later attempt inserts nothing and reports `already_issued`.

The credit amount comes from the order data, and the model only decides whether to refund and drafts the message to the customer. A retry can draft different wording, but the stored credit keeps the first one.

### When the side effect is an API call

If the credit goes through a payment provider rather than your own database, pass the same key to the provider. Stripe reads it from the `Idempotency-Key` header on `POST` requests and returns the saved response when it sees the key again, for at least 24 hours. With `stripe-python`:

```python
stripe_client.v1.refunds.create(
    params={"payment_intent": payment_intent_id, "amount": amount_cents},
    options={"idempotency_key": credit_key(account_id, order_id)},
)
```

Stripe's SDKs add a random key to their own retries, but a Render retry is a new attempt in a new instance, so it gets a new random key. Pass your own. See Stripe's [idempotent requests](https://docs.stripe.com/api/idempotent_requests).

If the provider takes no key, record the call in your database under the key first, and check that record before calling.

## Prerequisites

- Python 3.11+
- A local Postgres (for example `brew install postgresql`)
- The [Render CLI](https://render.com/docs/cli)
- An [OpenAI API key](https://platform.openai.com/api-keys)

## Run locally

```bash
pip install -r requirements.txt
createdb idempotent_side_effects
cp .env.example .env
# Edit .env: set OPENAI_API_KEY and DATABASE_URL. OPENAI_MODEL defaults to gpt-6.2-sol.
```

Start the local task server. It loads `.env` when it starts:

```bash
render workflows dev -- python main.py
```

In another terminal, refund the first ticket and fail once after the credit. Task input is a JSON array of the task's arguments, and the second argument turns on the failure:

```bash
render workflows tasks runs start handle_refund --local \
  --input='[{"id": "TCK-1001", "account_id": "ACC-100", "order_id": "ORD-2001", "subject": "Charged twice", "body": "I was charged twice for ORD-2001."}, true]'
```

`refund-fail-once.json` holds the same kind of input, with the first ticket from `tickets.json`:

```bash
render workflows tasks runs start handle_refund --local --input-file refund-fail-once.json
```

The task server's output shows `issue_credit` failing with `Failed on purpose after issuing the credit`, then running again and completing. The result shows the retry found the credit:

```json
"credit": {
  "order_id": "ORD-2001",
  "status": "already_issued",
  "amount_cents": 4900,
  "credits_for_order": 1
}
```

Check the run's attempts and the table:

```bash
render workflows tasks runs list issue_credit --local            # the last row is the run you just made
render workflows tasks runs show <run-id> --local -o json        # "attempts": failed, then completed
psql idempotent_side_effects -c "SELECT order_id, amount_cents, task_run_id FROM refund_credits"
```

Run the same ticket again without the failure (drop `, true`). The model drafts a new decision, and `issue_credit` reports `already_issued` again: the key matches the credit from the first run. A ticket without an order, like `TCK-1002`, comes back as `{"ticket_id": ..., "error": ...}` without calling the model.

## Deploy to Render

Create two resources in the Render Dashboard, in the same region:

1. **Postgres**: **New > Postgres**. Copy its **Internal Database URL**.
2. **Workflow service**: **New > Workflow**, connected to your repository.

   | Field | Value |
   |---|---|
   | Language | Python |
   | Root Directory | `idempotent-side-effects` (the folder that holds `main.py`). Leave it empty if this folder is the repository root. |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `python main.py` |
   | Region | The database's region |
   | Environment variables | `DATABASE_URL` (internal URL), `OPENAI_API_KEY`, and optionally `OPENAI_MODEL` |

The workflow creates its table on first use. Once the first version is ready, run `handle_refund` from the Dashboard with `[<a ticket from tickets.json>, true]` and open the `issue_credit` run: it shows two attempts, and the table holds one credit.

## Trigger from another service

`client.py` starts `handle_refund` for the first ticket, twice, with the same idempotency key:

```bash
export RENDER_API_KEY=your-render-api-key
export WORKFLOW_SLUG=your-workflow-slug   # defaults to idempotent-side-effects
python client.py
```

The core of it:

```python
from render import Render

render = Render()  # reads RENDER_API_KEY

run = render.workflows.run_task(
    f"{SLUG}/handle_refund",
    [ticket],
    idempotency_key=f"handle_refund:{ticket['id']}",
)
```

This key works one level up from the credit's key. Starting a run again with the same key within 24 hours returns the first run, so both calls print the same run ID and the model runs once. On the local task server, which doesn't apply these keys, the two calls start two runs, and the credit's key still keeps it to one credit.

## Limits and gotchas

- **Run idempotency keys don't cover retries.** A key on `run_task` or `start_task` stops a second run from starting. It doesn't stop a run's own retries from repeating work, so the side effect needs its own key. See [idempotency keys](https://render.com/docs/workflows-running#idempotency-keys).
- **Run idempotency keys last 24 hours and are scoped to a workflow service.** After 24 hours the same key starts a new run. The credit's key in Postgres has no expiry.
- **Every retry is a new attempt in a new instance.** In-memory state from the failed attempt is gone, so the check has to live in a database or with the provider. See [retry settings](https://render.com/docs/workflows-defining-tasks#retry-settings).
- **The failure comes after the commit.** The connection uses autocommit, so the credit is committed as soon as it's inserted, like a real payment would be. If the insert sat in a transaction that the failure rolled back, there would be no credit for the retry to find.
- **Retries rerun the model call.** `draft_refund` retries twice, and each retry is another paid OpenAI request. The OpenAI client's own retries are off (`max_retries=0`), so Render's policy is the only one.
- **Timeouts apply per attempt.** `timeout_seconds=30` bounds each attempt of `issue_credit`, and a retry gets a fresh 30 seconds. See [timeouts](https://render.com/docs/workflows-defining-tasks#timeout).
- **Arguments are capped at 4 MB.** The combined arguments of a task run can't exceed 4 MB. Pass IDs instead of large payloads. See [limits](https://render.com/docs/workflows-limits).

## Links

- [Render Workflows](https://render.com/docs/workflows)
- [Defining tasks](https://render.com/docs/workflows-defining-tasks)
- [Triggering runs](https://render.com/docs/workflows-running)
- [Workflows SDK for Python](https://render.com/docs/workflows-sdk-python)
- [Render Postgres](https://render.com/docs/postgresql)
- [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests)
