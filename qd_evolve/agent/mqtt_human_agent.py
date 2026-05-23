"""A2A over MQTT v5 HumanAgent — wraps HumanAgent with MQTT pub/sub for serve mode.

HumanAgent uses HTTP webhooks in A2A mode. For MQTT serve mode,
MqttHumanAgent adds MQTT v5 broker connectivity: subscribes to request
topic, routes incoming tasks to HumanAgent.receive_task(), and publishes
completed responses via push notification events on the caller's event topic.

On startup:
- Publishes AgentCard to $a2a/v1/discovery/{name} (retained, a2a-status=online)
- Sets LWT to clear discovery (retained, a2a-status=offline)
- Subscribes to request topic
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes

from qd_evolve.agent.a2a import (
    AgentCard,
    Message,
    Task,
    TaskState,
    TaskStatus,
    make_text_message,
)
from qd_evolve.agent.mqtt_transport import (
    QOS_DISCOVERY,
    QOS_EVENT,
    QOS_TASK,
    _build_tls_params,
    _discovery_topic,
    _event_topic,
    _request_topic,
)
from qd_evolve.agent.server import TaskStore
from qd_evolve.core.logger import logger


class MqttHumanAgent:
    """Wraps HumanAgent with MQTT v5 server.

    Implements the same interface as HumanAgent so it can be used
    interchangeably in serve functions.
    """

    def __init__(self, human_agent: Any, broker_host: str, broker_port: int, mqtt_config: Any, will_delay_interval: int = 0) -> None:
        self._human = human_agent
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._config = mqtt_config
        self._will_delay_interval = will_delay_interval
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

    async def complete_task(self, task_id: str, response: str) -> None:
        """Complete a task — delegate to HumanAgent, then publish push notification via event topic."""
        self._human.complete_task(task_id, response)

        task = self.task_store.get(task_id)
        if task is None:
            return

        # Get the caller's agent name from task metadata
        from_agent = task.metadata.get("from_agent", "")
        if not from_agent:
            return

        # Publish push notification event to the caller's event topic
        push_event = json.dumps({
            "jsonrpc": "2.0",
            "method": "pushNotification",
            "params": {
                "task_id": task_id,
                "state": task.status.state.value if isinstance(task.status.state, TaskState) else str(task.status.state),
                "content": response,
            },
        }, ensure_ascii=False)

        if self._client is not None:
            try:
                await self._client.publish(
                    _event_topic(from_agent),
                    push_event.encode("utf-8"),
                    qos=QOS_TASK,
                )
            except Exception:
                logger.debug("MqttHumanAgent: push notification publish failed", exc_info=True)

    def receive_task(self, task_id: str, content: str, callback_url: str = "", from_agent: str = "", req_id: str = "") -> None:
        """Receive a task — delegate to HumanAgent, storing from_agent for push notification."""
        self._human.receive_task(task_id, content, callback_url=callback_url, from_agent=from_agent)
        task = self.task_store.get(task_id)
        if task:
            if from_agent:
                task.metadata["from_agent"] = from_agent

    def _push_event(self, event: dict) -> None:
        self._human._push_event(event)

    # ── MQTT v5 lifecycle ──────────────────────────────────────────

    async def start(self) -> None:
        """Connect to MQTT v5 broker, publish AgentCard (retained), set LWT, subscribe to request topic."""
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
        client_id = f"qd-evolve/{agent_name}/{agent_name}-{uuid4().hex[:8]}"

        # Build LWT: publish a2a-status=lwt on abnormal disconnect
        will_props = Properties(PacketTypes.WILLMESSAGE)
        will_props.UserProperty = [("a2a-status", "lwt")]
        if self._will_delay_interval > 0:
            will_props.WillDelayInterval = self._will_delay_interval
        will_payload = json.dumps({"a2a-status": "lwt", "agent_name": agent_name}, ensure_ascii=False)
        will = aiomqtt.Will(
            topic=_discovery_topic(agent_name),
            payload=will_payload.encode("utf-8"),
            qos=QOS_DISCOVERY,
            retain=True,
            properties=will_props,
        )

        tls = _build_tls_params(self._config)
        self._client = aiomqtt.Client(
            hostname=self._broker_host,
            port=self._broker_port,
            username=self._config.username or None,
            password=self._config.password or None,
            keepalive=self._config.keepalive,
            identifier=client_id,
            protocol=aiomqtt.ProtocolVersion.V5,
            will=will,
            tls_params=tls,
        )
        try:
            await self._client.__aenter__()
        except Exception as exc:
            logger.error("MqttHumanAgent: '%s' failed to connect to %s:%s — %s",
                         agent_name, self._broker_host, self._broker_port, exc)
            self._client = None
            raise
        self._connected = True

        # Subscribe to request topic
        await self._client.subscribe(_request_topic(agent_name), qos=QOS_TASK)

        # Publish AgentCard to discovery topic (retained, a2a-status=online)
        disc_props = Properties(PacketTypes.PUBLISH)
        disc_props.UserProperty = [("a2a-status", "online")]
        card_payload = json.dumps(self.card.model_dump(), ensure_ascii=False)
        await self._client.publish(
            _discovery_topic(agent_name),
            card_payload.encode("utf-8"),
            qos=QOS_DISCOVERY,
            retain=True,
            properties=disc_props,
        )

        # Push immediate "connected" event
        events_topic = _event_topic(agent_name)
        connected_event = json.dumps({"jsonrpc": "2.0", "method": "event",
                                       "params": {"type": "status", "text": "Connected"}}, ensure_ascii=False)
        await self._client.publish(events_topic, connected_event.encode("utf-8"), qos=QOS_EVENT)

        # Start request listener
        self._listener_task = asyncio.create_task(self._listen_requests())

        # Start event pusher
        self._event_pusher_task = asyncio.create_task(self._push_events())

        logger.info("MqttHumanAgent: '%s' connected to %s:%s (v5), LWT set, AgentCard published",
                     agent_name, self._broker_host, self._broker_port)

    async def stop(self) -> None:
        """Graceful shutdown: publish a2a-status=offline (clear retained), disconnect."""
        agent_name = self.card.name

        # Stop heartbeat loop first
        self.stop_heartbeat_loop()

        # Clear discovery retained message with a2a-status=offline (graceful, not LWT)
        if self._client is not None:
            try:
                disc_props = Properties(PacketTypes.PUBLISH)
                disc_props.UserProperty = [("a2a-status", "offline")]
                await self._client.publish(
                    _discovery_topic(agent_name),
                    b"",
                    qos=QOS_DISCOVERY,
                    retain=True,
                    properties=disc_props,
                )
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

        # Disconnect via __aexit__ to properly clean up aiomqtt internals
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
            self._client = None
        self._connected = False
        logger.info("MqttHumanAgent: '%s' disconnected", agent_name)

    # ── Request listener ────────────────────────────────────────────

    async def _listen_requests(self) -> None:
        if self._client is None:
            return
        agent_name = self.card.name
        request_prefix = _request_topic(agent_name)

        try:
            async for message in self._client.messages:
                topic = str(message.topic)
                if topic != request_prefix:
                    continue

                payload = message.payload.decode("utf-8") if isinstance(message.payload, bytes) else str(message.payload)
                try:
                    rpc = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                method = rpc.get("method", "")
                params = rpc.get("params", {})
                req_id = rpc.get("id", "")

                # Extract from_agent from User Properties
                from_agent = ""
                user_props = getattr(message.properties, "UserProperty", None) if message.properties else None
                if user_props:
                    for key, val in user_props:
                        if key == "a2a-from-agent":
                            from_agent = val
                            break
                if not from_agent:
                    from_agent = params.get("message", {}).get("metadata", {}).get("from_agent", "")

                asyncio.ensure_future(self._dispatch(method, params, req_id, from_agent, message))

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("MqttHumanAgent: listener error: %s", e)

    async def _dispatch(self, method: str, params: dict, req_id: str, from_agent: str, message: Any) -> None:
        try:
            if method == "message/send":
                await self._on_message_send(params, req_id, from_agent, message)
            elif method == "message/stream":
                await self._on_message_stream(params, req_id, from_agent, message)
            elif method == "tasks/get":
                await self._on_tasks_get(params, req_id, from_agent, message)
            elif method == "tasks/cancel":
                await self._on_tasks_cancel(params, req_id, from_agent, message)
            elif method == "agent/getExtendedAgentCard":
                await self._on_extended_card(params, req_id, from_agent, message)
            else:
                logger.debug("MqttHumanAgent: unknown method '%s'", method)
        except Exception as e:
            logger.exception("MqttHumanAgent: error handling '%s': %s", method, e)
            if req_id:
                await self._publish_error_response(message, req_id, str(e))

    # ── Request handlers ────────────────────────────────────────────

    async def _on_message_send(self, params: dict, req_id: str, from_agent: str, message: Any) -> None:
        message_data = params.get("message", {})
        msg = Message.model_validate(message_data) if message_data else make_text_message("user", "")
        task_text = self._extract_text(msg)

        task_id = params.get("task_id", "") or f"task-{uuid4().hex[:12]}"

        # Store from_agent in task metadata before receive_task
        self.receive_task(task_id, task_text, callback_url="", from_agent=from_agent)

        # Publish input_required response immediately
        task = self.task_store.get(task_id)
        if task and req_id:
            await self._publish_rpc_response(message, req_id, task.model_dump())

    async def _on_message_stream(self, params: dict, req_id: str, from_agent: str, message: Any) -> None:
        # For human agents, stream is the same as send
        await self._on_message_send(params, req_id, from_agent, message)

    async def _on_tasks_get(self, params: dict, req_id: str, from_agent: str, message: Any) -> None:
        task_id = params.get("id", "")
        task = self.task_store.get(task_id)
        if task is None:
            task = Task(status=TaskStatus(state=TaskState.failed, message=make_text_message("agent", f"Task '{task_id}' not found")))
        if req_id:
            await self._publish_rpc_response(message, req_id, task.model_dump())

    async def _on_tasks_cancel(self, params: dict, req_id: str, from_agent: str, message: Any) -> None:
        task_id = params.get("id", "")
        task = self.task_store.update_state(task_id, TaskState.canceled)
        if task is None:
            task = Task(status=TaskStatus(state=TaskState.failed, message=make_text_message("agent", f"Task '{task_id}' not found")))
        if req_id:
            await self._publish_rpc_response(message, req_id, task.model_dump())

    async def _on_extended_card(self, params: dict, req_id: str, from_agent: str, message: Any) -> None:
        task = Task(
            status=TaskStatus(state=TaskState.completed, message=make_text_message("agent", "agent_card")),
            metadata={"agent_card": self.card.model_dump()},
        )
        if req_id:
            await self._publish_rpc_response(message, req_id, task.model_dump())

    # ── Event pusher ────────────────────────────────────────────────

    async def _push_events(self) -> None:
        queue = self._human.subscribe_events()
        agent_name = self.card.name
        events_topic = _event_topic(agent_name)

        try:
            while True:
                event = await queue.get()
                try:
                    rpc = json.dumps({"jsonrpc": "2.0", "method": "event", "params": event}, ensure_ascii=False)
                    await self._client.publish(events_topic, rpc.encode("utf-8"), qos=QOS_EVENT)
                except Exception as e:
                    logger.debug("MqttHumanAgent: event push failed: %s", e)
        except asyncio.CancelledError:
            pass
        finally:
            self._human.unsubscribe_events(queue)

    # ── Publish helpers ─────────────────────────────────────────────

    async def _publish_rpc_response(self, original_message: Any, req_id: str, data: dict) -> None:
        """Publish JSON-RPC response using MQTT v5 Response Topic + Correlation Data."""
        if self._client is None:
            return

        rpc = {"jsonrpc": "2.0", "result": data, "id": req_id}
        payload = json.dumps(rpc, ensure_ascii=False).encode("utf-8")

        if original_message.properties and original_message.properties.ResponseTopic:
            resp_topic = original_message.properties.ResponseTopic
            resp_props = Properties(PacketTypes.PUBLISH)
            if original_message.properties.CorrelationData:
                resp_props.CorrelationData = original_message.properties.CorrelationData
            await self._client.publish(resp_topic, payload, qos=QOS_TASK, properties=resp_props)
        else:
            logger.warning("MqttHumanAgent: no ResponseTopic in request — cannot reply")

    async def _publish_error_response(self, original_message: Any, req_id: str, error: str) -> None:
        task = Task(
            status=TaskStatus(state=TaskState.failed, message=make_text_message("agent", error)),
        )
        await self._publish_rpc_response(original_message, req_id, task.model_dump())

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(message: Message) -> str:
        for part in message.parts:
            if part.type == "text" and part.text:
                return part.text
        return ""