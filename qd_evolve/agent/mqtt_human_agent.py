"""MqttHumanAgent — wraps HumanAgent with MQTT pub/sub for serve mode.

HumanAgent uses HTTP webhooks in A2A mode. For MQTT serve mode,
MqttHumanAgent adds MQTT broker connectivity: subscribes to request
topics, routes incoming tasks to HumanAgent.receive_task(), and
publishes completed responses via MQTT instead of HTTP webhooks.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from qd_evolve.agent.a2a import (
    AgentCard,
    Message,
    Task,
    TaskState,
    TaskStatus,
    make_text_message,
    make_task_with_text,
)
from qd_evolve.agent.server import TaskStore
from qd_evolve.core.logger import logger

QOS_TASK = 1
QOS_EVENT = 0


class MqttHumanAgent:
    """Wraps HumanAgent with MQTT server — subscribes to request topics, publishes responses.

    Implements the same interface as HumanAgent so it can be used
    interchangeably in serve functions.
    """

    def __init__(self, human_agent: Any, broker_host: str, broker_port: int, mqtt_config: Any) -> None:
        self._human = human_agent
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._config = mqtt_config
        self._client: Any = None
        self._connected = False
        self._listener_task: asyncio.Task | None = None
        self._event_pusher_task: asyncio.Task | None = None

    # ── Delegates to HumanAgent ─────────────────────────────────────

    @property
    def card(self) -> AgentCard:
        return self._human.card

    @property
    def task_store(self) -> TaskStore:
        return self._human.task_store

    def run(self, message: str, **kwargs: Any) -> str:
        return self._human.run(message, **kwargs)

    def subscribe_events(self) -> asyncio.Queue:
        return self._human.subscribe_events()

    def unsubscribe_events(self, queue: asyncio.Queue) -> None:
        self._human.unsubscribe_events(queue)

    def start_heartbeat_loop(self, idle_seconds: int) -> None:
        self._human.start_heartbeat_loop(idle_seconds)

    def stop_heartbeat_loop(self) -> None:
        self._human.stop_heartbeat_loop()

    def complete_task(self, task_id: str, response: str) -> None:
        """Complete a task — delegate to HumanAgent, then publish via MQTT."""
        self._human.complete_task(task_id, response)

        # Publish completed result via MQTT
        task = self.task_store.get(task_id)
        if task is None:
            return
        from_agent = task.metadata.get("from_agent", "")
        req_id = task.metadata.get("req_id", "")
        if from_agent and req_id and self._client is not None:
            asyncio.ensure_future(self._publish_response(from_agent, req_id, task.model_dump()))

    def receive_task(self, task_id: str, content: str, callback_url: str = "", from_agent: str = "", req_id: str = "") -> None:
        """Receive a task — delegate to HumanAgent, storing from_agent + req_id for MQTT response."""
        self._human.receive_task(task_id, content, callback_url=callback_url, from_agent=from_agent)
        task = self.task_store.get(task_id)
        if task:
            if from_agent:
                task.metadata["from_agent"] = from_agent
            if req_id:
                task.metadata["req_id"] = req_id

    def _push_event(self, event: dict) -> None:
        self._human._push_event(event)

    # ── MQTT lifecycle ──────────────────────────────────────────────

    async def start(self) -> None:
        """Connect to MQTT broker, subscribe to request topics, publish online status."""
        if not self._broker_host or not self._broker_port:
            return

        try:
            import aiomqtt
        except ImportError:
            logger.error("aiomqtt not installed — run: pip install aiomqtt")
            raise

        if self._connected:
            return

        agent_name = self.card.name
        client_id = f"qd-evolve-human-{agent_name}-{uuid4().hex[:8]}"

        self._client = aiomqtt.Client(
            hostname=self._broker_host,
            port=self._broker_port,
            username=self._config.username or None if self._config else None,
            password=self._config.password or None if self._config else None,
            keepalive=self._config.keepalive if self._config else 60,
            identifier=client_id,
        )
        try:
            await self._client.__aenter__()
        except Exception as exc:
            logger.error("MqttHumanAgent: '%s' failed to connect to %s:%s — %s",
                         agent_name, self._broker_host, self._broker_port, exc)
            self._client = None
            raise
        self._connected = True

        # Subscribe to all request topics for this agent
        request_topic = f"a2a/{agent_name}/#"
        await self._client.subscribe(request_topic, qos=QOS_TASK)

        # Publish retained "online" message
        online_topic = f"a2a/{agent_name}/agent/online"
        online_payload = json.dumps({
            "name": agent_name,
            "description": self.card.description,
            "status": "online",
        }, ensure_ascii=False)
        await self._client.publish(online_topic, online_payload.encode("utf-8"), qos=QOS_EVENT, retain=True)

        # Start request listener
        self._listener_task = asyncio.create_task(self._listen_requests())

        # Start event pusher
        self._event_pusher_task = asyncio.create_task(self._push_events())

        logger.info("MqttHumanAgent: '%s' connected to %s:%s, subscribed to %s",
                     agent_name, self._broker_host, self._broker_port, request_topic)

    async def stop(self) -> None:
        """Disconnect from MQTT broker and clean up."""
        agent_name = self.card.name

        # Publish offline status
        if self._client is not None:
            try:
                online_topic = f"a2a/{agent_name}/agent/online"
                offline_payload = json.dumps({
                    "name": agent_name,
                    "status": "offline",
                }, ensure_ascii=False)
                await self._client.publish(online_topic, offline_payload.encode("utf-8"), qos=QOS_EVENT, retain=True)
            except Exception:
                pass

        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        if self._event_pusher_task is not None:
            self._event_pusher_task.cancel()
            try:
                await self._event_pusher_task
            except asyncio.CancelledError:
                pass
            self._event_pusher_task = None

        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._connected = False
        logger.info("MqttHumanAgent: '%s' disconnected", agent_name)

    # ── Request listener ────────────────────────────────────────────

    async def _listen_requests(self) -> None:
        """Background task: listen for incoming MQTT requests and dispatch."""
        if self._client is None:
            return
        agent_name = self.card.name
        prefix = f"a2a/{agent_name}/"

        try:
            async for message in self._client.messages:
                topic = str(message.topic)
                if not topic.startswith(prefix):
                    continue

                payload = message.payload.decode("utf-8") if isinstance(message.payload, bytes) else str(message.payload)
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                method = topic[len(prefix):]

                # Skip non-request topics (events, online status)
                if method == "events" or method.startswith("agent/"):
                    continue

                req_id = data.get("req_id", "")
                from_agent = data.get("from_agent", "")

                asyncio.ensure_future(self._dispatch(method, data, req_id, from_agent))

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("MqttHumanAgent: listener error: %s", e)

    async def _dispatch(self, method: str, data: dict, req_id: str, from_agent: str) -> None:
        """Dispatch a request to the appropriate handler."""
        try:
            if method == "message/send":
                await self._on_message_send(data, req_id, from_agent)
            elif method == "message/stream":
                await self._on_message_stream(data, req_id, from_agent)
            elif method == "tasks/get":
                await self._on_tasks_get(data, req_id, from_agent)
            elif method == "tasks/cancel":
                await self._on_tasks_cancel(data, req_id, from_agent)
            elif method == "agent/card":
                await self._on_agent_card(data, req_id, from_agent)
            else:
                logger.debug("MqttHumanAgent: unknown method '%s'", method)
        except Exception as e:
            logger.exception("MqttHumanAgent: error handling '%s': %s", method, e)
            if req_id and from_agent:
                await self._publish_error(from_agent, req_id, str(e))

    # ── Request handlers ────────────────────────────────────────────

    async def _on_message_send(self, data: dict, req_id: str, from_agent: str) -> None:
        """Handle message/send: create input_required task via receive_task, publish response."""
        message_data = data.get("message", {})
        message = Message.model_validate(message_data) if message_data else make_text_message("user", "")
        task_text = self._extract_text(message)

        task_id = data.get("task_id", "") or f"task-{uuid4().hex[:12]}"

        # Store from_agent in task metadata before receive_task creates the task
        self.receive_task(task_id, task_text, callback_url="", from_agent=from_agent, req_id=req_id)

        # Publish input_required response immediately
        task = self.task_store.get(task_id)
        if task and req_id and from_agent:
            await self._publish_response(from_agent, req_id, task.model_dump())

    async def _on_message_stream(self, data: dict, req_id: str, from_agent: str) -> None:
        """Handle message/stream: same as message/send for human agents."""
        await self._on_message_send(data, req_id, from_agent)

    async def _on_tasks_get(self, data: dict, req_id: str, from_agent: str) -> None:
        """Handle tasks/get: return task from store."""
        task_id = data.get("id", "")
        task = self.task_store.get(task_id)
        if task is None:
            task = Task(status=TaskStatus(state=TaskState.failed, message=make_text_message("agent", f"Task '{task_id}' not found")))
        if req_id and from_agent:
            await self._publish_response(from_agent, req_id, task.model_dump())

    async def _on_tasks_cancel(self, data: dict, req_id: str, from_agent: str) -> None:
        """Handle tasks/cancel: mark task as canceled."""
        task_id = data.get("id", "")
        task = self.task_store.update_state(task_id, TaskState.canceled)
        if task is None:
            task = Task(status=TaskStatus(state=TaskState.failed, message=make_text_message("agent", f"Task '{task_id}' not found")))
        if req_id and from_agent:
            await self._publish_response(from_agent, req_id, task.model_dump())

    async def _on_agent_card(self, data: dict, req_id: str, from_agent: str) -> None:
        """Handle agent/card: return AgentCard."""
        task = Task(
            status=TaskStatus(state=TaskState.completed, message=make_text_message("agent", "agent_card")),
            metadata={"agent_card": self.card.model_dump()},
        )
        if req_id and from_agent:
            await self._publish_response(from_agent, req_id, task.model_dump())

    # ── Event pusher ────────────────────────────────────────────────

    async def _push_events(self) -> None:
        """Background task: subscribe to HumanAgent events and push to MQTT events topic."""
        queue = self._human.subscribe_events()
        agent_name = self.card.name
        events_topic = f"a2a/{agent_name}/events"

        try:
            while True:
                event = await queue.get()
                try:
                    payload = json.dumps(event, ensure_ascii=False)
                    await self._client.publish(events_topic, payload.encode("utf-8"), qos=QOS_EVENT)
                except Exception as e:
                    logger.debug("MqttHumanAgent: event push failed: %s", e)
        except asyncio.CancelledError:
            pass
        finally:
            self._human.unsubscribe_events(queue)

    # ── Publish helpers ─────────────────────────────────────────────

    async def _publish_response(self, target_agent: str, req_id: str, data: dict) -> None:
        """Publish response to a2a/{target_agent}/response/{req_id}."""
        if self._client is None:
            return
        topic = f"a2a/{target_agent}/response/{req_id}"
        payload = json.dumps(data, ensure_ascii=False)
        await self._client.publish(topic, payload.encode("utf-8"), qos=QOS_TASK)

    async def _publish_error(self, target_agent: str, req_id: str, error: str) -> None:
        """Publish error response."""
        task = Task(
            status=TaskStatus(state=TaskState.failed, message=make_text_message("agent", error)),
        )
        await self._publish_response(target_agent, req_id, task.model_dump())

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(message: Message) -> str:
        for part in message.parts:
            if part.type == "text" and part.text:
                return part.text
        return ""
