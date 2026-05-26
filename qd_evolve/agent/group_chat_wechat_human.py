"""GroupChatWechatHuman — WeChat iLink bridge for human agents in group chat.

Replaces terminal I/O (GroupChatHuman) with WeChat bidirectional bridge:

  WeChat phone -> iLink long-poll -> _poll_wechat_to_mqtt() ->
    _parse_mentions() -> transport.publish_group_chat() -> MQTT

  MQTT $a2a/v1/group/+/chat -> _listen_and_forward_to_wechat() ->
    wechat_client.send_message() -> iLink -> WeChat phone

Does not modify MqttHumanAgent or GroupChatTransport source code.
"""

from __future__ import annotations

import asyncio
from typing import Any

from qd_evolve.agent.group_chat_transport import GroupChatTransport, _parse_mentions
from qd_evolve.core.logger import logger


class GroupChatWechatHuman:
    """Wraps MqttHumanAgent for WeChat-based group chat participation.

    Provides:
    - WeChat message polling -> MQTT publish
    - MQTT group messages -> WeChat forwarding
    - event_queue for CLI display (read-only)
    """

    def __init__(
        self,
        mqtt_human: Any,
        transport: GroupChatTransport,
        member_names: list[str],
        wechat_client: Any,
    ) -> None:
        self._agent = mqtt_human
        self._transport = transport
        self._members = member_names
        self._client = wechat_client
        self._global_queue: asyncio.Queue[dict] | None = None
        self._poll_task: asyncio.Task | None = None
        self._forward_task: asyncio.Task | None = None
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
        """Start MqttHumanAgent + group chat subscription + WeChat bridge tasks."""
        await self._agent.start()

        self._global_queue = self._transport.subscribe_all_group_chat()
        self._poll_task = asyncio.create_task(self._poll_wechat_to_mqtt())
        self._forward_task = asyncio.create_task(self._listen_and_forward_to_wechat())

        logger.info("GroupChatWechatHuman: '%s' started, WeChat bridge active",
                     self._agent.card.name)

    async def stop(self) -> None:
        """Cancel bridge tasks, unsubscribe, stop WeChat client, stop agent."""
        for task in (self._poll_task, self._forward_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._poll_task = None
        self._forward_task = None

        if self._global_queue is not None:
            self._transport.unsubscribe_all_group_chat(self._global_queue)
            self._global_queue = None

        await self._client.stop()
        await self._agent.stop()

    # ── WeChat -> MQTT poller ───────────────────────────────────

    async def _poll_wechat_to_mqtt(self) -> None:
        """Long-poll WeChat messages and publish to group chat MQTT topic."""
        name = self._agent.card.name

        while True:
            try:
                msgs = await self._client.poll_updates()
            except Exception:
                logger.exception("WeChat poll_updates error, retrying in 1s")
                await asyncio.sleep(1)
                continue

            for msg in msgs:
                if msg.get("message_type") != 1:
                    continue

                item_list = msg.get("item_list") or []
                text = ""
                if item_list:
                    text = item_list[0].get("text_item", {}).get("text", "")

                from_id = msg.get("from_user_id", "")
                context_token = msg.get("context_token", "")

                self._client.last_contact = {
                    "from_id": from_id,
                    "context_token": context_token,
                }

                mentions = _parse_mentions(text, self._members)
                await self._transport.publish_group_chat(name, text, mentions)
                logger.debug("WeChat -> MQTT: '%s' published (%s chars)", name, len(text))

    # ── MQTT -> WeChat forwarder ────────────────────────────────

    async def _listen_and_forward_to_wechat(self) -> None:
        """Listen to all group messages and forward to WeChat user."""
        if self._global_queue is None:
            return

        name = self._agent.card.name

        while True:
            msg = await self._global_queue.get()

            if msg.get("from_agent", "") == name:
                continue

            last = self._client.last_contact
            to_id = last.get("from_id")
            ctx = last.get("context_token")
            if not to_id or not ctx:
                continue

            content = msg.get("content", "")
            from_agent = msg.get("from_agent", "")
            formatted = f"[{from_agent}]: {content}"

            try:
                await self._client.send_message(to_id, ctx, formatted)
            except Exception:
                logger.exception("WeChat send_message failed")
                continue

            await self._event_queue.put({
                "type": "group_message",
                "from_agent": from_agent,
                "content": content,
                "mentions": msg.get("mentions", []),
                "msg_id": msg.get("msg_id", ""),
            })
