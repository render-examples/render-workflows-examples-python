# Product image import on Render Workflows

Bulk-import product images from a list of URLs: resized JPEG, WebP and AVIF variants, a blurhash placeholder, and alt text from an OpenAI vision model, one set of task runs per image.

## What it shows

- Fanning out one task run per image from an orchestrator, with a cap on how many images run at once
- Image processing with Pillow in a task on Render's native Python runtime (AVIF and WebP support ship in Pillow's wheels)
- Calling a vision model with structured output (`responses.parse` with a Pydantic model) for alt text and tags
- Retries only on the tasks that call the network or the model, and none on the fan-out
- Returning bad URLs and files that aren't images as failed rows in the manifest, not as a failed batch
- Returning only small outputs, because task arguments are capped at 4 MB

## Task tree

```
import_images                (flex, no retries, 1 h timeout; up to MAX_CONCURRENT_IMAGES at once)
  ├── process_image          (2 retries, 5 min timeout per attempt; one run per image)
  │     ├── download the URL to local disk
  │     ├── Pillow           → 1200 / 600 / 300 px wide, each as JPEG, WebP and AVIF
  │     ├── blurhash         → ~30 character placeholder
  │     └── store_variants() → keeps each variant's size and key; the files are discarded
  └── generate_alt_text      (3 retries, 2 min timeout per attempt; one run per image)
        └── OpenAI vision model → {"alt_text", "tags"}
```

Widths larger than the source are skipped. `generate_alt_text` gets a 512 px JPEG preview from `process_image` (about 25 KB), not the original file.

## What comes back, and where real outputs would go

To keep the example self-contained, it needs no bucket. `import_images` returns a manifest of about 3 KB per image:

```json
{
  "total": 7, "imported": 5, "failed": 2, "missing_alt_text": 0,
  "images": [
    {
      "id": "wireless-mouse", "name": "Wireless mouse", "url": "https://upload.wikimedia.org/...", "status": "imported",
      "source": {"format": "JPEG", "width": 2600, "height": 1600, "has_alpha": false, "bytes": 476739},
      "blurhash": "LmNTzYD%?b_3%May%Mxu~qt6f6j[",
      "variants": [
        {"format": "jpeg", "width": 1200, "height": 738, "bytes": 77201, "key": "products/wireless-mouse/1200.jpeg"},
        {"format": "webp", "width": 1200, "height": 738, "bytes": 54124, "key": "products/wireless-mouse/1200.webp"},
        {"format": "avif", "width": 1200, "height": 738, "bytes": 28646, "key": "products/wireless-mouse/1200.avif"},
        ...
      ],
      "thumbnail_webp_base64": "UklGRgoGAABXRUJQ...",
      "alt_text": "Black Microsoft wireless mouse with a silver-trimmed scroll wheel and a matching USB receiver.",
      "tags": ["wireless mouse", "microsoft", "black", "scroll wheel", "usb receiver"],
      "seconds": {"download": 0.36, "variants": 1.14}
    },
    {"id": "missing-file", "status": "failed", "error": "Download failed: HTTP 404", ...}
  ]
}
```

The variant files are deleted with the run's disk. **A real app would upload them to object storage such as S3**, in `store_variants()`. That function is the one place that decides what leaves the run, and its docstring shows the upload call:

```python
def store_variants(variants):
    # In a real app, upload them here instead, for example with boto3:
    #     for v in variants:
    #         s3.upload_file(str(v["path"]), BUCKET, v["key"],
    #                        ExtraArgs={"ContentType": f"image/{v['format']}"})
    # and return each key or URL.
```

Each `key` comes from the image id, so a retry overwrites its earlier upload instead of leaving a copy.

## Prerequisites

- Python 3.11+
- The [Render CLI](https://render.com/docs/cli)
- An [OpenAI API key](https://platform.openai.com/api-keys)

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env   # then set OPENAI_API_KEY
render workflows dev -- python main.py
```

The local task server loads `.env` when it starts. In another terminal, import every image in `images.json`. Task input is a JSON array of the task's arguments, and `import_images` takes one: the list.

```bash
render workflows tasks runs start import_images --local --input="[$(cat images.json)]"
```

`images.json` holds five product photos and two broken rows: a URL that returns 404, and a Wikimedia file page (HTML) instead of the image. Both come back with `"status": "failed"`.

To process a single image without alt text, run `process_image` on its own:

```bash
render workflows tasks runs start process_image --local \
  --input='[{"id": "wireless-mouse", "url": "https://upload.wikimedia.org/wikipedia/commons/6/69/Microsoft-wireless-mouse.jpg"}]'
```

Each image is `{"id", "url"}` plus an optional `"name"`, which the model gets as context. The id becomes part of storage keys, so it may only contain letters, digits, `.`, `_` and `-`.

### Environment variables

| Variable | Default | What it does |
|---|---|---|
| `OPENAI_API_KEY` | none | Required for alt text. Without it, images still import, with `alt_text_error`. |
| `OPENAI_MODEL` | `gpt-6.2-sol` | Any OpenAI model that accepts image input |
| `MAX_CONCURRENT_IMAGES` | `8` | Images `import_images` works on at once |
| `MAX_INPUT_MB` | `25` | Larger downloads fail as `Image is ... over MAX_INPUT_MB` |
| `MAX_MEGAPIXELS` | `50` | Larger images fail before any pixels are decoded |

### Sample images

The five product photos are by Evan-Amos on Wikimedia Commons and are in the **public domain**. Each row in `images.json` links its Commons file page under `source`.

## Deploy to Render

1. Push this folder to a Git repository Render can access.
2. In the Render Dashboard, click **New > Workflow** and connect the repository.
3. Set these fields:

   | Field | Value |
   |---|---|
   | Language | Python |
   | Root Directory | `product-image-import` (the folder that holds `main.py`). Leave it empty if this folder is the repository root. |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `python main.py` |
   | Region | Any |
   | Environment variables | `OPENAI_API_KEY`. Optionally the others in the table above. |

4. Create the workflow. Once the first version is ready, run `import_images` from the Dashboard with the contents of `images.json` wrapped in `[ ]` as its input.

## Trigger from another service

`client.py` imports `images.json`, prints the manifest and writes each image's thumbnail to the current directory:

```bash
export RENDER_API_KEY=your-render-api-key
export WORKFLOW_SLUG=your-workflow-slug   # defaults to product-image-import
python client.py
```

The core of it:

```python
from render import Render

render = Render()  # reads RENDER_API_KEY

run = render.workflows.run_task(
    f"{SLUG}/import_images", [images], idempotency_key="import-sample-images-v1"
)
manifest = run.results[0]  # results is a list holding the return value
for row in manifest["images"]:
    print(row["id"], row["status"], row.get("alt_text") or row.get("error"))
```

`run_task` waits for the run to finish. To start a run and check on it later, use `start_task` instead. The idempotency key makes a repeated call within 24 hours return the first run instead of importing the same list again.

## Limits and gotchas

- **Images can't travel as task arguments.** The combined arguments of a task run are capped at 4 MB, so images come in as URLs, and the preview for the model is a 512 px JPEG. Keep results small too: Render keeps each run's inputs and results for 30 days and bills that state per GB ([state retention](https://render.com/docs/workflows-limits#state-retention)). Files belong in object storage, with only their keys in the result. See [limits](https://render.com/docs/workflows-limits#additional-limits).
- **Errors a retry can't fix are returned, not raised.** A 4xx on the image URL, a URL that isn't http(s), an HTML page, a file Pillow can't read, or an image over `MAX_INPUT_MB` or `MAX_MEGAPIXELS` comes back as a failed row. A missing `OPENAI_API_KEY`, an unknown model or another 400/401/403/404/422 from OpenAI comes back as `alt_text_error`, and the image stays imported.
- **Retries go on the tasks that call out, not on the fan-out.** `process_image` retries network errors, HTTP 5xx and 429; `generate_alt_text` retries OpenAI 429s, 5xx and connection errors. The OpenAI client's own retries are off (`max_retries=0`) so Render's policy is the only one. `import_images` never retries, because a retry would redo every image. See [retries](https://render.com/docs/workflows-defining-tasks#retry-settings).
- **A retry redoes the whole image.** That's safe here because `process_image` writes nothing outside its own disk. If you add uploads, keep the id-derived keys so a retry overwrites.
- **Timeouts apply per attempt.** Each attempt gets a fresh timeout. `import_images` has an hour to wait for every image, including retries. The seven sample images take about 10 seconds; raise the timeout for long lists. See [timeouts](https://render.com/docs/workflows-defining-tasks#timeout).
- **Big lists start many runs.** Each image starts two task runs. Render limits how fast a workspace can start chained runs, and fails runs past that limit instead of queuing them. `MAX_CONCURRENT_IMAGES` keeps the pace down; for thousands of images, also split the list into several `import_images` runs. See [workflows limits](https://render.com/docs/workflows-limits).
- **Tell the AVIF encoder how many CPUs it has.** Inside a task run, `os.cpu_count()` reports the host machine's CPUs, not your plan's. `available_cpus()` reads the plan's quota from the cgroup and passes it to Pillow as `max_threads`.
- **Some hosts need a User-Agent.** Wikimedia rejects Python's default one with HTTP 403. `download()` sends a descriptive `USER_AGENT`; change it to name your app.
- **Colors and orientation.** `open_image()` applies the EXIF rotation and keeps the source's RGB color profile in every variant. Images in CMYK or grayscale are converted to RGB without a profile.

## Links

- [Render Workflows](https://render.com/docs/workflows)
- [Defining tasks](https://render.com/docs/workflows-defining-tasks)
- [Workflows limits](https://render.com/docs/workflows-limits)
- [Triggering runs](https://render.com/docs/workflows-running)
- [OpenAI images and vision](https://platform.openai.com/docs/guides/images-vision)
- [OpenAI structured outputs](https://platform.openai.com/docs/guides/structured-outputs)
- [Pillow image file formats](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html)
- [Blurhash](https://blurha.sh/)
