"""
Moderate the sample posts from another service or script and print the
review queue.

Requires RENDER_API_KEY. Set WORKFLOW_SLUG to your workflow service's slug
(it defaults to content-moderation).
"""

import json
import os
import pathlib

from render import Render

SLUG = os.getenv("WORKFLOW_SLUG", "content-moderation")
posts = json.loads((pathlib.Path(__file__).parent / "posts.json").read_text())

# Render() reads RENDER_API_KEY from the environment.
render = Render()

# Task input is a list of the task's arguments. run_task waits for the run to
# finish and returns its details.
run = render.workflows.run_task(f"{SLUG}/moderate_posts", [posts])
if run.status != "completed":
    raise SystemExit(f"Run {run.id} ended as {run.status}: {run.error}")

# results is a list holding the task's return value.
report = run.results[0]
if "error" in report:
    raise SystemExit(report["error"])

counts = report["counts"]
print(f"{len(posts)} posts, moderated with {report['model']}")
print(", ".join(f"{d}: {counts[d]}" for d in ("approve", "flag", "reject", "error")))

print("\nReview queue:")
for item in report["flagged"]:
    top = item["reasons"][0]
    print(f"  {item['post_id']} ({top['category']} {top['score']}): {item['text']}")
    print(f"    note: {item.get('review_note') or item.get('note_error')}")

print("\nRejected:")
for item in report["rejected"]:
    top = item["reasons"][0]
    print(f"  {item['post_id']} ({top['category']} {top['score']})")

if report["errors"]:
    print("\nNot moderated (hold these for review):")
    for item in report["errors"]:
        print(f"  {item['post_id']}: {item['error']}")
