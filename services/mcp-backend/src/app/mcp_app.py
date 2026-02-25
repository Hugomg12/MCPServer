"""
MCP application instance.

Creates and exports the single FastMCP application object used by the entire
mcp-backend service. All MCP tools (health, products, stock, orders, etc.)
register themselves on this instance via the @mcp.tool decorator.

Note: Authentication is NOT handled here — it is managed at the HTTP layer
by a Starlette middleware defined in main.py, combined with a Docker
internal-network whitelist.
"""

from fastmcp import FastMCP

# The shared FastMCP application instance.
# Tool modules import this object and use @mcp.tool to register their functions.
mcp = FastMCP("agent-lab-mcp-backend")
