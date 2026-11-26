# Claude Agent SDK on Render Workflows

Run an agent built with the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) as a Render Workflows task, and fan it out across a batch of support tickets.

## What it shows

- A Claude Agent SDK agent with custom tools, running inside one task run
- Custom tools served from an in-process MCP server (`create_sdk_mcp_server`), with no built-in tools enabled
- Structured output: the agent returns a `Resolution` that matches a JSON schema
- A separate temporary working directory for every run
- Fanning out one task run per ticket with `ctx.run` and `asyncio.gather`
- Retrying the task that calls the model, and not the task that fans out
- Returning errors a retry can't fix (max turns hit, unknown model, bad or missing API key) as results instead of raising
- Triggering the tasks from another service with the Render SDK client

The SDK runs the agent loop (model calls and tool calls) inside a single task run. For an agent loop where every model call and tool call is its own task, see [openai-agent](../openai-agent/).

## Task tree

```
triage_batch (fans out, no retries)
  └── triage_ticket            (one per ticket, retries once)
        └── query(prompt, options)          ← inside the task, run by the SDK
              ├── starts a Claude Code process in a temporary directory
              ├── tools: lookup_order, get_account_status  (in-process MCP server)
              └── returns a Resolution (structured output)
```

## How the SDK runs on Render

Each `query()` call starts a Claude Code process and talks to it over stdin and stdout. The `claude-agent-sdk` package bundles that process as a native binary for Linux, macOS, and Windows, so a Render Python service needs no Node.js install and no extra build step. `pip install -r requirements.txt` is enough.

The bundled binary makes the package large (about 170 MB per platform wheel), so the first build takes longer than a typical Python service. Each task run also spends a few seconds starting the process before the first model call.

Each run gets its own directory from `tempfile.TemporaryDirectory()`. The agent's working directory (`cwd`) and its Claude Code config directory (`CLAUDE_CONFIG_DIR`) both live there, so concurrent runs share no files, and the session transcript is deleted when the run ends. `setting_sources=[]` keeps the agent from loading any settings files it finds on the machine.

## Prerequisites

- Python 3.11+
- The [Render CLI](https://render.com/docs/cli)
- An [Anthropic API key](https://platform.claude.com/settings/keys)

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY. ANTHROPIC_MODEL defaults to claude-opus-5-5.
```

Start the local task server. It loads `.env` when it starts:

```bash
render workflows dev -- python main.py
```

In another terminal, triage one ticket. Task input is a JSON array of the task's arguments:

```bash
render workflows tasks runs start triage_ticket --local \
  --input='[{"id": "TCK-1001", "account_id": "ACC-100", "order_id": "ORD-2001", "subject": "Charged twice for my order", "body": "I was charged two times for order ORD-2001 yesterday."}]'
```

To triage the five sample tickets in `tickets.json` at once, start `triage_batch` with the whole list as its one argument:

```bash
render workflows tasks runs start triage_batch --local --input="[$(cat tickets.json)]"
```

Each result includes the agent's `final_output`, the number of turns it took, and `total_cost_usd`, the SDK's estimate of the model cost for that run.

## Deploy to Render

1. Push this folder to a Git repository Render can access.
2. In the Render Dashboard, click **New > Workflow** and connect the repository.
3. Set these fields:

   | Field | Value |
   |---|---|
   | Language | Python |
   | Root Directory | `claude-agent-sdk` (the folder that holds `main.py`). Leave it empty if this folder is the repository root. |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `python main.py` |
   | Region | Any |
   | Environment variables | `ANTHROPIC_API_KEY`, and optionally `ANTHROPIC_MODEL` and `AGENT_MAX_TURNS` |

4. Create the workflow. Once the first version is ready, run `triage_ticket` from the Dashboard with a ticket from `tickets.json` wrapped in a JSON array.

## Trigger from another service

`client.py` triggers both tasks with the Render SDK:

```bash
export RENDER_API_KEY=your-render-api-key
export WORKFLOW_SLUG=your-workflow-slug   # defaults to claude-agent-sdk
python client.py
```

The core of it:

```python
from render import Render

render = Render()  # reads RENDER_API_KEY

run = render.workflows.run_task(f"{SLUG}/triage_ticket", [tickets[0]])
if run.status == "completed":
    result = run.results[0]  # results is a list holding the return value
    print(result["final_output"]["category"], result["final_output"]["customer_reply"])
```

`run_task` waits for the run to finish. To start a run and check on it later, use `start_task` instead.

## Limits and gotchas

- **A retry reruns the whole agent.** `triage_ticket` retries once, which repeats every model call and tool call in the run. That's safe here because both tools only read data. If a tool writes (a refund, an email), make that write idempotent or move it into its own task with retries off. See [retries](https://render.com/docs/workflows-defining-tasks#retry-settings).
- **One retry policy.** Claude Code retries failed API requests on its own by default. The task sets `CLAUDE_CODE_MAX_RETRIES=0` in the agent's environment, so a rate limit or overload fails the attempt and Render's retry policy decides what happens next.
- **Timeouts apply per attempt.** `timeout_seconds=180` bounds each attempt of `triage_ticket`, and a retry gets a fresh 180 seconds. See [timeouts](https://render.com/docs/workflows-defining-tasks#timeout).
- **Arguments are capped at 4 MB.** The combined arguments of a task run can't exceed 4 MB. Pass ticket IDs instead of large payloads if your tickets carry attachments. See [limits](https://render.com/docs/workflows-limits).
- **Fan-out meets Anthropic rate limits.** `triage_batch` starts every ticket at once. A 429 from the Anthropic API raises, and Render retries that ticket once. For large batches, fan out in chunks.
- **Errors a retry can't fix are returned, not raised.** Hitting `AGENT_MAX_TURNS`, failing to produce a valid `Resolution`, a missing `ANTHROPIC_API_KEY`, or an Anthropic 400/401/403/404/422 comes back as `{"ticket_id": ..., "error": ...}`. `triage_batch` lists those tickets under `failed`.
- **No built-in tools.** `tools=[]` removes Claude Code's built-in tools (Bash, Read, Edit, and the rest), so the agent can only call `lookup_order` and `get_account_status`. If you turn built-ins on, the agent runs commands and edits files on the task's instance. See the SDK's [permissions docs](https://code.claude.com/docs/en/agent-sdk/permissions) before you do.
- **Memory.** Anthropic suggests 1 GiB of RAM per agent as a starting point. The default `flex` plan gives a task run up to 4 GB. See [compute plans](https://render.com/docs/workflows-limits).

## Links

- [Render Workflows](https://render.com/docs/workflows)
- [Defining tasks](https://render.com/docs/workflows-defining-tasks)
- [Triggering runs](https://render.com/docs/workflows-running)
- [Workflows SDK for Python](https://render.com/docs/workflows-sdk-python)
- [Claude Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)
- [Custom tools in the Agent SDK](https://code.claude.com/docs/en/agent-sdk/custom-tools)
- [Hosting the Agent SDK](https://code.claude.com/docs/en/agent-sdk/hosting)
