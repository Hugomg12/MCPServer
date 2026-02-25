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

_cached_tools: List[Dict[str, Any]] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
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

    yield


app = FastAPI(title="agent-api", lifespan=lifespan)
agent = ToolAgent(settings.GROQ_API_KEY, settings.GROQ_MODEL)


class ChatIn(BaseModel):
    message: str


class ChatOut(BaseModel):
    answer: str
    trace: list


@app.get("/health")
async def health():
    return {
        "ok": True,
        "service": "agent-api",
        "tools_loaded": len(_cached_tools),
    }


@app.post("/chat", response_model=ChatOut)
async def chat(payload: ChatIn):
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="message is empty")

    if not _cached_tools:
        raise HTTPException(status_code=503, detail="MCP tools not available")

    client = make_client(settings.MCP_URL, settings.MCP_API_KEY)

    async with client:
        async def execute_tool(name: str, input_data: Dict[str, Any]) -> str:
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