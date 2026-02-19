from app.mcp_app import mcp
from app.db import get_pool


@mcp.tool
async def get_stock(sku: str) -> dict:
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
    Add or subtract stock quantity.
    Prevents negative stock.
    """
    if delta == 0:
        return {"ok": True, "sku": sku, "quantity_change": 0}

    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            prod = await conn.fetchrow(
                "SELECT id, sku, name FROM products WHERE sku = $1;",
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
