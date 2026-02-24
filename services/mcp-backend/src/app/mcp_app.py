from fastmcp import FastMCP

# Auth se gestiona a nivel HTTP (Starlette middleware en main.py)
# con whitelist de red interna Docker.
mcp = FastMCP("agent-lab-mcp-backend")
