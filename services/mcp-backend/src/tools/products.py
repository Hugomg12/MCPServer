"""
Product management tool.

Exposes an MCP tool to create (or update) a product in the database,
optionally setting an initial stock quantity. The operation is wrapped
in a database transaction so that the product, stock record, and
stock-movement log entry are all created atomically.
"""

from app.mcp_app import mcp
from app.db import get_pool


@mcp.tool
async def create_product(sku: str, name: str, initial_qty: int = 0) -> dict:
    """
    Create a new product or update an existing one by SKU, and optionally
    set its initial stock quantity.

    If a product with the same SKU already exists, its name is updated
    (upsert behaviour). The stock row is also upserted, and a
    stock-movement entry is recorded when initial_qty > 0.

    Args:
        sku: Unique product identifier (Stock Keeping Unit).
        name: Human-readable product name.
        initial_qty: Starting stock quantity (must be >= 0, default 0).

    Returns:
        dict: Confirmation with the product data and the initial quantity.

    Raises:
        ValueError: If initial_qty is negative.
    """
    if initial_qty < 0:
        raise ValueError("initial_qty must be >= 0")

    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Insert the product or update its name if the SKU already exists
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

            # Ensure a stock record exists for this product.
            # If one already exists, keep the higher quantity.
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

            # Log the initial stock as a movement only when there is stock to add
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
