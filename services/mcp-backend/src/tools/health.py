"""
Health-check tool for the MCP backend.

Exposes a simple tool that returns an "ok" status, which can be used by
monitoring systems or other services to verify the mcp-backend is running.
"""

from app.mcp_app import mcp


@mcp.tool
def health() -> dict:
    """
    Return a simple health-check response.

    Returns:
        dict: A dictionary with "ok" set to True and the service name.
    """
    return {"ok": True, "service": "agent-lab-mcp-backend"}
