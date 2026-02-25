"""
Database administration tools.

Provides MCP tools for basic database inspection:
  - db_ping: verify the database connection is alive.
  - list_tables: list all tables in a given schema.
  - query_readonly: execute arbitrary read-only (SELECT) queries.
"""

import re
from app.mcp_app import mcp
from app.db import get_pool


@mcp.tool
async def db_ping() -> dict:
    """
    Check that the database connection is working by running a trivial query.

    Returns:
        dict: {"ok": True} if the database responded correctly.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        value = await conn.fetchval("SELECT 1;")
    return {"ok": value == 1}


@mcp.tool
async def list_tables(schema: str = "public") -> list[str]:
    """
    List all table names in the given database schema.

    Args:
        schema: The PostgreSQL schema to query (defaults to "public").

    Returns:
        list[str]: Sorted list of table names.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT tablename
            FROM pg_catalog.pg_tables
            WHERE schemaname = $1
            ORDER BY tablename;
            """,
            schema,
        )
    return [r["tablename"] for r in rows]


# Regex that matches strings starting with a SELECT statement.
# Used to reject any non-SELECT queries in query_readonly.
_READONLY_SQL = re.compile(r"^\s*select\b", re.IGNORECASE)


@mcp.tool
async def query_readonly(sql: str, limit: int = 100) -> list[dict]:
    """
    Execute a read-only SQL query and return the results.

    Only SELECT statements are allowed; anything else raises a ValueError.
    A LIMIT clause is appended automatically if the query does not already
    include one, to prevent accidentally fetching huge result sets.

    Args:
        sql: The SELECT query to run.
        limit: Maximum number of rows to return (default 100).

    Returns:
        list[dict]: Each row as a dictionary of column-name → value.

    Raises:
        ValueError: If the query is not a SELECT statement.
    """
    if not _READONLY_SQL.match(sql):
        raise ValueError("Only SELECT queries are allowed.")

    # Append a LIMIT clause if the user didn't provide one
    if re.search(r"\blimit\b", sql, re.IGNORECASE) is None:
        sql = f"{sql.rstrip(';')} LIMIT {int(limit)};"

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)

    return [dict(r) for r in rows]
