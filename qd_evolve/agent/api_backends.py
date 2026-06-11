"""API dispatch backends for different LLM provider APIs.

Each backend encapsulates the provider-specific logic for:
- Building and calling the API
- Parsing responses (text vs tool calls)
- Executing tools and formatting results
- Token tracking
- Recursion for multi-turn tool use
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from qd_evolve.core.logger import logger
from qd_evolve.utils.cancellation import CancelledError as AgentCancelledError

if TYPE_CHECKING:
    from qd_evolve.agent.agent import Agent


class AnthropicBackend:
    """Handles Anthropic Messages API dispatch."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    def run(self, client: Any, system_prompt: str, max_tokens: int, _iter: int = 0) -> str:
        if self._agent._cancel_token is not None:
            self._agent._cancel_token.check()
        if _iter > 0:
            logger.debug("Agent: === LLM Request #%s (tool) ===", self._agent.iteration)
        response = client.messages.create(
            model=self._agent._model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=self._agent.messages,
            tools=self._agent.registry.definitions(
                api_format="anthropic",
                active_tools=self._agent._active_tools | self._agent._always_active,
            ),
        )
        self._track_tokens(response.usage)

        self._agent.messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            text = self._extract_text(response.content)
            logger.debug("Agent:\n=== LLM Response ===\n%s", self._format_completion_log(response))
            return text

        results = self._execute_tools(response.content)
        self._agent.messages.append({"role": "user", "content": results})
        if _iter >= self._agent.settings.max_iterations:
            return "Max tool iterations reached. Please simplify your request."
        self._agent.iteration += 1
        system_prompt = self._agent._update_status_tags(system_prompt)
        return self.run(client, system_prompt, max_tokens, _iter + 1)

    def _track_tokens(self, usage: Any) -> None:
        self._agent.last_input_tokens = usage.input_tokens
        self._agent.last_output_tokens = usage.output_tokens
        self._agent.total_input_tokens += usage.input_tokens
        self._agent.total_output_tokens += usage.output_tokens
        logger.debug("Agent: token usage: input=%s, output=%s, total=%s",
                     usage.input_tokens, usage.output_tokens, self._agent.total_tokens)
        if self._agent._on_event:
            self._agent._on_event({"type": "tokens", "input": self._agent.last_input_tokens,
                                   "output": self._agent.last_output_tokens,
                                   "total_in": self._agent.total_input_tokens,
                                   "total_out": self._agent.total_output_tokens})

    @staticmethod
    def _extract_text(content: list) -> str:
        parts: list[str] = []
        for block in content:
            if block.type == "text":
                parts.append(block.text)
        return "\n".join(parts)

    def _execute_tools(self, content: list) -> list[dict]:
        results: list[dict] = []
        for block in content:
            if block.type == "tool_use":
                if self._agent._cancel_token is not None:
                    self._agent._cancel_token.check()
                logger.info("Agent: tool call: %s(%s)", block.name,
                           json.dumps(block.input, ensure_ascii=False))
                args_brief = json.dumps(block.input, ensure_ascii=False)[:60]
                self._agent._update_status(f"Tool: {block.name}({args_brief})")
                try:
                    output = self._agent.registry.call(block.name, **block.input)
                except Exception as exc:
                    logger.error("Agent: tool call failed: %s(%s): %s",
                                block.name, args_brief, exc)
                    self._agent._record_tool_call(block.name, block.input, success=False, error=str(exc))
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Error: tool execution failed: {exc}",
                    })
                    continue
                limit = self._agent.settings.tool_output_limit
                if len(output) > limit:
                    output = output[:limit] + "\n... (truncated)"
                logger.info("Agent: tool result: %s -> %s", block.name,
                           self._agent._trunc(str(output)))
                self._agent._activate_tool(block.name, block.input, output)
                self._agent._record_tool_call(block.name, block.input, success=True)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
        return results

    def _format_completion_log(self, response: Any) -> str:
        parts: list[str] = []
        for block in response.content:
            if block.type == "text":
                parts.append(f"[text] {block.text[:300]}")
            elif block.type == "tool_use":
                inp = json.dumps(block.input, ensure_ascii=False)[:300]
                parts.append(f"[tool_use name={block.name}] {inp}")
        parts.append(f"stop_reason={response.stop_reason}")
        return "\n  ".join(parts)


class OpenAICompletionBackend:
    """Handles OpenAI Chat Completions API dispatch (streaming + non-streaming)."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    def run(self, client: Any, system_prompt: str, max_tokens: int, _iter: int = 0) -> str:
        if self._agent._cancel_token is not None:
            self._agent._cancel_token.check()
        if _iter > 0:
            logger.debug("Agent: === LLM Request #%s (tool) ===", self._agent.iteration)
        assert self._agent._model is not None, "model must be set before calling OpenAICompletionBackend"
        openai_messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        openai_messages.extend(self._agent.messages)

        tool_defs = self._agent.registry.definitions(
            "openai", active_tools=self._agent._active_tools | self._agent._always_active,
        )
        logger.debug("Agent: tool defs for API (count=%s): %s", len(tool_defs),
                     self._agent._trunc(json.dumps(tool_defs, ensure_ascii=False, indent=2)))
        kwargs: dict[str, Any] = {
            "model": self._agent._model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
        }
        if tool_defs:
            kwargs["tools"] = tool_defs

        prov = self._agent.providers.get(self._agent._provider_name)
        _reasoning_model = prov.get_reasoning(self._agent._model)
        if _reasoning_model:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

        _use_stream = self._agent.settings.stream
        if _use_stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}

        response = client.chat.completions.create(**kwargs)

        if _use_stream:
            return self._process_stream(response, prov, _reasoning_model, client, system_prompt, max_tokens, _iter)

        self._track_tokens(response.usage)

        choice = response.choices[0]
        msg = choice.message

        reasoning = ""
        if _reasoning_model:
            reasoning = getattr(msg, "reasoning_content", "") or ""
            if reasoning:
                logger.debug("Agent: reasoning (%d chars):\n%s", len(reasoning), reasoning)
                self._agent._print(f"[bold bright_cyan]Reasoning:[/bold bright_cyan] {reasoning}")
                self._agent._tc_buffer.append(f"[iter {self._agent.iteration}] Reasoning:\n{reasoning}")

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
            self._agent.messages.append(msg_dict)
            for tc in msg.tool_calls:
                if self._agent._cancel_token is not None:
                    self._agent._cancel_token.check()
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError as e:
                    logger.error("Agent: malformed tool call arguments from LLM: %s(%s)",
                                tc.function.name, tc.function.arguments[:200])
                    self._agent._record_tool_call(tc.function.name, {"raw": tc.function.arguments[:200]},
                                                  success=False, error=f"malformed JSON: {e}")
                    self._agent.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"Error: malformed JSON arguments: {e}",
                    })
                    continue
                logger.info("Agent: tool call: %s(%s)", tc.function.name,
                           json.dumps(args, ensure_ascii=False))
                args_brief = json.dumps(args, ensure_ascii=False)[:60]
                self._agent._update_status(f"Tool: {tc.function.name}({args_brief})")
                try:
                    output = self._agent.registry.call(tc.function.name, **args)
                except Exception as exc:
                    logger.error("Agent: tool call failed: %s(%s): %s",
                                tc.function.name, args_brief, exc)
                    self._agent._record_tool_call(tc.function.name, args, success=False, error=str(exc))
                    self._agent.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"Error: tool execution failed: {exc}",
                    })
                    continue
                limit = self._agent.settings.tool_output_limit
                if len(output) > limit:
                    output = output[:limit] + "\n... (truncated)"
                logger.info("Agent: tool result: %s -> %s", tc.function.name,
                           self._agent._trunc(str(output)))
                self._agent._activate_tool(tc.function.name, args, output)
                self._agent._record_tool_call(tc.function.name, args, success=True)
                self._agent.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": output,
                })
            if _iter >= self._agent.settings.max_iterations:
                return "Max tool iterations reached. Please simplify your request."
            self._agent.iteration += 1
            system_prompt = self._agent._update_status_tags(system_prompt)
            return self.run(client, system_prompt, max_tokens, _iter + 1)

        final_msg: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if reasoning:
            final_msg["reasoning_content"] = reasoning
        self._agent.messages.append(final_msg)
        logger.debug("Agent:\n=== LLM Response ===\n%s", self._format_completion_log(response))
        return msg.content or ""

    def _track_tokens(self, usage: Any) -> None:
        self._agent.last_input_tokens = usage.prompt_tokens
        self._agent.last_output_tokens = usage.completion_tokens
        self._agent.total_input_tokens += usage.prompt_tokens
        self._agent.total_output_tokens += usage.completion_tokens
        logger.debug("Agent: token usage: input=%s, output=%s, total=%s",
                     usage.prompt_tokens, usage.completion_tokens, self._agent.total_tokens)
        if self._agent._on_event:
            self._agent._on_event({"type": "tokens", "input": self._agent.last_input_tokens,
                                   "output": self._agent.last_output_tokens,
                                   "total_in": self._agent.total_input_tokens,
                                   "total_out": self._agent.total_output_tokens})

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
                if self._agent._cancel_token is not None:
                    self._agent._cancel_token.check()
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
        except AgentCancelledError:
            raise
        except Exception as e:
            logger.error("Agent: stream processing failed: %s", e)
            return f"Stream error: {type(e).__name__}: {e}"

        content = "".join(content_parts)
        reasoning = "".join(reasoning_parts)

        if reasoning:
            logger.debug("Agent: reasoning (%d chars):\n%s", len(reasoning), reasoning)
            self._agent._print(f"[bold bright_cyan]Reasoning:[/bold bright_cyan] {reasoning}")
            self._agent._tc_buffer.append(f"[iter {self._agent.iteration}] Reasoning:\n{reasoning}")

        if usage:
            self._track_tokens(usage)

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
            self._agent.messages.append(msg_dict)
            for tc in tool_calls:
                if self._agent._cancel_token is not None:
                    self._agent._cancel_token.check()
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError as e:
                    logger.error("Agent: malformed tool call arguments from LLM: %s(%s)",
                                tc["function"]["name"], tc["function"]["arguments"][:200])
                    self._agent._record_tool_call(tc["function"]["name"],
                                                  {"raw": tc["function"]["arguments"][:200]},
                                                  success=False, error=f"malformed JSON: {e}")
                    self._agent.messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": f"Error: malformed JSON arguments: {e}",
                    })
                    continue
                name = tc["function"]["name"]
                logger.info("Agent: tool call: %s(%s)", name, json.dumps(args, ensure_ascii=False))
                args_brief = json.dumps(args, ensure_ascii=False)[:60]
                self._agent._update_status(f"Tool: {name}({args_brief})")
                try:
                    output = self._agent.registry.call(name, **args)
                except Exception as exc:
                    logger.error("Agent: tool call failed: %s(%s): %s", name, args_brief, exc)
                    self._agent._record_tool_call(name, args, success=False, error=str(exc))
                    self._agent.messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": f"Error: tool execution failed: {exc}",
                    })
                    continue
                limit = self._agent.settings.tool_output_limit
                if len(output) > limit:
                    output = output[:limit] + "\n... (truncated)"
                logger.info("Agent: tool result: %s -> %s", name, self._agent._trunc(str(output)))
                self._agent._activate_tool(name, args, output)
                self._agent._record_tool_call(name, args, success=True)
                self._agent.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": output,
                })
            if _iter >= self._agent.settings.max_iterations:
                return "Max tool iterations reached. Please simplify your request."
            self._agent.iteration += 1
            system_prompt = self._agent._update_status_tags(system_prompt)
            return self.run(client, system_prompt, max_tokens, _iter + 1)

        final_msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
        if reasoning:
            final_msg["reasoning_content"] = reasoning
        self._agent.messages.append(final_msg)
        logger.debug("Agent:\n=== LLM Response (stream) ===\n%s", content)
        return content or ""

    def _format_completion_log(self, response: Any) -> str:
        parts: list[str] = []
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


class OpenAIResponseBackend:
    """Handles OpenAI Responses API dispatch."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    def run(self, client: Any, system_prompt: str, max_tokens: int, _iter: int = 0) -> str:
        if self._agent._cancel_token is not None:
            self._agent._cancel_token.check()
        if _iter > 0:
            logger.debug("Agent: === LLM Request #%s (tool) ===", self._agent.iteration)
        response = client.responses.create(
            model=self._agent._model,
            instructions=system_prompt,
            input=self._agent.messages,
            max_output_tokens=max_tokens,
            tools=self._agent.registry.definitions(
                "openai-response",
                active_tools=self._agent._active_tools | self._agent._always_active,
            ),
        )
        self._track_tokens(response.usage)

        for item in response.output:
            if item.type == "function_call":
                if self._agent._cancel_token is not None:
                    self._agent._cancel_token.check()
                try:
                    args = json.loads(item.arguments)
                except json.JSONDecodeError as e:
                    logger.error("Agent: malformed tool call arguments from LLM: %s(%s)",
                                item.name, item.arguments[:200])
                    self._agent._record_tool_call(item.name, {"raw": item.arguments[:200]},
                                                  success=False, error=f"malformed JSON: {e}")
                    self._agent.messages.append({
                        "role": "assistant", "content": None, "tool_calls": [item],
                    })
                    self._agent.messages.append({
                        "type": "function_call_output",
                        "call_id": item.call_id,
                        "output": f"Error: malformed JSON arguments: {e}",
                    })
                    continue
                logger.info("Agent: tool call: %s(%s)", item.name,
                           json.dumps(args, ensure_ascii=False))
                args_brief = json.dumps(args, ensure_ascii=False)[:60]
                self._agent._update_status(f"Tool: {item.name}({args_brief})")
                try:
                    result = self._agent.registry.call(item.name, **args)
                except Exception as exc:
                    logger.error("Agent: tool call failed: %s(%s): %s",
                                item.name, args_brief, exc)
                    self._agent._record_tool_call(item.name, args, success=False, error=str(exc))
                    self._agent.messages.append({"role": "assistant", "content": None, "tool_calls": [item]})
                    self._agent.messages.append({
                        "type": "function_call_output",
                        "call_id": item.call_id,
                        "output": f"Error: tool execution failed: {exc}",
                    })
                    continue
                limit = self._agent.settings.tool_output_limit
                if len(result) > limit:
                    result = result[:limit] + "\n... (truncated)"
                logger.info("Agent: tool result: %s -> %s", item.name,
                           self._agent._trunc(str(result)))
                self._agent._activate_tool(item.name, args, result)
                self._agent._record_tool_call(item.name, args, success=True)
                self._agent.messages.append({"role": "assistant", "content": None, "tool_calls": [item]})
                self._agent.messages.append({
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": result,
                })
                self._agent.iteration += 1
                system_prompt = self._agent._update_status_tags(system_prompt)
                return self.run(client, system_prompt, max_tokens)

        text_parts = [item.content[0].text for item in response.output if item.type == "message"]
        return "\n".join(text_parts)

    def _track_tokens(self, usage: Any) -> None:
        self._agent.last_input_tokens = usage.input_tokens
        self._agent.last_output_tokens = usage.output_tokens
        self._agent.total_input_tokens += usage.input_tokens
        self._agent.total_output_tokens += usage.output_tokens
        logger.debug("Agent: token usage: input=%s, output=%s, total=%s",
                     usage.input_tokens, usage.output_tokens, self._agent.total_tokens)
        if self._agent._on_event:
            self._agent._on_event({"type": "tokens", "input": self._agent.last_input_tokens,
                                   "output": self._agent.last_output_tokens,
                                   "total_in": self._agent.total_input_tokens,
                                   "total_out": self._agent.total_output_tokens})


def get_backend(api_type: str, agent: Agent) -> AnthropicBackend | OpenAICompletionBackend | OpenAIResponseBackend:
    """Return the correct API backend for the given api_type."""
    if api_type == "anthropic":
        return AnthropicBackend(agent)
    elif api_type == "openai_completion":
        return OpenAICompletionBackend(agent)
    elif api_type == "openai_response":
        return OpenAIResponseBackend(agent)
    else:
        raise ValueError(f"Unsupported api_type: {api_type}")
