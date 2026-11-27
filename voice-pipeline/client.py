"""
Send a recorded voice message to the pipeline and save the spoken reply.

Requires RENDER_API_KEY. Set WORKFLOW_SLUG to your workflow service's slug
(it defaults to voice-pipeline).

    python client.py                     # sends sample-call.mp3, writes reply.mp3
    python client.py my-message.wav      # any format the transcription model reads
"""

import base64
import os
import pathlib
import sys

from render import Render

SLUG = os.getenv("WORKFLOW_SLUG", "voice-pipeline")

# Audio travels base64-encoded inside the task arguments, which Render caps
# at 4 MB in total. main.py rejects anything bigger than this, so check first.
MAX_AUDIO_BYTES = 2_500_000

here = pathlib.Path(__file__).parent
audio_path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else here / "sample-call.mp3"
audio = audio_path.read_bytes()
if len(audio) > MAX_AUDIO_BYTES:
    raise SystemExit(f"{audio_path} is {len(audio)} bytes; the limit is {MAX_AUDIO_BYTES}")

# Render() reads RENDER_API_KEY from the environment.
render = Render()

# Task input is a list of the task's arguments. run_task waits for the run to
# finish and returns its details.
run = render.workflows.run_task(
    f"{SLUG}/handle_voice_message",
    [base64.b64encode(audio).decode(), audio_path.suffix.lstrip(".").lower()],
)
if run.status != "completed":
    raise SystemExit(f"Run {run.id} ended as {run.status}: {run.error}")

# results is a list holding the task's return value.
result = run.results[0]
if "error" in result:
    raise SystemExit(f"Failed at {result['stage']}: {result['error']}")

print(f"Caller: {result['transcript']}")
print(f"Agent:  {result['reply']}")

out = here / f"reply.{result['audio_format']}"
out.write_bytes(base64.b64decode(result["audio_base64"]))
print(f"Saved the spoken reply to {out}")
