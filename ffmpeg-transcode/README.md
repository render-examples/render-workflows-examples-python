# ffmpeg transcode on Render Workflows

Turn source videos into HLS renditions and thumbnails with ffmpeg, one task run per video on a 4 CPU / 16 GB compute plan, fanned out by a small orchestrator.

## What it shows

- Running ffmpeg in a task on Render's native Python runtime, which ships ffmpeg and ffprobe. No Docker image.
- A different compute plan per task: `transcode_video` runs on `4c-16g`, `transcode_batch` on the default `flex`
- A long per-attempt timeout (3 hours) on the heavy task
- Retries on the transcode task only, and none on the fan-out
- Passing videos in by URL and returning only small outputs, because task arguments are capped at 4 MB
- Returning errors a retry can't fix (broken URL, not a video, file too big) as results instead of raising

## Task tree

```
transcode_batch             (flex, no retries, 12 h timeout)
  └── transcode_video       (4c-16g, 2 retries, 3 h timeout per attempt; one run per video)
        ├── download the source URL to local disk
        ├── ffprobe          → duration, size, codecs
        ├── ffmpeg           → 4 JPEG thumbnails, 320 px wide
        ├── ffmpeg (1 pass)  → 720p / 480p / 360p H.264 + AAC, 6 s HLS segments, master.m3u8
        └── store_outputs()  → returns thumbnails (base64), the master playlist and per-rendition measurements
```

Rungs taller than the source are skipped, so a 360p source gets one 360p rendition.

## What comes back, and where real outputs would go

To keep the example self-contained, it needs no bucket and no keys. `transcode_video` returns small outputs only, about 25 KB per video:

```json
{
  "id": "sintel-trailer",
  "source": {"width": 1920, "height": 1080, "duration_seconds": 52.21, "video_codec": "h264", "audio_codec": "aac", "size_bytes": 14621544, "url": "..."},
  "renditions": [
    {"name": "720p", "resolution": "1280x720", "segments": 9, "duration_seconds": 52.21, "bytes": 15533876, "measured_kbps": 2380, "target_video_kbps": 2800, "playlist": "720p/index.m3u8", "height": 720},
    ...
  ],
  "master_playlist": "#EXTM3U\n#EXT-X-VERSION:3\n...",
  "thumbnails": [{"at_seconds": 10.44, "jpeg_base64": "/9j/4AAQ..."}, ...],
  "output_bytes": 28944776,
  "cpus": 4,
  "seconds": {"download": 0.4, "transcode": 3.2}
}
```

The HLS segments themselves are deleted with the run's disk. **A real app would upload them to object storage such as S3**, in `store_outputs()`. That function is the one place that decides what leaves the run, and its docstring shows the upload call:

```python
def store_outputs(out_dir, video_id, renditions, thumbnails):
    # In a real app, upload out_dir here instead, for example with boto3:
    #     for path in out_dir.rglob("*"):
    #         s3.upload_file(str(path), BUCKET, f"{video_id}/{path.relative_to(out_dir)}")
    # and return the object keys.
```

Use the same keys on every attempt (as above, derived from the video id) so a retry overwrites its earlier upload instead of leaving duplicates.

## Prerequisites

- Python 3.11+
- The [Render CLI](https://render.com/docs/cli)
- ffmpeg on your machine for local runs (`brew install ffmpeg`, `apt install ffmpeg`). Render's runtime already has it.

No API keys or environment variables are required.

## Run locally

```bash
pip install -r requirements.txt
render workflows dev -- python main.py
```

In another terminal, transcode one video. Task input is a JSON array of the task's arguments:

```bash
render workflows tasks runs start transcode_video --local \
  --input='[{"id": "sintel-trailer", "url": "https://download.blender.org/durian/trailer/sintel_trailer-1080p.mp4"}]'
```

`sample-input.json` holds the same input, so `--input-file sample-input.json` works too.

To transcode every video in `videos.json` at once, start `transcode_batch` with the whole list as its one argument:

```bash
render workflows tasks runs start transcode_batch --local --input="[$(cat videos.json)]"
```

`videos.json` holds two trailers, the 12-minute short film Tears of Steel (370 MB), and one broken URL that comes back under `failed`. Locally, the plan setting is ignored and every run uses your machine.

The sample videos are Blender Foundation open movies ([Sintel](https://durian.blender.org/), [Big Buck Bunny](https://peach.blender.org/), [Tears of Steel](https://mango.blender.org/)), licensed [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/).

## Deploy to Render

1. Push this folder to a Git repository Render can access.
2. In the Render Dashboard, click **New > Workflow** and connect the repository.
3. Set these fields:

   | Field | Value |
   |---|---|
   | Language | Python |
   | Root Directory | `ffmpeg-transcode` (the folder that holds `main.py`). Leave it empty if this folder is the repository root. |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `python main.py` |
   | Region | Any |
   | Environment variables | None required. Optionally `MAX_INPUT_MB` (default 1024) and `WORK_DIR`. |

4. Create the workflow. Once the first version is ready, run `transcode_video` from the Dashboard with the contents of `sample-input.json` as its input.

You don't install ffmpeg: it's one of the tools in Render's [native runtimes](https://render.com/docs/native-runtimes#tools-and-utilities). We checked from inside a task run on `4c-16g`: `/usr/bin/ffmpeg` is ffmpeg 5.1 with libx264, AAC and the HLS muxer.

## Trigger from another service

`client.py` transcodes one video, writes its thumbnails to the current directory, then runs the whole batch:

```bash
export RENDER_API_KEY=your-render-api-key
export WORKFLOW_SLUG=your-workflow-slug   # defaults to ffmpeg-transcode
python client.py
```

The core of it:

```python
import base64
from render import Render

render = Render()  # reads RENDER_API_KEY

run = render.workflows.run_task(
    f"{SLUG}/transcode_video",
    [{"id": "sintel-trailer", "url": "https://..."}],
    idempotency_key="transcode-sintel-trailer",
)
result = run.results[0]  # results is a list holding the return value
for r in result["renditions"]:
    print(r["name"], r["resolution"], r["bytes"])
open("thumb.jpg", "wb").write(base64.b64decode(result["thumbnails"][0]["jpeg_base64"]))
```

`run_task` waits for the run to finish. To start a run and check on it later, use `start_task` instead. The idempotency key makes a repeated call within 24 hours return the first run instead of starting another transcode.

## Limits and gotchas

- **Video can't travel as task arguments.** The combined arguments of a task run are capped at 4 MB, so videos come in as URLs. On the way out, keep results small too: Render keeps each run's inputs and results for 30 days and bills that state per GB ([state retention](https://render.com/docs/workflows-limits#state-retention)). Large files belong in object storage, with only their keys in the result. See [limits](https://render.com/docs/workflows-limits#additional-limits).
- **The plan is per task, and billed per second.** `transcode_video` gets 4 CPUs and 16 GB for as long as it runs; `transcode_batch` waits on `flex`, which bills for the CPU and RAM it actually uses. See [compute plans](https://render.com/docs/workflows-limits#compute-plans) and [setting a task's plan](https://render.com/docs/workflows-defining-tasks#compute-plan).
- **Big fan-outs queue.** Each workflow service can start new runs totaling 32 CPUs per minute on Pro workspaces (16 on Hobby). That's eight `4c-16g` runs per minute; the rest wait their turn. See [compute limits](https://render.com/docs/workflows-limits#compute-limits).
- **Timeouts apply per attempt.** Each attempt of `transcode_video` gets 3 hours (the maximum is 24), and a retry gets a fresh 3 hours. `transcode_batch` has 12 hours to cover the slowest video's attempts. See [timeouts](https://render.com/docs/workflows-defining-tasks#timeout).
- **A retry redoes the whole video.** `transcode_video` retries twice for errors that may pass (a network failure, ffmpeg killed for running out of memory). That's safe because it writes nothing outside its own disk. If you add uploads, write to keys derived from the video id so a retry overwrites. `transcode_batch` never retries, because a retry would rerun every video. See [retries](https://render.com/docs/workflows-defining-tasks#retry-settings).
- **Errors a retry can't fix are returned, not raised.** An HTTP 4xx on the source URL, a URL that isn't http(s), a file ffprobe can't read, or an input over `MAX_INPUT_MB` comes back as `{"id": ..., "error": ...}`. `transcode_batch` lists those videos under `failed`.
- **Tell ffmpeg how many CPUs it has.** Inside a task run, `os.cpu_count()` reports the host machine's CPUs, not your plan's. `available_cpus()` reads the plan's quota from the cgroup and passes it to ffmpeg as `-threads`.
- **Local disk is scratch space.** The source and all outputs sit on the run's disk, and the instance is gone after the run. In our test, `/tmp` on a task run was a separate 2 GB volume, while the project directory had more room. For inputs that don't fit, set `WORK_DIR` to a path with space, or lower `MAX_INPUT_MB`.
- **Your ffmpeg and Render's may differ.** Render's runtime is Debian 12, which ships ffmpeg 5.1. The options in `main.py` work on 5.1 and later. Check the runtime's version with `ffmpeg -version` in a task if you use newer options.

## Links

- [Render Workflows](https://render.com/docs/workflows)
- [Defining tasks](https://render.com/docs/workflows-defining-tasks)
- [Workflows limits](https://render.com/docs/workflows-limits)
- [Triggering runs](https://render.com/docs/workflows-running)
- [Native runtime tools](https://render.com/docs/native-runtimes#tools-and-utilities)
- [ffmpeg HLS muxer](https://ffmpeg.org/ffmpeg-formats.html#hls-2)
