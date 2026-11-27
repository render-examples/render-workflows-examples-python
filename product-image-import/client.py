"""
Trigger the import from another service or script.

Requires RENDER_API_KEY. Set WORKFLOW_SLUG to your workflow service's slug
(it defaults to product-image-import).
"""

import base64
import json
import os
import pathlib

from render import Render

SLUG = os.getenv("WORKFLOW_SLUG", "product-image-import")
images = json.loads((pathlib.Path(__file__).parent / "images.json").read_text())

# Render() reads RENDER_API_KEY from the environment.
render = Render()

# Task input is a list of the task's arguments; import_images takes one, the
# list of images. run_task waits for the run to finish and returns its details.
# The idempotency key makes a repeated call within 24 hours return the first
# run instead of importing the same list again.
run = render.workflows.run_task(
    f"{SLUG}/import_images", [images], idempotency_key="import-sample-images-v1"
)
if run.status != "completed":
    raise SystemExit(f"Run {run.id} ended as {run.status}: {run.error}")

# results is a list holding the task's return value: the manifest.
manifest = run.results[0]
print(
    f"Imported {manifest['imported']} of {manifest['total']}, "
    f"{manifest['failed']} failed, {manifest['missing_alt_text']} missing alt text"
)
for row in manifest["images"]:
    if row["status"] == "failed":
        print(f"  {row['id']}: failed: {row['error']}")
        continue
    smallest = min((v for v in row["variants"] if v["format"] == "avif"), key=lambda v: v["bytes"])
    print(f"  {row['id']}: {row['source']['width']}x{row['source']['height']} {row['source']['format']}, "
          f"{len(row['variants'])} variants, smallest AVIF {smallest['bytes']} bytes")
    print(f"    blurhash {row['blurhash']}")
    print(f"    alt text {row['alt_text']!r}" if row["alt_text"] else f"    alt text missing: {row['alt_text_error']}")

    # Each row carries a 160 px WebP thumbnail. Write it to the current directory.
    path = pathlib.Path(f"{row['id']}_thumb.webp")
    path.write_bytes(base64.b64decode(row["thumbnail_webp_base64"]))
    print(f"    wrote {path}")
