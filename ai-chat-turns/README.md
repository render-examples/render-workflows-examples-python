# AI Chat Turns on Render Workflows

A chat app where each turn of a conversation runs as a Render Workflows task: a FastAPI service stores messages in Postgres, a workflow task writes each reply, and the API tells the client when the reply is ready.

## What it shows

- Starting a task from a web service and returning `202 Accepted` right away
- One turn at a time per conversation, enforced with a claim in Postgres
- Idempotency keys at both levels: on the task run (from the message ID) and on a tool's write (from the conversation and order)
- Learning when a run ends from the task-run events stream, with polling as a fallback
- Finishing interrupted turns after the API restarts, for example during a deploy
- Returning errors a retry can't fix as results instead of raising

## How a turn works

```
client ──POST /conversations/{id}/messages──▶ API service (FastAPI)
                                              1. insert the message (unique per conversation + client_message_id)
                                              2. claim the conversation (one turn at a time)
                                              3. start_task("run_turn", idempotency_key=from the message)
           ◀────────── 202 Accepted ──────────
                                              4. wait for the run's terminal event, then
                                                 mark the message done or failed and release the claim

workflow service
run_turn (retries once)
  ├── load the conversation from Postgres
  ├── Runner.run(support_agent)          ← OpenAI Agents SDK, inside the task
  │     ├── lookup_order, get_account_status   (read only)
  │     └── open_refund_request                 (writes, idempotent)
  └── insert the reply (at most one per message)

client ──GET /conversations/{id}/messages──▶ reads the reply when the message is "done"
```

`api.py` is the web service, `workflow.py` is the workflow service, and `db.py` and `schema.sql` are shared. Both services deploy from this one folder.

## Prerequisites

- Python 3.11+
- A local Postgres (for example `brew install postgresql`)
- The [Render CLI](https://render.com/docs/cli)
- An [OpenAI API key](https://platform.openai.com/api-keys)
- For the deployed API: a [Render API key](https://render.com/docs/api-keys) with **Workflows: Read** and **Run workflow tasks: Run** permissions

## Run locally

```bash
pip install -r requirements.txt
createdb ai_chat_turns
cp .env.example .env
# Edit .env: set OPENAI_API_KEY and DATABASE_URL.
```

Start the local task server. It loads `.env` when it starts:

```bash
render workflows dev -- python workflow.py
```

In a second terminal, start the API. `RENDER_USE_LOCAL_DEV=true` points its Render client at the local task server, so it needs no Render API key. The API creates the tables when it starts:

```bash
RENDER_USE_LOCAL_DEV=true python -m uvicorn api:app --env-file .env --port 8000
```

In a third terminal, chat. `chat.py` opens a conversation from the first ticket in `tickets.json` and sends a follow-up:

```bash
python chat.py
```

Or call the API yourself:

```bash
curl -X POST localhost:8000/conversations/demo/messages \
  -H 'Content-Type: application/json' \
  -d '{"client_message_id": "m-1", "content": "Where is order ORD-2003?"}'
curl localhost:8000/conversations/demo/messages
```

Sending the same `client_message_id` again returns the stored message instead of starting a second turn. Sending a new message while a turn is running returns `409`.

## Deploy to Render

Create three resources in the Render Dashboard, all in the same region:

1. **Postgres**: **New > Postgres**. Copy its **Internal Database URL**.
2. **Workflow service**: **New > Workflow**, connected to your repository.

   | Field | Value |
   |---|---|
   | Language | Python |
   | Root Directory | `ai-chat-turns` (leave empty if this folder is the repository root) |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `python workflow.py` |
   | Environment variables | `DATABASE_URL` (internal URL), `OPENAI_API_KEY`, optionally `OPENAI_MODEL` |

3. **Web service**: **New > Web Service**, same repository.

   | Field | Value |
   |---|---|
   | Language | Python |
   | Root Directory | `ai-chat-turns` |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `uvicorn api:app --host 0.0.0.0 --port $PORT` |
   | Health Check Path | `/healthz` |
   | Environment variables | `DATABASE_URL` (internal URL), `RENDER_API_KEY`, `WORKFLOW_SLUG` (the workflow service's slug) |

Task runs reach the database over the private network, like your other services in the same region. Then chat against the web service:

```bash
API_URL=https://your-web-service.onrender.com python chat.py
```

## Trigger from another service

The web service is the other service. The core of it, from `api.py`:

```python
from render import RenderAsync

render = RenderAsync()  # reads RENDER_API_KEY

run = await render.workflows.start_task(
    f"{SLUG}/run_turn",
    [conversation_id, message_id],
    # Starting the same turn again within 24 hours returns the first run.
    idempotency_key=f"turn:{conversation_id}:{client_message_id}",
)

# Later: one event per run, when it reaches a terminal status.
async for details in render.workflows.task_run_events([run.id]):
    ...
```

## Limits and gotchas

- **A retry reruns the whole turn.** `run_turn` retries once. The reply insert (one reply per message) and the refund tool (one request per conversation and order) are idempotent, so a retry doesn't double either. Any tool that writes needs the same treatment. See [retries](https://render.com/docs/workflows-defining-tasks#retry-settings).
- **Idempotency keys expire after 24 hours.** A turn started again with the same key within 24 hours returns the first run. See [idempotency keys](https://render.com/docs/workflows-running#idempotency-keys).
- **No streaming of partial output.** The events stream sends one event per run, when it reaches a terminal status. To stream tokens, have the task write partial output to your own store and relay it from the web service.
- **Deploys stop the API mid-turn.** Turns keep running on Render. When the API starts again, it finds conversations whose turn was in flight and finishes the bookkeeping. See [deploys](https://render.com/docs/deploys).
- **Arguments are capped at 4 MB.** The task gets IDs and loads the conversation from Postgres, so long chats don't hit the cap. See [limits](https://render.com/docs/workflows-limits).
- **Timeouts apply per attempt.** `timeout_seconds=120` bounds each attempt of `run_turn`.

## Links

- [Render Workflows](https://render.com/docs/workflows)
- [Triggering runs](https://render.com/docs/workflows-running)
- [Local development](https://render.com/docs/workflows-local-development)
- [Workflows SDK for Python](https://render.com/docs/workflows-sdk-python)
- [OpenAI Agents SDK for Python](https://openai.github.io/openai-agents-python/)
