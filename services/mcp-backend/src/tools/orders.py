from app.mcp_app import mcp
from app.db import get_pool


def _normalize_order_id(order_id: str) -> str:
    oid = order_id.strip()
    if len(oid) < 30:
        raise ValueError("order_id looks invalid")
    return oid


@mcp.tool
async def create_order(sku: str, qty: int) -> dict:
    """
    Create order in PENDING state with one item.
    """
    if qty <= 0:
        raise ValueError("qty must be > 0")

    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            order = await conn.fetchrow(
                """
                INSERT INTO orders (status)
                VALUES ('PENDING')
                RETURNING id::text AS id, status, created_at, updated_at;
                """
            )

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
    Get order with items and reservations.
    """
    oid = _normalize_order_id(order_id)

    pool = await get_pool()

    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            """
            SELECT id::text AS id, status, created_at, updated_at
            FROM orders
            WHERE id = $1::uuid;
            """,
            oid,
        )

        if not order:
            return {"ok": False, "error": "ORDER_NOT_FOUND", "order_id": oid}

        items = await conn.fetch(
            """
            SELECT sku, qty
            FROM order_items
            WHERE order_id = $1::uuid;
            """,
            oid,
        )

        reservations = await conn.fetch(
            """
            SELECT id::text AS id, sku, qty, active, created_at, released_at
            FROM reservations
            WHERE order_id = $1::uuid;
            """,
            oid,
        )

    return {
        "ok": True,
        "order_id": order["id"],
        "status": order["status"],
        "items": [dict(r) for r in items],
        "reservations": [dict(r) for r in reservations],
    }


@mcp.tool
async def reserve_for_order(order_id: str) -> dict:
    """
    Reserve stock and mark order RESERVED.
    """
    oid = _normalize_order_id(order_id)

    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            order = await conn.fetchrow(
                """
                SELECT status
                FROM orders
                WHERE id = $1::uuid
                FOR UPDATE;
                """,
                oid,
            )

            if not order:
                return {"ok": False, "error": "ORDER_NOT_FOUND"}

            if order["status"] in ("PAID", "CANCELLED", "FAILED"):
                return {
                    "ok": False,
                    "error": "ORDER_NOT_RESERVABLE",
                    "status": order["status"],
                }

            item = await conn.fetchrow(
                """
                SELECT sku, qty
                FROM order_items
                WHERE order_id = $1::uuid
                LIMIT 1;
                """,
                oid,
            )

            if not item:
                return {"ok": False, "error": "ORDER_HAS_NO_ITEMS"}

            sku = item["sku"]
            qty = int(item["qty"])

            prod = await conn.fetchrow(
                "SELECT id FROM products WHERE sku = $1;",
                sku,
            )

            if not prod:
                return {"ok": False, "error": "SKU_NOT_FOUND", "sku": sku}

            stock_row = await conn.fetchrow(
                """
                SELECT quantity
                FROM stock
                WHERE product_id = $1
                FOR UPDATE;
                """,
                prod["id"],
            )

            current_qty = stock_row["quantity"] if stock_row else 0

            if current_qty < qty:
                return {
                    "ok": False,
                    "error": "INSUFFICIENT_STOCK",
                    "current_qty": current_qty,
                    "requested_qty": qty,
                }

            new_qty = current_qty - qty

            await conn.execute(
                """
                UPDATE stock
                SET quantity = $2, updated_at = now()
                WHERE product_id = $1;
                """,
                prod["id"],
                new_qty,
            )

            await conn.execute(
                """
                INSERT INTO stock_movements (product_id, delta, reason)
                VALUES ($1, $2, $3);
                """,
                prod["id"],
                -qty,
                f"reserve_order:{oid}",
            )

            reservation = await conn.fetchrow(
                """
                INSERT INTO reservations (order_id, sku, qty, active)
                VALUES ($1::uuid, $2, $3, TRUE)
                RETURNING id::text AS id, sku, qty, active, created_at, released_at;
                """,
                oid,
                sku,
                qty,
            )

            await conn.execute(
                """
                UPDATE orders
                SET status = 'RESERVED', updated_at = now()
                WHERE id = $1::uuid;
                """,
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
    Release active reservation and cancel order.
    """
    oid = _normalize_order_id(order_id)

    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            reservation = await conn.fetchrow(
                """
                SELECT id::text AS id, sku, qty
                FROM reservations
                WHERE order_id = $1::uuid
                AND active = TRUE
                FOR UPDATE;
                """,
                oid,
            )

            if not reservation:
                return {"ok": True, "released": False}

            sku = reservation["sku"]
            qty = reservation["qty"]

            prod = await conn.fetchrow(
                "SELECT id FROM products WHERE sku = $1;",
                sku,
            )

            stock_row = await conn.fetchrow(
                """
                SELECT quantity
                FROM stock
                WHERE product_id = $1
                FOR UPDATE;
                """,
                prod["id"],
            )

            current_qty = stock_row["quantity"] if stock_row else 0
            new_qty = current_qty + qty

            await conn.execute(
                """
                UPDATE stock
                SET quantity = $2, updated_at = now()
                WHERE product_id = $1;
                """,
                prod["id"],
                new_qty,
            )

            await conn.execute(
                """
                UPDATE reservations
                SET active = FALSE, released_at = now()
                WHERE id = $1::uuid;
                """,
                reservation["id"],
            )

            await conn.execute(
                """
                UPDATE orders
                SET status = 'CANCELLED', updated_at = now()
                WHERE id = $1::uuid;
                """,
                oid,
            )

    return {
        "ok": True,
        "order_id": oid,
        "released": True,
        "status": "CANCELLED",
    }


@mcp.tool
async def mark_paid(order_id: str) -> dict:
    oid = _normalize_order_id(order_id)

    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE orders
            SET status = 'PAID', updated_at = now()
            WHERE id = $1::uuid;
            """,
            oid,
        )

    return {"ok": True, "order_id": oid, "status": "PAID"}


@mcp.tool
async def mark_failed(order_id: str) -> dict:
    oid = _normalize_order_id(order_id)

    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE orders
            SET status = 'FAILED', updated_at = now()
            WHERE id = $1::uuid;
            """,
            oid,
        )

    return {"ok": True, "order_id": oid, "status": "FAILED"}
