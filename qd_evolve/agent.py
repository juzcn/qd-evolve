from __future__ import annotations

import json
from typing import Any, Callable

from loguru import logger

from qd_evolve.config import Settings
from qd_evolve.providers import ProviderRegistry
from qd_evolve.tools import ToolRegistry


class Agent:
    def __init__(self, settings: Settings, registry: ToolRegistry, providers: ProviderRegistry) -> None:
        self.settings = settings
        self.registry = registry
        self._active_tools: set[str] = set()
        # load_tool_detail and load_skill_detail are always active (need full schema)
        self._always_active: set[str] = {"load_tool_detail", "load_skill_detail"}
        self.providers = providers
        self.messages: list[dict[str, Any]] = []
        self._provider_name: str | None = None
        self._model: str | None = None
        self._api_type: str = "openai_completion"
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.last_input_tokens: int = 0
        self.last_output_tokens: int = 0
        self.iteration: int = 0
        self._on_status: Callable[[str], None] | None = None

    def set_status_callback(self, cb: Callable[[str], None]) -> None:
        self._on_status = cb

    def _update_status(self, text: str) -> None:
        if self._on_status:
            self._on_status(text)

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
        self.iteration = 0
        logger.info("User input: {}", user_input)

        prov = self.providers.get(self._provider_name)
        max_tokens = prov.get_max_tokens(self._model)
        self._api_type = prov.get_api_type(self._model)

        while True:
            self.iteration += 1
            self._update_status(f"Thinking... (iteration {self.iteration})")
            client = prov.create_client()
            active = self._active_tools | self._always_active
            logger.info(
                "LLM prompt: {} / {} ({}) | system={} tools={} messages={}",
                self._provider_name,
                self._model,
                self._api_type,
                system_prompt,
                json.dumps(self.registry.definitions(api_format=self._api_type if self._api_type != "openai_completion" else "openai", active_tools=active), ensure_ascii=False),
                json.dumps(self.messages, ensure_ascii=False),
            )

            if self._api_type == "anthropic":
                return self._run_anthropic(client, system_prompt, max_tokens)
            elif self._api_type == "openai_completion":
                return self._run_openai_completion(client, system_prompt, max_tokens)
            elif self._api_type == "openai_response":
                return self._run_openai_response(client, system_prompt, max_tokens)
            else:
                raise ValueError(f"Unsupported api_type: {self._api_type}")

    MAX_ITERATIONS = 20

    def _run_anthropic(self, client: Any, system_prompt: str, max_tokens: int, _iter: int = 0) -> str:
        response = client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=self.messages,
            tools=self.registry.definitions(api_format="anthropic", active_tools=self._active_tools | self._always_active),
        )
        self._track_tokens_anthropic(response.usage)

        self.messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            text = self._extract_text(response.content)
            logger.info("LLM completion: {}", text[:500])
            return text

        results = self._execute_tools_anthropic(response.content)
        self.messages.append({"role": "user", "content": results})
        if _iter >= self.MAX_ITERATIONS:
            return "Max tool iterations reached. Please simplify your request."
        self.iteration += 1
        self._update_status(f"Thinking... (iteration {self.iteration})")
        return self._run_anthropic(client, system_prompt, max_tokens, _iter + 1)

    def _run_openai_completion(self, client: Any, system_prompt: str, max_tokens: int, _iter: int = 0) -> str:
        openai_messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        openai_messages.extend(self.messages)

        tool_defs = self.registry.definitions("openai", active_tools=self._active_tools | self._always_active)
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
                logger.info("Tool call: {}({})", tc.function.name, json.dumps(args, ensure_ascii=False))
                self._update_status(f"Running tool: {tc.function.name} (iteration {self.iteration})")
                output = self.registry.call(tc.function.name, **args)
                logger.info("Tool result: {} -> {}", tc.function.name, output[:200])
                self._activate_tool(tc.function.name, args)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": output,
                })
            if _iter >= self.MAX_ITERATIONS:
                return "Max tool iterations reached. Please simplify your request."
            self.iteration += 1
            self._update_status(f"Thinking... (iteration {self.iteration})")
            return self._run_openai_completion(client, system_prompt, max_tokens, _iter + 1)

        self.messages.append({"role": "assistant", "content": msg.content or ""})
        logger.info("LLM completion: {}", (msg.content or "")[:500])
        return msg.content or ""

    def _run_openai_response(self, client: Any, system_prompt: str, max_tokens: int, _iter: int = 0) -> str:
        response = client.responses.create(
            model=self._model,
            instructions=system_prompt,
            input=self.messages,
            max_output_tokens=max_tokens,
            tools=self.registry.definitions("openai-response", active_tools=self._active_tools | self._always_active),
        )
        self._track_tokens_openai_response(response.usage)

        for item in response.output:
            if item.type == "function_call":
                args = json.loads(item.arguments)
                logger.info("Tool call: {}({})", item.name, json.dumps(args, ensure_ascii=False))
                self._update_status(f"Running tool: {item.name} (iteration {self.iteration})")
                result = self.registry.call(item.name, **args)
                logger.info("Tool result: {} -> {}", item.name, result[:200])
                self._activate_tool(item.name, args)
                self.messages.append({"role": "assistant", "content": None, "tool_calls": [item]})
                self.messages.append({
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": result,
                })
                self.iteration += 1
                self._update_status(f"Thinking... (iteration {self.iteration})")
                return self._run_openai_response(client, system_prompt, max_tokens)

        text_parts = [item.content[0].text for item in response.output if item.type == "message"]
        return "\n".join(text_parts)

    def _track_tokens_anthropic(self, usage: Any) -> None:
        self.last_input_tokens = usage.input_tokens
        self.last_output_tokens = usage.output_tokens
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        logger.info("Token usage: input={}, output={}, total={}", usage.input_tokens, usage.output_tokens, self.total_tokens)

    def _track_tokens_openai_completion(self, usage: Any) -> None:
        self.last_input_tokens = usage.prompt_tokens
        self.last_output_tokens = usage.completion_tokens
        self.total_input_tokens += usage.prompt_tokens
        self.total_output_tokens += usage.completion_tokens
        logger.info("Token usage: input={}, output={}, total={}", usage.prompt_tokens, usage.completion_tokens, self.total_tokens)

    def _track_tokens_openai_response(self, usage: Any) -> None:
        self.last_input_tokens = usage.input_tokens
        self.last_output_tokens = usage.output_tokens
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        logger.info("Token usage: input={}, output={}, total={}", usage.input_tokens, usage.output_tokens, self.total_tokens)

    def _activate_tool(self, tool_name: str) -> None:
        """When load_tool_detail is called, activate that tool for full schema in subsequent turns."""
        if tool_name == "load_tool_detail":
            # The argument to load_tool_detail is the tool name to activate
            # We need to extract it from the last tool call
            pass
        else:
            self._active_tools.add(tool_name)

    def _activate_tool(self, tool_name: str, tool_args: dict) -> None:
        """After a tool call, activate tools as needed for full schema in subsequent turns."""
        self._active_tools.add(tool_name)
        if tool_name == "load_tool_detail":
            target = tool_args.get("name", "")
            if target:
                self._active_tools.add(target)
                logger.debug(f"Activated tool: {target}")

    def _execute_tools_anthropic(self, content: list) -> list[dict]:
        results: list[dict] = []
        for block in content:
            if block.type == "tool_use":
                logger.info("Tool call: {}({})", block.name, json.dumps(block.input, ensure_ascii=False))
                self._update_status(f"Running tool: {block.name} (iteration {self.iteration})")
                output = self.registry.call(block.name, **block.input)
                logger.info("Tool result: {} -> {}", block.name, output[:200])
                # Activate tool after call
                self._activate_tool(block.name, block.input)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
        return results

    
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