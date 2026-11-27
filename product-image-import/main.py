"""
Product image import on Render Workflows

Imports product images from a list of URLs. For each image, one task run
downloads it, makes resized JPEG, WebP and AVIF variants with Pillow, and
computes a blurhash placeholder. A second task run asks an OpenAI vision model
for alt text, with structured output. An orchestrator fans out over the list
and returns a manifest. A bad URL or a file that isn't an image comes back as a
failed row, not a failed batch.

To stay self-contained, this example needs no bucket. Variants are built in a
temporary directory and discarded; the manifest keeps their sizes, a blurhash
and one tiny base64 thumbnail per image. A real app would upload the variants
to object storage such as S3 in store_variants().
"""

import asyncio
import base64
import io
import logging
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import blurhash
import openai
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from render import Retry, TaskContext, Workflows

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Vision model for alt text. Any OpenAI model that accepts image input works.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6.2-sol")
# Refuse downloads bigger than this, and images with more pixels than this.
MAX_INPUT_MB = int(os.getenv("MAX_INPUT_MB", "25"))
MAX_MEGAPIXELS = int(os.getenv("MAX_MEGAPIXELS", "50"))
# How many images import_images works on at once.
MAX_CONCURRENT_IMAGES = int(os.getenv("MAX_CONCURRENT_IMAGES", "8"))

# Variant widths in pixels. Widths larger than the source are skipped.
WIDTHS = [1200, 600, 300]
# Encoder settings per format. Quality 50 for AVIF looks about like 80 for JPEG.
FORMATS = {
    "jpeg": {"format": "JPEG", "quality": 80, "optimize": True, "progressive": True},
    "webp": {"format": "WEBP", "quality": 80, "method": 4},
    "avif": {"format": "AVIF", "quality": 50, "speed": 6},
}
# Returned in the manifest as base64, so keep it tiny (about 2-5 KB).
THUMBNAIL_WIDTH = 160
# Sent to the vision model. 512 px is the size OpenAI's "low" detail uses.
PREVIEW_SIZE = 512

# Some hosts (Wikimedia among them) reject requests without a descriptive
# User-Agent. Python's default one gets HTTP 403 there.
USER_AGENT = "render-workflows-product-image-import/1.0 (+https://github.com/render-examples)"

ALT_TEXT_INSTRUCTIONS = """\
You write alt text for product photos in an online store.
Describe the product itself: what it is, its color, material and notable parts.
Don't describe the background or the photo ("image of", "photo of").
Keep alt_text under 125 characters. Add 3 to 6 short lowercase search tags.\
"""

app = Workflows()


class PermanentError(Exception):
    """A failure a retry can't fix: bad URL, not an image, file too big."""


class AltText(BaseModel):
    alt_text: str = Field(description="One sentence, under 125 characters")
    tags: list[str] = Field(description="3 to 6 short lowercase search tags")


# ============================================================================
# Helpers
# ============================================================================


def available_cpus() -> int:
    """CPUs this run may use.

    On Render, os.cpu_count() reports the host's CPUs, not the compute plan's.
    The plan's limit is in the cgroup's cpu.max ("100000 100000" is 1 CPU).
    The AVIF encoder sizes its thread pool from the CPU count, so pass the
    real one.
    """
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            return max(1, int(quota) // int(period))
    except (OSError, ValueError):
        pass
    return os.cpu_count() or 1


def download(url: str, dest: Path) -> int:
    """Stream url to dest. Returns the size in bytes."""
    limit = MAX_INPUT_MB * 1024 * 1024
    # urllib also opens file:// and ftp:// URLs. Only fetch from the web.
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise PermanentError("url must start with http:// or https://")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=60) as response, dest.open("wb") as out:
            content_type = response.headers.get_content_type()
            if content_type.startswith("text/"):
                raise PermanentError(f"Not an image: the URL returned {content_type}")
            length = int(response.headers.get("Content-Length") or 0)
            if length > limit:
                raise PermanentError(f"Image is {length} bytes, over MAX_INPUT_MB={MAX_INPUT_MB}")
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise PermanentError(f"Image is over MAX_INPUT_MB={MAX_INPUT_MB}")
                out.write(chunk)
            return size
    except urllib.error.HTTPError as e:
        # 4xx (other than timeouts and rate limits) won't change on a retry.
        if 400 <= e.code < 500 and e.code not in (408, 429):
            raise PermanentError(f"Download failed: HTTP {e.code}") from e
        raise
    except ValueError as e:  # malformed URL
        raise PermanentError(f"Bad URL: {e}") from e


def open_image(path: Path) -> tuple[Image.Image, dict]:
    """Open and decode the image, upright and in RGB or RGBA.

    Returns the image and a description of the source file.
    """
    try:
        img = Image.open(path)
        source = {"format": img.format, "width": img.width, "height": img.height}
        # The size is in the header, so check it before decoding any pixels.
        if img.width * img.height > MAX_MEGAPIXELS * 1_000_000:
            raise PermanentError(
                f"Image is {img.width}x{img.height}, over MAX_MEGAPIXELS={MAX_MEGAPIXELS}"
            )
        # Phones store photos sideways plus an EXIF rotation tag. Apply it.
        img = ImageOps.exif_transpose(img)
        source.update(width=img.width, height=img.height)
        has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
        # Keep an RGB color profile so browsers show the colors as shot. A
        # CMYK or grayscale profile wouldn't match the converted pixels.
        icc = img.info.get("icc_profile") if img.mode in ("RGB", "RGBA") else None
        img = img.convert("RGBA" if has_alpha else "RGB")
        img.info = {"icc_profile": icc} if icc else {}
    except UnidentifiedImageError as e:
        raise PermanentError("Not an image Pillow can read") from e
    except Image.DecompressionBombError as e:
        raise PermanentError(str(e)) from e
    except OSError as e:  # truncated or corrupt file
        raise PermanentError(f"Can't decode image: {e}") from e
    return img, {**source, "has_alpha": has_alpha}


def flatten(img: Image.Image) -> Image.Image:
    """Put a transparent image on white, for formats without alpha."""
    if img.mode != "RGBA":
        return img
    background = Image.new("RGB", img.size, "white")
    background.paste(img, mask=img.getchannel("A"))
    return background


def resize_to_width(img: Image.Image, width: int) -> Image.Image:
    height = max(1, round(img.height * width / img.width))
    return img.resize((width, height), Image.Resampling.LANCZOS)


def make_variants(img: Image.Image, out_dir: Path, image_id: str) -> list[dict]:
    """Write every width in every format to out_dir. Returns one entry per file."""
    threads = available_cpus()
    widths = [w for w in WIDTHS if w <= img.width] or [img.width]
    variants = []
    for width in widths:
        resized = resize_to_width(img, width)
        for ext, options in FORMATS.items():
            path = out_dir / f"{width}.{ext}"
            if icc := img.info.get("icc_profile"):
                options = {**options, "icc_profile": icc}
            if ext == "jpeg":
                flatten(resized).save(path, **options)
            elif ext == "avif":
                resized.save(path, **options, max_threads=threads)
            else:
                resized.save(path, **options)
            variants.append(
                {
                    "format": ext,
                    "width": resized.width,
                    "height": resized.height,
                    "bytes": path.stat().st_size,
                    "path": path,
                    # Where a real app would store it. Derived from the image
                    # id, so a retry overwrites instead of adding a copy.
                    "key": f"products/{image_id}/{width}.{ext}",
                }
            )
    return variants


def make_blurhash(img: Image.Image) -> str:
    """A blurhash string: a ~30 character placeholder the frontend can paint
    while the real image loads. Computed from a tiny copy, which is plenty."""
    small = flatten(img).copy()
    small.thumbnail((32, 32))
    pixels = list(small.getdata())
    rows = [pixels[y * small.width : (y + 1) * small.width] for y in range(small.height)]
    x, y = (4, 3) if img.width >= img.height else (3, 4)
    return blurhash.encode(rows, components_x=x, components_y=y)


def encode_base64(img: Image.Image, max_size: int, fmt: str, **options) -> str:
    copy = img.copy()
    copy.thumbnail((max_size, max_size))
    buf = io.BytesIO()
    copy.save(buf, fmt, **options)
    return base64.b64encode(buf.getvalue()).decode()


# ============================================================================
# Outputs. This is the one place that decides what leaves the run.
# ============================================================================


def store_variants(variants: list[dict]) -> list[dict]:
    """Decide what to keep from the variant files before the run's disk goes away.

    This example keeps only each file's format, size and intended key. The
    files themselves are deleted with the work directory.

    In a real app, upload them here instead, for example with boto3:
        for v in variants:
            s3.upload_file(str(v["path"]), BUCKET, v["key"],
                           ExtraArgs={"ContentType": f"image/{v['format']}"})
    and return each key or URL. The keys come from the image id, so a retry
    overwrites its earlier upload.
    """
    return [{k: v for k, v in variant.items() if k != "path"} for variant in variants]


# ============================================================================
# Alt text
# ============================================================================


# HTTP statuses a retry can't fix: bad request, bad key, no access, no such
# model, invalid input. 408, 429 and 5xx raise so Render retries.
PERMANENT_OPENAI_STATUSES = {400, 401, 403, 404, 422}


async def ask_for_alt_text(name: str | None, preview_jpeg_base64: str) -> tuple[AltText, dict]:
    """Ask the vision model for alt text. Returns (parsed output, usage)."""
    # Render retries the task, so turn off the client's own retries to keep
    # one retry policy in charge.
    client = openai.AsyncOpenAI(max_retries=0, timeout=60)
    prompt = f"Product name: {name}" if name else "Product name: unknown"
    response = await client.responses.parse(
        model=OPENAI_MODEL,
        instructions=ALT_TEXT_INSTRUCTIONS,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{preview_jpeg_base64}",
                        "detail": "low",
                    },
                ],
            }
        ],
        text_format=AltText,
    )
    if response.output_parsed is None:
        # A refusal or a cut-off answer. Asking again rarely changes it.
        raise PermanentError(f"No alt text in the response (status {response.status})")
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return response.output_parsed, usage


# ============================================================================
# Tasks
# ============================================================================


# process_image downloads, so it retries: a network error, an HTTP 5xx or a
# 429 gets two more attempts, 5 and 10 seconds apart. A retry redoes the whole
# image. That's safe because the task writes nothing outside its own disk.
@app.task(
    timeout_seconds=5 * 60,
    retry=Retry(max_retries=2, wait_duration_ms=5000, backoff_scaling=2),
)
def process_image(ctx: TaskContext, image: dict) -> dict:
    """Download one image, make its variants and blurhash, and return small outputs."""
    image_id = str(image.get("id") or "").strip()
    url = str(image.get("url") or "").strip()
    if not image_id or not url:
        return {"id": image_id or None, "error": "Each image needs an id and a url"}
    # The id becomes part of file names and storage keys.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", image_id):
        return {"id": image_id, "error": "id may only contain letters, digits, '.', '_' and '-'"}

    logger.info(f"[PROCESS] {image_id}: {url}")
    timings = {}
    work = Path(tempfile.mkdtemp(prefix=f"{image_id}-"))
    try:
        started = time.monotonic()
        size = download(url, work / "source")
        timings["download"] = round(time.monotonic() - started, 2)

        started = time.monotonic()
        img, source = open_image(work / "source")
        out = work / "variants"
        out.mkdir()
        variants = store_variants(make_variants(img, out, image_id))
        placeholder = make_blurhash(img)
        thumbnail = encode_base64(img, THUMBNAIL_WIDTH, "WEBP", quality=70)
        preview = encode_base64(flatten(img), PREVIEW_SIZE, "JPEG", quality=80)
        timings["variants"] = round(time.monotonic() - started, 2)
    except PermanentError as e:
        logger.error(f"[PROCESS] {image_id}: {e} (not retrying)")
        return {"id": image_id, "error": str(e)}
    finally:
        shutil.rmtree(work, ignore_errors=True)

    logger.info(
        f"[PROCESS] {image_id}: {source['width']}x{source['height']} {source['format']}, "
        f"{len(variants)} variants, {sum(v['bytes'] for v in variants)} bytes, {timings}"
    )
    return {
        "id": image_id,
        "source": {**source, "bytes": size},
        "blurhash": placeholder,
        "variants": variants,
        "thumbnail_webp_base64": thumbnail,
        # For generate_alt_text. import_images leaves it out of the manifest.
        "preview_jpeg_base64": preview,
        "seconds": timings,
    }


# generate_alt_text calls the OpenAI API, so it retries: a 429, a 5xx or a
# connection error gets three more attempts, 2, 4 and 8 seconds apart.
# Calling the model again is safe because nothing is written.
@app.task(
    timeout_seconds=2 * 60,
    retry=Retry(max_retries=3, wait_duration_ms=2000, backoff_scaling=2),
)
async def generate_alt_text(
    ctx: TaskContext, image_id: str, name: str | None, preview_jpeg_base64: str
) -> dict:
    """Ask a vision model for alt text and tags for one product image."""
    if not os.getenv("OPENAI_API_KEY"):
        # A setup problem a retry can't fix, so return it instead of raising.
        return {"id": image_id, "error": "OPENAI_API_KEY is not set"}

    started = time.monotonic()
    try:
        answer, usage = await ask_for_alt_text(name, preview_jpeg_base64)
    except PermanentError as e:
        return {"id": image_id, "error": str(e)}
    except openai.APIStatusError as e:
        if e.status_code in PERMANENT_OPENAI_STATUSES:
            body = e.body if isinstance(e.body, dict) else {}
            message = f"OpenAI error {e.status_code}: {body.get('message') or e.message}"
            logger.error(f"[ALT TEXT] {image_id}: {message} (not retrying)")
            return {"id": image_id, "error": message}
        raise

    seconds = round(time.monotonic() - started, 2)
    logger.info(f"[ALT TEXT] {image_id}: {answer.alt_text!r} in {seconds} s")
    return {"id": image_id, "model": OPENAI_MODEL, **answer.model_dump(), **usage, "seconds": seconds}


# import_images only fans out and waits, so it runs on the default flex plan
# and never retries: a retry would redo every image, including the ones that
# finished.
@app.task(
    timeout_seconds=60 * 60,
    retry=Retry(max_retries=0, wait_duration_ms=1000, backoff_scaling=1.5),
)
async def import_images(ctx: TaskContext, images: list[dict]) -> dict:
    """Import many product images in parallel and return a manifest."""
    logger.info(f"[IMPORT] {len(images)} images, up to {MAX_CONCURRENT_IMAGES} at a time")
    limit = asyncio.Semaphore(MAX_CONCURRENT_IMAGES)

    async def import_one(image: dict) -> dict:
        row = {"id": image.get("id"), "name": image.get("name"), "url": image.get("url")}
        async with limit:
            # Each ctx.run is its own task run with its own retries.
            try:
                processed = await ctx.run(process_image, image)
            except Exception as e:  # the run raised on every attempt
                return {**row, "status": "failed", "error": str(e)}
            if "error" in processed:
                return {**row, "status": "failed", "error": processed["error"]}

            preview = processed.pop("preview_jpeg_base64")
            row = {**row, "status": "imported", **processed}
            try:
                alt = await ctx.run(generate_alt_text, row["id"], row["name"], preview)
            except Exception as e:
                alt = {"error": str(e)}

        # The image itself is fine without alt text, so the row stays
        # imported. alt_text_error marks it for a person to review.
        if "error" in alt:
            return {**row, "alt_text": None, "alt_text_error": alt["error"]}
        return {**row, "alt_text": alt["alt_text"], "tags": alt["tags"]}

    rows = await asyncio.gather(*(import_one(image) for image in images))

    imported = [r for r in rows if r["status"] == "imported"]
    summary = {
        "total": len(rows),
        "imported": len(imported),
        "failed": len(rows) - len(imported),
        "missing_alt_text": sum(1 for r in imported if r["alt_text"] is None),
    }
    logger.info(f"[IMPORT] {summary}")
    return {**summary, "images": rows}


if __name__ == "__main__":
    app.start()
