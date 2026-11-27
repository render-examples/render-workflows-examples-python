# Voice pipeline on Render Workflows

Answer a recorded voice message with speech-to-text, an LLM reply and text-to-speech, each running as its own Render Workflows task.

## What it shows

- A three-step voice pipeline where each step (STT, LLM, TTS) is its own task run
- An orchestrator task that chains the steps with `ctx.run` and returns the transcript, reply text and reply audio
- Retrying each step on its own, and not the orchestrator, so a failed text-to-speech call doesn't rerun transcription
- Moving audio between tasks as base64 inside the task arguments, under Render's 4 MB argument cap
- Returning errors a retry can't fix (empty audio, unknown model, missing API key) as results instead of raising
- Triggering the pipeline from another service with the Render SDK client

This is a batch pipeline: one recorded message in, one spoken reply out. Each step starts a new task run, so it suits voicemail, async voice notes and call follow-ups, not live back-and-forth conversation.

## Task tree

```
handle_voice_message (orchestrator, no retries)
  ├── transcribe   speech-to-text   OPENAI_STT_MODEL  (retries twice)
  ├── reply        support answer   OPENAI_MODEL      (retries twice)
  └── synthesize   text-to-speech   OPENAI_TTS_MODEL  (retries twice)
```

The support agent answers from the same orders and accounts as the [openai-agents-sdk](../openai-agents-sdk/) example. `sample-call.mp3` is a caller asking about ticket TCK-1001 from `tickets.json`. It was generated with the `synthesize` task in the `cedar` voice from the text in `sample-call.txt`.

## Prerequisites

- Python 3.11+
- The [Render CLI](https://render.com/docs/cli)
- An [OpenAI API key](https://platform.openai.com/api-keys)

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set OPENAI_API_KEY. The model variables have working defaults.
```

Start the local task server. It loads `.env` when it starts:

```bash
render workflows dev -- python main.py
```

In another terminal, run one step on its own. Task input is a JSON array of the task's arguments:

```bash
render workflows tasks runs start reply --local \
  --input='["Hi, I was charged two times for order ORD-2001 yesterday. Can you fix this?"]'
```

Send the sample voice message through the whole pipeline. `RENDER_USE_LOCAL_DEV=true` points the client at the local task server:

```bash
RENDER_USE_LOCAL_DEV=true python client.py
```

The client prints the transcript and the reply, and saves the spoken reply to `reply.mp3`. Pass a path to send your own recording: `python client.py my-message.wav`.

### Models

| Variable | Default | Step |
|---|---|---|
| `OPENAI_STT_MODEL` | `gpt-transcribe` | `transcribe`, with the [Transcriptions API](https://developers.openai.com/api/docs/guides/speech-to-text) |
| `OPENAI_MODEL` | `gpt-6.2-sol` | `reply`, with the Responses API |
| `OPENAI_TTS_MODEL` | `gpt-audio-1.5` | `synthesize`, with [audio output from Chat Completions](https://developers.openai.com/api/docs/guides/audio-chat-completions) |
| `OPENAI_TTS_VOICE` | `marin` | `synthesize` |

`synthesize` doesn't use OpenAI's `/v1/audio/speech` endpoint because every model it serves is deprecated and shuts down on January 6, 2027 (see OpenAI's [deprecations](https://developers.openai.com/api/docs/deprecations)). `gpt-audio-1.5` is a conversational model, so the task hands it the text as a script to read and tells it not to reply. The task returns `spoken_text`, the model's transcript of the audio, so you can check it read the script word for word.

## Deploy to Render

1. Push this folder to a Git repository Render can access.
2. In the Render Dashboard, click **New > Workflow** and connect the repository.
3. Set these fields:

   | Field | Value |
   |---|---|
   | Language | Python |
   | Root Directory | `voice-pipeline` (the folder that holds `main.py`). Leave it empty if this folder is the repository root. |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `python main.py` |
   | Region | Any |
   | Environment variables | `OPENAI_API_KEY`, and optionally `OPENAI_MODEL`, `OPENAI_STT_MODEL`, `OPENAI_TTS_MODEL` and `OPENAI_TTS_VOICE` |

4. Create the workflow. Once the first version is ready, run `reply` from the Dashboard with `["I was charged twice for order ORD-2001."]` as the input.

## Trigger from another service

`client.py` sends a recording to `handle_voice_message` with the Render SDK and saves the spoken reply:

```bash
export RENDER_API_KEY=your-render-api-key
export WORKFLOW_SLUG=your-workflow-slug   # defaults to voice-pipeline
python client.py
```

The core of it:

```python
from render import Render

render = Render()  # reads RENDER_API_KEY

run = render.workflows.run_task(
    f"{SLUG}/handle_voice_message", [base64.b64encode(audio).decode(), "mp3"]
)
if run.status == "completed":
    result = run.results[0]  # results is a list holding the return value
    print(result["transcript"], result["reply"])
    pathlib.Path("reply.mp3").write_bytes(base64.b64decode(result["audio_base64"]))
```

`run_task` waits for the run to finish. To start a run and check on it later, use `start_task` instead.

## Limits and gotchas

- **Audio rides in the task arguments, so it's capped.** The combined arguments of a task run can't exceed 4 MB, and base64 makes audio a third bigger. `transcribe` and `client.py` reject audio over 2.5 MB, about two and a half minutes of 128 kbps MP3. For longer recordings, upload the file to object storage and pass its URL or key instead of the bytes. See [limits](https://render.com/docs/workflows-limits).
- **Render keeps the audio for 30 days.** Render retains each run's arguments and return value for 30 days, and here those include the caller's recording and the spoken reply. If recordings are sensitive, pass a storage reference instead. See [state retention](https://render.com/docs/workflows-limits#state-retention).
- **A retry reruns one step, and bills it again.** Each step retries twice on its own, so a failed `synthesize` reruns only text-to-speech. `handle_voice_message` has retries off: a retry would rerun every step, including the ones that already succeeded. See [retries](https://render.com/docs/workflows-defining-tasks#retry-settings).
- **Timeouts apply per attempt.** `timeout_seconds=120` bounds each attempt of a step, and a retry gets a fresh 120 seconds. See [timeouts](https://render.com/docs/workflows-defining-tasks#timeout).
- **Errors a retry can't fix are returned, not raised.** Empty or unreadable audio, an unsupported format, a missing `OPENAI_API_KEY`, or an OpenAI 400/401/403/404/422 comes back as `{"error": ...}`. `handle_voice_message` adds the `stage` that failed and keeps what earlier steps produced. A connection error or a 429 raises, so Render retries the step.
- **Chained runs are rate limited.** Each voice message starts three chained runs. Render allows 1,000 chained runs per minute per workspace, and `ctx.run` fails rather than queues over that. See [rate limits](https://render.com/docs/workflows-limits#api-rate-limits).

## Links

- [Render Workflows](https://render.com/docs/workflows)
- [Defining tasks](https://render.com/docs/workflows-defining-tasks)
- [Triggering runs](https://render.com/docs/workflows-running)
- [Workflows SDK for Python](https://render.com/docs/workflows-sdk-python)
- [OpenAI speech to text](https://developers.openai.com/api/docs/guides/speech-to-text)
- [OpenAI audio in Chat Completions](https://developers.openai.com/api/docs/guides/audio-chat-completions)
