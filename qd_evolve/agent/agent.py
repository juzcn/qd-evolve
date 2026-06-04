from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime
from typing import Any, Callable

from qd_evolve.core.config import AgentEntry, Settings, LOG_TRUNCATION
from qd_evolve.core.logger import logger
from qd_evolve.core.memory import MemoryStore, RecalledMemoryRegistry
from qd_evolve.core.providers import ProviderRegistry
from qd_evolve.core.registry import ToolRegistry


class Agent:
    def __init__(self, settings: Settings, registry: ToolRegistry, providers: ProviderRegistry,
                 memory: MemoryStore | None = None, default_system_prompt: str = "",
                 preload_tools: set[str] | None = None,
                 preload_skills: set[str] | None = None,
                 preload_cli: set[str] | None = None,
                 template_mgr: Any = None,
                 template_name: str = "",
                 template_context: dict[str, Any] | None = None) -> None:
        self.settings = settings
        self.registry = registry
        self.default_system_prompt = default_system_prompt
        self._template_mgr = template_mgr
        self._template_name = template_name
        self._template_context = template_context or {}
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
        self._on_event: Callable[[dict], None] | None = None
        self._recalled = RecalledMemoryRegistry()
        self._loaded_skill_names: set[str] = set()
        self._loaded_cli_names: set[str] = set()
        self._preload_skills: set[str] = preload_skills or set()
        self._preload_cli: set[str] = preload_cli or set()
        self._cancel_token: Any = None  # CancellationToken, set by run()
        self._hb_task: asyncio.Task | None = None
        self.name: str = ""
        self._running: bool = False
        self._run_lock: "threading.Lock" = __import__("threading").Lock()

    @staticmethod
    def _create_memory(entry: AgentEntry, settings: Settings, registry: ToolRegistry) -> MemoryStore | None:
        memory_db = entry.memory_db
        if memory_db:
            backend_name = settings.memory_search.embeddings_backend
            backend = settings.embeddings_backends.get(backend_name) if backend_name else None
            if backend is None:
                logger.warning("Agent [%s]: no embeddings backend, skipping memory", entry.name)
                return None
            memory = MemoryStore(memory_db, backend,
                                 list_all_limit=settings.memory_search.list_all_limit)
            from qd_evolve.tools.recall_memory import set_memory_store, set_default_limit
            set_memory_store(memory)
            set_default_limit(settings.memory_search.recall_memory_limit)
            return memory
        else:
            logger.info("Agent [%s]: memory disabled (memory_db is empty/null)", entry.name)
            recall_td = registry.get("recall_memory")
            if recall_td:
                recall_td.enabled = False
            return None

    def set_status_callback(self, cb: Callable[[str], None]) -> None:
        self._on_status = cb

    def set_print_callback(self, cb: Callable[[str], None]) -> None:
        self._on_print = cb

    def set_event_callback(self, cb: Callable[[dict], None] | None) -> None:
        self._on_event = cb

    def _update_status(self, text: str) -> None:
        msg = f"[#{self.iteration}] {text}"
        if self._on_status:
            self._on_status(msg)
        if self._on_event:
            self._on_event({"type": "status", "text": msg})

    def _print(self, text: str) -> None:
        if self._on_print:
            self._on_print(text)
        if self._on_event:
            self._on_event({"type": "print", "text": text})

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def heartbeat_check(self, idle_seconds: int) -> str | None:
        """Send a heartbeat message to the LLM after idle period.

        Returns the LLM's response. Caller decides whether to display it
        (e.g. suppress '.' which means 'stay silent').
        """
        if self._running:
            logger.debug("Heartbeat: skipped, agent is busy")
            return None
        if self._template_mgr is not None:
            msg = self._template_mgr.render("heartbeat", idle_seconds=idle_seconds,
                                            now=datetime.now().strftime("%Y-%m-%d %A %H:%M:%S"))
        else:
            msg = f"[System heartbeat: idle {idle_seconds}s. Chat if you want, '.' to stay silent.]"
        logger.debug("Heartbeat: idle %ss, sending heartbeat message", idle_seconds)
        try:
            response = self.run(msg)
        except Exception as e:
            logger.warning("Heartbeat: LLM call failed: %s", e)
            return None
        if response.strip() == ".":
            logger.debug("Heartbeat: LLM sent '.' — staying silent")
            if self._on_event:
                self._on_event({"type": "heartbeat_silent"})
        else:
            logger.info("Heartbeat: LLM responded (%s chars)", len(response))
            if self._on_event:
                self._on_event({"type": "heartbeat", "content": response})
        return response

    def start_heartbeat_loop(self) -> None:
        """Start internal heartbeat loop. Called after event subscribers are ready."""
        seconds = self.settings.heartbeat_idle_seconds
        if seconds <= 0:
            return

        self._hb_idle_seconds = seconds
        self._hb_event = asyncio.Event()

        async def _loop() -> None:
            try:
                while True:
                    try:
                        await asyncio.wait_for(self._hb_event.wait(), timeout=self._hb_idle_seconds)
                        # touch_heartbeat() set the event — reset idle timer, don't fire
                        self._hb_event.clear()
                    except asyncio.TimeoutError:
                        # No touch for idle_seconds — fire heartbeat
                        self._hb_event.clear()
                        try:
                            await asyncio.to_thread(self.heartbeat_check, self._hb_idle_seconds)
                        except Exception as e:
                            logger.debug("Heartbeat loop error: %s", e)
            except asyncio.CancelledError:
                pass  # clean shutdown

        self._hb_task = asyncio.ensure_future(_loop())

    def touch_heartbeat(self) -> None:
        """Reset the heartbeat idle timer. Call whenever user activity occurs."""
        if hasattr(self, '_hb_event'):
            self._hb_event.set()

    def stop_heartbeat_loop(self) -> None:
        if self._hb_task and not self._hb_task.done():
            self._hb_task.cancel()

            async def _cleanup(task: asyncio.Task) -> None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            try:
                asyncio.ensure_future(_cleanup(self._hb_task))
            except RuntimeError:
                pass  # no running event loop

    def run(
        self,
        user_input: str,
        system: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        cancel_token: Any = None,
    ) -> str:
        from qd_evolve.tools.config_manager import _current_agent_var
        agent_name = getattr(self, "name", "")
        tok = _current_agent_var.set(agent_name) if agent_name else None
        self._cancel_token = cancel_token
        try:
            with self._run_lock:
                self._running = True
                try:
                    return self._run_inner(user_input, system, provider, model)
                finally:
                    self._running = False
        finally:
            self._cancel_token = None
            if tok is not None:
                _current_agent_var.reset(tok)

    def _run_inner(
        self,
        user_input: str,
        system: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> str:
        if provider is not None:
            self._provider_name = provider
        if model is not None:
            self._model = model
        # Fallback to config defaults if not set
        if not self._provider_name:
            self._provider_name = self.settings.default_provider
        if not self._model:
            self._model = self.settings.default_model
        system_prompt = system or self.default_system_prompt
        self.touch_heartbeat()
        self.messages.append({"role": "user", "content": user_input})
        self.iteration = 0
        self._tc_buffer: list[str] = []
        self._log_limit = LOG_TRUNCATION
        logger.info("Agent: user input: %s", user_input)

        # Auto recall: inject relevant memory into system prompt
        system_prompt = self._auto_recall(user_input, system_prompt)

        prov = self.providers.get(self._provider_name)
        max_tokens = prov.get_max_tokens(self._model)
        self._api_type = prov.get_api_type(self._model)

        while True:
            self.iteration += 1

            # Inject completed sub-agent results before each LLM request
            from qd_evolve.tools.sub_agent_manager import collect_sub_results
            sub = collect_sub_results()
            if sub:
                self.messages.append({"role": "user", "content": sub})
                logger.debug("Agent: injected sub-agent result (%s chars)", len(sub))

            if self._on_event:
                self._on_event({"type": "iteration", "num": self.iteration,
                              "provider": self._provider_name, "model": self._model})
            client = prov.create_client()
            active = self._active_tools | self._always_active
            msg = self._format_messages_log()
            logger.debug("Agent: === LLM Request #%s === Provider: %s / %s (%s), Active tools: %s\n"
                        "--- Messages ---\n  %s",
                        self.iteration, self._provider_name, self._model, self._api_type, active,
                        self._trunc(msg, tail=True))

            try:
                if self._api_type == "anthropic":
                    result = self._run_anthropic(client, system_prompt, max_tokens)
                elif self._api_type == "openai_completion":
                    result = self._run_openai_completion(client, system_prompt, max_tokens)
                elif self._api_type == "openai_response":
                    result = self._run_openai_response(client, system_prompt, max_tokens)
                else:
                    raise ValueError(f"Unsupported api_type: {self._api_type}")
            except Exception as e:
                logger.error("Agent: API call failed: %s", e)
                msg = self._format_api_error(e)
                if self._on_event:
                    self._on_event({"type": "error", "content": msg})
                self.messages.pop()  # remove the user message we just appended
                return msg

            if self.memory:
                self._compress_messages()
                try:
                    process = "\n".join(self._tc_buffer) if self._tc_buffer else None
                    self.memory.save(user_input, result, process=process)
                except Exception as e:
                    logger.warning("Agent: memory.save failed: %s", e)
            if self._on_event:
                self._on_event({"type": "completed", "content": result})
            self.touch_heartbeat()
            return result

    def _format_api_error(self, e: Exception) -> str:
        """Return a human-readable error message for an API exception."""
        name = type(e).__name__
        provider = self._provider_name or "AI"

        friendly: dict[str, str] = {
            "APIConnectionError": f"Cannot reach {provider} API — network or proxy issue. Check your connection.",
            "APITimeoutError": f"{provider} API request timed out. The service may be overloaded; retry later.",
            "AuthenticationError": f"{provider} API authentication failed — check your API key.",
            "RateLimitError": f"{provider} API rate limit hit. Pause and try again.",
            "InternalServerError": f"{provider} API server error. The service may be down.",
            "BadRequestError": f"Bad request to {provider} API: {e}",
            "UnprocessableEntityError": f"Bad request to {provider} API: {e}",
            "PermissionDeniedError": f"Access denied by {provider} API — check your account permissions.",
            "NotFoundError": f"{provider} API — resource not found: {e}",
            "ConflictError": f"{provider} API conflict: {e}",
            "APIResponseValidationError": f"{provider} API returned an unexpected response: {e}",
        }
        if name in friendly:
            return f"API error ({name}): {friendly[name]}"
        return f"API error ({name}): {e}"

    def _compress_messages(self) -> None:
        if not self._model:
            return
        prov = self.providers.get(self._provider_name)
        context_window = prov.get_context_window(self._model)
        if context_window <= 0:
            return

        compress_threshold = self.settings.compress_threshold
        target_threshold = self.settings.target_threshold
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

    def _trunc(self, text: str, tail: bool = False) -> str:
        """Truncate for logging. 0 means no limit. tail=True keeps the end."""
        if self._log_limit <= 0:
            return text
        if len(text) > self._log_limit:
            if tail:
                return "..." + text[-(self._log_limit):]
            return text[:self._log_limit] + "..."
        return text

    def _update_status_tags(self, system_prompt: str) -> str:
        """Update status tags from [inactive] to [ready] for tools loaded at runtime.

        Uses regex to match ``- [inactive] <name>:`` or ``- [inactive] <name>``
        lines and replaces the tag.  Exact name matching (via re.escape) avoids
        substring collisions.
        """
        import re

        for _, names in (
            ("skills", self._loaded_skill_names),
            ("CLI tools", self._loaded_cli_names),
            ("func tools", self._active_tools),
        ):
            for name in names:
                pattern = re.compile(
                    rf'^(- )\[inactive\] ({re.escape(name)})(:.*|\s*)$', re.MULTILINE
                )
                system_prompt = pattern.sub(r'\1[ready] \2\3', system_prompt)

        return system_prompt

    def _auto_recall(self, user_input: str, system_prompt: str) -> str:
        if not self.memory or not self.settings.memory_search.auto_recall:
            return system_prompt

        try:
            entries = self.memory.recall(query=user_input, limit=self.settings.memory_search.auto_recall_top_k)
        except Exception as e:
            logger.warning("Agent: auto_recall failed: %s", e)
            return system_prompt
        new_entries = self._recalled.add(entries)
        if not new_entries:
            return system_prompt

        logger.info("Agent: auto-recalled %s new memory entries for query: %s", len(new_entries), user_input[:50])
        for entry in new_entries:
            u = entry.user_msg.replace("\n", " ")[:60]
            a = entry.assistant_msg.replace("\n", " ")[:60]
            logger.info("Agent:   Memory [%s] user: %s | assistant: %s (distance: %s)",
                        entry.session_id, u, a, entry.distance)

        memory_text = self._recalled.format_section()
        return self._template_mgr.render(self._template_name, memory_section=memory_text, **self._template_context)

    def _run_anthropic(self, client: Any, system_prompt: str, max_tokens: int, _iter: int = 0) -> str:
        if self._cancel_token is not None:
            self._cancel_token.check()
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
        system_prompt = self._update_status_tags(system_prompt)
        return self._run_anthropic(client, system_prompt, max_tokens, _iter + 1)

    def _run_openai_completion(self, client: Any, system_prompt: str, max_tokens: int, _iter: int = 0) -> str:
        if self._cancel_token is not None:
            self._cancel_token.check()
        if _iter > 0:
            logger.debug("Agent: === LLM Request #%s (tool) ===", self.iteration)
        assert self._model is not None, "model must be set before calling _run_openai_completion"
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
                self._tc_buffer.append(f"[iter {self.iteration}] Reasoning:\n{reasoning}")

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
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError as e:
                    logger.error("Agent: malformed tool call arguments from LLM: %s(%s)", tc.function.name, tc.function.arguments[:200])
                    self._record_tool_call(tc.function.name, {"raw": tc.function.arguments[:200]}, success=False, error=f"malformed JSON: {e}")
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"Error: malformed JSON arguments: {e}",
                    })
                    continue
                logger.info("Agent: tool call: %s(%s)",tc.function.name, json.dumps(args, ensure_ascii=False))
                args_brief = json.dumps(args, ensure_ascii=False)[:60]
                self._update_status(f"Tool: {tc.function.name}({args_brief})")
                try:
                    output = self.registry.call(tc.function.name, **args)
                except Exception as exc:
                    logger.error("Agent: tool call failed: %s(%s): %s", tc.function.name, args_brief, exc)
                    self._record_tool_call(tc.function.name, args, success=False, error=str(exc))
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"Error: tool execution failed: {exc}",
                    })
                    continue
                limit = self.settings.tool_output_limit
                if len(output) > limit:
                    output = output[:limit] + "\n... (truncated)"
                logger.info("Agent: tool result: %s -> %s", tc.function.name,
                            self._trunc(str(output)))
                self._activate_tool(tc.function.name, args, output)
                self._record_tool_call(tc.function.name, args, success=True)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": output,
                })
            if _iter >= self.settings.max_iterations:
                return "Max tool iterations reached. Please simplify your request."
            self.iteration += 1
            system_prompt = self._update_status_tags(system_prompt)
            return self._run_openai_completion(client, system_prompt, max_tokens, _iter + 1)

        final_msg: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if reasoning:
            final_msg["reasoning_content"] = reasoning
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
        chunk: Any = None

        try:
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
        except Exception as e:
            logger.error("Agent: stream processing failed: %s", e)
            return f"Stream error: {type(e).__name__}: {e}"

        content = "".join(content_parts)
        reasoning = "".join(reasoning_parts)

        if reasoning:
            logger.debug("Agent: reasoning (%d chars):\n%s", len(reasoning), reasoning)
            self._print(f"[bold bright_cyan]Reasoning:[/bold bright_cyan] {reasoning}")
            self._tc_buffer.append(f"[iter {self.iteration}] Reasoning:\n{reasoning}")

        if usage:
            self._track_tokens_openai_completion(usage)

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
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError as e:
                    logger.error("Agent: malformed tool call arguments from LLM: %s(%s)", tc["function"]["name"], tc["function"]["arguments"][:200])
                    self._record_tool_call(tc["function"]["name"], {"raw": tc["function"]["arguments"][:200]}, success=False, error=f"malformed JSON: {e}")
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": f"Error: malformed JSON arguments: {e}",
                    })
                    continue
                name = tc["function"]["name"]
                logger.info("Agent: tool call: %s(%s)", name, json.dumps(args, ensure_ascii=False))
                args_brief = json.dumps(args, ensure_ascii=False)[:60]
                self._update_status(f"Tool: {name}({args_brief})")
                try:
                    output = self.registry.call(name, **args)
                except Exception as exc:
                    logger.error("Agent: tool call failed: %s(%s): %s", name, args_brief, exc)
                    self._record_tool_call(name, args, success=False, error=str(exc))
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": f"Error: tool execution failed: {exc}",
                    })
                    continue
                limit = self.settings.tool_output_limit
                if len(output) > limit:
                    output = output[:limit] + "\n... (truncated)"
                logger.info("Agent: tool result: %s -> %s", name, self._trunc(str(output)))
                self._activate_tool(name, args, output)
                self._record_tool_call(name, args, success=True)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": output,
                })
            if _iter >= self.settings.max_iterations:
                return "Max tool iterations reached. Please simplify your request."
            self.iteration += 1
            system_prompt = self._update_status_tags(system_prompt)
            return self._run_openai_completion(client, system_prompt, max_tokens, _iter + 1)

        final_msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
        if reasoning:
            final_msg["reasoning_content"] = reasoning
        self.messages.append(final_msg)
        logger.debug("Agent:\n=== LLM Response (stream) ===\n%s", content)
        return content or ""

    def _run_openai_response(self, client: Any, system_prompt: str, max_tokens: int, _iter: int = 0) -> str:
        if self._cancel_token is not None:
            self._cancel_token.check()
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
                try:
                    args = json.loads(item.arguments)
                except json.JSONDecodeError as e:
                    logger.error("Agent: malformed tool call arguments from LLM: %s(%s)", item.name, item.arguments[:200])
                    self._record_tool_call(item.name, {"raw": item.arguments[:200]}, success=False, error=f"malformed JSON: {e}")
                    self.messages.append({
                        "role": "assistant", "content": None, "tool_calls": [item],
                    })
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": item.call_id,
                        "content": f"Error: malformed JSON arguments: {e}",
                    })
                    continue
                logger.info("Agent: tool call: %s(%s)",item.name, json.dumps(args, ensure_ascii=False))
                args_brief = json.dumps(args, ensure_ascii=False)[:60]
                self._update_status(f"Tool: {item.name}({args_brief})")
                try:
                    result = self.registry.call(item.name, **args)
                except Exception as exc:
                    logger.error("Agent: tool call failed: %s(%s): %s", item.name, args_brief, exc)
                    self._record_tool_call(item.name, args, success=False, error=str(exc))
                    self.messages.append({"role": "assistant", "content": None, "tool_calls": [item]})
                    self.messages.append({
                        "type": "function_call_output",
                        "call_id": item.call_id,
                        "output": f"Error: tool execution failed: {exc}",
                    })
                    continue
                limit = self.settings.tool_output_limit
                if len(result) > limit:
                    result = result[:limit] + "\n... (truncated)"
                logger.info("Agent: tool result: %s -> %s", item.name,
                            self._trunc(str(result)))
                self._activate_tool(item.name, args, result)
                self._record_tool_call(item.name, args, success=True)
                self.messages.append({"role": "assistant", "content": None, "tool_calls": [item]})
                self.messages.append({
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": result,
                })
                self.iteration += 1
                system_prompt = self._update_status_tags(system_prompt)
                return self._run_openai_response(client, system_prompt, max_tokens)

        text_parts = [item.content[0].text for item in response.output if item.type == "message"]
        return "\n".join(text_parts)

    def _track_tokens_anthropic(self, usage: Any) -> None:
        self.last_input_tokens = usage.input_tokens
        self.last_output_tokens = usage.output_tokens
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        logger.debug("Agent: token usage: input=%s, output=%s, total=%s", usage.input_tokens, usage.output_tokens, self.total_tokens)
        if self._on_event:
            self._on_event({"type": "tokens", "input": self.last_input_tokens, "output": self.last_output_tokens,
                          "total_in": self.total_input_tokens, "total_out": self.total_output_tokens})

    def _track_tokens_openai_completion(self, usage: Any) -> None:
        self.last_input_tokens = usage.prompt_tokens
        self.last_output_tokens = usage.completion_tokens
        self.total_input_tokens += usage.prompt_tokens
        self.total_output_tokens += usage.completion_tokens
        logger.debug("Agent: token usage: input=%s, output=%s, total=%s", usage.prompt_tokens, usage.completion_tokens, self.total_tokens)
        if self._on_event:
            self._on_event({"type": "tokens", "input": self.last_input_tokens, "output": self.last_output_tokens,
                          "total_in": self.total_input_tokens, "total_out": self.total_output_tokens})

    def _track_tokens_openai_response(self, usage: Any) -> None:
        self.last_input_tokens = usage.input_tokens
        self.last_output_tokens = usage.output_tokens
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        logger.debug("Agent: token usage: input=%s, output=%s, total=%s", usage.input_tokens, usage.output_tokens, self.total_tokens)
        if self._on_event:
            self._on_event({"type": "tokens", "input": self.last_input_tokens, "output": self.last_output_tokens,
                          "total_in": self.total_input_tokens, "total_out": self.total_output_tokens})

    def _activate_tool(self, tool_name: str, tool_args: dict, result: str = "") -> None:
        """After a tool call, activate tools and track loaded skill/CLI names."""
        self._active_tools.add(tool_name)
        if tool_name == "activate_func":
            target = tool_args.get("name", "")
            if target:
                self._active_tools.add(target)
                logger.debug("Agent: activated tool: %s", target)
        elif tool_name == "open_skill":
            name = tool_args.get("name", "")
            if name:
                self._loaded_skill_names.add(name)
        elif tool_name == "enable_cli":
            name = tool_args.get("name", "")
            if name:
                self._loaded_cli_names.add(name)

    def _record_tool_call(self, name: str, args: dict, success: bool, error: str | None = None) -> None:
        params = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())
        mark = "✓" if success else f"✗ {error}"
        self._tc_buffer.append(f"[iter {self.iteration}] {name}({params}) {mark}")

    def _execute_tools_anthropic(self, content: list) -> list[dict]:
        results: list[dict] = []
        for block in content:
            if block.type == "tool_use":
                logger.info("Agent: tool call: %s(%s)",block.name, json.dumps(block.input, ensure_ascii=False))
                args_brief = json.dumps(block.input, ensure_ascii=False)[:60]
                self._update_status(f"Tool: {block.name}({args_brief})")
                try:
                    output = self.registry.call(block.name, **block.input)
                except Exception as exc:
                    logger.error("Agent: tool call failed: %s(%s): %s", block.name, args_brief, exc)
                    self._record_tool_call(block.name, block.input, success=False, error=str(exc))
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Error: tool execution failed: {exc}",
                    })
                    continue
                limit = self.settings.tool_output_limit
                if len(output) > limit:
                    output = output[:limit] + "\n... (truncated)"
                logger.info("Agent: tool result: %s -> %s", block.name,
                            self._trunc(str(output)))
                self._activate_tool(block.name, block.input, output)
                self._record_tool_call(block.name, block.input, success=True)
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
        self._active_tools = set(self._always_active)
        self._loaded_skill_names.clear()
        self._loaded_cli_names.clear()
        if self.memory:
            self.memory.new_session()