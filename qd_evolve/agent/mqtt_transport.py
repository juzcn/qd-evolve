"""MQTT transport — pub/sub A2A protocol over MQTT broker.

Implements the same A2ATransport interface as InprocTransport/HttpTransport.
Uses aiomqtt for async MQTT client communication.

Topic structure:
  a2a/{agent}/message/send       → Request: send task (blocking)
  a2a/{agent}/message/stream     → Request: stream task (SSE-like)
  a2a/{agent}/tasks/get          → Request: get task status
  a2a/{agent}/tasks/cancel       → Request: cancel task
  a2a/{agent}/agent/card         → Request: get agent card
  a2a/{agent}/events             → Pub: event stream (heartbeat, status, etc.)
  a2a/{agent}/response/{req_id}  → Response: per-request reply
  a2a/{agent}/agent/online       → Retained: online status

Message format: JSON payload with existing A2A pydantic models.
Each request includes a req_id; responses go to a2a/{agent}/response/{req_id}.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator
from uuid import uuid4

from qd_evolve.agent.a2a import (
    AgentCard,
    Message,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    make_text_message,
    make_task_with_text,
)
from qd_evolve.core.config import MqttConfig
from qd_evolve.core.logger import logger

# QoS levels
QOS_TASK = 1  # at-least-once for task commands
QOS_EVENT = 0  # at-most-once for heartbeat/status events


def _topic(agent: str, method: str) -> str:
    """Build MQTT topic: a2a/{agent}/{method}."""
    return f"a2a/{agent}/{method}"


def _response_topic(agent: str, req_id: str) -> str:
    """Build response topic: a2a/{agent}/response/{req_id}."""
    return f"a2a/{agent}/response/{req_id}"


def _new_req_id() -> str:
    return uuid4().hex


class MqttTransport:
    """MQTT transport — pub/sub A2A protocol over MQTT broker.

    Uses aiomqtt for async MQTT client. Each request includes a req_id;
    the response arrives on a2a/{self_name}/response/{req_id}.
    """

    def __init__(self, broker_host: str, broker_port: int, mqtt_config: MqttConfig, client_name: str = "") -> None:
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._config = mqtt_config
        self._client_name = client_name
        self._client: Any = None  # aiomqtt.Client
        self._connected = False
        self._pending: dict[str, asyncio.Future[Task]] = {}
        self._listener_task: asyncio.Task | None = None
        self._registry: Any = None

    def _get_registry(self) -> Any:
        if self._registry is None:
            from qd_evolve.agent.registry import get_agent_registry
            self._registry = get_agent_registry()
        return self._registry

    async def connect(self, timeout: float = 10.0) -> None:
        """Connect to MQTT broker and start listener."""
        try:
            import aiomqtt
        except ImportError:
            logger.error("aiomqtt not installed — run: pip install aiomqtt")
            raise

        if self._connected:
            return

        client_id = f"qd-evolve-{self._client_name or 'transport'}-{uuid4().hex[:8]}"
        self._client = aiomqtt.Client(
            hostname=self._broker_host,
            port=self._broker_port,
            username=self._config.username or None,
            password=self._config.password or None,
            keepalive=self._config.keepalive,
            identifier=client_id,
        )
        # aiomqtt.Client is an async context manager — enter it to connect
        try:
            await asyncio.wait_for(self._client.__aenter__(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Connection to MQTT broker at {self._broker_host}:{self._broker_port} timed out. "
                f"Is the broker running? Start it with: qd-evolve mqtt broker"
            )
        self._connected = True
        logger.info("MqttTransport: connected to %s:%s as %s",
                     self._broker_host, self._broker_port, client_id)

        # Start listener for responses
        self._listener_task = asyncio.create_task(self._listen_responses())

    async def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._connected = False
        logger.info("MqttTransport: disconnected")

    async def _listen_responses(self) -> None:
        """Background task: listen for response messages on subscribed topics."""
        if self._client is None:
            return
        try:
            async for message in self._client.messages:
                topic = str(message.topic)
                payload = message.payload.decode("utf-8") if isinstance(message.payload, bytes) else str(message.payload)
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                # Check if it's a response topic: a2a/{agent}/response/{req_id}
                parts = topic.split("/")
                if len(parts) == 4 and parts[0] == "a2a" and parts[2] == "response":
                    req_id = parts[3]
                    future = self._pending.get(req_id)
                    if future and not future.done():
                        try:
                            task = Task.model_validate(data)
                            future.set_result(task)
                        except Exception as e:
                            future.set_exception(e)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("MqttTransport: listener error: %s", e)

    async def _subscribe_response(self, req_id: str) -> None:
        """Subscribe to response topic for a specific request."""
        if self._client is None:
            return
        topic = _response_topic(self._client_name, req_id)
        await self._client.subscribe(topic, qos=QOS_TASK)

    async def _unsubscribe_response(self, req_id: str) -> None:
        """Unsubscribe from response topic after request completes."""
        if self._client is None:
            return
        topic = _response_topic(self._client_name, req_id)
        try:
            await self._client.unsubscribe(topic)
        except Exception:
            pass

    async def _publish_request(self, target: str, method: str, payload: dict, req_id: str) -> None:
        """Publish a request to a2a/{target}/{method}."""
        if self._client is None:
            raise RuntimeError("MqttTransport not connected")
        payload["req_id"] = req_id
        payload["from_agent"] = self._client_name
        topic = _topic(target, method)
        await self._client.publish(topic, json.dumps(payload, ensure_ascii=False).encode("utf-8"), qos=QOS_TASK)

    # ── A2ATransport interface ──────────────────────────────────────

    async def send_task(self, target: str, message: Message) -> Task:
        """Blocking: publish message/send, await response on response topic."""
        req_id = _new_req_id()
        msg_dict = message.model_dump()
        payload = {"message": msg_dict}

        await self._subscribe_response(req_id)
        future: asyncio.Future[Task] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        try:
            await self._publish_request(target, "message/send", payload, req_id)
            # Wait for response with timeout
            try:
                result = await asyncio.wait_for(future, timeout=120)
            except asyncio.TimeoutError:
                return self._error_task(target, f"Timeout waiting for response from '{target}'")
            return result
        finally:
            self._pending.pop(req_id, None)
            await self._unsubscribe_response(req_id)

    async def send_stream(self, target: str, message: Message) -> AsyncIterator[StreamResponse]:
        """Stream: publish message/stream, subscribe to events topic, yield StreamResponse events."""
        if self._client is None:
            yield StreamResponse(task=self._error_task(target, "MqttTransport not connected"))
            return

        req_id = _new_req_id()
        msg_dict = message.model_dump()
        payload = {"message": msg_dict}

        # Subscribe to events and response topics
        events_topic = _topic(target, "events")
        await self._client.subscribe(events_topic, qos=QOS_EVENT)
        await self._subscribe_response(req_id)

        # Publish stream request
        await self._publish_request(target, "message/stream", payload, req_id)

        # First event: create a Task object
        task_text = self._extract_text(message)
        task = make_task_with_text(task_text)
        task.status.state = TaskState.working
        yield StreamResponse(task=task)

        try:
            # Listen for events until final response
            final_received = False
            while not final_received:
                try:
                    # Use the client's message iterator with timeout
                    async for message in self._client.messages:
                        topic = str(message.topic)
                        raw = message.payload.decode("utf-8") if isinstance(message.payload, bytes) else str(message.payload)
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        # Check if it's a response (final)
                        parts = topic.split("/")
                        if len(parts) == 4 and parts[0] == "a2a" and parts[2] == "response" and parts[3] == req_id:
                            result_task = Task.model_validate(data)
                            final_state = result_task.status.state
                            result_text = self._extract_task_text(result_task)
                            yield StreamResponse(statusUpdate=TaskStatusUpdateEvent(
                                task_id=task.id,
                                context_id=task.session_id,
                                status=TaskStatus(state=final_state, message=make_text_message("agent", result_text)),
                                final=True,
                            ))
                            final_received = True
                            break

                        # Check if it's an event from the target agent
                        if topic == events_topic:
                            event = data
                            etype = event.get("type", "")
                            if etype == "final":
                                # Final event from streaming agent
                                result_text = event.get("content", "")
                                final_state_str = event.get("state", "completed")
                                try:
                                    final_state = TaskState(final_state_str)
                                except ValueError:
                                    final_state = TaskState.completed
                                yield StreamResponse(statusUpdate=TaskStatusUpdateEvent(
                                    task_id=task.id,
                                    context_id=task.session_id,
                                    status=TaskStatus(state=final_state, message=make_text_message("agent", result_text)),
                                    final=True,
                                ))
                                final_received = True
                                break
                            else:
                                # Intermediate event (iteration, status, print, tokens, heartbeat)
                                yield StreamResponse(statusUpdate=TaskStatusUpdateEvent(
                                    task_id=task.id,
                                    context_id=task.session_id,
                                    status=TaskStatus(state=TaskState.working),
                                    metadata=event,
                                ))

                except asyncio.TimeoutError:
                    # Send ping-like event
                    yield StreamResponse(statusUpdate=TaskStatusUpdateEvent(
                        task_id=task.id,
                        context_id=task.session_id,
                        status=TaskStatus(state=TaskState.working),
                        metadata={"type": "ping"},
                    ))
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            try:
                await self._client.unsubscribe(events_topic)
            except Exception:
                pass
            await self._unsubscribe_response(req_id)
            self._pending.pop(req_id, None)

    async def get_task(self, target: str, task_id: str) -> Task:
        """Query task status via MQTT request/response."""
        req_id = _new_req_id()
        payload = {"id": task_id}

        await self._subscribe_response(req_id)
        future: asyncio.Future[Task] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        try:
            await self._publish_request(target, "tasks/get", payload, req_id)
            try:
                result = await asyncio.wait_for(future, timeout=30)
            except asyncio.TimeoutError:
                return self._error_task(target, f"Timeout querying task '{task_id}' from '{target}'")
            return result
        finally:
            self._pending.pop(req_id, None)
            await self._unsubscribe_response(req_id)

    async def cancel_task(self, target: str, task_id: str) -> Task:
        """Cancel task via MQTT request/response."""
        req_id = _new_req_id()
        payload = {"id": task_id}

        await self._subscribe_response(req_id)
        future: asyncio.Future[Task] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        try:
            await self._publish_request(target, "tasks/cancel", payload, req_id)
            try:
                result = await asyncio.wait_for(future, timeout=30)
            except asyncio.TimeoutError:
                return self._error_task(target, f"Timeout canceling task '{task_id}' on '{target}'")
            return result
        finally:
            self._pending.pop(req_id, None)
            await self._unsubscribe_response(req_id)

    async def get_agent_card(self, target: str) -> AgentCard:
        """Discover remote agent's capabilities via MQTT request/response."""
        req_id = _new_req_id()
        payload = {}

        await self._subscribe_response(req_id)
        future: asyncio.Future[Task] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        try:
            await self._publish_request(target, "agent/card", payload, req_id)
            try:
                result_task = await asyncio.wait_for(future, timeout=10)
            except asyncio.TimeoutError:
                return AgentCard(name=target, description=f"Agent '{target}' timeout")

            # The response Task contains the AgentCard in metadata
            card_data = result_task.metadata.get("agent_card", {})
            if card_data:
                return AgentCard.model_validate(card_data)
            # Fallback: extract from message
            if result_task.status.message:
                for part in result_task.status.message.parts:
                    if part.type == "text" and part.text:
                        try:
                            return AgentCard.model_validate(json.loads(part.text))
                        except json.JSONDecodeError:
                            pass
            return AgentCard(name=target, description=f"Agent '{target}' card unavailable")
        finally:
            self._pending.pop(req_id, None)
            await self._unsubscribe_response(req_id)

    async def get_extended_agent_card(self, target: str) -> AgentCard:
        """Get extended AgentCard with runtime status via MQTT."""
        req_id = _new_req_id()
        payload = {"extended": True}

        await self._subscribe_response(req_id)
        future: asyncio.Future[Task] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        try:
            await self._publish_request(target, "agent/card", payload, req_id)
            try:
                result_task = await asyncio.wait_for(future, timeout=10)
            except asyncio.TimeoutError:
                return AgentCard(name=target, description=f"Agent '{target}' timeout")

            card_data = result_task.metadata.get("agent_card", {})
            if card_data:
                return AgentCard.model_validate(card_data)
            return AgentCard(name=target, description=f"Agent '{target}' extended card unavailable")
        finally:
            self._pending.pop(req_id, None)
            await self._unsubscribe_response(req_id)

    async def resubscribe(self, target: str, task_id: str = "") -> AsyncIterator[StreamResponse]:
        """Subscribe to agent events via MQTT events topic."""
        if self._client is None:
            return

        events_topic = _topic(target, "events")
        await self._client.subscribe(events_topic, qos=QOS_EVENT)

        try:
            while True:
                async for message in self._client.messages:
                    topic = str(message.topic)
                    if topic != events_topic:
                        continue
                    raw = message.payload.decode("utf-8") if isinstance(message.payload, bytes) else str(message.payload)
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    yield StreamResponse(statusUpdate=TaskStatusUpdateEvent(
                        task_id=task_id,
                        status=TaskStatus(state=TaskState.working),
                        metadata=event,
                    ))
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            try:
                await self._client.unsubscribe(events_topic)
            except Exception:
                pass

    async def is_online(self, target: str) -> bool:
        """Check if a remote agent is online via retained message on agent/online topic."""
        if self._client is None:
            return False

        online_topic = _topic(target, "agent/online")
        try:
            # Subscribe and check for retained message
            await self._client.subscribe(online_topic, qos=QOS_EVENT)
            # Wait briefly for retained message
            try:
                async for message in self._client.messages:
                    topic = str(message.topic)
                    if topic == online_topic:
                        await self._client.unsubscribe(online_topic)
                        return True
                    # If we get other messages, keep waiting briefly
            except asyncio.TimeoutError:
                pass
            await self._client.unsubscribe(online_topic)
            return False
        except Exception:
            return False

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(message: Message) -> str:
        for part in message.parts:
            if part.type == "text" and part.text:
                return part.text
        return ""

    @staticmethod
    def _extract_task_text(task: Task) -> str:
        if task.status.message:
            for part in task.status.message.parts:
                if part.type == "text" and part.text:
                    return part.text
        return ""

    @staticmethod
    def _error_task(target: str, error: str) -> Task:
        return Task(
            status=TaskStatus(
                state=TaskState.failed,
                message=make_text_message("agent", error),
            ),
            metadata={"target": target},
        )