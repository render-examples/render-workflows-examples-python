"""
Extract every invoice in invoices.json and save the results to Postgres.

Requires RENDER_API_KEY and DATABASE_URL, and the table from schema.sql.
Set WORKFLOW_SLUG to your workflow service's slug (it defaults to
pdf-extraction).
"""

import asyncio
import json
import os
import pathlib

import psycopg
from psycopg.types.json import Jsonb
from render import RenderAsync

SLUG = os.getenv("WORKFLOW_SLUG", "pdf-extraction")
documents = json.loads((pathlib.Path(__file__).parent / "invoices.json").read_text())

UPSERT = """
INSERT INTO invoice_extractions (document_id, record, exceptions, task_run_id, updated_at)
VALUES (%s, %s, %s, %s, now())
ON CONFLICT (document_id) DO UPDATE SET
    record = EXCLUDED.record,
    exceptions = EXCLUDED.exceptions,
    task_run_id = EXCLUDED.task_run_id,
    updated_at = now()
"""


async def extract(render: RenderAsync, doc: dict):
    # One idempotency key per document. If this script stops halfway and you
    # run it again within 24 hours, Render returns the runs it already started
    # instead of extracting those documents (and paying for the model) again.
    return await render.workflows.run_task(
        f"{SLUG}/extract_document",
        [doc],
        idempotency_key=f"extract-{doc['document_id']}",
    )


async def main():
    # RenderAsync() reads RENDER_API_KEY from the environment.
    render = RenderAsync()

    # One task run per document, in parallel. run_task waits for each run to
    # finish. return_exceptions keeps one failed run from stopping the rest.
    runs = await asyncio.gather(
        *(extract(render, doc) for doc in documents), return_exceptions=True
    )

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for doc, run in zip(documents, runs):
            if isinstance(run, BaseException):
                print(f"{doc['document_id']}: run failed: {run}")
                continue

            # results is a list holding the task's return value.
            result = run.results[0]
            record = result["record"]
            conn.execute(
                UPSERT,
                (
                    result["document_id"],
                    Jsonb(record) if record is not None else None,
                    Jsonb(result["exceptions"]),
                    run.id,
                ),
            )
            codes = [e["code"] for e in result["exceptions"]]
            print(f"{result['document_id']}: saved, exceptions: {codes or 'none'}")


if __name__ == "__main__":
    asyncio.run(main())
