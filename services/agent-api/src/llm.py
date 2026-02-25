import json
import logging
import re
from typing import Any, Callable, Dict, List, Tuple

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an operations assistant with access to inventory and order tools.
You MUST use the available tools to answer questions — never invent data.
Be concise and respond in the same language the user writes in.
"""


class ToolAgent:
    def __init__(self, api_key: str, model: str):
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
        )
        self.model = model

    async def run(
        self,
        user_message: str,
        tools: List[Dict[str, Any]],
        tool_executor: Callable,
        max_rounds: int = 6,
    ) -> Tuple[str, List[Dict[str, Any]]]:

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
        trace: List[Dict[str, Any]] = []

        for round_num in range(max_rounds):
            logger.info(f"LLM round {round_num + 1}")

            try:
                resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=openai_tools,
                    tool_choice="auto",
                    max_tokens=1024,
                    parallel_tool_calls=False,
                )
            except Exception as e:
                # Groq falla con tool_use_failed → reintentar sin tools
                logger.warning(f"Tool call failed: {e}. Retrying without tools.")
                resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=1024,
                )
                text = resp.choices[0].message.content or ""
                text = re.sub(r"<function>.*?</function>", "", text, flags=re.DOTALL).strip()
                if not text.strip() and trace:
                    last_result = trace[-1].get("tool_result", {}).get("result", "")
                    text = last_result
                return text, trace

            msg = resp.choices[0].message
            tool_calls = msg.tool_calls or []

            # Sin tool_calls → respuesta final
            if not tool_calls:
                text = msg.content or ""
                text = re.sub(r"<function>.*?</function>", "", text, flags=re.DOTALL).strip()
                if not text.strip() and trace:
                    last_result = trace[-1].get("tool_result", {}).get("result", "")
                    text = last_result
                return text, trace

            # Ejecutar cada tool call
            messages.append(msg)

            for tc in tool_calls:
                name = tc.function.name

                try:
                    input_data = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except Exception:
                    input_data = {}

                logger.info(f"Calling tool: {name} with {input_data}")
                trace.append({"tool_call": {"name": name, "input": input_data}})

                try:
                    result_str = await tool_executor(name, input_data)
                except Exception as e:
                    result_str = json.dumps({"ok": False, "error": str(e)})

                trace.append({"tool_result": {"name": name, "result": result_str}})

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

        return "Could not complete the task within the allowed tool-call rounds.", trace