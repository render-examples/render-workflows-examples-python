# Browser screenshots on Render Workflows

Check many web pages with headless Chromium: one task run per URL records the HTTP status, title, load time, console errors and broken images, and takes a screenshot.

## What it shows

- Running headless Chromium ([Playwright](https://playwright.dev/python/)) in a task on Render's native Python runtime. The build command downloads the browser into the project. No Docker image.
- Fanning out one task run per URL with a concurrency cap in code (`MAX_CONCURRENCY`), since Render has no per-task concurrency setting
- Retries per URL for errors that may pass (timeouts, dropped connections, 5xx), and none on the fan-out
- Returning errors a retry can't fix (bad URL, unknown host, bad certificate) as results instead of raising
- Returning only small outputs (a 320 px JPEG thumbnail per page), because results are stored with the run

## Task tree

```
check_pages                 (flex, no retries, 1 h timeout)
  └── check_page            (flex, 2 retries, 2 min timeout per attempt; one run per URL,
        │                    at most MAX_CONCURRENCY at once)
        ├── launch headless Chromium
        ├── page.goto(url)       → status, final URL, load time
        │     └── listeners      → console errors, uncaught exceptions, failed image requests
        ├── page.title()
        ├── page.screenshot()    → 1280x800 PNG
        └── store_screenshot()   → returns a 320 px JPEG thumbnail (base64), discards the PNG
```

## What comes back, and where real screenshots would go

`check_page` returns about 10-20 KB per page:

```json
{
  "url": "https://render.com/docs/workflows",
  "final_url": "https://render.com/docs/workflows",
  "ok": false,
  "problems": ["2 console errors"],
  "status": 200,
  "title": "Intro to Render Workflows – Render Docs",
  "load_ms": 1426,
  "console_error_count": 2,
  "console_errors": ["Loading the script 'https://...' violates the following Content Security Policy directive: ..."],
  "broken_image_count": 0,
  "broken_images": [],
  "screenshot_bytes": 136299,
  "thumbnail_jpeg_base64": "/9j/4AAQ..."
}
```

`ok` is true when the page returned a status below 400 with no console errors and no broken images. `problems` says what failed. A page that couldn't be loaded at all comes back as `{"url": ..., "ok": false, "error": "..."}`.

`check_pages` returns `total`, `checked`, `with_problems` (a list of URLs), `failed` (pages that couldn't be loaded) and `pages` (every result above).

The full screenshot is discarded when the run ends. **A real app would upload it to object storage such as S3**, in `store_screenshot()`. That function is the one place that decides what leaves the run, and its docstring shows the upload call:

```python
async def store_screenshot(context, url, png):
    # In a real app, upload png here instead, for example with boto3:
    #     key = f"screenshots/{hashlib.sha256(url.encode()).hexdigest()}.png"
    #     s3.put_object(Bucket=BUCKET, Key=key, Body=png, ContentType="image/png")
    # and return the key.
```

A key derived from the URL means a retry overwrites its earlier upload instead of leaving a duplicate. The thumbnail is made by Chromium itself (a canvas in a blank page), so the example needs no image library.

## Prerequisites

- Python 3.11+
- The [Render CLI](https://render.com/docs/cli)

No API keys or environment variables are required.

## Run locally

Install the dependencies, then download Playwright's headless Chromium (about 100 MB):

```bash
pip install -r requirements.txt
PLAYWRIGHT_BROWSERS_PATH=0 playwright install --only-shell chromium
render workflows dev -- python main.py
```

`PLAYWRIGHT_BROWSERS_PATH=0` puts the browser inside the installed `playwright` package instead of your user cache. `main.py` sets the same value before it starts Playwright, so it finds the browser there, locally and on Render. `--only-shell` downloads only the headless build, which is all this example uses.

In another terminal, check one page. Task input is a JSON array of the task's arguments:

```bash
render workflows tasks runs start check_page --local --input='["https://render.com/docs/workflows"]'
```

`sample-input.json` holds the same input, so `--input-file sample-input.json` works too.

To check every URL in `urls.json` at once, start `check_pages` with the whole list as its one argument:

```bash
render workflows tasks runs start check_pages --local --input="[$(cat urls.json)]"
```

`urls.json` holds a few public pages, a docs URL that returns 404, and an unknown host that comes back under `failed`. Check only sites you own or are allowed to load automatically.

Optional environment variables:

| Variable | Default | What it does |
|---|---|---|
| `MAX_CONCURRENCY` | `5` | How many `check_page` runs `check_pages` keeps going at once |
| `MAX_URLS` | `100` | The most URLs one `check_pages` run accepts |
| `NAVIGATION_TIMEOUT_MS` | `30000` | How long a page may take to fire its load event, per attempt |

## Deploy to Render

1. Push this folder to a Git repository Render can access.
2. In the Render Dashboard, click **New > Workflow** and connect the repository.
3. Set these fields:

   | Field | Value |
   |---|---|
   | Language | Python |
   | Root Directory | `browser-screenshots` (the folder that holds `main.py`). Leave it empty if this folder is the repository root. |
   | Build Command | `pip install -r requirements.txt && PLAYWRIGHT_BROWSERS_PATH=0 playwright install --only-shell chromium` |
   | Start Command | `python main.py` |
   | Region | Any |
   | Environment variables | None required. Optionally the three above. |

4. Create the workflow. Once the first version is ready, run `check_page` from the Dashboard with the contents of `sample-input.json` as its input.

The browser has to live inside the project. We checked from inside a task run: the project directory from the build is there, but `~/.cache`, where Playwright downloads browsers by default, isn't. You don't need Playwright's `--with-deps` option, which installs system packages with apt: Render's [native runtimes](https://render.com/docs/native-runtimes#tools-and-utilities) (Debian 12) already have every library Chromium needs, and it launches in under a second on `flex`.

## Trigger from another service

`client.py` checks every URL in `urls.json`, prints a line per page and writes the thumbnails to `thumbnails/`:

```bash
export RENDER_API_KEY=your-render-api-key
export WORKFLOW_SLUG=your-workflow-slug   # defaults to browser-screenshots
python client.py
```

The core of it:

```python
import base64
from render import Render

render = Render()  # reads RENDER_API_KEY

run = render.workflows.run_task(f"{SLUG}/check_pages", [urls])
batch = run.results[0]  # results is a list holding the return value
for page in batch["pages"]:
    print(page["status"], page["load_ms"], page["url"], page["problems"])
open("first.jpg", "wb").write(base64.b64decode(batch["pages"][0]["thumbnail_jpeg_base64"]))
```

`run_task` waits for the run to finish. To start a run and check on it later, use `start_task` instead.

## Limits and gotchas

- **Keep results small.** Render keeps each run's arguments and results for 30 days and bills that state per GB ([state retention](https://render.com/docs/workflows-limits#state-retention)), and a run's combined arguments are capped at 4 MB ([limits](https://render.com/docs/workflows-limits#additional-limits)). Full screenshots belong in object storage, with only their keys in the result. With `MAX_URLS` at 100, one batch's thumbnails stay under about 2 MB.
- **The cap is yours to set.** Render starts every `ctx.run` you call, up to the service's [compute limits](https://render.com/docs/workflows-limits#compute-limits): new runs totaling 32 CPUs and 128 GB per minute on Pro workspaces (16 CPUs and 64 GB on Hobby). Runs past that queue. `MAX_CONCURRENCY` keeps you well under that and keeps you from flooding the sites you check. Chained runs are also limited to 1,000 per minute per workspace and fail instead of queueing when you go over ([API rate limits](https://render.com/docs/workflows-limits#api-rate-limits)).
- **Plan for Chromium's memory.** `check_page` runs on `flex` (up to 1 CPU and 4 GB, billed for what the run uses). Heavy pages may need more: set `plan="2c-4g"` or larger on `check_page` only. See [compute plans](https://render.com/docs/workflows-limits#compute-plans).
- **Timeouts apply per attempt.** Each attempt of `check_page` gets 2 minutes, and a retry gets a fresh 2 minutes. `check_pages` has an hour to cover every URL's attempts. See [timeouts](https://render.com/docs/workflows-defining-tasks#timeout).
- **A retry reloads the page.** `check_page` retries twice, waiting 5 then 10 seconds, for errors that may pass: a navigation timeout, a dropped connection, a 5xx or 429 response. Loading a page is safe to repeat here; if you add uploads, write to keys derived from the URL. `check_pages` never retries, because a retry would recheck every URL. See [retries](https://render.com/docs/workflows-defining-tasks#retry-settings).
- **Load time is measured from the task.** `load_ms` is the time until the page's load event, from wherever the run happens to be. Compare pages within one batch, not across regions or days.

## Links

- [Render Workflows](https://render.com/docs/workflows)
- [Defining tasks](https://render.com/docs/workflows-defining-tasks)
- [Workflows limits](https://render.com/docs/workflows-limits)
- [Python SDK reference](https://render.com/docs/workflows-sdk-python)
- [Playwright for Python: browsers](https://playwright.dev/python/docs/browsers)
