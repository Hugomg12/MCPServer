from app.mcp_app import mcp
from app.db import get_pool


@mcp.tool
async def create_product(sku: str, name: str, initial_qty: int = 0) -> dict:
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
