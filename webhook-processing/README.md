# Webhook processing on Render Workflows

Receive GitHub webhooks reliably: a FastAPI service verifies each delivery's signature, stores it, answers in milliseconds, and hands it to a workflow task that retries and applies each delivery exactly once, even when GitHub sends it again.

## What it shows

- Verifying GitHub's `X-Hub-Signature-256` header against the raw body, with a constant-time comparison
- Acknowledging fast: the receiver only stores the delivery and starts a task, so it answers well inside GitHub's 10-second limit
- Starting a task from a web service with the Render SDK, passing the delivery ID instead of the payload
- Deduplicating by delivery ID: a redelivery of a processed delivery is a no-op, in the receiver and again in the task
- Retries that can't double-count: the task's writes and its "processed" mark commit in one Postgres transaction
- Replaying deliveries that never got processed, with a task that fans out one subtask per delivery

## How it works

```
GitHub ──POST /webhooks/github──▶ receiver (FastAPI web service)
         X-GitHub-Delivery: d1      1. verify X-Hub-Signature-256      (401 if bad)
         X-Hub-Signature-256        2. INSERT delivery d1 + payload     (Postgres)
                                    3. already processed? ── 200 {"duplicate": true}
                                    4. start_task("process_delivery", ["d1"])
       ◀── 202 in under a second ───   (503 if it can't start, so GitHub shows a failure)

workflow service
process_delivery(d1)                        retries 3 times
  ├── attempts += 1
  └── one transaction:
        ├── SELECT d1 FOR UPDATE ── processed already? return "duplicate"
        ├── add the event to repo_activity    (pushes, commits, issues opened/closed)
        └── mark d1 processed

replay_pending(older_than_minutes)          no retries
  └── process_delivery(d) for every delivery still pending   (one subtask each)
```

`receiver.py` is the web service and `workflow.py` is the workflow service. They share `db.py` and `schema.sql`. Both deploy from this folder.

The processing itself is small on purpose: it keeps per-repository counters of pushes, commits, and opened and closed issues. A counter is the simplest side effect that goes wrong when a delivery is applied twice, which is what this example guards against. Replace `apply_event` in `workflow.py` with your own work.

### Why Postgres for deduplication

The receiver has to store the payload somewhere: GitHub payloads can be up to 25 MB, and task arguments are capped at 4 MB, so the task gets only the delivery ID. Once the payload is in Postgres, the delivery's row is also the dedupe record, and the task can mark it processed in the same transaction as its own writes. A separate dedupe store, such as a Render Key Value `SET NX`, can't commit together with the Postgres writes, so a crash between the two would either lose the event or apply it twice.

Render's idempotency keys don't replace this. They last 24 hours, GitHub lets you redeliver for 3 days, and within the 24 hours the same key returns the earlier run, even if that run failed and the redelivery is meant to retry it.

## Prerequisites

- Python 3.11+
- The [Render CLI](https://render.com/docs/cli)
- Postgres (local for development; a Render Postgres database when deployed)
- For the deployed receiver: a [Render API key](https://render.com/docs/api-keys) and a GitHub repository where you can add a webhook

## Run locally

GitHub can't reach `localhost`, so locally you send signed test deliveries with `send_delivery.py`. It sends the same headers GitHub does and signs the body with your secret. Real GitHub deliveries come in once you deploy.

```bash
pip install -r requirements.txt
createdb webhook_processing
cp .env.example .env
```

In `.env`, set `DATABASE_URL` and a `GITHUB_WEBHOOK_SECRET`. Generate the secret with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Start the local task server. It loads `.env`:

```bash
render workflows dev -- python workflow.py
```

In a second terminal, start the receiver. `RENDER_USE_LOCAL_DEV=true` points its Render client at the local task server, so it needs no Render API key:

```bash
RENDER_USE_LOCAL_DEV=true python -m uvicorn receiver:app --env-file .env --port 8000
```

If you started the task server on another port (`render workflows dev --port 8121 ...`), also set `RENDER_LOCAL_DEV_URL=http://localhost:8121`.

In a third terminal, send a delivery:

```bash
python send_delivery.py deliveries/issue-opened.json
```

```
delivery 5f1c0b9e-6a3d-4c4e-9a43-3d2f6a0e7b11 (issues)
202 {"delivery_id":"5f1c0b9e-6a3d-4c4e-9a43-3d2f6a0e7b11","run_id":"trn-dc4pp4ve8m851dmlhtn0"}
```

Send it again with the same delivery ID, as a GitHub redelivery would. Nothing is processed twice:

```bash
python send_delivery.py deliveries/issue-opened.json --delivery-id 5f1c0b9e-6a3d-4c4e-9a43-3d2f6a0e7b11
# 200 {"delivery_id":"5f1c0b9e-...","duplicate":true}
```

A wrong signature is rejected before anything is stored:

```bash
python send_delivery.py deliveries/push.json --bad-signature
# 401 {"detail":"Bad signature"}
```

Watch a retry: `deliveries/issue-fail-once.json` is an issue titled `[fail-once] ...`, and `process_delivery` fails the first attempt of every delivery about such an issue, after it has updated the counters. The transaction rolls back, Render retries, and the issue is counted once:

```bash
python send_delivery.py deliveries/issue-fail-once.json
render workflows tasks runs show <run_id> --local
```

The run shows two attempts: one `failed` (`Failed on purpose on the first attempt ([fail-once])`) and one `completed`. Check the tables:

```bash
psql webhook_processing -c "SELECT delivery_id, event, status, receipts, attempts FROM webhook_deliveries"
psql webhook_processing -c "SELECT * FROM repo_activity"
```

### Replay

Stop the task server (Ctrl+C) and send a delivery. The receiver stores it but can't start a task, so it answers `503`, and GitHub would show the delivery as failed:

```bash
python send_delivery.py deliveries/push.json
# 503 {"detail":"Couldn't start processing"}
```

Start the task server again and replay every pending delivery. `[0]` means "received at least 0 minutes ago":

```bash
render workflows tasks runs start replay_pending --local --input='[0]'
```

The run returns `{"pending": 1, "processed": [{"delivery_id": "...", "status": "processed"}], "failed": []}`. On Render you can also redeliver from GitHub, which starts processing again because the delivery is still pending.

You can also run `process_delivery` by hand with a delivery ID. For a delivery that's already processed it returns `{"status": "duplicate"}` and changes nothing:

```bash
render workflows tasks runs start process_delivery --local --input='["<delivery_id>"]'
```

## Deploy to Render

Create three resources in the Render Dashboard, connected to your repository:

1. **Postgres**: **New > Postgres**. Any plan works. Copy its **Internal Database URL**.

2. **Workflow service**: **New > Workflow**.

   | Field | Value |
   |---|---|
   | Language | Python |
   | Root Directory | `webhook-processing` (leave empty if this folder is the repository root) |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `python workflow.py` |
   | Environment variables | `DATABASE_URL` (the internal URL) |

   Note the service's slug, shown on its page.

3. **Web service**: **New > Web Service**.

   | Field | Value |
   |---|---|
   | Language | Python |
   | Root Directory | `webhook-processing` |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `uvicorn receiver:app --host 0.0.0.0 --port $PORT` |
   | Instance Type | A paid instance type (see [Limits and gotchas](#limits-and-gotchas)) |
   | Health Check Path | `/healthz` |
   | Environment variables | `DATABASE_URL`, `GITHUB_WEBHOOK_SECRET`, `RENDER_API_KEY`, `WORKFLOW_SLUG` |

   `RENDER_API_KEY` is a [Render API key](https://render.com/docs/api-keys) from the same workspace as the workflow service; the receiver only needs **Run workflow tasks: Run**. `WORKFLOW_SLUG` is the workflow service's slug. Generate a new `GITHUB_WEBHOOK_SECRET` for production.

Then add the webhook in GitHub: in your repository, **Settings > Webhooks > Add webhook**.

| Field | Value |
|---|---|
| Payload URL | `https://<your-web-service>.onrender.com/webhooks/github` |
| Content type | `application/json` |
| Secret | the same `GITHUB_WEBHOOK_SECRET` |
| Which events | **Let me select individual events**: **Pushes** and **Issues** |

GitHub sends a `ping` when you save; the receiver answers `200`. Push a commit or open an issue, then open the webhook's **Recent Deliveries** tab: each delivery shows the `202` and how long the receiver took. In our test on a Starter instance, GitHub recorded 0.3 to 0.9 seconds per delivery. **Redeliver** sends the same delivery again with the same `X-GitHub-Delivery` ID; the receiver answers `200 {"duplicate": true}`.

You can also redeliver with the GitHub CLI. The delivery's numeric `id` from the first call goes in the second; its `guid` is the `X-GitHub-Delivery` value:

```bash
gh api repos/OWNER/REPO/hooks/HOOK_ID/deliveries --jq '.[] | {id, guid, event, status_code}'
gh api -X POST repos/OWNER/REPO/hooks/HOOK_ID/deliveries/DELIVERY_ID/attempts
```

To replay pending deliveries on Render, start `replay_pending` from the Dashboard (input `[10]`) or the CLI, or on a schedule from a [cron job](https://render.com/docs/cronjobs) with the SDK:

```bash
render workflows tasks runs start <workflow-slug>/replay_pending --input='[10]'
```

## Trigger from another service

The receiver is the other service. The core of it, from `receiver.py`:

```python
from render import RenderAsync

render = RenderAsync()  # reads RENDER_API_KEY

# After verifying the signature and storing the delivery:
run = await render.workflows.start_task(f"{SLUG}/process_delivery", [delivery_id])
return JSONResponse(status_code=202, content={"delivery_id": delivery_id, "run_id": run.id})
```

`start_task` returns as soon as Render accepts the run; it doesn't wait for the task. Use `run_task` only when you want to wait for the result, which a webhook receiver shouldn't.

## Limits and gotchas

- **Answer within 10 seconds.** GitHub drops the connection after 10 seconds and marks the delivery failed. Do the work in the task, not in the request. See [GitHub's best practices](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks).
- **GitHub doesn't retry failed deliveries.** A `5xx`, a timeout, or an unreachable receiver leaves the delivery failed until you redeliver it, by hand or with the API, within 3 days. Stripe and most other providers retry on their own, so expect duplicates from them. See [handling failed deliveries](https://docs.github.com/en/webhooks/using-webhooks/handling-failed-webhook-deliveries).
- **Use a paid instance for the receiver.** A free web service spins down when idle and takes about a minute to start again, which is longer than GitHub waits. See [free instances](https://render.com/docs/free).
- **Verify the raw body.** The signature covers the exact bytes GitHub sent. Parsing the JSON and serializing it again can change them. Set the webhook's content type to `application/json`; the receiver answers `415` to form-encoded deliveries.
- **A retry reruns the whole attempt.** `process_delivery` is safe to retry because an attempt that fails rolls back everything it wrote. If your processing calls another API, make that call idempotent too, for example with the delivery ID as its idempotency key. See [retries](https://render.com/docs/workflows-defining-tasks#retry-settings).
- **Timeouts apply per attempt.** `timeout_seconds=60` bounds each attempt of `process_delivery`; a retry gets a fresh 60 seconds.
- **Arguments are capped at 4 MB.** Pass the delivery ID, not the payload. See [limits](https://render.com/docs/workflows-limits).
- **Run history lasts 30 days on Render.** The `webhook_deliveries` table is your own record; delete old rows on whatever schedule you need. See [state retention](https://render.com/docs/workflows-limits#state-retention).
- **Order isn't guaranteed.** Each delivery runs as its own task run, so two deliveries can finish in either order. Don't rely on arrival order; read the current state from GitHub's API if you need it.

## Links

- [Render Workflows](https://render.com/docs/workflows)
- [Triggering runs](https://render.com/docs/workflows-running)
- [Local development](https://render.com/docs/workflows-local-development)
- [Workflows SDK for Python](https://render.com/docs/workflows-sdk-python)
- [Validating GitHub webhook deliveries](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
- [Redelivering GitHub webhooks](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/redelivering-webhooks)
