"""GroupChatAgent — wraps MqttAgent with group chat behavior.

Subscribes to /chat topics of all other members, processes incoming
group messages in parallel, and publishes responses back to the group.

Does not modify MqttAgent source code.
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime
from typing import Any

from qd_evolve.agent.a2a import AgentCard
from qd_evolve.agent.group_chat_transport import GroupChatTransport, _parse_mentions
from qd_evolve.agent.server import TaskStore
from qd_evolve.core.logger import logger
from qd_evolve.core.prompts import PromptTemplateManager


class GroupChatAgent:
    """Wraps MqttAgent with group chat message handling.

    Adds: group message listener, dedup, parallel agent.run(),
    and group message publishing. The original MqttAgent handles
    discovery, request/response, and event topics unchanged.
    """

    def __init__(
        self,
        mqtt_agent: Any,
        transport: GroupChatTransport,
        member_names: list[str],
        template_mgr: PromptTemplateManager,
    ) -> None:
        self._agent = mqtt_agent
        self._transport = transport
        self._members = member_names
        self._template_mgr = template_mgr
        self._seen_msg_ids: set[str] = set()
        self._chat_queues: list[tuple[str, asyncio.Queue]] = []
        self._group_listener_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        # Capture the running event loop for thread-safe publish
        self._loop: asyncio.AbstractEventLoop | None = None
        # Event queue for CLI display (incoming + outgoing group messages)
        self._event_queue: asyncio.Queue[dict] = asyncio.Queue()

    @property
    def event_queue(self) -> asyncio.Queue[dict]:
        """Queue of group events for CLI display."""
        return self._event_queue

    @property
    def card(self) -> AgentCard:
        return self._agent.card

    @property
    def task_store(self) -> TaskStore:
        return self._agent.task_store

    # ── Lifecycle ───────────────────────────────────────────────

    async def start(self) -> None:
        """Start original MqttAgent + subscribe group chat + start listener."""
        # Capture event loop before any to_thread calls
        self._loop = asyncio.get_running_loop()

        await self._agent.start()

        agent_name = self._agent.card.name
        for name in self._members:
            if name != agent_name:
                q = self._transport.subscribe_group_chat(name)
                self._chat_queues.append((name, q))
                logger.debug("GroupChatAgent: subscribed to %s's group chat", name)

        self._group_listener_task = asyncio.create_task(self._listen_group_chat())
        logger.info("GroupChatAgent: '%s' started, watching %d members",
                     agent_name, len(self._chat_queues))

    async def stop(self) -> None:
        """Stop group listener + unsubscribe + original stop."""
        if self._group_listener_task is not None:
            self._group_listener_task.cancel()
            try:
                await self._group_listener_task
            except asyncio.CancelledError:
                pass
            self._group_listener_task = None

        for name, q in self._chat_queues:
            self._transport.unsubscribe_group_chat(name, q)
        self._chat_queues.clear()

        self.stop_heartbeat_loop()
        await self._agent.stop()

    # ── Group message listener ──────────────────────────────────

    async def _listen_group_chat(self) -> None:
        """Merge all member chat queues and process messages in parallel."""
        if not self._chat_queues:
            return

        while True:
            msg = await self._get_any_chat_message()
            if msg is None:
                continue

            from_agent = msg.get("from_agent", "")
            msg_id = msg.get("msg_id", "")

            # Skip own messages
            if from_agent == self._agent.card.name:
                continue

            # Dedup
            if msg_id in self._seen_msg_ids:
                continue
            self._seen_msg_ids.add(msg_id)

            # Cap seen set to avoid unbounded growth
            if len(self._seen_msg_ids) > 10000:
                self._seen_msg_ids = set(list(self._seen_msg_ids)[-5000:])

            # Push incoming message to CLI event queue
            await self._event_queue.put({
                "type": "group_message",
                "from_agent": from_agent,
                "content": msg.get("content", ""),
                "mentions": msg.get("mentions", []),
            })

            # Format message via template
            mentions = msg.get("mentions", [])
            formatted = self._template_mgr.render(
                "group-message",
                from_agent=from_agent,
                content=msg.get("content", ""),
                mentions=mentions,
            )

            logger.info("GroupChatAgent: '%s' received group message from '%s' (%s chars)",
                         self._agent.card.name, from_agent, len(formatted))

            # Run agent in parallel (don't await)
            asyncio.create_task(
                asyncio.to_thread(self._run_and_publish, formatted)
            )

    async def _get_any_chat_message(self) -> dict | None:
        """Get a message from any of the subscribed chat queues."""
        if not self._chat_queues:
            await asyncio.sleep(1)
            return None

        tasks = [asyncio.ensure_future(q.get()) for _, q in self._chat_queues]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            for t in done:
                try:
                    return t.result()
                except (asyncio.CancelledError, Exception):
                    pass
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            raise
        return None

    def _run_and_publish(self, formatted_msg: str) -> None:
        """Run agent with formatted group message, publish if not silent."""
        agent_name = self._agent.card.name

        # Simulate natural reply delay (before thinking)
        cfg = self._agent.agent.settings.agents_config.gchat
        if cfg.reply_delay_max > 0:
            delay = random.uniform(cfg.reply_delay_min, cfg.reply_delay_max)
            logger.debug("GroupChatAgent: '%s' reply delay %.1fs", agent_name, delay)
            time.sleep(delay)

        try:
            result = self._agent.run(formatted_msg)
        except Exception as e:
            logger.warning("GroupChatAgent: '%s' run failed: %s", agent_name, e)
            return

        stripped = result.strip()
        if not stripped or stripped == ".":
            logger.debug("GroupChatAgent: '%s' silent", agent_name)
            return
        # Guard against invisible-only responses (zero-width chars etc.)
        import unicodedata
        visible = "".join(c for c in stripped if unicodedata.category(c)[0] not in "CZ")
        if not visible or visible == ".":
            logger.debug("GroupChatAgent: '%s' silent", agent_name)
            return

        # Push outgoing message to CLI event queue
        mentions = _parse_mentions(result, self._members)
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._event_queue.put({
                    "type": "group_message",
                    "from_agent": agent_name,
                    "content": result,
                    "mentions": mentions,
                }),
                self._loop,
            )

        # Publish response to group
        try:
            if self._loop and self._loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    self._transport.publish_group_chat(agent_name, result, mentions),
                    self._loop,
                )
                future.result(timeout=10)
            else:
                asyncio.run(self._transport.publish_group_chat(agent_name, result, mentions))
        except Exception as e:
            logger.warning("GroupChatAgent: '%s' publish failed: %s", agent_name, e)

    # ── Heartbeat ───────────────────────────────────────────────

    def start_heartbeat_loop(self) -> None:
        """Start group-mode heartbeat loop."""
        seconds = self._agent.settings.heartbeat_idle_seconds
        if seconds <= 0:
            return

        self._agent.agent._hb_idle_seconds = seconds
        self._agent.agent._hb_event = asyncio.Event()

        async def _loop() -> None:
            while True:
                try:
                    await asyncio.wait_for(
                        self._agent.agent._hb_event.wait(), timeout=seconds
                    )
                    self._agent.agent._hb_event.clear()
                except asyncio.TimeoutError:
                    self._agent.agent._hb_event.clear()
                    try:
                        await asyncio.to_thread(self.heartbeat_check, seconds)
                    except Exception as e:
                        logger.debug("GroupChat heartbeat loop error: %s", e)

        self._heartbeat_task = asyncio.ensure_future(_loop())

    def stop_heartbeat_loop(self) -> None:
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self._agent.stop_heartbeat_loop()

    def heartbeat_check(self, idle_seconds: int) -> str | None:
        """Group-mode heartbeat using the heartbeat template."""
        if self._agent.agent._running:
            logger.debug("GroupChat Heartbeat: skipped, agent is busy")
            return None

        msg = self._template_mgr.render(
            "heartbeat",
            idle_seconds=idle_seconds,
            now=datetime.now().strftime("%Y-%m-%d %A %H:%M:%S"),
            agent_name=self.card.name,
            mqtt_broker_host=self._transport._broker_host,
            mqtt_broker_port=self._transport._broker_port,
        )

        logger.debug("GroupChat Heartbeat: idle %ss", idle_seconds)
        try:
            result = self._agent.run(msg)
        except Exception as e:
            logger.warning("GroupChat Heartbeat: LLM call failed: %s", e)
            return None

        stripped = result.strip()
        if not stripped or stripped == ".":
            logger.debug("GroupChat Heartbeat: '%s' silent", self.card.name)
            self._agent._push_event({"type": "heartbeat_silent"})
            return None
        # Guard against invisible-only responses
        import unicodedata
        visible = "".join(c for c in stripped if unicodedata.category(c)[0] not in "CZ")
        if not visible or visible == ".":
            logger.debug("GroupChat Heartbeat: '%s' silent", self.card.name)
            self._agent._push_event({"type": "heartbeat_silent"})
            return None

        logger.info("GroupChat Heartbeat: '%s' responded (%s chars)", self.card.name, len(result))
        self._agent._push_event({"type": "heartbeat", "content": result})

        # Heartbeat response also publishes to group
        mentions = _parse_mentions(result, self._members)
        try:
            if self._loop and self._loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    self._transport.publish_group_chat(self.card.name, result, mentions),
                    self._loop,
                )
                future.result(timeout=10)
            else:
                asyncio.run(self._transport.publish_group_chat(self.card.name, result, mentions))
        except Exception as e:
            logger.warning("GroupChat Heartbeat: '%s' publish failed: %s", self.card.name, e)

        return result
