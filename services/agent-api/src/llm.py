"""
LLM tool-calling agent.

Contains the ToolAgent class that orchestrates a multi-turn conversation
with an OpenAI-compatible LLM (served by Groq). The agent sends the user's
message along with the available MCP tool definitions, then enters a loop
where it:
  1. Asks the LLM for a response.
  2. If the LLM requests tool calls, executes them via a callback.
  3. Feeds the tool results back to the LLM.
  4. Repeats until the LLM produces a final text answer or the maximum
     number of rounds is reached.
"""

import json
import logging
import re
from typing import Any, Callable, Dict, List, Tuple

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# System prompt that sets the LLM's behavior and constraints
SYSTEM_PROMPT = """You are an operations assistant with access to inventory and order tools.
You MUST use the available tools to answer questions — never invent data.
Be concise and respond in the same language the user writes in.
"""


class ToolAgent:
    """
    An agent that connects to an OpenAI-compatible LLM and can execute
    tools in a multi-round loop until a final answer is produced.
    """

    def __init__(self, api_key: str, model: str):
        """
        Initialize the agent with Groq API credentials.

        Args:
            api_key: API key for the Groq service.
            model:   Name of the LLM model to use.
        """
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
        """
        Run the agent loop: send the user message to the LLM, execute any
        requested tools, and repeat until a final text answer is produced.

        Args:
            user_message:  The user's input text.
            tools:         List of tool definitions (name, description, input_schema).
            tool_executor: Async callable that runs a tool by name and input dict,
                           returning a JSON string with the result.
            max_rounds:    Maximum number of LLM request rounds (default 6).

        Returns:
            A tuple of (final_answer_text, trace) where trace is a list of
            dicts recording every tool call and result for debugging.
        """

        # Convert tool definitions to the OpenAI function-calling format
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

        # Build the initial conversation with system prompt and user message
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
        trace: List[Dict[str, Any]] = []  # Records all tool interactions

        for round_num in range(max_rounds):
            logger.info(f"LLM round {round_num + 1}")

            try:
                resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=openai_tools,
                    tool_choice="auto",
                    max_tokens=1024,
                    parallel_tool_calls=False,  # Execute one tool at a time
                )
            except Exception as e:
                # If Groq returns a tool_use_failed error, retry without tools
                # so the LLM can still produce a text response
                logger.warning(f"Tool call failed: {e}. Retrying without tools.")
                resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=1024,
                )
                text = resp.choices[0].message.content or ""
                # Remove any raw <function> XML tags the LLM may hallucinate
                text = re.sub(r"<function>.*?</function>", "", text, flags=re.DOTALL).strip()
                # If the LLM returned empty text, fall back to the last tool result
                if not text.strip() and trace:
                    last_result = trace[-1].get("tool_result", {}).get("result", "")
                    text = last_result
                return text, trace

            msg = resp.choices[0].message
            tool_calls = msg.tool_calls or []

            # No tool calls means the LLM has produced its final answer
            if not tool_calls:
                text = msg.content or ""
                # Clean up any hallucinated function tags
                text = re.sub(r"<function>.*?</function>", "", text, flags=re.DOTALL).strip()
                # Fall back to last tool result if the answer is empty
                if not text.strip() and trace:
                    last_result = trace[-1].get("tool_result", {}).get("result", "")
                    text = last_result
                return text, trace

            # The LLM wants to call one or more tools — execute each one
            messages.append(msg)

            for tc in tool_calls:
                name = tc.function.name

                # Parse the tool arguments from the JSON string
                try:
                    input_data = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except Exception:
                    input_data = {}

                logger.info(f"Calling tool: {name} with {input_data}")
                trace.append({"tool_call": {"name": name, "input": input_data}})

                # Execute the tool via the provided callback
                try:
                    result_str = await tool_executor(name, input_data)
                except Exception as e:
                    result_str = json.dumps({"ok": False, "error": str(e)})

                trace.append({"tool_result": {"name": name, "result": result_str}})

                # Feed the tool result back to the LLM for the next round
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

        # If we exhausted all rounds without a final answer, return an error message
        return "Could not complete the task within the allowed tool-call rounds.", trace