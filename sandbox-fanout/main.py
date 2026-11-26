"""
Sandbox fan-out on Render Workflows

Runs a batch of untrusted jobs in parallel, one Render Sandbox per job. Each
job is a Python script and an input CSV. A workflow task creates a sandbox,
copies the job in, runs it, copies the result out, and terminates the sandbox.
A second task fans the batch out and caps how many sandboxes run at once.

The job code never runs on the workflow service itself. It runs in a sandbox
with outbound network access turned off.
"""

import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path

from render import RenderAsync, Retry, TaskContext, Workflows
from render.client.errors import ClientError
from render.sandbox import SandboxCreateWaitError, SandboxExecExit, SandboxExecOutput

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# How many sandboxes run_batch keeps running at once. The default sandbox group
# allows 1,000 concurrent sandboxes on Pro workspaces and 100 on Hobby, and you
# can create at most 100 sandboxes per minute.
MAX_SANDBOXES = int(os.getenv("MAX_SANDBOXES", "2"))

# How long one job's script may run inside its sandbox before it's stopped.
JOB_TIMEOUT_SECONDS = int(os.getenv("JOB_TIMEOUT_SECONDS", "60"))

# Sandbox timeouts. The runtime timeout terminates the sandbox even if this
# task never reaches its finally block (for example, if the attempt is killed).
# It's set a little above the task timeout below. The minimums are 60 seconds
# for the runtime timeout and 300 seconds for the idle timeout.
SANDBOX_TIMEOUT_SECONDS = 180
SANDBOX_IDLE_TIMEOUT_SECONDS = 300

# Sandboxes API errors that mean the request itself is wrong: a bad API key, a
# workspace the key can't access, an invalid setting. A retry can't fix them,
# so run_job returns them as a result instead of raising.
PERMANENT_ERROR_STATUSES = {400, 401, 403, 404, 422}

app = Workflows()


def tail(text: str, limit: int = 2000) -> str:
    """Keep the end of a long output, where errors usually are."""
    return text if len(text) <= limit else "..." + text[-limit:]


# ============================================================================
# Tasks
# ============================================================================


# run_job calls the Sandboxes API and runs code that might fail, so it retries
# once. Each attempt creates its own sandbox, so a retry starts clean.
@app.task(
    retry=Retry(max_retries=1, wait_duration_ms=2000, backoff_scaling=2),
    timeout_seconds=150,
)
async def run_job(ctx: TaskContext, job: dict) -> dict:
    """Run one job's script against its input CSV in a new sandbox."""
    job_id = job.get("id", "unknown")

    # RenderAsync reads RENDER_API_KEY, and the sandbox client needs the
    # workspace to create sandboxes in from RENDER_WORKSPACE_ID. A missing
    # setting is a problem a retry can't fix, so return it instead of raising.
    missing = [name for name in ("RENDER_API_KEY", "RENDER_WORKSPACE_ID") if not os.getenv(name)]
    if missing:
        return {"job_id": job_id, "error": f"{' and '.join(missing)} not set"}

    sandboxes = RenderAsync().sandboxes
    sandbox_id = None
    started = time.monotonic()

    with tempfile.TemporaryDirectory() as workdir:
        local = Path(workdir)
        (local / "job.py").write_text(job["script"])
        (local / "input.csv").write_text(job["input_csv"])

        try:
            try:
                sandbox = await sandboxes.create(
                    name=f"job-{job_id}"[:64],
                    # The job is untrusted code, so it gets no network access.
                    # To let it reach specific HTTPS hosts, pass
                    # NetworkPolicy(type="allow-list", rules=[EgressRule(domain="...")]).
                    network_policy="deny-all",
                    timeout_seconds=SANDBOX_TIMEOUT_SECONDS,
                    idle_timeout_seconds=SANDBOX_IDLE_TIMEOUT_SECONDS,
                    wait=True,
                )
            except SandboxCreateWaitError as e:
                # The sandbox exists but never reached running. Keep its id so
                # the finally block terminates it, then let Render retry.
                sandbox_id = e.sandbox_id
                raise
            except ClientError as e:
                if e.status_code in PERMANENT_ERROR_STATUSES:
                    logger.error(f"[JOB] {job_id}: Sandboxes API error {e.status_code}, not retrying")
                    return {"job_id": job_id, "error": f"Sandboxes API error {e.status_code}: {e}"}
                raise

            sandbox_id = sandbox.id
            logger.info(f"[JOB] {job_id}: running in sandbox {sandbox_id}")

            # Relative paths resolve from the sandbox's home directory, which is
            # also where commands start.
            await sandboxes.copy_to(sandbox_id, local / "job.py", "job.py")
            await sandboxes.copy_to(sandbox_id, local / "input.csv", "input.csv")

            stdout, stderr, exit_code = [], [], None
            async for event in sandboxes.exec(
                sandbox_id, f"timeout {JOB_TIMEOUT_SECONDS} python3 job.py"
            ):
                if isinstance(event, SandboxExecOutput):
                    (stderr if event.stream == "stderr" else stdout).append(event.data)
                elif isinstance(event, SandboxExecExit):
                    exit_code = event.exit_code

            # A nonzero exit code doesn't raise in the Sandboxes SDK. Raise
            # here so Render retries the job (in a new sandbox) and then marks
            # the run failed.
            if exit_code != 0:
                logger.warning(f"[JOB] {job_id}: exited with code {exit_code} in {sandbox_id}")
                raise RuntimeError(
                    f"Job {job_id} exited with code {exit_code} in sandbox {sandbox_id}: "
                    f"{tail(''.join(stderr), 500)}"
                )

            # Copy the result out before the sandbox is terminated. Termination
            # deletes the sandbox's filesystem.
            await sandboxes.copy_from(sandbox_id, "output.json", local / "output.json")
            output = json.loads((local / "output.json").read_text())
        finally:
            # Terminate on every path: success, a failed job, or an API error.
            if sandbox_id:
                try:
                    await sandboxes.terminate(sandbox_id)
                    logger.info(f"[JOB] {job_id}: terminated sandbox {sandbox_id}")
                except Exception as e:
                    # Don't let a failed cleanup hide the job's own result or
                    # error. The sandbox's runtime timeout still ends it.
                    logger.error(f"[JOB] {job_id}: couldn't terminate {sandbox_id}: {e}")

    logger.info(f"[JOB] {job_id}: done in {time.monotonic() - started:.1f}s")
    return {
        "job_id": job_id,
        "sandbox_id": sandbox_id,
        "output": output,
        "stdout": tail("".join(stdout)),
    }


# run_batch only fans out, so it doesn't retry: a retry would rerun every job,
# including the ones that already finished. Each run_job run retries on its own.
@app.task(
    retry=Retry(max_retries=0, wait_duration_ms=1000, backoff_scaling=1.5),
    timeout_seconds=900,
)
async def run_batch(ctx: TaskContext, jobs: list[dict]) -> dict:
    """Run many jobs in parallel, one sandbox per job, at most MAX_SANDBOXES at once."""
    logger.info(f"[BATCH] Running {len(jobs)} jobs, at most {MAX_SANDBOXES} at a time")

    # Each run_job run holds one sandbox, including while it retries. The
    # semaphore caps how many of them run at once.
    limit = asyncio.Semaphore(MAX_SANDBOXES)

    async def run_one(job: dict):
        async with limit:
            return await ctx.run(run_job, job)

    # return_exceptions keeps one failed job from failing the batch.
    results = await asyncio.gather(*(run_one(job) for job in jobs), return_exceptions=True)

    succeeded, failed = [], []
    for job, result in zip(jobs, results):
        if isinstance(result, BaseException):
            failed.append({"job_id": job.get("id"), "error": str(result)})
        elif "error" in result:
            failed.append(result)
        else:
            succeeded.append(result)

    logger.info(f"[BATCH] {len(succeeded)} succeeded, {len(failed)} failed")
    return {
        "total": len(jobs),
        "succeeded": len(succeeded),
        "failed": failed,
        "results": succeeded,
    }


if __name__ == "__main__":
    app.start()
