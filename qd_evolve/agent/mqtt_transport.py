"""A2A over MQTT v5 transport — pub/sub with JSON-RPC payloads.

Implements the A2ATransport interface using MQTT v5 as the transport layer.
Uses MQTT v5 features: Response Topic, Correlation Data, User Properties,
Last Will and Testament (LWT), and Retained Messages.

Topic structure (A2A over MQTT spec):
  $a2a/v1/discovery/{agent_name}           (retained, Agent Card)
  $a2a/v1/request/{agent_name}             (task requests)
  $a2a/v1/response/{agent_name}/{req_id}   (task responses)
  $a2a/v1/event/{agent_name}               (streaming events + push notifications)

Message format: A2A JSON-RPC (same payloads as HTTP transport).
Request-response correlation: MQTT v5 Response Topic + Correlation Data.
Service discovery: Retained AgentCard on discovery topic + LWT for offline.
Push notifications: pushNotification events on the caller's event topic.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator
from uuid import uuid4

from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes

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
QOS_TASK = 1     # at-least-once for task commands
QOS_EVENT = 0    # at-most-once for streaming events
QOS_DISCOVERY = 1  # at-least-once for discovery (retained)


# ── Topic helpers ──────────────────────────────────────────────────

def _discovery_topic(name: str) -> str:
    return f"$a2a/v1/discovery/{name}"

def _request_topic(name: str) -> str:
    return f"$a2a/v1/request/{name}"

def _response_topic(name: str, req_id: str = "+") -> str:
    return f"$a2a/v1/response/{name}/{req_id}"

def _event_topic(name: str) -> str:
    return f"$a2a/v1/event/{name}"

def _new_req_id() -> str:
    return uuid4().hex


def _build_tls_params(config: MqttConfig) -> Any:
    """Build aiomqtt.TLSParameters from MqttConfig, or return None if no TLS."""
    if not config.ca_certs and not config.certfile:
        return None
    import aiomqtt
    return aiomqtt.TLSParameters(
        ca_certs=config.ca_certs or None,
        certfile=config.certfile or None,
        keyfile=config.keyfile or None,
    )


# ── JSON-RPC helpers ───────────────────────────────────────────────

def _rpc_request(method: str, params: dict, req_id: str) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params, "id": req_id}

def _rpc_response(result: dict, req_id: str) -> dict:
    return {"jsonrpc": "2.0", "result": result, "id": req_id}

def _rpc_event(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


class MqttTransport:
    """A2A over MQTT v5 transport.

    Single-consumer design: _listen_all() is the sole consumer of
    self._client.messages. Other methods register subscriber queues
    to receive specific message types.

    MQTT v5 features used:
    - Response Topic + Correlation Data for request-response correlation
    - User Properties for a2a-from-agent, a2a-status, a2a-authorization
    - LWT (Last Will) for offline detection
    - Retained messages for Agent Card discovery
    """

    def __init__(self, broker_host: str, broker_port: int, mqtt_config: MqttConfig, client_name: str = "") -> None:
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._config = mqtt_config
        self._client_name = client_name
        self._client: Any = None  # aiomqtt.Client
        self._connected = False
        self._listener_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        # Pending futures keyed by req_id (from CorrelationData)
        self._pending: dict[str, asyncio.Future[Task]] = {}

        # Event subscriber registry: agent_name → list of queues
        self._event_subscribers: dict[str, list[asyncio.Queue[dict]]] = {}

        # Discovery subscriber registry: list of queues (receives all discovery events)
        self._discovery_subscribers: list[asyncio.Queue[dict]] = []

        # One-shot online check futures: agent_name → list of futures
        self._online_subscribers: dict[str, list[asyncio.Future[bool]]] = {}

        # Last known discovery status: agent_name → "online"|"offline"|"lwt"
        self._last_discovery_status: dict[str, str] = {}

    async def connect(self, timeout: float = 10.0) -> None:
        """Connect to MQTT v5 broker and start the sole message listener."""
        try:
            import aiomqtt
        except ImportError:
            logger.error("aiomqtt not installed — run: pip install aiomqtt")
            raise

        if self._connected:
            return

        client_id = f"qd-evolve/{self._client_name or 'cli'}/{uuid4().hex[:8]}"
        tls = _build_tls_params(self._config)
        self._client = aiomqtt.Client(
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
            await asyncio.wait_for(self._client.__aenter__(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Connection to MQTT broker at {self._broker_host}:{self._broker_port} timed out. "
                f"Is mosquitto running?"
            )
        self._connected = True
        self._loop = asyncio.get_running_loop()
        logger.info("MqttTransport: connected to %s:%s (v5) as %s",
                     self._broker_host, self._broker_port, client_id)

        # Subscribe to response topic for this client
        await self._client.subscribe(_response_topic(self._client_name, "+"), qos=QOS_TASK)

        # Subscribe to all discovery topics
        await self._client.subscribe("$a2a/v1/discovery/+", qos=QOS_DISCOVERY)

        # Start the sole message listener
        self._listener_task = asyncio.create_task(self._listen_all())

    async def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        # Clear pending futures
        for f in self._pending.values():
            if not f.done():
                f.cancel()
        self._pending.clear()

        # Clear subscriber registries
        for queues in self._event_subscribers.values():
            for q in queues:
                while not q.empty():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
        self._event_subscribers.clear()
        self._discovery_subscribers.clear()
        for futures in self._online_subscribers.values():
            for f in futures:
                if not f.done():
                    f.cancel()
        self._online_subscribers.clear()

        if self._client is not None:
            self._client._client.on_unsubscribe = None  # noqa: SLF001
            try:
                await asyncio.wait_for(self._client.__aexit__(None, None, None), timeout=3.0)
            except (Exception, asyncio.TimeoutError):
                pass
            self._client = None
        self._connected = False
        logger.info("MqttTransport: disconnected")

    # ── Sole message consumer ─────────────────────────────────────

    async def _listen_all(self) -> None:
        """Background task: sole consumer of self._client.messages.

        Dispatches to:
        - _pending futures for response topics (via CorrelationData)
        - _event_subscribers queues for event topics
        - _discovery_subscribers queues for discovery topics
        - _online_subscribers futures for discovery topics (one-shot)
        """
        if self._client is None:
            return
        try:
            async for message in self._client.messages:
                topic = str(message.topic)
                payload = message.payload.decode("utf-8") if isinstance(message.payload, bytes) else str(message.payload)

                parts = topic.split("/")

                # Response topic: $a2a/v1/response/{agent_name}/{req_id}
                if len(parts) == 5 and parts[0] == "$a2a" and parts[1] == "v1" and parts[2] == "response":
                    req_id = parts[4]
                    # Also try CorrelationData from v5 properties
                    correlation_data = getattr(message.properties, "CorrelationData", None) if message.properties else None
                    if correlation_data:
                        req_id = correlation_data.decode("utf-8")
                    future = self._pending.get(req_id)
                    if future and not future.done():
                        try:
                            rpc = json.loads(payload)
                            result = rpc.get("result", {})
                            task = Task.model_validate(result)
                            future.set_result(task)
                        except Exception as e:
                            future.set_exception(e)

                # Event topic: $a2a/v1/event/{agent_name}
                elif len(parts) == 4 and parts[0] == "$a2a" and parts[1] == "v1" and parts[2] == "event":
                    agent_name = parts[3]
                    try:
                        rpc = json.loads(payload)
                        event_data = rpc.get("params", {})
                        event_type = rpc.get("method", "event")
                        # Push notification handling
                        if event_type == "pushNotification":
                            event_data["type"] = "task_completed"
                            # Update a2a_tools _task_store
                            from qd_evolve.agent.a2a_tools import on_push_notification
                            on_push_notification(
                                event_data.get("task_id", ""),
                                event_data.get("state", "completed"),
                                event_data.get("content", ""),
                            )
                        queues = self._event_subscribers.get(agent_name, [])
                        for q in queues:
                            await q.put(event_data)
                    except json.JSONDecodeError:
                        pass

                # Discovery topic: $a2a/v1/discovery/{agent_name}
                elif len(parts) == 4 and parts[0] == "$a2a" and parts[1] == "v1" and parts[2] == "discovery":
                    agent_name = parts[3]
                    # Determine status from User Properties or payload
                    # a2a-status: "online" = agent connected, "offline" = graceful shutdown,
                    # "lwt" = abnormal disconnect (Broker published Last Will)
                    a2a_status = "online"
                    user_props = getattr(message.properties, "UserProperty", None) if message.properties else None
                    if user_props:
                        for key, val in user_props:
                            if key == "a2a-status":
                                a2a_status = val
                                break
                    # Empty payload with no a2a-status = cleared retained (offline)
                    if not payload.strip() and a2a_status not in ("lwt", "offline"):
                        a2a_status = "offline"
                    # LWT payload may contain JSON with a2a-status
                    if a2a_status == "lwt" and payload.strip():
                        try:
                            lwt_data = json.loads(payload)
                            if lwt_data.get("a2a-status") == "lwt":
                                a2a_status = "lwt"
                        except json.JSONDecodeError:
                            pass

                    is_online = a2a_status == "online"

                    # Track last known status
                    self._last_discovery_status[agent_name] = a2a_status

                    discovery_event = {
                        "agent_name": agent_name,
                        "online": is_online,
                        "status": a2a_status,
                    }
                    if is_online and payload.strip():
                        try:
                            discovery_event["card"] = AgentCard.model_validate(json.loads(payload))
                        except Exception:
                            pass

                    # Resolve one-shot online futures
                    futures = self._online_subscribers.pop(agent_name, [])
                    for f in futures:
                        if not f.done():
                            f.set_result(is_online)

                    # Push to discovery subscriber queues
                    for q in self._discovery_subscribers:
                        await q.put(discovery_event)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("MqttTransport: listener error: %s", e)

    # ── Event subscriber registry ──────────────────────────────────

    def subscribe_agent_events(self, agent_name: str) -> asyncio.Queue[dict]:
        """Register a queue to receive events for an agent."""
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._event_subscribers.setdefault(agent_name, []).append(queue)
        return queue

    def unsubscribe_agent_events(self, agent_name: str, queue: asyncio.Queue[dict]) -> None:
        """Unregister an event subscriber queue."""
        queues = self._event_subscribers.get(agent_name)
        if queues is not None:
            try:
                queues.remove(queue)
            except ValueError:
                pass
            if not queues:
                self._event_subscribers.pop(agent_name, None)

    # ── Discovery subscriber registry ────────────────────────────────

    async def subscribe_discovery(self) -> asyncio.Queue[dict]:
        """Subscribe to agent discovery events (online/offline + AgentCard)."""
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._discovery_subscribers.append(queue)
        return queue

    def unsubscribe_discovery(self, queue: asyncio.Queue[dict]) -> None:
        """Unregister a discovery subscriber queue."""
        try:
            self._discovery_subscribers.remove(queue)
        except ValueError:
            pass

    # ── A2ATransport interface ─────────────────────────────────────

    async def send_task(self, target: str, message: Message, *, from_agent: str = "") -> Task:
        """Blocking: publish message/send, await response via CorrelationData."""
        task_text = self._extract_text(message)
        logger.info("MqttTransport: send_task to '%s' — %s chars", target, len(task_text))

        # Quick online check
        if not await self.is_online(target):
            logger.warning("MqttTransport: send_task to '%s' — agent offline", target)
            return self._error_task(target, f"Agent '{target}' is offline")

        req_id = _new_req_id()
        msg_dict = message.model_dump()
        if from_agent:
            msg_dict.setdefault("metadata", {})["from_agent"] = from_agent

        rpc = _rpc_request("message/send", {"message": msg_dict}, req_id)

        # Build v5 PUBLISH properties
        pub_props = Properties(PacketTypes.PUBLISH)
        pub_props.ResponseTopic = _response_topic(self._client_name, req_id)
        pub_props.CorrelationData = req_id.encode("utf-8")
        if from_agent:
            pub_props.UserProperty = [("a2a-from-agent", from_agent)]

        # Register pending future
        future: asyncio.Future[Task] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        # Subscribe to the specific response topic
        await self._client.subscribe(_response_topic(self._client_name, req_id), qos=QOS_TASK)

        try:
            await self._client.publish(
                _request_topic(target),
                json.dumps(rpc, ensure_ascii=False).encode("utf-8"),
                qos=QOS_TASK,
                properties=pub_props,
            )
            try:
                result = await asyncio.wait_for(future, timeout=120)
            except asyncio.TimeoutError:
                logger.error("MqttTransport: send_task to '%s' — timeout (120s)", target)
                return self._error_task(target, f"Timeout waiting for response from '{target}'")
            logger.info("MqttTransport: send_task to '%s' done — state=%s", target, result.status.state)
            return result
        finally:
            self._pending.pop(req_id, None)
            try:
                await self._client.unsubscribe(_response_topic(self._client_name, req_id))
            except Exception:
                pass

    async def send_stream(self, target: str, message: Message, *, from_agent: str = "") -> AsyncIterator[StreamResponse]:
        """Stream: publish message/stream, receive events via subscriber queue, yield StreamResponse events."""
        if self._client is None:
            yield StreamResponse(task=self._error_task(target, "MqttTransport not connected"))
            return

        task_text = self._extract_text(message)
        logger.info("MqttTransport: send_stream to '%s' — %s chars", target, len(task_text))

        if not await self.is_online(target):
            logger.warning("MqttTransport: send_stream to '%s' — agent offline", target)
            yield StreamResponse(task=self._error_task(target, f"Agent '{target}' is offline"))
            return

        req_id = _new_req_id()
        msg_dict = message.model_dump()
        if from_agent:
            msg_dict.setdefault("metadata", {})["from_agent"] = from_agent

        rpc = _rpc_request("message/stream", {"message": msg_dict}, req_id)

        # Build v5 PUBLISH properties
        pub_props = Properties(PacketTypes.PUBLISH)
        pub_props.ResponseTopic = _response_topic(self._client_name, req_id)
        pub_props.CorrelationData = req_id.encode("utf-8")
        if from_agent:
            pub_props.UserProperty = [("a2a-from-agent", from_agent)]

        # Subscribe to events topic and register event subscriber
        events_topic = _event_topic(target)
        await self._client.subscribe(events_topic, qos=QOS_EVENT)
        event_queue = self.subscribe_agent_events(target)

        # Subscribe to response topic and register pending future
        await self._client.subscribe(_response_topic(self._client_name, req_id), qos=QOS_TASK)
        future: asyncio.Future[Task] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        # Publish stream request
        await self._client.publish(
            _request_topic(target),
            json.dumps(rpc, ensure_ascii=False).encode("utf-8"),
            qos=QOS_TASK,
            properties=pub_props,
        )

        # First event: create a Task object
        task = make_task_with_text(task_text)
        task.status.state = TaskState.working
        yield StreamResponse(task=task)

        try:
            final_received = False
            while not final_received:
                event_wait = asyncio.ensure_future(event_queue.get())
                future_wait = asyncio.ensure_future(asyncio.shield(future))
                try:
                    done, pending = await asyncio.wait(
                        {event_wait, future_wait},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except asyncio.CancelledError:
                    event_wait.cancel()
                    future_wait.cancel()
                    break

                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

                for t in done:
                    if t is event_wait:
                        try:
                            event = t.result()
                        except (asyncio.CancelledError, Exception):
                            continue
                        etype = event.get("type", "")
                        logger.debug("MqttTransport: send_stream event type=%s content_len=%d",
                                   etype, len(str(event.get("content", ""))))
                        if etype == "task_completed":
                            result_text = event.get("content", "")
                            final_state_str = event.get("state", "completed")
                            logger.debug("MqttTransport: send_stream FINAL via task_completed (%d chars)", len(result_text))
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
                            yield StreamResponse(statusUpdate=TaskStatusUpdateEvent(
                                task_id=task.id,
                                context_id=task.session_id,
                                status=TaskStatus(state=TaskState.working),
                                metadata=event,
                            ))

                    elif t is future_wait:
                        try:
                            result_task = t.result()
                        except (asyncio.CancelledError, Exception):
                            continue
                        result_text = self._extract_task_text(result_task)
                        yield StreamResponse(statusUpdate=TaskStatusUpdateEvent(
                            task_id=task.id,
                            context_id=task.session_id,
                            status=TaskStatus(state=result_task.status.state, message=make_text_message("agent", result_text)),
                            final=True,
                        ))
                        final_received = True
                        break
        except GeneratorExit:
            pass
        except asyncio.CancelledError:
            raise
        finally:
            self.unsubscribe_agent_events(target, event_queue)
            remaining = self._event_subscribers.get(target)
            if not remaining:
                try:
                    await self._client.unsubscribe(events_topic)
                except Exception:
                    pass
            try:
                await self._client.unsubscribe(_response_topic(self._client_name, req_id))
            except Exception:
                pass
            self._pending.pop(req_id, None)

    async def get_task(self, target: str, task_id: str) -> Task:
        """Query task status via MQTT request/response."""
        logger.debug("MqttTransport: get_task from '%s', task=%s", target, task_id[:8])
        req_id = _new_req_id()
        rpc = _rpc_request("tasks/get", {"id": task_id}, req_id)

        pub_props = Properties(PacketTypes.PUBLISH)
        pub_props.ResponseTopic = _response_topic(self._client_name, req_id)
        pub_props.CorrelationData = req_id.encode("utf-8")

        await self._client.subscribe(_response_topic(self._client_name, req_id), qos=QOS_TASK)
        future: asyncio.Future[Task] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        try:
            await self._client.publish(
                _request_topic(target),
                json.dumps(rpc, ensure_ascii=False).encode("utf-8"),
                qos=QOS_TASK,
                properties=pub_props,
            )
            try:
                result = await asyncio.wait_for(future, timeout=30)
            except asyncio.TimeoutError:
                logger.error("MqttTransport: get_task from '%s' — timeout (30s)", target)
                return self._error_task(target, f"Timeout querying task '{task_id}' from '{target}'")
            return result
        finally:
            self._pending.pop(req_id, None)
            try:
                await self._client.unsubscribe(_response_topic(self._client_name, req_id))
            except Exception:
                pass

    async def cancel_task(self, target: str, task_id: str) -> Task:
        """Cancel task via MQTT request/response."""
        logger.info("MqttTransport: cancel_task on '%s', task=%s", target, task_id[:8])
        req_id = _new_req_id()
        rpc = _rpc_request("tasks/cancel", {"id": task_id}, req_id)

        pub_props = Properties(PacketTypes.PUBLISH)
        pub_props.ResponseTopic = _response_topic(self._client_name, req_id)
        pub_props.CorrelationData = req_id.encode("utf-8")

        await self._client.subscribe(_response_topic(self._client_name, req_id), qos=QOS_TASK)
        future: asyncio.Future[Task] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        try:
            await self._client.publish(
                _request_topic(target),
                json.dumps(rpc, ensure_ascii=False).encode("utf-8"),
                qos=QOS_TASK,
                properties=pub_props,
            )
            try:
                result = await asyncio.wait_for(future, timeout=30)
            except asyncio.TimeoutError:
                logger.error("MqttTransport: cancel_task on '%s' — timeout (30s)", target)
                return self._error_task(target, f"Timeout canceling task '{task_id}' on '{target}'")
            return result
        finally:
            self._pending.pop(req_id, None)
            try:
                await self._client.unsubscribe(_response_topic(self._client_name, req_id))
            except Exception:
                pass

    async def get_agent_card(self, target: str) -> AgentCard:
        """Discover remote agent's capabilities via discovery topic."""
        logger.debug("MqttTransport: get_agent_card from '%s'", target)
        return await self._get_card_via_discovery(target)

    async def _get_card_via_discovery(self, target: str) -> AgentCard:
        """Get AgentCard by subscribing to discovery topic and waiting for retained message."""
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._discovery_subscribers.append(queue)
        disc_topic = _discovery_topic(target)
        await self._client.subscribe(disc_topic, qos=QOS_DISCOVERY)

        try:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=3.0)
            except asyncio.TimeoutError:
                return AgentCard(name=target, description=f"Agent '{target}' timeout")

            card_data = event.get("card")
            if card_data:
                return AgentCard.model_validate(card_data) if isinstance(card_data, dict) else card_data
            return AgentCard(name=target, description=f"Agent '{target}' card unavailable")
        finally:
            self._discovery_subscribers.remove(queue)
            try:
                await self._client.unsubscribe(disc_topic)
            except Exception:
                pass

    async def get_extended_agent_card(self, target: str) -> AgentCard:
        """Get extended AgentCard via MQTT request/response."""
        req_id = _new_req_id()
        rpc = _rpc_request("agent/getExtendedAgentCard", {}, req_id)

        pub_props = Properties(PacketTypes.PUBLISH)
        pub_props.ResponseTopic = _response_topic(self._client_name, req_id)
        pub_props.CorrelationData = req_id.encode("utf-8")

        await self._client.subscribe(_response_topic(self._client_name, req_id), qos=QOS_TASK)
        future: asyncio.Future[Task] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        try:
            await self._client.publish(
                _request_topic(target),
                json.dumps(rpc, ensure_ascii=False).encode("utf-8"),
                qos=QOS_TASK,
                properties=pub_props,
            )
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
            try:
                await self._client.unsubscribe(_response_topic(self._client_name, req_id))
            except Exception:
                pass

    async def resubscribe(self, target: str, task_id: str = "") -> AsyncIterator[StreamResponse]:
        """Subscribe to agent events via MQTT events topic."""
        if self._client is None:
            return

        events_topic = _event_topic(target)
        await self._client.subscribe(events_topic, qos=QOS_EVENT)
        event_queue = self.subscribe_agent_events(target)

        try:
            while True:
                event = await event_queue.get()
                yield StreamResponse(statusUpdate=TaskStatusUpdateEvent(
                    task_id=task_id,
                    status=TaskStatus(state=TaskState.working),
                    metadata=event,
                ))
        except GeneratorExit:
            pass
        except asyncio.CancelledError:
            raise
        finally:
            self.unsubscribe_agent_events(target, event_queue)
            remaining = self._event_subscribers.get(target)
            if not remaining:
                try:
                    await self._client.unsubscribe(events_topic)
                except Exception:
                    pass

    async def is_online(self, target: str) -> bool:
        """Check if a remote agent is online via discovery retained message."""
        status = await self.get_agent_status(target)
        return status == "online"

    async def get_agent_status(self, target: str) -> str:
        """Check agent status via discovery retained message. Returns 'online', 'offline', 'lwt', or 'unknown'."""
        if self._client is None:
            return "unknown"

        # Register future BEFORE subscribing
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._online_subscribers.setdefault(target, []).append(future)

        disc_topic = _discovery_topic(target)
        await self._client.subscribe(disc_topic, qos=QOS_DISCOVERY)
        logger.debug("MqttTransport: get_agent_status '%s' — subscribed to %s", target, disc_topic)

        try:
            try:
                await asyncio.wait_for(future, timeout=2.0)
                is_online = future.result()
            except asyncio.TimeoutError:
                is_online = False
            # Check _last_discovery_status for detailed status
            status = self._last_discovery_status.get(target, "offline" if not is_online else "online")
            logger.debug("MqttTransport: get_agent_status '%s' -> %s", target, status)
            return status
        finally:
            futures = self._online_subscribers.get(target)
            if futures is not None:
                try:
                    futures.remove(future)
                except ValueError:
                    pass
                if not futures:
                    self._online_subscribers.pop(target, None)
            try:
                await self._client.unsubscribe(disc_topic)
            except Exception:
                pass

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