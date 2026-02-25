"""
Entry point for the agent-api service.

This FastAPI application acts as a bridge between end users and the
MCP backend. On startup it connects to the MCP backend to discover
available tools. When a user sends a chat message, the service:
  1. Opens a new MCP client session.
  2. Passes the message and tool definitions to the ToolAgent (LLM).
  3. The LLM may call MCP tools and reason over the results.
  4. Returns the final answer along with a trace of tool calls.
"""

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from settings import settings
from mcp_client import make_client
from llm import ToolAgent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cache of tool definitions fetched from the MCP backend at startup.
# Stored as a module-level list so it's available to the /chat endpoint.
_cached_tools: List[Dict[str, Any]] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan handler — runs once at startup before the
    server begins accepting requests.

    Connects to the MCP backend, fetches the list of available tools,
    and caches their definitions for later use by the chat endpoint.
    """
    global _cached_tools
    logger.info("Connecting to MCP to fetch tools...")
    try:
        client = make_client(settings.MCP_URL, settings.MCP_API_KEY)
        async with client:
            tools = await client.list_tools()
            _cached_tools = [
                {
                    "name": t.name,
                    "description": t.description or f"Execute {t.name}",
                    # If the tool has no input parameters, provide an empty schema
                    "input_schema": (
                        t.inputSchema
                        if (t.inputSchema and t.inputSchema.get("properties"))
                        else {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        }
                    ),
                }
                for t in tools
            ]
        logger.info(f"Loaded {len(_cached_tools)} tools: {[t['name'] for t in _cached_tools]}")
    except Exception as e:
        logger.error(f"Failed to load tools from MCP: {e}")

    yield  # Application runs while this generator is suspended


app = FastAPI(title="agent-api", lifespan=lifespan)
agent = ToolAgent(settings.GROQ_API_KEY, settings.GROQ_MODEL)


class ChatIn(BaseModel):
    """Request body for the /chat endpoint."""
    message: str


class ChatOut(BaseModel):
    """Response body for the /chat endpoint."""
    answer: str
    trace: list


@app.get("/health")
async def health():
    """
    Simple health-check endpoint.

    Returns:
        dict: Service status and the number of MCP tools loaded.
    """
    return {
        "ok": True,
        "service": "agent-api",
        "tools_loaded": len(_cached_tools),
    }


@app.post("/chat", response_model=ChatOut)
async def chat(payload: ChatIn):
    """
    Main chat endpoint — receives a user message, runs the LLM agent
    with the available MCP tools, and returns the answer.

    Args:
        payload: A ChatIn object containing the user's message.

    Returns:
        ChatOut: The LLM's final answer and the full trace of tool calls.

    Raises:
        HTTPException 400: If the message is empty.
        HTTPException 503: If no MCP tools are available.
    """
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="message is empty")

    if not _cached_tools:
        raise HTTPException(status_code=503, detail="MCP tools not available")

    # Open a fresh MCP client session for this request
    client = make_client(settings.MCP_URL, settings.MCP_API_KEY)

    async with client:
        async def execute_tool(name: str, input_data: Dict[str, Any]) -> str:
            """Call a tool on the MCP backend and return its result as JSON."""
            res = await client.call_tool(name, input_data or {})
            try:
                return json.dumps(res.data, ensure_ascii=False)
            except Exception:
                return str(res)

        answer, trace = await agent.run(
            user_message=payload.message,
            tools=_cached_tools,
            tool_executor=execute_tool,
        )

    return ChatOut(answer=answer, trace=trace)