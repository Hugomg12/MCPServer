"""
Order management tools.

Provides MCP tools for the full order lifecycle:
  - create_order:       create a new order in PENDING state.
  - get_order:          retrieve an order with its items and reservations.
  - reserve_for_order:  reserve stock for the order and mark it RESERVED.
  - release_stock:      release a reservation and cancel the order.
  - mark_paid:          mark an order as PAID.
  - mark_failed:        mark an order as FAILED.

All write operations use database transactions to keep data consistent.
"""

from app.mcp_app import mcp
from app.db import get_pool


def _normalize_order_id(order_id: str) -> str:
    """
    Validate and clean up the incoming order_id string.

    Args:
        order_id: Raw order ID from the caller.

    Returns:
        str: Trimmed order ID.

    Raises:
        ValueError: If the order_id is too short to be a valid UUID.
    """
    oid = order_id.strip()
    # UUIDs are 36 characters; anything much shorter is likely invalid
    if len(oid) < 30:
        raise ValueError("order_id looks invalid")
    return oid


@mcp.tool
async def create_order(sku: str, qty: int) -> dict:
    """
    Create a new order in PENDING state with a single item.

    Args:
        sku: The product SKU to order.
        qty: The quantity to order (must be > 0).

    Returns:
        dict: The new order ID, status, SKU, and quantity.

    Raises:
        ValueError: If qty is not positive.
    """
    if qty <= 0:
        raise ValueError("qty must be > 0")

    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Create the order header with PENDING status
            order = await conn.fetchrow(
                """
                INSERT INTO orders (status)
                VALUES ('PENDING')
                RETURNING id::text AS id, status, created_at, updated_at;
                """
            )

            # Add the single line item to the order
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
    Retrieve an order together with its items and any stock reservations.

    Args:
        order_id: The UUID of the order to look up.

    Returns:
        dict: Order details including items and reservations on success,
              or an error with "ORDER_NOT_FOUND" if the order does not exist.
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

        # Fetch all line items for this order
        items = await conn.fetch(
            """
            SELECT sku, qty
            FROM order_items
            WHERE order_id = $1::uuid;
            """,
            oid,
        )

        # Fetch all stock reservations (active or released) for this order
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
    Reserve stock for an order and transition it to RESERVED status.

    This locks the stock row, subtracts the required quantity, records a
    stock movement, creates a reservation record, and updates the order
    status — all inside a single transaction.

    Args:
        order_id: The UUID of the order to reserve stock for.

    Returns:
        dict: Reservation details on success, or an error describing
              why the reservation could not be made.
    """
    oid = _normalize_order_id(order_id)

    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Lock the order row to prevent concurrent reservation attempts
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

            # Only PENDING or RESERVED orders can be reserved
            if order["status"] in ("PAID", "CANCELLED", "FAILED"):
                return {
                    "ok": False,
                    "error": "ORDER_NOT_RESERVABLE",
                    "status": order["status"],
                }

            # Get the first (and currently only) item in the order
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

            # Look up the product to get its internal ID
            prod = await conn.fetchrow(
                "SELECT id FROM products WHERE sku = $1;",
                sku,
            )

            if not prod:
                return {"ok": False, "error": "SKU_NOT_FOUND", "sku": sku}

            # Lock the stock row to prevent race conditions
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

            # Check there is enough stock to fulfill the order
            if current_qty < qty:
                return {
                    "ok": False,
                    "error": "INSUFFICIENT_STOCK",
                    "current_qty": current_qty,
                    "requested_qty": qty,
                }

            new_qty = current_qty - qty

            # Subtract the reserved quantity from stock
            await conn.execute(
                """
                UPDATE stock
                SET quantity = $2, updated_at = now()
                WHERE product_id = $1;
                """,
                prod["id"],
                new_qty,
            )

            # Log the stock movement with a negative delta (stock removed)
            await conn.execute(
                """
                INSERT INTO stock_movements (product_id, delta, reason)
                VALUES ($1, $2, $3);
                """,
                prod["id"],
                -qty,
                f"reserve_order:{oid}",
            )

            # Create the reservation record
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

            # Move the order to RESERVED status
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
    Release an active stock reservation and cancel the order.

    The reserved quantity is added back to the stock, the reservation is
    marked inactive, and the order status is set to CANCELLED.

    Args:
        order_id: The UUID of the order whose reservation should be released.

    Returns:
        dict: Confirmation with released=True if a reservation was found
              and released, or released=False if there was nothing to release.
    """
    oid = _normalize_order_id(order_id)

    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Find and lock the active reservation for this order
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

            # If no active reservation exists, nothing to release
            if not reservation:
                return {"ok": True, "released": False}

            sku = reservation["sku"]
            qty = reservation["qty"]

            prod = await conn.fetchrow(
                "SELECT id FROM products WHERE sku = $1;",
                sku,
            )

            # Lock the stock row for update
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
            new_qty = current_qty + qty  # Add the reserved quantity back

            # Restore the stock quantity
            await conn.execute(
                """
                UPDATE stock
                SET quantity = $2, updated_at = now()
                WHERE product_id = $1;
                """,
                prod["id"],
                new_qty,
            )

            # Mark the reservation as inactive
            await conn.execute(
                """
                UPDATE reservations
                SET active = FALSE, released_at = now()
                WHERE id = $1::uuid;
                """,
                reservation["id"],
            )

            # Cancel the order
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
    """
    Mark an order as PAID.

    Args:
        order_id: The UUID of the order to mark as paid.

    Returns:
        dict: Confirmation with the order ID and new status.
    """
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
    """
    Mark an order as FAILED.

    Args:
        order_id: The UUID of the order to mark as failed.

    Returns:
        dict: Confirmation with the order ID and new status.
    """
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
