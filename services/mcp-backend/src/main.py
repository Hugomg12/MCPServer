import anyio

from app.mcp_app import mcp
from app.db import close_pool

# Import tools so decorators register
import tools.health  # noqa: F401
import tools.db_admin  # noqa: F401
import tools.products  # noqa: F401
import tools.stock  # noqa: F401
import tools.orders  # noqa: F401


def main() -> None:
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=8000,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Stop server without ugly traceback
        pass
    finally:
        # Best-effort: close asyncpg pool if it was created
        try:
            anyio.run(close_pool)
        except Exception:
            pass
