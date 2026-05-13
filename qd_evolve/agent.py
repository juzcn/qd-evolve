from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable

from qd_evolve.logger import logger

from qd_evolve.config import Settings
from qd_evolve.memory import RecalledMemoryRegistry
from qd_evolve.providers import ProviderRegistry
from qd_evolve.tools import ToolRegistry

if TYPE_CHECKING:
    from qd_evolve.memory import MemoryStore


class Agent:
    def __init__(self, settings: Settings, registry: ToolRegistry, providers: ProviderRegistry,
                 memory: MemoryStore | None = None, default_system_prompt: str = "",
                 preload_tools: set[str] | None = None,
                 preload_skills: set[str] | None = None,
                 preload_cli: set[str] | None = None) -> None:
        self.settings = settings
        self.registry = registry
        self.default_system_prompt = default_system_prompt
        self._active_tools: set[str] = set()
        self._always_active: set[str] = preload_tools or set()
        self.providers = providers
        self.memory = memory
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
        self._on_print: Callable[[str], None] | None = None
        self._recalled = RecalledMemoryRegistry()
        self._loaded_skills: dict[str, str] = {}
        self._loaded_cli: dict[str, str] = {}
        self._preload_skills: set[str] = preload_skills or set()
        self._preload_cli: set[str] = preload_cli or set()

    def set_status_callback(self, cb: Callable[[str], None]) -> None:
        self._on_status = cb

    def set_print_callback(self, cb: Callable[[str], None]) -> None:
        self._on_print = cb

    def _update_status(self, text: str) -> None:
        if self._on_status:
            self._on_status(f"[#{self.iteration}] {text}")

    def _print(self, text: str) -> None:
        if self._on_print:
            self._on_print(text)

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
        system_prompt = system or self.default_system_prompt
        self.messages.append({"role": "user", "content": user_input})
        self.iteration = 0
        self._log_limit = self.settings.log.truncation
        logger.info("Agent: user input: %s", user_input)

        # Auto recall: inject relevant memory into system prompt
        system_prompt = self._auto_recall(user_input, system_prompt)
        system_prompt = self._inject_loaded_content(system_prompt)

        prov = self.providers.get(self._provider_name)
        max_tokens = prov.get_max_tokens(self._model)
        self._api_type = prov.get_api_type(self._model)

        while True:
            self.iteration += 1
            client = prov.create_client()
            active = self._active_tools | self._always_active
            msg = self._format_messages_log()
            logger.debug("Agent: === LLM Request #%s === Provider: %s / %s (%s), Active tools: %s\n"
                        "--- Messages ---\n  %s",
                        self.iteration, self._provider_name, self._model, self._api_type, active,
                        self._trunc(msg))

            if self._api_type == "anthropic":
                result = self._run_anthropic(client, system_prompt, max_tokens)
            elif self._api_type == "openai_completion":
                result = self._run_openai_completion(client, system_prompt, max_tokens)
            elif self._api_type == "openai_response":
                result = self._run_openai_response(client, system_prompt, max_tokens)
            else:
                raise ValueError(f"Unsupported api_type: {self._api_type}")

            if self.memory:
                self._compress_messages()
                self.memory.save(user_input, result)
            return result

    def _compress_messages(self) -> None:
        prov = self.providers.get(self._provider_name)
        context_window = prov.get_context_window(self._model)
        if context_window <= 0:
            return

        compress_threshold = self.settings.memory_search.compress_threshold
        target_threshold = self.settings.memory_search.target_threshold
        current_ratio = self.last_input_tokens / context_window

        if current_ratio <= compress_threshold:
            return

        target_tokens = int(context_window * target_threshold)
        total_chars = sum(len(str(m.get("content", ""))) for m in self.messages)
        if total_chars == 0:
            return

        chars_per_token = total_chars / self.last_input_tokens
        removed = 0

        while self.messages and self.last_input_tokens > target_tokens:
            # Remove user message
            if self.messages and self.messages[0].get("role") == "user":
                self.messages.pop(0)
                removed += 1
            # Remove assistant message (may include tool_calls)
            if self.messages and self.messages[0].get("role") == "assistant":
                self.messages.pop(0)
                removed += 1
            # Remove tool result if present
            while self.messages and self.messages[0].get("role") == "tool":
                self.messages.pop(0)
                removed += 1

            # Re-estimate tokens based on remaining chars
            remaining_chars = sum(len(str(m.get("content", ""))) for m in self.messages)
            self.last_input_tokens = int(remaining_chars / chars_per_token)

        if self.memory:
            self.memory.new_session()

        logger.info(
            "Agent: context compressed: removed %s messages, %s -> %s tokens (target=%s)",
            removed, int(current_ratio * context_window), self.last_input_tokens, target_tokens,
        )

    def _trunc(self, text: str) -> str:
        """Truncate for logging. 0 means no limit."""
        if self._log_limit <= 0:
            return text
        if len(text) > self._log_limit:
            return text[:self._log_limit] + "..."
        return text

    def _inject_loaded_content(self, system_prompt: str) -> str:
        """Inject loaded skill/CLI content and remove them from unloaded sections."""
        updated = False

        if self._loaded_skills:
            skills_content = "\n".join(self._loaded_skills.values())
            marker = "## Loaded skills details: SKILL.md — follow these instructions."
            if marker in system_prompt:
                parts = system_prompt.split(marker, 1)
                after = parts[1]
                next_section = after.find("\n## ")
                if next_section != -1:
                    after = after[next_section:]
                else:
                    after = ""
                system_prompt = parts[0] + marker + "\n" + skills_content + after
                updated = True
            system_prompt = self._remove_from_unloaded(
                system_prompt,
                "## Unloaded Skills Summary",
                self._loaded_skills.keys(),
            )

        if self._loaded_cli:
            cli_content = "\n".join(self._loaded_cli.values())
            marker = "## Loaded CLI details: Help — use them to construct correct command arguments."
            if marker in system_prompt:
                parts = system_prompt.split(marker, 1)
                after = parts[1]
                next_section = after.find("\n## ")
                if next_section != -1:
                    after = after[next_section:]
                else:
                    after = ""
                system_prompt = parts[0] + marker + "\n" + cli_content + after
                updated = True
            system_prompt = self._remove_from_unloaded(
                system_prompt,
                "## Unloaded CLI Tools Summary",
                self._loaded_cli.keys(),
            )

        if updated:
            logger.debug("Agent: system prompt updated — %d loaded skills, %d loaded cli (%d chars)",
                         len(self._loaded_skills), len(self._loaded_cli), len(system_prompt))

        # Also remove activated tools from unloaded section
        if self._active_tools:
            system_prompt = self._remove_from_unloaded(
                system_prompt,
                "## Unloaded Tools Summary",
                self._active_tools,
            )
            logger.debug("Agent: removed %d active tools from unloaded section", len(self._active_tools))

        return system_prompt

    @staticmethod
    def _remove_from_unloaded(text: str, section_header: str, loaded_names: Any) -> str:
        """Remove lines matching loaded_names from an unloaded section."""
        idx = text.find(section_header)
        if idx < 0:
            return text
        # Find end of section (next ## or end of text)
        end = text.find("\n## ", idx + len(section_header))
        if end < 0:
            end = len(text)
        section = text[idx:end]
        lines = section.split("\n")
        # Keep header line, filter out lines starting with "- name:"
        kept = [lines[0]]  # header
        for line in lines[1:]:
            stripped = line.strip()
            if stripped.startswith("- "):
                name = stripped[2:].split(":")[0].strip()
                if name in loaded_names:
                    continue
            kept.append(line)
        # If only header remains, remove entire section
        if len(kept) <= 1:
            # Remove the section + trailing newlines
            before = text[:idx].rstrip("\n") + "\n"
            after = text[end:]
            return before + after.lstrip("\n")
        new_section = "\n".join(kept)
        return text[:idx] + new_section + text[end:]

    def _auto_recall(self, user_input: str, system_prompt: str) -> str:
        if not self.memory or not self.settings.memory_search.auto_recall:
            return system_prompt

        entries = self.memory.recall(query=user_input, limit=self.settings.memory_search.auto_recall_top_k)
        new_entries = self._recalled.add(entries)
        if not new_entries:
            return system_prompt

        logger.info("Agent: auto-recalled %s new memory entries for query: %s", len(new_entries), user_input[:50])
        for entry in new_entries:
            u = entry.user_msg.replace("\n", " ")[:60]
            a = entry.assistant_msg.replace("\n", " ")[:60]
            logger.info("Agent:   Memory [%s] user: %s | assistant: %s (distance: %s)",
                        entry.session_id, u, a, entry.distance)

        # Rebuild entire memory section from registry
        memory_text = self._recalled.format_section()
        marker = "\n## Relevant Past Conversations\n"
        next_section = "\n## "
        idx = system_prompt.find(marker)
        if idx >= 0:
            end = system_prompt.find(next_section, idx + len(marker))
            if end < 0:
                end = len(system_prompt)
            system_prompt = system_prompt[:idx] + marker + memory_text + "\n" + system_prompt[end:]
        else:
            first = system_prompt.find(next_section)
            if first >= 0:
                system_prompt = system_prompt[:first] + marker + memory_text + "\n" + system_prompt[first:]
            else:
                system_prompt += marker + memory_text + "\n"

        return system_prompt

    def _run_anthropic(self, client: Any, system_prompt: str, max_tokens: int, _iter: int = 0) -> str:
        if _iter > 0:
            logger.debug("Agent: === LLM Request #%s (tool) ===", self.iteration)
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
            logger.debug("Agent:\n=== LLM Response ===\n%s", self._format_completion_log(response))
            return text

        results = self._execute_tools_anthropic(response.content)
        self.messages.append({"role": "user", "content": results})
        if _iter >= self.settings.max_iterations:
            return "Max tool iterations reached. Please simplify your request."
        self.iteration += 1
        system_prompt = self._inject_loaded_content(system_prompt)
        return self._run_anthropic(client, system_prompt, max_tokens, _iter + 1)

    def _run_openai_completion(self, client: Any, system_prompt: str, max_tokens: int, _iter: int = 0) -> str:
        if _iter > 0:
            logger.debug("Agent: === LLM Request #%s (tool) ===", self.iteration)
        openai_messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        openai_messages.extend(self.messages)

        tool_defs = self.registry.definitions("openai", active_tools=self._active_tools | self._always_active)
        logger.debug("Agent: tool defs for API (count=%s): %s", len(tool_defs),
                     self._trunc(json.dumps(tool_defs, ensure_ascii=False, indent=2)))
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
        }
        if tool_defs:
            kwargs["tools"] = tool_defs

        prov = self.providers.get(self._provider_name)
        _reasoning_model = prov.get_reasoning(self._model)
        if _reasoning_model:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

        _use_stream = self.settings.stream
        if _use_stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}

        response = client.chat.completions.create(**kwargs)

        if _use_stream:
            return self._process_stream(response, prov, _reasoning_model, client, system_prompt, max_tokens, _iter)

        self._track_tokens_openai_completion(response.usage)

        choice = response.choices[0]
        msg = choice.message

        reasoning = ""
        if _reasoning_model:
            reasoning = getattr(msg, "reasoning_content", "") or ""
            if reasoning:
                logger.debug("Agent: reasoning (%d chars):\n%s", len(reasoning), reasoning)
                self._print(f"[bold bright_cyan]Reasoning:[/bold bright_cyan] {reasoning}")

        if msg.tool_calls:
            msg_dict: dict[str, Any] = {
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
            }
            if reasoning:
                msg_dict["reasoning_content"] = reasoning
            self.messages.append(msg_dict)
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                logger.info("Agent: tool call: %s(%s)",tc.function.name, json.dumps(args, ensure_ascii=False))
                args_brief = json.dumps(args, ensure_ascii=False)[:60]
                self._update_status(f"Tool: {tc.function.name}({args_brief})")
                output = self.registry.call(tc.function.name, **args)
                logger.info("Agent: tool result: %s -> %s", tc.function.name,
                            self._trunc(str(output)))
                self._activate_tool(tc.function.name, args, output)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": output,
                })
            if _iter >= self.settings.max_iterations:
                return "Max tool iterations reached. Please simplify your request."
            self.iteration += 1
            system_prompt = self._inject_loaded_content(system_prompt)
            return self._run_openai_completion(client, system_prompt, max_tokens, _iter + 1)

        final_msg: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if reasoning:
            final_msg["reasoning_content"] = reasoning
            logger.debug("Agent: reasoning (%d chars):\n%s", len(reasoning), reasoning)
            self._print(f"[bold bright_cyan]Reasoning:[/bold bright_cyan] {reasoning}")
        self.messages.append(final_msg)
        logger.debug("Agent:\n=== LLM Response ===\n%s", self._format_completion_log(response))
        return msg.content or ""

    def _process_stream(self, response: Any, prov: Any, reasoning_model: bool,
                        client: Any, system_prompt: str, max_tokens: int, _iter: int) -> str:
        """Process a streaming OpenAI-compatible response, accumulating content/reasoning/tool_calls."""
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_call_chunks: dict[int, dict[str, Any]] = {}
        usage: Any = None

        for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if getattr(delta, "content", None):
                content_parts.append(delta.content)

            if reasoning_model:
                rc = getattr(delta, "reasoning_content", None)
                if rc:
                    reasoning_parts.append(rc)

            if getattr(delta, "tool_calls", None):
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_chunks:
                        tool_call_chunks[idx] = {"id": tc.id or "", "function": {"name": "", "arguments": ""}}
                    if tc.id:
                        tool_call_chunks[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_call_chunks[idx]["function"]["name"] += tc.function.name
                        if tc.function.arguments:
                            tool_call_chunks[idx]["function"]["arguments"] += tc.function.arguments

            if chunk.usage:
                usage = chunk.usage

        content = "".join(content_parts)
        reasoning = "".join(reasoning_parts)

        if reasoning:
            logger.debug("Agent: reasoning (%d chars):\n%s", len(reasoning), reasoning)
            self._print(f"[bold bright_cyan]Reasoning:[/bold bright_cyan] {reasoning}")

        if usage:
            self.last_input_tokens = usage.prompt_tokens or 0
            self.last_output_tokens = usage.completion_tokens or 0
            self.total_input_tokens += self.last_input_tokens
            self.total_output_tokens += self.last_output_tokens
            logger.debug("Agent: token usage: input=%s, output=%s, total=%s",
                        self.last_input_tokens, self.last_output_tokens, self.total_tokens)

        if tool_call_chunks:
            tool_calls = sorted(tool_call_chunks.values(), key=lambda t: t.get("index", 0))
            msg_dict: dict[str, Any] = {
                "role": "assistant",
                "content": content or None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": tc["function"],
                    }
                    for tc in tool_calls
                ],
            }
            if reasoning:
                msg_dict["reasoning_content"] = reasoning
            self.messages.append(msg_dict)
            for tc in tool_calls:
                args = json.loads(tc["function"]["arguments"])
                name = tc["function"]["name"]
                logger.info("Agent: tool call: %s(%s)", name, json.dumps(args, ensure_ascii=False))
                args_brief = json.dumps(args, ensure_ascii=False)[:60]
                self._update_status(f"Tool: {name}({args_brief})")
                output = self.registry.call(name, **args)
                logger.info("Agent: tool result: %s -> %s", name, self._trunc(str(output)))
                self._activate_tool(name, args, output)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": output,
                })
            if _iter >= self.settings.max_iterations:
                return "Max tool iterations reached. Please simplify your request."
            self.iteration += 1
            system_prompt = self._inject_loaded_content(system_prompt)
            return self._run_openai_completion(client, system_prompt, max_tokens, _iter + 1)

        final_msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
        if reasoning:
            final_msg["reasoning_content"] = reasoning
            logger.debug("Agent: reasoning (%d chars):\n%s", len(reasoning), reasoning)
            self._print(f"[bold bright_cyan]Reasoning:[/bold bright_cyan] {reasoning}")
        self.messages.append(final_msg)
        logger.debug("Agent:\n=== LLM Response (stream) ===\n%s", content)
        return content or ""

    def _run_openai_response(self, client: Any, system_prompt: str, max_tokens: int, _iter: int = 0) -> str:
        if _iter > 0:
            logger.debug("Agent: === LLM Request #%s (tool) ===", self.iteration)
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
                logger.info("Agent: tool call: %s(%s)",item.name, json.dumps(args, ensure_ascii=False))
                args_brief = json.dumps(args, ensure_ascii=False)[:60]
                self._update_status(f"Tool: {item.name}({args_brief})")
                result = self.registry.call(item.name, **args)
                logger.info("Agent: tool result: %s -> %s", item.name,
                            self._trunc(str(result)))
                self._activate_tool(item.name, args, result)
                self.messages.append({"role": "assistant", "content": None, "tool_calls": [item]})
                self.messages.append({
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": result,
                })
                self.iteration += 1
                system_prompt = self._inject_loaded_content(system_prompt)
                return self._run_openai_response(client, system_prompt, max_tokens)

        text_parts = [item.content[0].text for item in response.output if item.type == "message"]
        return "\n".join(text_parts)

    def _track_tokens_anthropic(self, usage: Any) -> None:
        self.last_input_tokens = usage.input_tokens
        self.last_output_tokens = usage.output_tokens
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        logger.debug("Agent: token usage: input=%s, output=%s, total=%s", usage.input_tokens, usage.output_tokens, self.total_tokens)

    def _track_tokens_openai_completion(self, usage: Any) -> None:
        self.last_input_tokens = usage.prompt_tokens
        self.last_output_tokens = usage.completion_tokens
        self.total_input_tokens += usage.prompt_tokens
        self.total_output_tokens += usage.completion_tokens
        logger.debug("Agent: token usage: input=%s, output=%s, total=%s", usage.prompt_tokens, usage.completion_tokens, self.total_tokens)

    def _track_tokens_openai_response(self, usage: Any) -> None:
        self.last_input_tokens = usage.input_tokens
        self.last_output_tokens = usage.output_tokens
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        logger.debug("Agent: token usage: input=%s, output=%s, total=%s", usage.input_tokens, usage.output_tokens, self.total_tokens)

    def _activate_tool(self, tool_name: str, tool_args: dict, result: str = "") -> None:
        """After a tool call, activate tools and track loaded skill/CLI content."""
        self._active_tools.add(tool_name)
        if tool_name == "load_tool_detail":
            target = tool_args.get("name", "")
            if target:
                self._active_tools.add(target)
                logger.debug("Agent: activated tool: %s", target)
        elif tool_name == "load_skill_detail" and result:
            name = tool_args.get("name", "")
            if name:
                self._loaded_skills[name] = result
        elif tool_name == "register_tool":
            target = tool_args.get("name", "")
            if target:
                self._active_tools.add(target)
                logger.debug("Agent: activated registered tool: %s", target)
        elif tool_name == "load_cli_detail" and result:
            name = tool_args.get("name", "")
            if name:
                self._loaded_cli[name] = result

    def _execute_tools_anthropic(self, content: list) -> list[dict]:
        results: list[dict] = []
        for block in content:
            if block.type == "tool_use":
                logger.info("Agent: tool call: %s(%s)",block.name, json.dumps(block.input, ensure_ascii=False))
                args_brief = json.dumps(block.input, ensure_ascii=False)[:60]
                self._update_status(f"Tool: {block.name}({args_brief})")
                output = self.registry.call(block.name, **block.input)
                limit = self.settings.tool_output_limit
                if len(output) > limit:
                    output = output[:limit] + "\n... (truncated)"
                logger.info("Agent: tool result: %s -> %s", block.name,
                            self._trunc(str(output)))
                self._activate_tool(block.name, block.input, output)
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

    def _format_messages_log(self) -> str:
        """Format messages list as readable multi-line summary for logging."""
        parts = []
        for i, msg in enumerate(self.messages):
            role = msg.get("role", "?")
            if role == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    parts.append(f"[{i}] [user] {content}")
                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "tool_result":
                            tid = item.get("tool_use_id", "")
                            out = str(item.get("content", ""))
                            parts.append(f"[{i}] [tool_result id={tid}] {out}")
                        else:
                            parts.append(f"[{i}] [user] {str(item)}")
            elif role == "assistant":
                content = msg.get("content", "")
                tool_calls = msg.get("tool_calls")
                if content:
                    parts.append(f"[{i}] [assistant] {content}")
                if tool_calls:
                    for tc in tool_calls:
                        name = tc.get("function", {}).get("name", tc.get("name", ""))
                        args = tc.get("function", {}).get("arguments", tc.get("arguments", ""))
                        parts.append(f"[{i}] [assistant/tool_call name={name}] {args}")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("type") == "text":
                                parts.append(f"[{i}] [assistant/text] {item.get('text', '')}")
                            elif item.get("type") == "tool_use":
                                name = item.get("name", "")
                                inp = json.dumps(item.get("input", {}), ensure_ascii=False)
                                parts.append(f"[{i}] [assistant/tool_use name={name}] {inp}")
                            else:
                                parts.append(f"[{i}] [assistant] {str(item)}")
            elif role == "tool":
                tid = msg.get("tool_call_id", "")
                out = str(msg.get("content", ""))
                parts.append(f"[{i}] [tool id={tid}] {out}")
            else:
                parts.append(f"[{i}] [{role}] {str(msg)[:500]}")
        return "\n  ".join(parts)

    def _format_completion_log(self, response: Any) -> str:
        """Format LLM completion response as readable summary for logging."""
        parts = []
        if self._api_type == "anthropic":
            for block in response.content:
                if block.type == "text":
                    parts.append(f"[text] {block.text[:300]}")
                elif block.type == "tool_use":
                    inp = json.dumps(block.input, ensure_ascii=False)[:300]
                    parts.append(f"[tool_use name={block.name}] {inp}")
            parts.append(f"stop_reason={response.stop_reason}")
        elif self._api_type == "openai_response":
            for item in response.output:
                if item.type == "message":
                    for c in item.content:
                        parts.append(f"[text] {c.text[:300]}")
                elif item.type == "function_call":
                    inp = item.arguments[:300] if hasattr(item, "arguments") else ""
                    parts.append(f"[function_call name={item.name}] {inp}")
            parts.append(f"status={response.status}")
        else:  # openai_completion
            choice = response.choices[0] if response.choices else None
            if choice:
                msg = choice.message
                if msg.content:
                    parts.append(f"[text] {msg.content[:300]}")
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        parts.append(f"[tool_call name={tc.function.name}] {tc.function.arguments[:300]}")
                parts.append(f"finish_reason={choice.finish_reason}")
        return "\n  ".join(parts)

    def reset(self) -> None:
        self.messages.clear()
        self._recalled.clear()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        if self.memory:
            self.memory.new_session()