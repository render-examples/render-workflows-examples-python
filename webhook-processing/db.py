"""Database helpers shared by the receiver and the workflow service."""

import os
import pathlib

import psycopg
from psycopg.rows import dict_row

SCHEMA = (pathlib.Path(__file__).parent / "schema.sql").read_text()


async def connect() -> psycopg.AsyncConnection:
    # autocommit: each statement commits on its own, unless it runs inside an
    # explicit `async with conn.transaction()` block.
    return await psycopg.AsyncConnection.connect(
        os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row
    )


async def apply_schema() -> None:
    async with await connect() as conn:
        await conn.execute(SCHEMA)
