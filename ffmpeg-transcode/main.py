"""
ffmpeg transcode on Render Workflows

Turns source videos into HLS (three H.264 renditions and a master playlist)
plus JPEG thumbnails. One task run per video, each on a 4 CPU / 16 GB compute
plan, fanned out by an orchestrator on the default flex plan.

ffmpeg and ffprobe come preinstalled on Render's native runtimes, so this is a
plain Python workflow service with no Docker image.

Videos are too big for task arguments, so inputs are URLs. To stay
self-contained, this example returns only small outputs: base64 thumbnails,
ffprobe metadata, and measurements of each HLS rendition. The HLS files
themselves are discarded when the run ends. A real app would upload them to
object storage such as S3 in store_outputs().
"""

import asyncio
import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from render import Retry, TaskContext, Workflows

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Refuse inputs bigger than this. Each task run works on local disk, so the
# source and every output must fit there at once.
MAX_INPUT_MB = int(os.getenv("MAX_INPUT_MB", "1024"))
# Scratch space for one run. Defaults to the system temp directory.
WORK_DIR = os.getenv("WORK_DIR") or None

# The HLS ladder: (name, height, video bitrate kbps). Rungs taller than the
# source are skipped, so a 720p source gets 720p, 480p and 360p.
LADDER = [("720p", 720, 2800), ("480p", 480, 1400), ("360p", 360, 800)]
SEGMENT_SECONDS = 6
THUMBNAIL_COUNT = 4
# Thumbnails come back base64-encoded in the result, so keep them small:
# 320 px wide is about 10-25 KB each.
THUMBNAIL_WIDTH = 320

app = Workflows()


class PermanentError(Exception):
    """A failure a retry can't fix: bad URL, not a video, file too big."""


# ============================================================================
# Helpers
# ============================================================================


def available_cpus() -> int:
    """CPUs this run may use.

    On Render, os.cpu_count() reports the host's CPUs, not the compute plan's.
    The plan's limit is in the cgroup's cpu.max ("400000 100000" is 4 CPUs).
    ffmpeg sizes its thread pools from the CPU count, so pass the real one.
    """
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            return max(1, int(quota) // int(period))
    except (OSError, ValueError):
        pass
    return os.cpu_count() or 1


def run_ffmpeg(args: list[str]) -> str:
    """Run ffmpeg or ffprobe and return stdout.

    Exit code > 0 means ffmpeg rejected the input or the options: permanent.
    A negative code means a signal killed it (for example, out of memory):
    raise a plain error so Render retries the run.
    """
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode < 0:
        raise RuntimeError(f"{args[0]} killed by signal {-proc.returncode}")
    if proc.returncode > 0:
        tail = proc.stderr.strip().splitlines()[-3:]
        raise PermanentError(f"{args[0]} exited {proc.returncode}: {' | '.join(tail)}")
    return proc.stdout


def download(url: str, dest: Path) -> int:
    """Stream url to dest. Returns the size in bytes."""
    limit = MAX_INPUT_MB * 1024 * 1024
    # urllib also opens file:// and ftp:// URLs. Only fetch from the web.
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise PermanentError("url must start with http:// or https://")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "render-workflows-ffmpeg-example"})
        with urllib.request.urlopen(request, timeout=60) as response, dest.open("wb") as out:
            length = int(response.headers.get("Content-Length") or 0)
            if length > limit:
                raise PermanentError(f"Input is {length} bytes, over MAX_INPUT_MB={MAX_INPUT_MB}")
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise PermanentError(f"Input is over MAX_INPUT_MB={MAX_INPUT_MB}")
                out.write(chunk)
            return size
    except urllib.error.HTTPError as e:
        # 4xx (other than timeouts and rate limits) won't change on a retry.
        if 400 <= e.code < 500 and e.code not in (408, 429):
            raise PermanentError(f"Download failed: HTTP {e.code}") from e
        raise
    except ValueError as e:  # malformed URL
        raise PermanentError(f"Bad URL: {e}") from e


def probe(path: Path) -> dict:
    """Read duration, size and the first video and audio streams."""
    out = run_ffmpeg(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    )
    info = json.loads(out)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if video is None:
        raise PermanentError("Input has no video stream")
    return {
        "duration_seconds": round(float(info["format"].get("duration", 0)), 2),
        "width": video["width"],
        "height": video["height"],
        "video_codec": video["codec_name"],
        "audio_codec": audio["codec_name"] if audio else None,
    }


def make_thumbnails(src: Path, out_dir: Path, duration: float) -> list[dict]:
    """Grab THUMBNAIL_COUNT frames spread across the video, THUMBNAIL_WIDTH px wide."""
    thumbs = []
    for i in range(THUMBNAIL_COUNT):
        at = round(duration * (i + 1) / (THUMBNAIL_COUNT + 1), 2)
        name = f"thumb_{i + 1}.jpg"
        # -ss before -i seeks quickly to the nearest keyframe.
        run_ffmpeg(
            ["ffmpeg", "-v", "error", "-y", "-ss", str(at), "-i", str(src),
             "-frames:v", "1", "-vf", f"scale={THUMBNAIL_WIDTH}:-2", "-q:v", "4", str(out_dir / name)]
        )
        thumbs.append({"file": name, "at_seconds": at})
    return thumbs


def make_hls(src: Path, out_dir: Path, meta: dict, threads: int) -> list[dict]:
    """Encode every rendition in one ffmpeg pass and write a master playlist."""
    rungs = [r for r in LADDER if r[1] <= meta["height"]] or [LADDER[-1]]
    has_audio = meta["audio_codec"] is not None

    # Decode once, split the picture, scale each copy to its rung.
    splits = "".join(f"[v{i}]" for i in range(len(rungs)))
    scales = ";".join(f"[v{i}]scale=-2:{h}[v{i}out]" for i, (_, h, _) in enumerate(rungs))
    args = [
        "ffmpeg", "-v", "error", "-y", "-i", str(src), "-threads", str(threads),
        "-filter_complex", f"[0:v]split={len(rungs)}{splits};{scales}",
    ]
    stream_map = []
    for i, (name, _, kbps) in enumerate(rungs):
        args += [
            "-map", f"[v{i}out]",
            f"-c:v:{i}", "libx264", f"-b:v:{i}", f"{kbps}k",
            f"-maxrate:v:{i}", f"{int(kbps * 1.1)}k", f"-bufsize:v:{i}", f"{kbps * 2}k",
        ]
        if has_audio:
            args += ["-map", "0:a:0", f"-c:a:{i}", "aac", f"-b:a:{i}", "128k", f"-ac:a:{i}", "2"]
        stream_map.append(f"v:{i},a:{i},name:{name}" if has_audio else f"v:{i},name:{name}")
    args += [
        "-preset", "veryfast",
        # A keyframe at every segment boundary, so each segment starts cleanly
        # and all renditions switch at the same points.
        "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT_SECONDS})", "-sc_threshold", "0",
        "-f", "hls", "-hls_time", str(SEGMENT_SECONDS), "-hls_playlist_type", "vod",
        "-hls_segment_filename", str(out_dir / "%v" / "segment_%03d.ts"),
        "-master_pl_name", "master.m3u8",
        "-var_stream_map", " ".join(stream_map),
        str(out_dir / "%v" / "index.m3u8"),
    ]
    run_ffmpeg(args)
    return [
        {"name": name, "height": h, "target_video_kbps": kbps, "playlist": f"{name}/index.m3u8"}
        for name, h, kbps in rungs
    ]


# ============================================================================
# Outputs. This is the one place that decides what leaves the run.
# ============================================================================


def measure_rendition(out_dir: Path, rendition: dict) -> dict:
    """Count a rendition's segments, bytes and duration from its playlist."""
    playlist = out_dir / rendition["playlist"]
    duration = sum(
        float(line.split(":", 1)[1].rstrip(","))
        for line in playlist.read_text().splitlines()
        if line.startswith("#EXTINF:")
    )
    segments = sorted(playlist.parent.glob("*.ts"))
    size = sum(p.stat().st_size for p in segments)
    return {
        **rendition,
        "segments": len(segments),
        "duration_seconds": round(duration, 2),
        "bytes": size,
        "measured_kbps": round(size * 8 / duration / 1000) if duration else None,
    }


def store_outputs(out_dir: Path, video_id: str, renditions: list[dict], thumbnails: list[dict]) -> dict:
    """Decide what to keep from out_dir before the run's disk goes away.

    This example keeps only small things: thumbnails as base64, the master
    playlist's text, and measurements of each rendition. Everything else is
    deleted with the work directory.

    In a real app, upload out_dir here instead, for example with boto3:
        for path in out_dir.rglob("*"):
            s3.upload_file(str(path), BUCKET, f"{video_id}/{path.relative_to(out_dir)}")
    and return the object keys. Use the same keys on every attempt so a retry
    overwrites instead of leaving duplicates.
    """
    master = (out_dir / "master.m3u8").read_text()
    # The master playlist lists each rendition's real size after scaling.
    lines = master.splitlines()
    resolutions = {
        lines[i + 1]: line.split("RESOLUTION=")[1].split(",")[0]
        for i, line in enumerate(lines[:-1])
        if line.startswith("#EXT-X-STREAM-INF:") and "RESOLUTION=" in line
    }
    return {
        "master_playlist": master,
        "renditions": [
            {**measure_rendition(out_dir, r), "resolution": resolutions.get(r["playlist"])}
            for r in renditions
        ],
        "thumbnails": [
            {
                "at_seconds": t["at_seconds"],
                "jpeg_base64": base64.b64encode((out_dir / t["file"]).read_bytes()).decode(),
            }
            for t in thumbnails
        ],
        "output_bytes": sum(p.stat().st_size for p in out_dir.rglob("*") if p.is_file()),
    }


# ============================================================================
# Tasks
# ============================================================================


# transcode_video is the heavy task. It runs on 4c-16g: 4 CPUs and 16 GB just
# for this run, billed per second while it runs. Each attempt may take up to
# 3 hours. It retries twice for transient failures (network, out of memory).
# A retry redoes the whole video. That's safe here because the task writes
# nothing outside its own disk; see store_outputs() if you add uploads.
@app.task(
    plan="4c-16g",
    timeout_seconds=3 * 60 * 60,
    retry=Retry(max_retries=2, wait_duration_ms=10_000, backoff_scaling=2),
)
def transcode_video(ctx: TaskContext, video: dict) -> dict:
    """Transcode one video to HLS plus thumbnails and return small outputs."""
    video_id = str(video.get("id") or "").strip()
    url = str(video.get("url") or "").strip()
    if not video_id or not url:
        return {"id": video_id or None, "error": "Each video needs an id and a url"}
    if "/" in video_id or video_id.startswith("."):
        return {"id": video_id, "error": "id can't contain '/' or start with '.'"}

    threads = available_cpus()
    logger.info(f"[TRANSCODE] {video_id}: {url} ({threads} CPUs)")
    timings = {}
    work = Path(tempfile.mkdtemp(prefix=f"{video_id}-", dir=WORK_DIR))
    try:
        started = time.monotonic()
        src = work / "source"
        size = download(url, src)
        timings["download"] = round(time.monotonic() - started, 1)

        meta = probe(src)
        meta["size_bytes"] = size
        logger.info(f"[TRANSCODE] {video_id}: {json.dumps(meta)}")

        out = work / "out"
        out.mkdir()
        started = time.monotonic()
        thumbnails = make_thumbnails(src, out, meta["duration_seconds"])
        renditions = make_hls(src, out, meta, threads)
        timings["transcode"] = round(time.monotonic() - started, 1)

        outputs = store_outputs(out, video_id, renditions, thumbnails)
    except PermanentError as e:
        logger.error(f"[TRANSCODE] {video_id}: {e} (not retrying)")
        return {"id": video_id, "error": str(e)}
    finally:
        shutil.rmtree(work, ignore_errors=True)

    logger.info(f"[TRANSCODE] {video_id}: done, {outputs['output_bytes']} bytes of HLS and thumbnails, {timings}")
    return {
        "id": video_id,
        "source": {"url": url, **meta},
        **outputs,
        "cpus": threads,
        "seconds": timings,
    }


# transcode_batch only fans out and waits, so it runs on the default flex plan
# and never retries: a retry would re-transcode every video, including the ones
# that finished. Its timeout covers the slowest video's attempts and retries.
@app.task(
    timeout_seconds=12 * 60 * 60,
    retry=Retry(max_retries=0, wait_duration_ms=1000, backoff_scaling=1.5),
)
async def transcode_batch(ctx: TaskContext, videos: list[dict]) -> dict:
    """Transcode many videos in parallel, one task run per video."""
    logger.info(f"[BATCH] Transcoding {len(videos)} videos")

    # ctx.run starts each video as its own task run on its own 4c-16g instance.
    # return_exceptions keeps one failed video from failing the batch.
    results = await asyncio.gather(
        *(ctx.run(transcode_video, video) for video in videos),
        return_exceptions=True,
    )

    done, failed = [], []
    for video, result in zip(videos, results):
        if isinstance(result, BaseException):
            # The run raised on every attempt (retries used up).
            failed.append({"id": video.get("id"), "error": str(result)})
        elif "error" in result:
            failed.append(result)
        else:
            done.append(result)

    logger.info(f"[BATCH] {len(done)} transcoded, {len(failed)} failed")
    return {
        "total": len(videos),
        "transcoded": len(done),
        "failed": failed,
        "results": done,
    }


if __name__ == "__main__":
    app.start()
