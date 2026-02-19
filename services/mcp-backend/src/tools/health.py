from app.mcp_app import mcp


@mcp.tool
def health() -> dict:
    return {"ok": True, "service": "agent-lab-mcp-backend"}
