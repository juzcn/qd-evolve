"""GroupChatHuman — wraps MqttHumanAgent with group chat behavior.

Subscribes to all /chat topics, pushes incoming group messages to an
event queue for CLI display, and publishes human keyboard input as
group messages.

Does not modify MqttHumanAgent source code.
"""

from __future__ import annotations

import asyncio
from typing import Any

from qd_evolve.agent.group_chat_transport import GroupChatTransport, _parse_mentions
from qd_evolve.core.logger import logger


class GroupChatHuman:
    """Wraps MqttHumanAgent for group chat participation.

    Provides:
    - Group message listener → event_queue for CLI display
    - publish_human_input() → publish keyboard input as group message
    """

    def __init__(
        self,
        mqtt_human: Any,
        transport: GroupChatTransport,
        member_names: list[str],
    ) -> None:
        self._agent = mqtt_human
        self._transport = transport
        self._members = member_names
        self._global_queue: asyncio.Queue[dict] | None = None
        self._group_listener_task: asyncio.Task | None = None
        self._event_queue: asyncio.Queue[dict] = asyncio.Queue()

    @property
    def card(self) -> Any:
        return self._agent.card

    @property
    def event_queue(self) -> asyncio.Queue[dict]:
        """Queue of group messages for CLI display."""
        return self._event_queue

    # ── Lifecycle ───────────────────────────────────────────────

    async def start(self) -> None:
        """Start original MqttHumanAgent + subscribe all group chat."""
        await self._agent.start()

        self._global_queue = self._transport.subscribe_all_group_chat()
        self._group_listener_task = asyncio.create_task(self._listen_group_chat())

        logger.info("GroupChatHuman: '%s' started, listening to group", self._agent.card.name)

    async def stop(self) -> None:
        """Stop group listener + unsubscribe + original stop."""
        if self._group_listener_task is not None:
            self._group_listener_task.cancel()
            try:
                await self._group_listener_task
            except asyncio.CancelledError:
                pass
            self._group_listener_task = None

        if self._global_queue is not None:
            self._transport.unsubscribe_all_group_chat(self._global_queue)
            self._global_queue = None

        await self._agent.stop()

    # ── Group message listener ──────────────────────────────────

    async def _listen_group_chat(self) -> None:
        """Listen to all group messages and push to event queue for CLI."""
        if self._global_queue is None:
            return

        while True:
            msg = await self._global_queue.get()
            # Skip own messages — CLI already displays them via prompt_toolkit
            if msg.get("from_agent", "") == self._agent.card.name:
                continue
            await self._event_queue.put({
                "type": "group_message",
                "from_agent": msg.get("from_agent", ""),
                "content": msg.get("content", ""),
                "mentions": msg.get("mentions", []),
                "msg_id": msg.get("msg_id", ""),
            })

    # ── Human input → group publish ─────────────────────────────

    async def publish_human_input(self, content: str) -> str:
        """Publish human keyboard input as a group message. Returns msg_id."""
        mentions = _parse_mentions(content, self._members)
        msg_id = await self._transport.publish_group_chat(
            self._agent.card.name, content, mentions,
        )
        logger.debug("GroupChatHuman: '%s' published (%s chars, msg_id=%s)",
                      self._agent.card.name, len(content), msg_id[:8])
        return msg_id
