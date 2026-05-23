"""Group chat transport — independent MQTT connection for /chat topic.

Wraps MqttTransport to reuse its discovery/is_online features.
Adds an independent aiomqtt.Client that subscribes to
`$a2a/v1/group/+/chat`, keeping the sole-consumer `_listen_all()`
of the original transport untouched.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import uuid4

from qd_evolve.core.config import MqttConfig
from qd_evolve.core.logger import logger

QOS_GROUP_CHAT = 0


def _group_chat_topic(name: str) -> str:
    return f"$a2a/v1/group/{name}/chat"


def _parse_mentions(content: str, member_names: list[str]) -> list[str]:
    """Extract @mentions from content. @all takes precedence."""
    if "@all" in content:
        return ["all"]
    return [n for n in member_names if f"@{n}" in content]


def _build_tls_params(config: MqttConfig) -> Any:
    from qd_evolve.agent.mqtt_transport import _build_tls_params as _orig
    return _orig(config)


class GroupChatTransport:
    """Group chat transport with independent MQTT connection.

    Uses a separate aiomqtt.Client for `$a2a/v1/group/+/chat` so the
    original MqttTransport sole-consumer remains undisturbed.
    """

    def __init__(
        self,
        mqtt_transport: Any,
        broker_host: str,
        broker_port: int,
        mqtt_config: MqttConfig,
        client_name: str = "",
    ) -> None:
        self._transport = mqtt_transport
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._config = mqtt_config
        self._client_name = client_name
        self._group_client: Any = None  # aiomqtt.Client
        self._connected = False
        self._listener_task: asyncio.Task | None = None

        # Per-agent subscribers: agent_name -> list of queues
        self._group_subscribers: dict[str, list[asyncio.Queue]] = {}
        # Global subscribers (receives all group messages)
        self._global_subscribers: list[asyncio.Queue] = []

    # ── Lifecycle ───────────────────────────────────────────────

    async def connect(self, timeout: float = 10.0) -> None:
        """Connect original transport + start independent group listener."""
        await self._transport.connect()

        try:
            import aiomqtt
        except ImportError:
            logger.error("aiomqtt not installed — run: pip install aiomqtt")
            raise

        if self._connected:
            return

        client_id = f"qd-evolve/{self._client_name or 'group'}/{uuid4().hex[:8]}"
        tls = _build_tls_params(self._config)
        self._group_client = aiomqtt.Client(
            hostname=self._broker_host,
            port=self._broker_port,
            username=self._config.username or None,
            password=self._config.password or None,
            keepalive=self._config.keepalive,
            identifier=client_id,
            protocol=aiomqtt.ProtocolVersion.V5,
            tls_params=tls,
        )
        try:
            await asyncio.wait_for(self._group_client.__aenter__(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"GroupChatTransport: connection to {self._broker_host}:{self._broker_port} timed out."
            )
        self._connected = True

        await self._group_client.subscribe("$a2a/v1/group/+/chat", qos=QOS_GROUP_CHAT)
        self._listener_task = asyncio.create_task(self._listen_group_chat())

        logger.info("GroupChatTransport: connected to %s:%s as %s",
                     self._broker_host, self._broker_port, client_id)

    async def disconnect(self) -> None:
        """Stop group listener + close group client + disconnect original."""
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        for queues in self._group_subscribers.values():
            for q in queues:
                while not q.empty():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
        self._group_subscribers.clear()

        for q in self._global_subscribers:
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break
        self._global_subscribers.clear()

        if self._group_client is not None:
            try:
                await self._group_client.__aexit__(None, None, None)
            except Exception:
                pass
            self._group_client = None
        self._connected = False

        await self._transport.disconnect()
        logger.info("GroupChatTransport: disconnected")

    # ── Sole consumer for group chat topics ─────────────────────

    async def _listen_group_chat(self) -> None:
        """Background task: sole consumer of group client.messages."""
        if self._group_client is None:
            return
        try:
            async for message in self._group_client.messages:
                topic = str(message.topic)
                payload = (
                    message.payload.decode("utf-8")
                    if isinstance(message.payload, bytes)
                    else str(message.payload)
                )

                parts = topic.split("/")
                # $a2a/v1/group/{agent_name}/chat
                if len(parts) == 5 and parts[0] == "$a2a" and parts[2] == "group" and parts[4] == "chat":
                    agent_name = parts[3]
                    try:
                        msg = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    # Dispatch to per-agent subscribers
                    for q in self._group_subscribers.get(agent_name, []):
                        await q.put(msg)
                    # Dispatch to global subscribers
                    for q in self._global_subscribers:
                        await q.put(msg)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("GroupChatTransport: listener error: %s", e)

    # ── Subscribe / unsubscribe ─────────────────────────────────

    def subscribe_group_chat(self, agent_name: str) -> asyncio.Queue[dict]:
        """Subscribe to group messages from a specific agent."""
        q: asyncio.Queue[dict] = asyncio.Queue()
        self._group_subscribers.setdefault(agent_name, []).append(q)
        return q

    def subscribe_all_group_chat(self) -> asyncio.Queue[dict]:
        """Subscribe to all group messages (for CLI display)."""
        q: asyncio.Queue[dict] = asyncio.Queue()
        self._global_subscribers.append(q)
        return q

    def unsubscribe_group_chat(self, agent_name: str, q: asyncio.Queue) -> None:
        queues = self._group_subscribers.get(agent_name)
        if queues is not None:
            try:
                queues.remove(q)
            except ValueError:
                pass
            if not queues:
                self._group_subscribers.pop(agent_name, None)

    def unsubscribe_all_group_chat(self, q: asyncio.Queue) -> None:
        try:
            self._global_subscribers.remove(q)
        except ValueError:
            pass

    # ── Publish ──────────────────────────────────────────────────

    async def publish_group_chat(
        self,
        from_agent: str,
        content: str,
        mentions: list[str] | None = None,
    ) -> str:
        """Publish a group message. Returns the msg_id."""
        msg_id = uuid4().hex
        msg = {
            "msg_id": msg_id,
            "from_agent": from_agent,
            "content": content,
            "mentions": mentions or [],
            "ts": int(time.time()),
        }
        topic = _group_chat_topic(from_agent)
        if self._group_client is not None:
            try:
                await self._group_client.publish(
                    topic,
                    json.dumps(msg, ensure_ascii=False).encode("utf-8"),
                    qos=QOS_GROUP_CHAT,
                )
                logger.debug("GroupChatTransport: published to %s (%s chars, msg_id=%s)",
                             topic, len(content), msg_id[:8])
            except Exception as e:
                logger.warning("GroupChatTransport: publish failed: %s", e)
        return msg_id

    # ── Delegate discovery to original MqttTransport ─────────────

    async def is_online(self, target: str) -> bool:
        return await self._transport.is_online(target)

    async def get_agent_status(self, target: str) -> str:
        return await self._transport.get_agent_status(target)

    async def subscribe_discovery(self) -> asyncio.Queue:
        return await self._transport.subscribe_discovery()

    def unsubscribe_discovery(self, q: asyncio.Queue) -> None:
        self._transport.unsubscribe_discovery(q)