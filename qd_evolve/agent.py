from __future__ import annotations

import json
from typing import Any

from loguru import logger

from qd_evolve.config import Settings
from qd_evolve.providers import ProviderRegistry
from qd_evolve.tools import ToolRegistry


class Agent:
    def __init__(self, settings: Settings, registry: ToolRegistry, providers: ProviderRegistry) -> None:
        self.settings = settings
        self.registry = registry
        self.providers = providers
        self.messages: list[dict[str, Any]] = []
        self._provider_name: str | None = None
        self._model: str | None = None
        self._api_type: str = "openai_completion"
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def run(
        self,
        user_input: str,
        system: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> str:
        self._provider_name = provider or self.settings.default_provider
        self._model = model or self.settings.default_model
        system_prompt = system or self.settings.default_system_prompt
        self.messages.append({"role": "user", "content": user_input})
        logger.info("User input: {}", user_input)

        prov = self.providers.get(self._provider_name)
        max_tokens = prov.get_max_tokens(self._model)
        self._api_type = prov.get_api_type(self._model)

        while True:
            client = prov.create_client()
            logger.info(
                "LLM prompt: {} / {} ({}) | {} messages",
                self._provider_name,
                self._model,
                self._api_type,
                len(self.messages),
            )
            logger.info("LLM prompt messages: {}", self.messages)

            if self._api_type == "anthropic":
                return self._run_anthropic(client, system_prompt, max_tokens)
            elif self._api_type == "openai_completion":
                return self._run_openai_completion(client, system_prompt, max_tokens)
            elif self._api_type == "openai_response":
                return self._run_openai_response(client, system_prompt, max_tokens)
            else:
                raise ValueError(f"Unsupported api_type: {self._api_type}")

    def _run_anthropic(self, client: Any, system_prompt: str, max_tokens: int) -> str:
        response = client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=self.messages,
            tools=self.registry.definitions(),
        )
        self._track_tokens_anthropic(response.usage)

        self.messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            text = self._extract_text(response.content)
            logger.info("LLM completion: {}", text[:500])
            return text

        results = self._execute_tools_anthropic(response.content)
        self.messages.append({"role": "user", "content": results})
        return self._run_anthropic(client, system_prompt, max_tokens)

    def _run_openai_completion(self, client: Any, system_prompt: str, max_tokens: int) -> str:
        openai_messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        openai_messages.extend(self.messages)

        tool_defs = self._openai_tool_definitions()
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
        }
        if tool_defs:
            kwargs["tools"] = tool_defs

        response = client.chat.completions.create(**kwargs)
        self._track_tokens_openai_completion(response.usage)

        choice = response.choices[0]
        msg = choice.message

        if msg.tool_calls:
            self.messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                output = self.registry.execute(tc.function.name, **args)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": output,
                })
            return self._run_openai_completion(client, system_prompt, max_tokens)

        self.messages.append({"role": "assistant", "content": msg.content or ""})
        logger.info("LLM completion: {}", (msg.content or "")[:500])
        return msg.content or ""

    def _run_openai_response(self, client: Any, system_prompt: str, max_tokens: int) -> str:
        response = client.responses.create(
            model=self._model,
            instructions=system_prompt,
            input=self.messages,
            max_output_tokens=max_tokens,
            tools=self._openai_response_tool_definitions(),
        )
        self._track_tokens_openai_response(response.usage)

        for item in response.output:
            if item.type == "function_call":
                result = self.registry.execute(item.name, **json.loads(item.arguments))
                self.messages.append({"role": "assistant", "content": None, "tool_calls": [item]})
                self.messages.append({
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": result,
                })
                return self._run_openai_response(client, system_prompt, max_tokens)

        text_parts = [item.content[0].text for item in response.output if item.type == "message"]
        return "\n".join(text_parts)

    def _track_tokens_anthropic(self, usage: Any) -> None:
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        logger.info("Token usage: input={}, output={}, total={}", usage.input_tokens, usage.output_tokens, self.total_tokens)

    def _track_tokens_openai_completion(self, usage: Any) -> None:
        self.total_input_tokens += usage.prompt_tokens
        self.total_output_tokens += usage.completion_tokens
        logger.info("Token usage: input={}, output={}, total={}", usage.prompt_tokens, usage.completion_tokens, self.total_tokens)

    def _track_tokens_openai_response(self, usage: Any) -> None:
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        logger.info("Token usage: input={}, output={}, total={}", usage.input_tokens, usage.output_tokens, self.total_tokens)

    def _execute_tools_anthropic(self, content: list) -> list[dict]:
        results: list[dict] = []
        for block in content:
            if block.type == "tool_use":
                output = self.registry.execute(block.name, **block.input)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
        return results

    def _openai_tool_definitions(self) -> list[dict]:
        defs = self.registry.definitions()
        return [
            {
                "type": "function",
                "function": {
                    "name": d["name"],
                    "description": d["description"],
                    "parameters": d["input_schema"],
                },
            }
            for d in defs
        ]

    def _openai_response_tool_definitions(self) -> list[dict]:
        defs = self.registry.definitions()
        return [
            {
                "type": "function",
                "name": d["name"],
                "description": d["description"],
                "parameters": d["input_schema"],
            }
            for d in defs
        ]

    @staticmethod
    def _extract_text(content: list) -> str:
        parts: list[str] = []
        for block in content:
            if block.type == "text":
                parts.append(block.text)
        return "\n".join(parts)

    def reset(self) -> None:
        self.messages.clear()
        self.total_input_tokens = 0
        self.total_output_tokens = 0