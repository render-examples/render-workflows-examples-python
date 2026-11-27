"""Database helpers shared by the API service and the workflow service."""

import os
import pathlib

import psycopg
from psycopg.rows import dict_row

SCHEMA = (pathlib.Path(__file__).parent / "schema.sql").read_text()


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


async def connect() -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(database_url(), row_factory=dict_row)


async def apply_schema() -> None:
    async with await connect() as conn:
        await conn.execute(SCHEMA)
