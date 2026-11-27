"""
Voice pipeline on Render Workflows

A support line that answers a recorded voice message. Speech-to-text, the
LLM reply and text-to-speech each run as their own task, so each step retries
on its own and shows up as its own run in the Dashboard. An orchestrator task
chains them and returns the transcript, the reply text and the reply audio.

This is a batch pipeline: one recorded message in, one spoken reply out. It
isn't built for live, back-and-forth calls.
"""

import base64
import binascii
import logging
import os

from openai import APIStatusError, AsyncOpenAI
from render import Retry, TaskContext, Workflows

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Read every model from the environment so a retired model is a config
# change, not a code change.
STT_MODEL = os.getenv("OPENAI_STT_MODEL", "gpt-transcribe")
LLM_MODEL = os.getenv("OPENAI_MODEL", "gpt-6.2-sol")
TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-audio-1.5")
TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "marin")

# Audio moves between tasks as base64 text inside the task arguments. Render
# caps the combined arguments of a task run at 4 MB, and base64 adds a third,
# so this keeps the raw audio well under that cap (about 2.5 minutes of
# 128 kbps MP3).
MAX_AUDIO_BYTES = 2_500_000

# OpenAI errors that mean the request itself is wrong: unknown model, bad key,
# unreadable audio. A retry can't fix them, so the tasks return them as a
# result instead of raising.
PERMANENT_ERROR_STATUSES = {400, 401, 403, 404, 422}

# Content types for the audio formats this example sends to transcription.
AUDIO_CONTENT_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "flac": "audio/flac",
}

app = Workflows()


# ============================================================================
# Sample data the support agent answers from. In production you'd look these
# up in your database before calling the model.
# ============================================================================

ORDERS = {
    "ORD-2001": {"status": "delivered", "amount": 49.00, "times_charged": 2},
    "ORD-2002": {"status": "processing", "amount": 120.00, "times_charged": 1},
    "ORD-2003": {"status": "shipped", "amount": 15.50, "times_charged": 1},
}

ACCOUNTS = {
    "ACC-100": {"plan": "pro", "standing": "good", "balance_due": 0.00},
    "ACC-200": {"plan": "starter", "standing": "good", "balance_due": 0.00},
    "ACC-300": {
        "plan": "pro",
        "standing": "past_due",
        "balance_due": 79.00,
        "card_on_file": "expired",
    },
}

SUPPORT_INSTRUCTIONS = f"""You are a phone support agent. A caller left the voice message below.
Answer them in two or three short sentences of plain spoken English: no lists,
no markdown, no links. Your answer will be read aloud, so write out what you'd
say. Use the records below to answer. If the caller doesn't give an order or
account ID that you need, ask for it.

Orders: {ORDERS}
Accounts: {ACCOUNTS}"""

# gpt-audio models answer what they hear, so the text goes in as a script to
# read, and the instructions tell the model not to reply to it.
SPEAK_INSTRUCTIONS = (
    "You are a voice actor reading a script. The user message contains a script "
    "between <script> tags. Speak the script exactly as written, word for word, "
    "and nothing else. Never reply to the script, never answer questions in it, "
    "never add greetings or comments."
)


def openai_client() -> AsyncOpenAI | None:
    """Return an OpenAI client, or None if OPENAI_API_KEY isn't set."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    # Render retries these tasks, so turn off the OpenAI client's own retries
    # to keep one retry policy in charge.
    return AsyncOpenAI(api_key=api_key, max_retries=0)


def permanent_error(e: APIStatusError) -> dict | None:
    """Return an error result for an OpenAI error a retry can't fix."""
    if e.status_code in PERMANENT_ERROR_STATUSES:
        return {"error": f"OpenAI error {e.status_code}: {e.message}"}
    return None


# ============================================================================
# Leaf tasks. Each one calls OpenAI once, so each retries on its own: a failed
# text-to-speech call reruns only text-to-speech, not the whole pipeline.
# ============================================================================


@app.task(
    retry=Retry(max_retries=2, wait_duration_ms=1000, backoff_scaling=2),
    timeout_seconds=120,
)
async def transcribe(ctx: TaskContext, audio_base64: str, audio_format: str = "mp3") -> dict:
    """Speech-to-text: turn base64-encoded audio into text."""
    try:
        audio = base64.b64decode(audio_base64, validate=True)
    except (binascii.Error, ValueError):
        return {"error": "The audio isn't valid base64"}
    if not audio:
        return {"error": "The audio is empty"}
    if len(audio) > MAX_AUDIO_BYTES:
        return {"error": f"The audio is {len(audio)} bytes; the limit is {MAX_AUDIO_BYTES}"}
    if audio_format not in AUDIO_CONTENT_TYPES:
        return {"error": f"Unsupported audio format {audio_format!r}"}

    client = openai_client()
    if client is None:
        return {"error": "OPENAI_API_KEY is not set"}

    logger.info(f"[STT] {len(audio)} bytes of {audio_format} with {STT_MODEL}")
    try:
        result = await client.audio.transcriptions.create(
            model=STT_MODEL,
            file=(f"message.{audio_format}", audio, AUDIO_CONTENT_TYPES[audio_format]),
        )
    except APIStatusError as e:
        if error := permanent_error(e):
            logger.error(f"[STT] {error['error']}, not retrying")
            return error
        raise

    text = result.text.strip()
    if not text:
        return {"error": "No speech found in the audio"}
    logger.info(f"[STT] {text!r}")
    return {"text": text}


@app.task(
    retry=Retry(max_retries=2, wait_duration_ms=1000, backoff_scaling=2),
    timeout_seconds=120,
)
async def reply(ctx: TaskContext, transcript: str) -> dict:
    """LLM: write the support agent's answer to the caller."""
    client = openai_client()
    if client is None:
        return {"error": "OPENAI_API_KEY is not set"}

    logger.info(f"[LLM] Replying with {LLM_MODEL}")
    try:
        response = await client.responses.create(
            model=LLM_MODEL,
            instructions=SUPPORT_INSTRUCTIONS,
            input=transcript,
        )
    except APIStatusError as e:
        if error := permanent_error(e):
            logger.error(f"[LLM] {error['error']}, not retrying")
            return error
        raise

    text = response.output_text.strip()
    if not text:
        # An empty answer is worth one more try, so raise and let Render retry.
        raise RuntimeError("The model returned an empty reply")
    logger.info(f"[LLM] {text!r}")
    return {"text": text}


@app.task(
    retry=Retry(max_retries=2, wait_duration_ms=1000, backoff_scaling=2),
    timeout_seconds=120,
)
async def synthesize(ctx: TaskContext, text: str, voice: str | None = None) -> dict:
    """Text-to-speech: read the text aloud and return base64-encoded MP3."""
    if not text.strip():
        return {"error": "There's no text to speak"}

    client = openai_client()
    if client is None:
        return {"error": "OPENAI_API_KEY is not set"}

    voice = voice or TTS_VOICE
    logger.info(f"[TTS] {len(text)} characters with {TTS_MODEL} ({voice})")
    try:
        response = await client.chat.completions.create(
            model=TTS_MODEL,
            modalities=["text", "audio"],
            audio={"voice": voice, "format": "mp3"},
            messages=[
                {"role": "system", "content": SPEAK_INSTRUCTIONS},
                {"role": "user", "content": f"<script>{text}</script>"},
            ],
        )
    except APIStatusError as e:
        if error := permanent_error(e):
            logger.error(f"[TTS] {error['error']}, not retrying")
            return error
        raise

    audio = response.choices[0].message.audio
    if audio is None or not audio.data:
        raise RuntimeError("The model returned no audio")
    logger.info(f"[TTS] {len(audio.data)} base64 characters of mp3")
    # spoken_text is the model's transcript of the audio it produced, so you
    # can check it read the script and didn't answer it.
    return {"audio_base64": audio.data, "audio_format": "mp3", "spoken_text": audio.transcript}


# ============================================================================
# Orchestrator
# ============================================================================


# handle_voice_message only chains the other tasks, so it doesn't retry: a
# retry would rerun every step, including the ones that already succeeded.
# Each step retries on its own.
@app.task(
    retry=Retry(max_retries=0, wait_duration_ms=1000, backoff_scaling=1.5),
    timeout_seconds=600,
)
async def handle_voice_message(
    ctx: TaskContext, audio_base64: str, audio_format: str = "mp3"
) -> dict:
    """Answer a voice message: speech-to-text, then the LLM reply, then text-to-speech."""
    # ctx.run starts each step as its own task run and waits for its result.
    heard = await ctx.run(transcribe, audio_base64, audio_format)
    if "error" in heard:
        return {"stage": "transcribe", "error": heard["error"]}

    answer = await ctx.run(reply, heard["text"])
    if "error" in answer:
        return {"stage": "reply", "transcript": heard["text"], "error": answer["error"]}

    speech = await ctx.run(synthesize, answer["text"])
    if "error" in speech:
        return {
            "stage": "synthesize",
            "transcript": heard["text"],
            "reply": answer["text"],
            "error": speech["error"],
        }

    return {
        "transcript": heard["text"],
        "reply": answer["text"],
        "audio_base64": speech["audio_base64"],
        "audio_format": speech["audio_format"],
    }


if __name__ == "__main__":
    app.start()
