"""
Web service API: the web service

A FastAPI app that puts an HTTP API in front of the build_report task:

1. POST /jobs with a region, a quarter and an Idempotency-Key header. The API
   checks the input, starts build_report, and returns 202 with the run ID.
2. GET /jobs/{run_id} to read the run's status, and its result once it's done.

The API stores nothing. Render keeps each run's status and result, so the run
ID is all a client needs.
"""

import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from render import RenderAsync
from render.client.errors import ClientError

import report

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

SLUG = os.getenv("WORKFLOW_SLUG", "web-service-api")
RUN_ID = re.compile(r"trn-[a-z0-9]+")

render: RenderAsync | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global render
    # Reads RENDER_API_KEY, or RENDER_USE_LOCAL_DEV to use the local task server.
    render = RenderAsync()
    yield


app = FastAPI(title="Web service API", lifespan=lifespan)


class JobRequest(BaseModel):
    region: str
    quarter: str


# FastAPI answers malformed requests with 422. This API uses 400 for every kind
# of bad input, so clients have one status to handle.
@app.exception_handler(RequestValidationError)
async def bad_request(request: Request, exc: RequestValidationError):
    problems = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return JSONResponse(status_code=400, content={"detail": "; ".join(problems)})


@app.post("/jobs", status_code=202)
async def create_job(
    body: JobRequest,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    # Check everything here, so bad input never becomes a task run.
    problem = report.check_request(body.region, body.quarter)
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    try:
        # Within 24 hours, the same key returns the run it first started, even
        # if the body is different. A client that times out can send the same
        # request again without starting a second run.
        run = await render.workflows.start_task(
            f"{SLUG}/build_report",
            [body.region, body.quarter],
            idempotency_key=f"build_report:{idempotency_key}",
        )
    except Exception:
        logger.exception("[API] Couldn't start build_report")
        raise HTTPException(
            status_code=503,
            detail="Couldn't start the job. Send the same request again.",
        )

    logger.info(f"[API] {idempotency_key} -> {run.id}")
    response.headers["Location"] = f"/jobs/{run.id}"
    return {"run_id": run.id}


@app.get("/jobs/{run_id}")
async def get_job(run_id: str):
    if not RUN_ID.fullmatch(run_id):
        raise HTTPException(status_code=404, detail="No such job")
    try:
        details = await render.workflows.get_task_run(run_id)
    except ClientError as e:
        # Render answers 400 for a run ID it doesn't know.
        if e.status_code in (400, 404):
            raise HTTPException(status_code=404, detail="No such job")
        logger.exception(f"[API] Couldn't read {run_id}")
        raise HTTPException(status_code=502, detail="Couldn't read the job. Try again.")
    except Exception:
        logger.exception(f"[API] Couldn't read {run_id}")
        raise HTTPException(status_code=502, detail="Couldn't read the job. Try again.")

    job = {"run_id": details.id, "status": str(details.status)}
    if details.status == "completed":
        # build_report returns {"error": ...} for input a retry can't fix.
        job["result"] = details.results[0] if details.results else None
    elif details.status == "failed":
        job["error"] = details.error if isinstance(details.error, str) else "The run failed"
    return job


@app.get("/healthz")
async def healthz():
    return {"ok": True}
