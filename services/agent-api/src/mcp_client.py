from fastmcp import Client


def make_client(mcp_url: str, api_key: str) -> Client:
    return Client(mcp_url, auth=api_key)