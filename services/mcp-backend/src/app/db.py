"""
Database connection pool module.

Manages a single shared asyncpg connection pool for the mcp-backend service.
Other modules call `get_pool()` to obtain the pool (creating it lazily on
first use) and `close_pool()` to shut it down gracefully when the
application exits.
"""

import asyncpg
from typing import Optional
from app.config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

# Module-level variable that holds the single shared connection pool.
# It starts as None and is created on the first call to get_pool().
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """
    Return the shared database connection pool, creating it if it does
    not exist yet (lazy initialization).

    Returns:
        asyncpg.Pool: The active connection pool.
    """
    global _pool

    if _pool is None:
        # Create a new pool with a minimum of 1 and maximum of 5 connections
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


async def close_pool() -> None:
    """
    Close the shared connection pool and release all database connections.
    Safe to call even if the pool was never created.
    """
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
