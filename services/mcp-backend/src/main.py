"""
Entry point for the mcp-backend service.

Starts a Uvicorn HTTP server that serves the FastMCP application.
Before the app is ready, it:
  1. Imports all tool modules so they register their MCP tools.
  2. Wraps the ASGI app with an API-key authentication middleware
     that lets Docker-internal traffic through without a key.
"""

import anyio
import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.mcp_app import mcp
from app.config import MCP_API_KEY
from app.db import close_pool

# Import tool modules so their @mcp.tool decorators run at startup.
# The "noqa: F401" comments tell linters these imports are intentional
# even though the modules are not used directly in this file.
import tools.health   # noqa: F401
import tools.db_admin # noqa: F401
import tools.products # noqa: F401
import tools.stock    # noqa: F401
import tools.orders   # noqa: F401

# IP prefixes that belong to Docker internal networks.
# Requests from these IPs skip API-key authentication.
_TRUSTED_PREFIXES = ("172.", "10.", "192.168.", "127.0.0.1")


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that enforces Bearer-token authentication.

    Requests originating from trusted Docker-internal IPs are allowed
    through without a token. All other requests must include a valid
    'Authorization: Bearer <key>' header.
    """

    async def dispatch(self, request, call_next):
        """
        Intercept every incoming HTTP request and verify authentication.

        Args:
            request: The incoming HTTP request.
            call_next: Callable that forwards the request to the next handler.

        Returns:
            The response from the next handler, or a 401 JSON error.
        """
        if MCP_API_KEY:
            client_ip = request.client.host if request.client else ""
            is_internal = client_ip.startswith(_TRUSTED_PREFIXES)
            if not is_internal:
                auth = request.headers.get("authorization", "")
                if auth != f"Bearer {MCP_API_KEY}":
                    # Reject with a JSON-RPC-style error so MCP clients
                    # can parse the failure programmatically.
                    return JSONResponse(
                        {"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32001, "message": "Unauthorized"}},
                        status_code=401,
                    )
        return await call_next(request)


def main() -> None:
    """
    Build the ASGI application from FastMCP, attach the auth middleware,
    and start the Uvicorn server on port 8000.
    """
    # Get the ASGI app that FastMCP generates and add the auth layer
    asgi_app = mcp.http_app(transport="http")
    asgi_app.add_middleware(ApiKeyMiddleware)

    uvicorn.run(asgi_app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    finally:
        # Ensure the database pool is closed cleanly on shutdown,
        # even if the server was interrupted.
        try:
            anyio.run(close_pool)
        except Exception:
            pass