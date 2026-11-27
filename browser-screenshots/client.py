"""
Trigger the page checks from another service or script.

Requires RENDER_API_KEY. Set WORKFLOW_SLUG to your workflow service's slug
(it defaults to browser-screenshots).
"""

import base64
import json
import os
import pathlib
from urllib.parse import urlsplit

from render import Render

SLUG = os.getenv("WORKFLOW_SLUG", "browser-screenshots")
urls = json.loads((pathlib.Path(__file__).parent / "urls.json").read_text())

# Render() reads RENDER_API_KEY from the environment.
render = Render()

# Check every URL in urls.json. Task input is a list of the task's arguments,
# so the URL list is wrapped in another list. run_task waits for the run to
# finish and returns its details.
run = render.workflows.run_task(f"{SLUG}/check_pages", [urls])
if run.status != "completed":
    raise SystemExit(f"Run {run.id} ended as {run.status}: {run.error}")

# results is a list holding the task's return value.
batch = run.results[0]
if "error" in batch:
    raise SystemExit(batch["error"])
print(f"Checked {batch['checked']} of {batch['total']} pages")

out = pathlib.Path("thumbnails")
out.mkdir(exist_ok=True)
for page in batch["pages"]:
    verdict = "ok" if page["ok"] else ", ".join(page["problems"])
    print(f"  {page['status']} {page['load_ms']:>5} ms  {page['url']}  ({verdict})")
    for message in page["console_errors"][:3]:
        print(f"      console: {message[:120]}")
    # Thumbnails come back base64-encoded. Write them to thumbnails/.
    parts = urlsplit(page["url"])
    name = (parts.netloc + parts.path).strip("/").replace("/", "_") or "page"
    (out / f"{name}.jpg").write_bytes(base64.b64decode(page["thumbnail_jpeg_base64"]))

for failure in batch["failed"]:
    print(f"  failed: {failure['url']}: {failure['error']}")
print(f"Wrote {len(batch['pages'])} thumbnails to {out}/")
