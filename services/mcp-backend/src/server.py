import os  # permite leer variables de entorno
import re  # expresiones regulares
import asyncpg  # driver async para PostgreSQL
from dotenv import load_dotenv
from fastmcp import FastMCP
from typing import Optional

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


@mcp.tool
async def create_product(sku: str, name: str, initial_qty: int = 0) -> dict:
    """
    Create a product and optionally set initial stock.
    If product exists, returns existing product info (id/sku/name).
    """
    if initial_qty < 0:
        raise ValueError("initial_qty must be >= 0")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO products (sku, name)
                VALUES ($1, $2)
                ON CONFLICT (sku) DO UPDATE SET name = EXCLUDED.name
                RETURNING id, sku, name;
                """,
                sku,
                name,
            )

            # ensure stock row exists
            await conn.execute(
                """
                INSERT INTO stock (product_id, quantity)
                VALUES ($1, $2)
                ON CONFLICT (product_id) DO UPDATE SET
                  quantity = GREATEST(stock.quantity, EXCLUDED.quantity),
                  updated_at = now();
                """,
                row["id"],
                initial_qty,
            )

            if initial_qty > 0:
                await conn.execute(
                    """
                    INSERT INTO stock_movements (product_id, delta, reason)
                    VALUES ($1, $2, $3);
                    """,
                    row["id"],
                    initial_qty,
                    "initial",
                )

    return {"ok": True, "product": dict(row), "initial_qty": initial_qty}


@mcp.tool
async def get_stock(sku: str) -> dict:
    """Get current stock for a product by sku."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT p.sku, p.name, s.quantity, s.updated_at
            FROM products p
            JOIN stock s ON s.product_id = p.id
            WHERE p.sku = $1;
            """,
            sku,
        )
    if not row:
        return {"ok": False, "error": "SKU_NOT_FOUND", "sku": sku}
    return {
        "ok": True,
        "sku": row["sku"],
        "name": row["name"],
        "quantity": row["quantity"],
        "updated_at": str(row["updated_at"]),
    }


@mcp.tool
async def add_stock(sku: str, delta: int, reason: str = "adjustment") -> dict:
    """
    Add/subtract stock by delta. Prevents going negative.
    """
    if delta == 0:
        return {"ok": True, "sku": sku, "quantity_change": 0}

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            prod = await conn.fetchrow(
                "SELECT id, sku, name FROM products WHERE sku = $1;", sku
            )
            if not prod:
                return {"ok": False, "error": "SKU_NOT_FOUND", "sku": sku}

            # lock the stock row to avoid race conditions
            stock_row = await conn.fetchrow(
                "SELECT quantity FROM stock WHERE product_id = $1 FOR UPDATE;",
                prod["id"],
            )
            if not stock_row:
                # create stock row if missing
                await conn.execute(
                    "INSERT INTO stock (product_id, quantity) VALUES ($1, 0);",
                    prod["id"],
                )
                current_qty = 0
            else:
                current_qty = stock_row["quantity"]

            new_qty = current_qty + int(delta)
            if new_qty < 0:
                return {
                    "ok": False,
                    "error": "INSUFFICIENT_STOCK",
                    "sku": sku,
                    "current_qty": current_qty,
                    "requested_delta": delta,
                }

            await conn.execute(
                "UPDATE stock SET quantity = $2, updated_at = now() WHERE product_id = $1;",
                prod["id"],
                new_qty,
            )
            await conn.execute(
                "INSERT INTO stock_movements (product_id, delta, reason) VALUES ($1, $2, $3);",
                prod["id"],
                int(delta),
                reason,
            )

    return {
        "ok": True,
        "sku": sku,
        "quantity_before": current_qty,
        "quantity_after": new_qty,
        "delta": int(delta),
        "reason": reason,
    }


@mcp.tool
async def reserve_stock(sku: str, qty: int) -> dict:
    """
    Reserve stock for an order by subtracting qty.
    Fails if not enough stock.
    """
    if qty <= 0:
        raise ValueError("qty must be > 0")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            prod = await conn.fetchrow(
                "SELECT id, sku, name FROM products WHERE sku = $1;", sku
            )
            if not prod:
                return {"ok": False, "error": "SKU_NOT_FOUND", "sku": sku}

            stock_row = await conn.fetchrow(
                "SELECT quantity FROM stock WHERE product_id = $1 FOR UPDATE;",
                prod["id"],
            )
            current_qty = stock_row["quantity"] if stock_row else 0
            if current_qty < qty:
                return {
                    "ok": False,
                    "error": "INSUFFICIENT_STOCK",
                    "sku": sku,
                    "current_qty": current_qty,
                    "requested_qty": qty,
                }

            new_qty = current_qty - qty
            await conn.execute(
                "UPDATE stock SET quantity = $2, updated_at = now() WHERE product_id = $1;",
                prod["id"],
                new_qty,
            )
            await conn.execute(
                "INSERT INTO stock_movements (product_id, delta, reason) VALUES ($1, $2, $3);",
                prod["id"],
                -int(qty),
                "reserve",
            )

    return {
        "ok": True,
        "sku": sku,
        "reserved": qty,
        "quantity_before": current_qty,
        "quantity_after": new_qty,
    }


if __name__ == "__main__":
    # HTTP transport (Streamable) -> endpoint por defecto: http://localhost:8000/mcp
    mcp.run(transport="http", host="127.0.0.1", port=8000)
