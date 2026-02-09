import os  # permite leer variables de entorno
import re  # expresiones regulares
import asyncpg  # driver async para PostgreSQL
from dotenv import load_dotenv
from fastmcp import FastMCP

# Lee el archivo .env + inserta las variables en el entorno
load_dotenv()

# leemos cada variable de entorno
# si no existe usamos el valor por defecto
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "5433"))
DB_NAME = os.getenv("DB_NAME", "n8n")
DB_USER = os.getenv("DB_USER", "n8n")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# crear servidor MCP
mcp = FastMCP("agent-lab-mcp-backend")

# Pool de conexiones a Postgres
_pool: asyncpg.Pool | None = None


#
async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            min_size=1,
            max_size=5,
        )
    return _pool


@mcp.tool
def health() -> dict:
    return {"ok": True, "service": "agent-lab-mcp-backend"}


# comprueba conectividad con Postgres
@mcp.tool
async def db_ping() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        value = await conn.fetchval("SELECT 1;")
    return {"ok": value == 1}


# publica la tabla del parametro
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


# valida que el SQL empieza por SELECT
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


if __name__ == "__main__":
    # HTTP transport (Streamable) -> endpoint por defecto: http://localhost:8000/mcp
    mcp.run(transport="http", host="127.0.0.1", port=8000)
