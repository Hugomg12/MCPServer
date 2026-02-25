"""
Stock management tools.

Provides MCP tools for querying and adjusting product stock levels:
  - get_stock: look up the current stock for a product by SKU.
  - add_stock: add or subtract stock (positive or negative delta),
    while preventing the quantity from going below zero.

All stock changes are recorded in the stock_movements table for
auditing purposes.
"""

from app.mcp_app import mcp
from app.db import get_pool


@mcp.tool
async def get_stock(sku: str) -> dict:
    """
    Retrieve the current stock information for a product.

    Args:
        sku: The product SKU to look up.

    Returns:
        dict: Stock details (sku, name, quantity, updated_at) on success,
              or an error with "SKU_NOT_FOUND" if the product does not exist.
    """
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

    A positive delta increases stock; a negative delta decreases it.
    The operation is atomic (wrapped in a transaction) and locks the
    stock row with FOR UPDATE to avoid race conditions.

    Args:
        sku: The product SKU to adjust.
        delta: Amount to add (positive) or subtract (negative).
        reason: Short description of why the adjustment is made
                (default "adjustment").

    Returns:
        dict: The quantities before and after the change on success,
              or an error if the SKU is not found or stock would go negative.
    """
    if delta == 0:
        return {"ok": True, "sku": sku, "quantity_change": 0}

    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Find the product by SKU
            prod = await conn.fetchrow(
                "SELECT id, sku, name FROM products WHERE sku = $1;",
                sku,
            )

            if not prod:
                return {"ok": False, "error": "SKU_NOT_FOUND", "sku": sku}

            # Lock the stock row to prevent concurrent modifications
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

            # Prevent stock from going below zero
            if new_qty < 0:
                return {
                    "ok": False,
                    "error": "INSUFFICIENT_STOCK",
                    "sku": sku,
                    "current_qty": current_qty,
                    "requested_delta": delta,
                }

            # Apply the new quantity
            await conn.execute(
                """
                UPDATE stock
                SET quantity = $2, updated_at = now()
                WHERE product_id = $1;
                """,
                prod["id"],
                new_qty,
            )

            # Record the movement for audit purposes
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
