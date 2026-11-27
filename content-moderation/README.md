# Content moderation on Render Workflows

Moderate user-generated posts (text plus an optional image) with the OpenAI moderation endpoint: each post runs in its own task run, a simple policy decides approve, flag for review, or reject, and flagged posts get a short note for the human reviewer.

## What it shows

- Fanning out one task run per post with `ctx.run` and `asyncio.gather`, with a cap on runs in flight
- Retrying only the tasks that call OpenAI, and not the task that fans out
- Returning policy decisions and errors a retry can't fix (a missing image, an unknown model, a missing API key) as results instead of raising
- Telling permanent download errors (404, not an image) from transient ones (timeouts, 429, 5xx) so only the second kind retries
- A second, conditional step: only flagged posts go to a chat model for a reviewer note
- Reading the models and the policy thresholds from environment variables

`posts.json` holds six synthetic posts from made-up users. The text is mild on purpose: an idiom, a heated opinion, a joking threat and a plain insult are enough to show all three decisions.

## Task tree

```
moderate_posts (fans out, no retries)
  ├── moderate_post      (one per post, retries on 429/5xx/network errors)
  │     ├── download the image, if the post has one
  │     └── one call to the moderation endpoint (text and image together)
  │           → approve, flag or reject
  └── write_review_note  (one per flagged post, retries the same way)
        └── one call to OPENAI_MODEL
→ counts per decision, the review queue with notes, rejected posts, errors
```

## The policy

The moderation endpoint scores each category (harassment, violence, self-harm and so on) from 0 to 1. `moderate_post` turns the scores into a decision:

| Decision | Rule (defaults) |
|---|---|
| `reject` | Any category scores at or above `REJECT_THRESHOLD` (0.9) |
| `flag` | Any category scores at or above `FLAG_THRESHOLD` (0.4) |
| `approve` | Every category scores below `FLAG_THRESHOLD` |

Each decision comes with its reasons: every category over `FLAG_THRESHOLD`, its score, and whether the text or the image triggered it. With the defaults, the sample posts come out as 3 approved, 2 flagged and 1 rejected.

The thresholds are a starting point. A real policy usually sets them per category (stricter for some, routed to a specialist team for others) and tunes them on your own content.

## Prerequisites

- Python 3.11+
- The [Render CLI](https://render.com/docs/cli)
- An [OpenAI API key](https://platform.openai.com/api-keys). The moderation endpoint is free to use; the reviewer notes are billed as normal model calls.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set OPENAI_API_KEY.
```

The rest of `.env` is optional:

| Variable | Default | Meaning |
|---|---|---|
| `MODERATION_MODEL` | `omni-moderation-2024-09-26` | The moderation model. A dated snapshot keeps scores stable, so the thresholds keep meaning the same thing. `omni-moderation-latest` follows OpenAI's newest model. |
| `OPENAI_MODEL` | `gpt-6.2-sol` | The model that writes reviewer notes for flagged posts. |
| `FLAG_THRESHOLD` | `0.4` | A category score at or above this flags the post for review. |
| `REJECT_THRESHOLD` | `0.9` | A category score at or above this rejects the post. |
| `MAX_CONCURRENT_POSTS` | `8` | Task runs `moderate_posts` keeps in flight. |

Start the local task server. It loads `.env` when it starts:

```bash
render workflows dev -- python main.py
```

In another terminal, moderate the sample posts. Task input is a JSON array of the task's arguments, here one list of posts:

```bash
render workflows tasks runs start moderate_posts --local --input="[$(cat posts.json)]"
```

The result looks like this (trimmed):

```json
{
  "model": "omni-moderation-2024-09-26",
  "policy": {"flag_threshold": 0.4, "reject_threshold": 0.9},
  "counts": {"approve": 3, "flag": 2, "reject": 1, "error": 0},
  "flagged": [
    {
      "post_id": "post-105",
      "decision": "flag",
      "reasons": [
        {"category": "violence", "score": 0.764, "inputs": ["text"]},
        {"category": "harassment/threatening", "score": 0.463, "inputs": ["text"]},
        {"category": "harassment", "score": 0.404, "inputs": ["text"]}
      ],
      "review_note": "The phrase “I swear I’ll hunt you down” likely triggered the hold because it sounds like a threat directed at another person. In context, “haha” and “Kidding, but please stop” make it read as a joke expressing frustration about spoilers rather than a serious threat.",
      "author": "sample-user-5",
      "text": "If you post one more spoiler I swear I'll hunt you down, haha. Kidding, but please stop."
    }
  ],
  "rejected": [
    {"post_id": "post-106", "decision": "reject", "reasons": [{"category": "harassment", "score": 0.98, "inputs": ["text"]}]}
  ],
  "approved": ["post-101", "post-102", "post-103"],
  "errors": []
}
```

To moderate one post on its own, or see how a missing image comes back:

```bash
render workflows tasks runs start moderate_post --local \
  --input='[{"id": "post-201", "text": "Look at my cat", "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c6/Does_not_exist.jpg/960px-Does_not_exist.jpg"}]'
```

```json
{"post_id": "post-201", "error": "Image URL returned HTTP 404"}
```

## Deploy to Render

1. Push this folder to a Git repository Render can access.
2. In the Render Dashboard, click **New > Workflow** and connect the repository.
3. Set these fields:

   | Field | Value |
   |---|---|
   | Language | Python |
   | Root Directory | `content-moderation` (the folder that holds `main.py`). Leave it empty if this folder is the repository root. |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `python main.py` |
   | Region | Any |
   | Environment variables | `OPENAI_API_KEY`, and optionally `MODERATION_MODEL`, `OPENAI_MODEL`, `FLAG_THRESHOLD`, `REJECT_THRESHOLD` and `MAX_CONCURRENT_POSTS` |

4. Create the workflow. Once the first version is ready, run `moderate_posts` from the Dashboard with the contents of `posts.json` wrapped in an array: `[<posts>]`.

## Trigger from another service

In a real app, the service that accepts a post starts a moderation run and stores the decision. `client.py` runs the sample batch with the Render SDK and prints the review queue:

```bash
export RENDER_API_KEY=your-render-api-key
export WORKFLOW_SLUG=your-workflow-slug   # defaults to content-moderation
python client.py
```

The core of it:

```python
from render import Render

render = Render()  # reads RENDER_API_KEY

run = render.workflows.run_task(f"{SLUG}/moderate_posts", [posts])
if run.status == "completed":
    report = run.results[0]  # results is a list holding the return value
    print(report["counts"])
    for item in report["flagged"]:
        print(item["post_id"], item["review_note"])
```

`run_task` waits for the run to finish. To start a run and check on it later, use `start_task` instead. To moderate a single post as it arrives, start `moderate_post` with `[post]`.

## Limits and gotchas

- **Retries are for the API, not the decision.** `moderate_post` and `write_review_note` raise on 429s, 5xx errors and network errors, and Render retries them up to three times (2, 4 and 8 seconds apart). A `reject` is the task's result, not a failure, so it's never retried. See [retries](https://render.com/docs/workflows-defining-tasks#retry-settings).
- **Errors a retry can't fix are returned, not raised.** A missing API key, an unknown model (404), another OpenAI 400/401/403/413/422, an image URL that returns 404 or isn't an image: each comes back as `{"post_id": ..., "error": ...}` and is counted under `error`. Treat those posts as not moderated and hold them for review; don't publish them.
- **`moderate_posts` doesn't retry.** A retry would moderate every post again. A post whose run failed after its retries is listed under `errors`, and the rest of the batch still completes.
- **The task downloads the image itself.** OpenAI can fetch image URLs, but then any download problem comes back as the same 400 error, whether the image is gone or the host was briefly unavailable. Downloading in the task lets a 404 return at once and a timeout retry. The image goes to OpenAI as a base64 data URL; this example caps downloads at 5 MB (OpenAI accepts up to 20 MB). Downloads send a User-Agent with a contact URL: Wikimedia, which hosts the sample images, answers 403 to requests from servers without one.
- **Only fetch images you host.** The task downloads whatever URL a post contains. In production, accept only URLs from your own upload storage, so users can't make your task fetch arbitrary addresses.
- **One image per request.** The moderation endpoint rejects a request with more than one image, so this example takes one `image_url` per post. For more, moderate each image in its own request. Not every category applies to images; see OpenAI's [supported categories](https://developers.openai.com/api/docs/guides/moderation).
- **The reviewer note doesn't see the image.** It gets the post's text and the flagged categories. The note helps the reviewer; it never changes the decision, and a failed note leaves the post in the review queue with `note_error`.
- **Render doesn't cap concurrency for you.** Render queues runs only when they'd exceed your workspace's compute limits. `moderate_posts` holds a semaphore around each `ctx.run`; set `MAX_CONCURRENT_POSTS` to what your OpenAI rate limits allow. See [limits](https://render.com/docs/workflows-limits).
- **Chained runs are rate limited.** A workspace can start up to 1,000 chained runs per minute, and `ctx.run` fails past that. Split large backlogs into batches.
- **Timeouts apply per attempt.** `timeout_seconds=60` bounds each attempt of `moderate_post` and `write_review_note`, and a retry gets a fresh 60 seconds. `moderate_posts` has 30 minutes for the whole batch. See [timeouts](https://render.com/docs/workflows-defining-tasks#timeout).
- **Arguments are capped at 4 MB.** The combined arguments of a run can't exceed 4 MB. Pass image URLs, not image bytes, and split large batches.

## Sample images

- [Sourdough Bread Loaf](https://commons.wikimedia.org/wiki/File:Sourdough_Bread_Loaf.jpg) by Agelaia, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0)
- [A Golden Retriever-9 (Barras)](https://commons.wikimedia.org/wiki/File:A_Golden_Retriever-9_(Barras).JPG) by Barras, [CC BY 3.0](https://creativecommons.org/licenses/by/3.0)

## Links

- [Render Workflows](https://render.com/docs/workflows)
- [Defining tasks](https://render.com/docs/workflows-defining-tasks)
- [Triggering runs](https://render.com/docs/workflows-running)
- [Workflows SDK for Python](https://render.com/docs/workflows-sdk-python)
- [OpenAI moderation guide](https://developers.openai.com/api/docs/guides/moderation)
