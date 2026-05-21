"""A2A Agent — wraps Agent with A2A identity and event subscriber fan-out.

Composition, not inheritance. Agent stays pure (LLM loop + callbacks + heartbeat).
A2AAgent adds: card, task_store, _event_subscribers, subscribe_events/unsubscribe_events,
and heartbeat_check override that checks pending task results from push notifications.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from qd_evolve.agent.a2a import AgentCard
from qd_evolve.agent.agent import Agent
from qd_evolve.agent.server import TaskStore
from qd_evolve.core.logger import logger


class A2AAgent:
    """Wraps an Agent with A2A identity (AgentCard, TaskStore) and event fan-out.

    Delegates all Agent methods/attributes through .agent.
    Hooks agent._on_event to _push_event for multi-subscriber fan-out.
    """

    def __init__(self, agent: Agent, card: AgentCard, task_store: TaskStore | None = None) -> None:
        self.agent = agent
        self.card = card
        self.task_store = task_store or TaskStore()
        self._event_subscribers: list[asyncio.Queue] = []

    @property
    def _running(self) -> bool:
        return self.agent._running

    # Hook our _push_event into the agent's event callback
        agent._on_event = self._push_event

    # ── Delegate key Agent methods ──────────────────────────────────

    def run(self, *args: Any, **kwargs: Any) -> str:
        return self.agent.run(*args, **kwargs)

    def heartbeat_check(self, idle_seconds: int) -> str:
        """A2A heartbeat: check pending task results before calling LLM.

        Uses a2a-heartbeat template and injects any completed task results
        from push notifications (e.g. human agent replies).
        """
        pending_results = self._check_pending_task_results()

        if self.agent._template_mgr is not None:
            msg = self.agent._template_mgr.render("a2a-heartbeat", idle_seconds=idle_seconds,
                                                   now=datetime.now().strftime("%Y-%m-%d %A %H:%M:%S"),
                                                   pending_task_results=pending_results)
        else:
            msg = f"[System heartbeat: idle {idle_seconds}s. Chat if you want, '.' to stay silent.]"
            if pending_results:
                msg += "\n" + pending_results

        logger.debug("A2A Heartbeat: idle %ss, sending heartbeat message", idle_seconds)
        try:
            response = self.agent.run(msg)
        except Exception as e:
            logger.warning("A2A Heartbeat: LLM call failed: %s", e)
            return None
        if response.strip() == ".":
            logger.debug("A2A Heartbeat: LLM sent '.' — staying silent")
            self._push_event({"type": "heartbeat_silent"})
        else:
            logger.info("A2A Heartbeat: LLM responded (%s chars)", len(response))
            self._push_event({"type": "heartbeat", "content": response})
        return response

    def _check_pending_task_results(self) -> str:
        """Check _task_store for tasks that were input_required and now completed.

        Returns a formatted string of completed task results, or empty string.
        """
        from qd_evolve.agent.a2a_tools import _task_store
        completed_items = []
        for task_id, entry in list(_task_store.items()):
            if entry.get("state") in ("completed", "failed", "canceled") and entry.get("result"):
                target = entry.get("target", "")
                result = entry.get("result", "")
                completed_items.append(f"- Task {task_id[:8]} → {target}: [{entry['state']}] {result[:200]}")
        if not completed_items:
            return ""
        return "\nPending task results arrived via push notification:\n" + "\n".join(completed_items)

    def start_heartbeat_loop(self) -> None:
        """Start heartbeat loop using A2AAgent.heartbeat_check (not Agent's).

        Must run our own loop so heartbeat_check resolves to the A2A override
        (which uses a2a-heartbeat template and checks pending task results),
        not the base Agent.heartbeat_check.
        """
        seconds = self.agent.settings.heartbeat_idle_seconds
        if seconds <= 0:
            return

        self.agent._hb_idle_seconds = seconds
        self.agent._hb_event = asyncio.Event()

        async def _loop() -> None:
            while True:
                try:
                    await asyncio.wait_for(self.agent._hb_event.wait(), timeout=self.agent._hb_idle_seconds)
                    self.agent._hb_event.clear()
                except asyncio.TimeoutError:
                    self.agent._hb_event.clear()
                    try:
                        await asyncio.to_thread(self.heartbeat_check, self.agent._hb_idle_seconds)
                    except Exception as e:
                        logger.debug("A2A Heartbeat loop error: %s", e)

        self.agent._hb_task = asyncio.ensure_future(_loop())

    def stop_heartbeat_loop(self) -> None:
        self.agent.stop_heartbeat_loop()

    def reset(self) -> None:
        self.agent.reset()

    def set_status_callback(self, cb: Any) -> None:
        self.agent.set_status_callback(cb)

    def set_print_callback(self, cb: Any) -> None:
        self.agent.set_print_callback(cb)

    def set_event_callback(self, cb: Any) -> None:
        self.agent.set_event_callback(cb)

    def _update_status(self, text: str) -> None:
        self.agent._update_status(text)

    def _print(self, text: str) -> None:
        self.agent._print(text)

    def _track_tokens_anthropic(self, usage: Any) -> None:
        self.agent._track_tokens_anthropic(usage)

    def _track_tokens_openai_completion(self, usage: Any) -> None:
        self.agent._track_tokens_openai_completion(usage)

    def _track_tokens_openai_response(self, usage: Any) -> None:
        self.agent._track_tokens_openai_response(usage)

    # ── Delegate key Agent attributes ────────────────────────────────

    @property
    def settings(self) -> Any:
        return self.agent.settings

    @property
    def registry(self) -> Any:
        return self.agent.registry

    @property
    def providers(self) -> Any:
        return self.agent.providers

    @property
    def memory(self) -> Any:
        return self.agent.memory

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self.agent.messages

    @messages.setter
    def messages(self, value: list[dict[str, Any]]) -> None:
        self.agent.messages = value

    @property
    def _provider_name(self) -> str | None:
        return self.agent._provider_name

    @_provider_name.setter
    def _provider_name(self, value: str | None) -> None:
        self.agent._provider_name = value

    @property
    def _model(self) -> str | None:
        return self.agent._model

    @_model.setter
    def _model(self, value: str | None) -> None:
        self.agent._model = value

    @property
    def _always_active(self) -> set[str]:
        return self.agent._always_active

    @property
    def _active_tools(self) -> set[str]:
        return self.agent._active_tools

    @property
    def _preload_skills(self) -> set[str]:
        return self.agent._preload_skills

    @property
    def _preload_cli(self) -> set[str]:
        return self.agent._preload_cli

    @property
    def _loaded_skill_names(self) -> set[str]:
        return self.agent._loaded_skill_names

    @property
    def _loaded_cli_names(self) -> set[str]:
        return self.agent._loaded_cli_names

    @property
    def last_input_tokens(self) -> int:
        return self.agent.last_input_tokens

    @property
    def last_output_tokens(self) -> int:
        return self.agent.last_output_tokens

    @property
    def total_input_tokens(self) -> int:
        return self.agent.total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self.agent.total_output_tokens

    @property
    def total_tokens(self) -> int:
        return self.agent.total_tokens

    @property
    def iteration(self) -> int:
        return self.agent.iteration

    @iteration.setter
    def iteration(self, value: int) -> None:
        self.agent.iteration = value

    @property
    def default_system_prompt(self) -> str:
        return self.agent.default_system_prompt

    @default_system_prompt.setter
    def default_system_prompt(self, value: str) -> None:
        self.agent.default_system_prompt = value

    @property
    def _template_mgr(self) -> Any:
        return self.agent._template_mgr

    @property
    def _hb_task(self) -> asyncio.Task | None:
        return self.agent._hb_task

    @_hb_task.setter
    def _hb_task(self, value: asyncio.Task | None) -> None:
        self.agent._hb_task = value

    @property
    def _on_status(self) -> Any:
        return self.agent._on_status

    @_on_status.setter
    def _on_status(self, value: Any) -> None:
        self.agent._on_status = value

    @property
    def _on_print(self) -> Any:
        return self.agent._on_print

    @_on_print.setter
    def _on_print(self, value: Any) -> None:
        self.agent._on_print = value

    @property
    def _on_event(self) -> Any:
        return self.agent._on_event

    @_on_event.setter
    def _on_event(self, value: Any) -> None:
        self.agent._on_event = value

    @property
    def _recalled(self) -> Any:
        return self.agent._recalled

    # ── Event subscriber mechanism (A2A observability) ──────────────

    def subscribe_events(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._event_subscribers.append(q)
        return q

    def unsubscribe_events(self, q: asyncio.Queue) -> None:
        if q in self._event_subscribers:
            self._event_subscribers.remove(q)

    # Backward compat aliases
    def subscribe_heartbeat(self) -> asyncio.Queue:
        return self.subscribe_events()

    def unsubscribe_heartbeat(self, q: asyncio.Queue) -> None:
        self.unsubscribe_events(q)

    def _push_event(self, event: dict) -> None:
        for q in self._event_subscribers:
            try:
                q.put_nowait(event)
            except Exception:
                pass