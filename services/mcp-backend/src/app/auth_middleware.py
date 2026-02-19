import secrets

from mcp import McpError
from mcp.types import ErrorData

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext


class ApiKeyBearerAuthMiddleware(Middleware):
    def __init__(self, api_key: str):
        self.api_key = api_key

    def _is_authorized(self) -> bool:
        headers = get_http_headers() or {}
        auth = headers.get("authorization") or headers.get("Authorization")
        if not auth:
            return False

        prefix = "Bearer "
        if not auth.startswith(prefix):
            return False

        token = auth[len(prefix) :].strip()
        return bool(token) and secrets.compare_digest(token, self.api_key)

    async def __call__(self, context: MiddlewareContext, call_next):
        if not self.api_key:
            return self._deny(context, "Server misconfigured: MCP_API_KEY is not set")

        if not self._is_authorized():
            return self._deny(
                context,
                "Unauthorized: missing or invalid Authorization Bearer token",
            )

        return await call_next(context)

    def _deny(self, context: MiddlewareContext, message: str):
        method = getattr(context, "method", "") or ""
        if method == "tools/call":
            raise ToolError(message)

        # Para list_tools / initialize / ping / etc.
        raise McpError(ErrorData(code=-32001, message=message))
