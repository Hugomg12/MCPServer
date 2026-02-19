import re
from app.mcp_app import mcp
from app.db import get_pool


@mcp.tool
async def db_ping() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        value = await conn.fetchval("SELECT 1;")
    return {"ok": value == 1}


@mcp.tool
async def list_tables(schema: str = "public") -> list[str]:
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


_READONLY_SQL = re.compile(r"^\s*select\b", re.IGNORECASE)


@mcp.tool
async def query_readonly(sql: str, limit: int = 100) -> list[dict]:
    if not _READONLY_SQL.match(sql):
        raise ValueError("Only SELECT queries are allowed.")

    if re.search(r"\blimit\b", sql, re.IGNORECASE) is None:
        sql = f"{sql.rstrip(';')} LIMIT {int(limit)};"

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)

    return [dict(r) for r in rows]
