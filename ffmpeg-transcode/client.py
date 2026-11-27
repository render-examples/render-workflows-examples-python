"""
Trigger the transcode tasks from another service or script.

Requires RENDER_API_KEY. Set WORKFLOW_SLUG to your workflow service's slug
(it defaults to ffmpeg-transcode).
"""

import base64
import json
import os
import pathlib

from render import Render

SLUG = os.getenv("WORKFLOW_SLUG", "ffmpeg-transcode")
videos = json.loads((pathlib.Path(__file__).parent / "videos.json").read_text())

# Render() reads RENDER_API_KEY from the environment.
render = Render()

# One video. Task input is a list of the task's arguments. run_task waits for
# the run to finish and returns its details. The idempotency key makes a
# repeated call within 24 hours return the first run instead of paying for a
# second transcode of the same video.
video = videos[0]
run = render.workflows.run_task(
    f"{SLUG}/transcode_video", [video], idempotency_key=f"transcode-{video['id']}"
)
if run.status != "completed":
    raise SystemExit(f"Run {run.id} ended as {run.status}: {run.error}")

# results is a list holding the task's return value.
result = run.results[0]
if "error" in result:
    raise SystemExit(f"{result['id']}: {result['error']}")
print(f"{result['id']}: {result['source']['duration_seconds']} s, took {result['seconds']}")
for r in result["renditions"]:
    print(f"  {r['name']} {r['resolution']}: {r['segments']} segments, {r['bytes']} bytes, {r['measured_kbps']} kbps")

# Thumbnails come back base64-encoded. Write them to the current directory.
for i, thumb in enumerate(result["thumbnails"], start=1):
    path = pathlib.Path(f"{result['id']}_{i}.jpg")
    path.write_bytes(base64.b64decode(thumb["jpeg_base64"]))
    print(f"  wrote {path} (frame at {thumb['at_seconds']} s)")

# Every video in videos.json, in parallel. One URL in the file is broken on
# purpose, so expect it under "failed".
run = render.workflows.run_task(f"{SLUG}/transcode_batch", [videos])
if run.status != "completed":
    raise SystemExit(f"Run {run.id} ended as {run.status}: {run.error}")

batch = run.results[0]
print(f"\nTranscoded {batch['transcoded']} of {batch['total']}")
for r in batch["results"]:
    sizes = ", ".join(f"{x['name']} {x['bytes']} B" for x in r["renditions"])
    print(f"  {r['id']}: {sizes} ({r['seconds']})")
for failure in batch["failed"]:
    print(f"  failed: {failure}")
