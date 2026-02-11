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


def _normalize_order_id(order_id: str) -> str:
    # validación simple (UUID típico tiene 36 chars con guiones)
    oid = order_id.strip()
    if len(oid) < 30:
        raise ValueError("order_id looks invalid")
    return oid


@mcp.tool
async def create_order(sku: str, qty: int) -> dict:
    """
    Create an order in PENDING state with a single item (sku, qty).
    Returns order_id (UUID).
    """
    if qty <= 0:
        raise ValueError("qty must be > 0")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # create order
            order = await conn.fetchrow(
                """
                INSERT INTO orders (status)
                VALUES ('PENDING')
                RETURNING id::text AS id, status, created_at, updated_at;
                """
            )

            # add item
            await conn.execute(
                """
                INSERT INTO order_items (order_id, sku, qty)
                VALUES ($1::uuid, $2, $3);
                """,
                order["id"],
                sku,
                qty,
            )

    return {
        "ok": True,
        "order_id": order["id"],
        "status": order["status"],
        "sku": sku,
        "qty": qty,
    }


@mcp.tool
async def get_order(order_id: str) -> dict:
    """
    Get order status and items.
    """
    oid = _normalize_order_id(order_id)

    pool = await get_pool()
    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT id::text AS id, status, created_at, updated_at FROM orders WHERE id = $1::uuid;",
            oid,
        )
        if not order:
            return {"ok": False, "error": "ORDER_NOT_FOUND", "order_id": oid}

        items = await conn.fetch(
            "SELECT sku, qty FROM order_items WHERE order_id = $1::uuid ORDER BY sku;",
            oid,
        )

        res = await conn.fetch(
            """
            SELECT sku, qty, active, created_at, released_at
            FROM reservations
            WHERE order_id = $1::uuid
            ORDER BY created_at DESC;
            """,
            oid,
        )

    return {
        "ok": True,
        "order_id": order["id"],
        "status": order["status"],
        "items": [dict(r) for r in items],
        "reservations": [dict(r) for r in res],
    }


@mcp.tool
async def reserve_for_order(order_id: str) -> dict:
    """
    Reserve stock for the order items (single-item for now).
    Marks order as RESERVED and creates an active reservation row.
    Idempotent-ish: if already RESERVED and has active reservation, returns ok.
    """
    oid = _normalize_order_id(order_id)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            order = await conn.fetchrow(
                "SELECT status FROM orders WHERE id = $1::uuid FOR UPDATE;",
                oid,
            )
            if not order:
                return {"ok": False, "error": "ORDER_NOT_FOUND", "order_id": oid}

            if order["status"] in ("PAID", "CANCELLED", "FAILED"):
                return {
                    "ok": False,
                    "error": "ORDER_NOT_RESERVABLE",
                    "status": order["status"],
                    "order_id": oid,
                }

            # if already reserved and reservation exists -> return
            existing_res = await conn.fetchrow(
                "SELECT id::text AS id, sku, qty FROM reservations WHERE order_id = $1::uuid AND active = TRUE;",
                oid,
            )
            if existing_res and order["status"] == "RESERVED":
                return {
                    "ok": True,
                    "order_id": oid,
                    "status": "RESERVED",
                    "reservation": dict(existing_res),
                }

            # get single item
            item = await conn.fetchrow(
                "SELECT sku, qty FROM order_items WHERE order_id = $1::uuid LIMIT 1;",
                oid,
            )
            if not item:
                return {"ok": False, "error": "ORDER_HAS_NO_ITEMS", "order_id": oid}

            sku = item["sku"]
            qty = int(item["qty"])

            # lock product + stock row
            prod = await conn.fetchrow("SELECT id FROM products WHERE sku = $1;", sku)
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
                -qty,
                f"reserve_order:{oid}",
            )

            reservation = await conn.fetchrow(
                """
                INSERT INTO reservations (order_id, sku, qty, active)
                VALUES ($1::uuid, $2, $3, TRUE)
                RETURNING id::text AS id, order_id::text AS order_id, sku, qty, active, created_at, released_at;
                """,
                oid,
                sku,
                qty,
            )

            await conn.execute(
                "UPDATE orders SET status = 'RESERVED', updated_at = now() WHERE id = $1::uuid;",
                oid,
            )

    return {
        "ok": True,
        "order_id": oid,
        "status": "RESERVED",
        "reservation": dict(reservation),
    }


@mcp.tool
async def release_stock(order_id: str) -> dict:
    """
    Release active reservation for an order (if any) and restock accordingly.
    Marks order CANCELLED if it was PENDING/RESERVED.
    Safe to call multiple times: if no active reservation, returns ok with released=false.
    """
    oid = _normalize_order_id(order_id)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            order = await conn.fetchrow(
                "SELECT status FROM orders WHERE id = $1::uuid FOR UPDATE;",
                oid,
            )
            if not order:
                return {"ok": False, "error": "ORDER_NOT_FOUND", "order_id": oid}

            if order["status"] == "PAID":
                return {
                    "ok": False,
                    "error": "CANNOT_RELEASE_PAID_ORDER",
                    "order_id": oid,
                }

            res = await conn.fetchrow(
                "SELECT id::text AS id, sku, qty FROM reservations WHERE order_id = $1::uuid AND active = TRUE FOR UPDATE;",
                oid,
            )
            if not res:
                # still cancel order if it's pending/reserved
                if order["status"] in ("PENDING", "RESERVED"):
                    await conn.execute(
                        "UPDATE orders SET status = 'CANCELLED', updated_at = now() WHERE id = $1::uuid;",
                        oid,
                    )
                return {
                    "ok": True,
                    "order_id": oid,
                    "released": False,
                    "status": "CANCELLED",
                }

            sku = res["sku"]
            qty = int(res["qty"])

            prod = await conn.fetchrow("SELECT id FROM products WHERE sku = $1;", sku)
            if not prod:
                return {"ok": False, "error": "SKU_NOT_FOUND", "sku": sku}

            stock_row = await conn.fetchrow(
                "SELECT quantity FROM stock WHERE product_id = $1 FOR UPDATE;",
                prod["id"],
            )
            current_qty = stock_row["quantity"] if stock_row else 0
            new_qty = current_qty + qty

            await conn.execute(
                "UPDATE stock SET quantity = $2, updated_at = now() WHERE product_id = $1;",
                prod["id"],
                new_qty,
            )
            await conn.execute(
                "INSERT INTO stock_movements (product_id, delta, reason) VALUES ($1, $2, $3);",
                prod["id"],
                qty,
                f"release_order:{oid}",
            )

            await conn.execute(
                "UPDATE reservations SET active = FALSE, released_at = now() WHERE id = $1::uuid;",
                res["id"],
            )

            if order["status"] in ("PENDING", "RESERVED"):
                await conn.execute(
                    "UPDATE orders SET status = 'CANCELLED', updated_at = now() WHERE id = $1::uuid;",
                    oid,
                )

    return {
        "ok": True,
        "order_id": oid,
        "released": True,
        "sku": sku,
        "qty": qty,
        "status": "CANCELLED",
    }


@mcp.tool
async def mark_paid(order_id: str) -> dict:
    """
    Mark an order as PAID. Should typically be called after reserve_for_order.
    """
    oid = _normalize_order_id(order_id)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            order = await conn.fetchrow(
                "SELECT status FROM orders WHERE id = $1::uuid FOR UPDATE;",
                oid,
            )
            if not order:
                return {"ok": False, "error": "ORDER_NOT_FOUND", "order_id": oid}

            if order["status"] == "PAID":
                return {"ok": True, "order_id": oid, "status": "PAID"}

            if order["status"] not in ("RESERVED", "PENDING"):
                return {
                    "ok": False,
                    "error": "ORDER_NOT_PAYABLE",
                    "status": order["status"],
                    "order_id": oid,
                }

            await conn.execute(
                "UPDATE orders SET status = 'PAID', updated_at = now() WHERE id = $1::uuid;",
                oid,
            )

    return {"ok": True, "order_id": oid, "status": "PAID"}


@mcp.tool
async def mark_failed(order_id: str) -> dict:
    """
    Mark an order as FAILED and release stock if there is an active reservation.
    """
    oid = _normalize_order_id(order_id)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            order = await conn.fetchrow(
                "SELECT status FROM orders WHERE id = $1::uuid FOR UPDATE;",
                oid,
            )
            if not order:
                return {"ok": False, "error": "ORDER_NOT_FOUND", "order_id": oid}

            if order["status"] == "FAILED":
                return {"ok": True, "order_id": oid, "status": "FAILED"}

            if order["status"] == "PAID":
                return {"ok": False, "error": "CANNOT_FAIL_PAID_ORDER", "order_id": oid}

            # set failed first
            await conn.execute(
                "UPDATE orders SET status = 'FAILED', updated_at = now() WHERE id = $1::uuid;",
                oid,
            )

    # release outside the tx above is ok, but we can do it immediately after; keep simple:
    release = await release_stock(oid)
    return {"ok": True, "order_id": oid, "status": "FAILED", "release": release}


if __name__ == "__main__":
    # HTTP transport (Streamable) -> endpoint por defecto: http://localhost:8000/mcp
    mcp.run(transport="http", host="0.0.0.0", port=8000)
