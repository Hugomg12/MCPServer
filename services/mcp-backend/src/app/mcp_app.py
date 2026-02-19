from fastmcp import FastMCP

from app.auth_middleware import ApiKeyBearerAuthMiddleware
from app.config import MCP_API_KEY

mcp = FastMCP("agent-lab-mcp-backend")

mcp.add_middleware(ApiKeyBearerAuthMiddleware(api_key=MCP_API_KEY))
