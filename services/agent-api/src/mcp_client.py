"""
MCP client factory.

Provides a helper function that creates a FastMCP Client instance
pre-configured with the MCP backend URL and authentication credentials.
This client is used by the agent-api to discover available tools and
to execute tool calls on the MCP backend.
"""

from fastmcp import Client


def make_client(mcp_url: str, api_key: str) -> Client:
    """
    Create and return a new FastMCP Client.

    Args:
        mcp_url: The full URL of the MCP backend endpoint (e.g. http://mcp-backend:8000/mcp).
        api_key: The Bearer token used to authenticate requests to the MCP backend.

    Returns:
        Client: A configured FastMCP client ready to be used as an async context manager.
    """
    return Client(mcp_url, auth=api_key)