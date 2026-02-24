import anyio
import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.mcp_app import mcp
from app.config import MCP_API_KEY
from app.db import close_pool

import tools.health   # noqa: F401
import tools.db_admin # noqa: F401
import tools.products # noqa: F401
import tools.stock    # noqa: F401
import tools.orders   # noqa: F401

# IPs internas de Docker → no requieren auth
_TRUSTED_PREFIXES = ("172.", "10.", "192.168.", "127.0.0.1")


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if MCP_API_KEY:
            client_ip = request.client.host if request.client else ""
            is_internal = client_ip.startswith(_TRUSTED_PREFIXES)
            if not is_internal:
                auth = request.headers.get("authorization", "")
                if auth != f"Bearer {MCP_API_KEY}":
                    return JSONResponse(
                        {"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32001, "message": "Unauthorized"}},
                        status_code=401,
                    )
        return await call_next(request)


def main() -> None:
    # Obtener la app ASGI de FastMCP y envolverla con el middleware
    asgi_app = mcp.http_app(transport="http")
    asgi_app.add_middleware(ApiKeyMiddleware)

    uvicorn.run(asgi_app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            anyio.run(close_pool)
        except Exception:
            pass