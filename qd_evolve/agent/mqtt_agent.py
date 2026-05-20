"""MQTT Agent — wraps A2AAgent with MQTT server-side pub/sub.

Subscribes to a2a/{agent_name}/* topics, processes requests (reusing A2AServer
logic), publishes responses to a2a/{requester}/response/{req_id}.
Publishes events to a2a/{agent_name}/events for streaming/heartbeat.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from qd_evolve.agent.a2a import (
    AgentCard,
    AgentCapabilities,
    Message,
    Part,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    make_text_message,
    make_task_with_text,
)
from qd_evolve.agent.a2a_agent import A2AAgent
from qd_evolve.agent.server import TaskStore
from qd_evolve.core.config import MqttConfig
from qd_evolve.core.logger import logger

QOS_TASK = 1
QOS_EVENT = 0


class MqttAgent:
    """Wraps A2AAgent with MQTT server — subscribes to request topics, publishes responses.

    Implements AgentProtocol so it can be registered in AgentRegistry.
    """

    def __init__(self, a2a_agent: A2AAgent, broker_host: str, broker_port: int, mqtt_config: MqttConfig) -> None:
        self.agent = a2a_agent
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._config = mqtt_config
        self._client: Any = None  # aiomqtt.Client
        self._connected = False
        self._listener_task: asyncio.Task | None = None
        self._event_pusher_task: asyncio.Task | None = None

    # ── AgentProtocol interface ─────────────────────────────────────

    @property
    def card(self) -> AgentCard:
        return self.agent.card

    @property
    def task_store(self) -> TaskStore:
        return self.agent.task_store

    def run(self, message: str, **kwargs: Any) -> str:
        return self.agent.run(message, **kwargs)

    def subscribe_events(self) -> asyncio.Queue:
        return self.agent.subscribe_events()

    def unsubscribe_events(self, queue: asyncio.Queue) -> None:
        self.agent.unsubscribe_events(queue)

    def _push_event(self, event: dict) -> None:
        """Push event to all subscribers via A2AAgent's event fan-out."""
        self.agent._push_event(event)

    def _check_pending_task_results(self) -> list:
        """Check task store for completed tasks from push notifications."""
        results = []
        try:
            for task_id, task in self.agent.task_store._tasks.items():
                if task.status.state in (TaskState.completed, TaskState.failed, TaskState.canceled):
                    if task.status.message:
                        results.append((task_id, task))
        except Exception:
            pass
        return results

    # ── Delegate key A2AAgent methods/attributes ────────────────────

    def heartbeat_check(self, idle_seconds: int) -> str:
        """MQTT heartbeat: use mqtt-heartbeat template with broker info."""
        from datetime import datetime

        pending_results = self._check_pending_task_results()

        if self.agent._template_mgr is not None:
            msg = self.agent._template_mgr.render("mqtt-heartbeat",
                                                   idle_seconds=idle_seconds,
                                                   now=datetime.now(),
                                                   agent_name=self.card.name,
                                                   friendly_name="",
                                                   mqtt_broker_host=self._broker_host,
                                                   mqtt_broker_port=self._broker_port,
                                                   pending_results=pending_results)
        else:
            msg = f"[MQTT Heartbeat: idle {idle_seconds}s. Broker: {self._broker_host}:{self._broker_port}.]"
            if pending_results:
                msg += "\n" + pending_results

        logger.debug("MQTT Heartbeat: idle %ss, broker %s:%s", idle_seconds,
                      self._broker_host, self._broker_port)
        try:
            response = self.agent.run(msg)
        except Exception as e:
            logger.warning("MQTT Heartbeat: LLM call failed: %s", e)
            return None
        if response.strip() == ".":
            logger.debug("MQTT Heartbeat: LLM sent '.' — staying silent")
            self._push_event({"type": "heartbeat_silent"})
        else:
            logger.info("MQTT Heartbeat: LLM responded (%s chars)", len(response))
            self._push_event({"type": "heartbeat", "content": response})
        return response

    def start_heartbeat_loop(self) -> None:
        """Start heartbeat loop using MqttAgent.heartbeat_check."""
        seconds = self.agent.settings.heartbeat_idle_seconds
        if seconds <= 0:
            return

        self._hb_idle_seconds = seconds
        self._hb_event = asyncio.Event()

        async def _loop() -> None:
            while True:
                try:
                    await asyncio.wait_for(self._hb_event.wait(), timeout=self._hb_idle_seconds)
                except asyncio.TimeoutError:
                    pass
                self._hb_event.clear()
                try:
                    await asyncio.to_thread(self.heartbeat_check, self._hb_idle_seconds)
                except Exception as e:
                    logger.debug("MQTT Heartbeat loop error: %s", e)

        self.agent._hb_task = asyncio.ensure_future(_loop())

    def touch_heartbeat(self) -> None:
        if hasattr(self, '_hb_event'):
            self._hb_event.set()

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

    # ── Delegate key Agent attributes ───────────────────────────────

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

    # ── MQTT lifecycle ──────────────────────────────────────────────

    async def start(self) -> None:
        """Connect to MQTT broker, subscribe to request topics, publish online status."""
        try:
            import aiomqtt
        except ImportError:
            logger.error("aiomqtt not installed — run: pip install aiomqtt")
            raise

        if self._connected:
            return

        agent_name = self.card.name
        client_id = f"qd-evolve-agent-{agent_name}-{uuid4().hex[:8]}"

        self._client = aiomqtt.Client(
            hostname=self._broker_host,
            port=self._broker_port,
            username=self._config.username or None,
            password=self._config.password or None,
            keepalive=self._config.keepalive,
            identifier=client_id,
        )
        # aiomqtt.Client is an async context manager — enter it to connect
        # aiomqtt.Client is an async context manager — enter it to connect
        await self._client.__aenter__()
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

        # Start event pusher (pushes A2AAgent events to MQTT events topic)
        self._event_pusher_task = asyncio.create_task(self._push_events())

        logger.info("MqttAgent: '%s' connected to %s:%s, subscribed to %s",
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

        # Cancel listener
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        # Cancel event pusher
        if self._event_pusher_task is not None:
            self._event_pusher_task.cancel()
            try:
                await self._event_pusher_task
            except asyncio.CancelledError:
                pass
            self._event_pusher_task = None

        # Disconnect
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._connected = False
        logger.info("MqttAgent: '%s' disconnected", agent_name)

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

                # Extract method from topic: a2a/{agent}/{method}
                method = topic[len(prefix):]
                req_id = data.get("req_id", "")
                from_agent = data.get("from_agent", "")

                # Dispatch to handler
                asyncio.ensure_future(self._dispatch(method, data, req_id, from_agent))

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("MqttAgent: listener error: %s", e)

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
                logger.debug("MqttAgent: unknown method '%s'", method)
        except Exception as e:
            logger.exception("MqttAgent: error handling '%s': %s", method, e)
            if req_id and from_agent:
                await self._publish_error(from_agent, req_id, str(e))

    # ── Request handlers ────────────────────────────────────────────

    async def _on_message_send(self, data: dict, req_id: str, from_agent: str) -> None:
        """Handle message/send: run agent, publish completed Task as response."""
        message_data = data.get("message", {})
        message = Message.model_validate(message_data) if message_data else make_text_message("user", "")
        task_text = self._extract_text(message)

        task = make_task_with_text(task_text)
        task.status.state = TaskState.working
        self.task_store.put(task)

        try:
            result = await asyncio.to_thread(self.agent.run, task_text)
            task.status = TaskStatus(
                state=TaskState.completed,
                message=make_text_message("agent", result),
            )
        except Exception as e:
            task.status = TaskStatus(
                state=TaskState.failed,
                message=make_text_message("agent", f"{type(e).__name__}: {e}"),
            )
        self.task_store.put(task)

        if req_id and from_agent:
            await self._publish_response(from_agent, req_id, task.model_dump())

    async def _on_message_stream(self, data: dict, req_id: str, from_agent: str) -> None:
        """Handle message/stream: run agent, push events to events topic, publish final response."""
        message_data = data.get("message", {})
        message = Message.model_validate(message_data) if message_data else make_text_message("user", "")
        task_text = self._extract_text(message)

        task = make_task_with_text(task_text)
        task.status.state = TaskState.working
        self.task_store.put(task)

        # Subscribe to agent events for intermediate updates
        event_queue = self.agent.subscribe_events()
        agent_name = self.card.name
        events_topic = f"a2a/{agent_name}/events"

        # Background execution
        run_task = asyncio.ensure_future(asyncio.to_thread(self.agent.run, task_text))

        try:
            while not run_task.done():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=30)
                    # Push intermediate event to MQTT events topic
                    event_payload = json.dumps(event, ensure_ascii=False)
                    await self._client.publish(events_topic, event_payload.encode("utf-8"), qos=QOS_EVENT)
                except asyncio.TimeoutError:
                    # Send ping
                    ping_payload = json.dumps({"type": "ping"}, ensure_ascii=False)
                    await self._client.publish(events_topic, ping_payload.encode("utf-8"), qos=QOS_EVENT)
        except asyncio.CancelledError:
            pass
        finally:
            self.agent.unsubscribe_events(event_queue)

        # Get result
        try:
            result = run_task.result()
            final_state = TaskState.completed
        except Exception as e:
            result = f"{type(e).__name__}: {e}"
            final_state = TaskState.failed

        # Push final event
        final_event = json.dumps({"type": "final", "state": final_state.value, "content": result}, ensure_ascii=False)
        await self._client.publish(events_topic, final_event.encode("utf-8"), qos=QOS_EVENT)

        # Update task store
        task.status = TaskStatus(state=final_state, message=make_text_message("agent", result))
        self.task_store.put(task)

        # Also publish response for req_id-based waiting
        if req_id and from_agent:
            await self._publish_response(from_agent, req_id, task.model_dump())

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
        extended = data.get("extended", False)
        if extended:
            from qd_evolve.agent.server import A2AServer
            server = A2AServer(self.agent)
            card = server._get_extended_agent_card()
        else:
            card = self.card

        # Wrap in a Task with card in metadata
        task = Task(
            status=TaskStatus(state=TaskState.completed, message=make_text_message("agent", "agent_card")),
            metadata={"agent_card": card.model_dump()},
        )
        if req_id and from_agent:
            await self._publish_response(from_agent, req_id, task.model_dump())

    # ── Event pusher ────────────────────────────────────────────────

    async def _push_events(self) -> None:
        """Background task: subscribe to A2AAgent events and push to MQTT events topic."""
        queue = self.agent.subscribe_events()
        agent_name = self.card.name
        events_topic = f"a2a/{agent_name}/events"

        try:
            while True:
                event = await queue.get()
                try:
                    payload = json.dumps(event, ensure_ascii=False)
                    await self._client.publish(events_topic, payload.encode("utf-8"), qos=QOS_EVENT)
                except Exception as e:
                    logger.debug("MqttAgent: event push failed: %s", e)
        except asyncio.CancelledError:
            pass
        finally:
            self.agent.unsubscribe_events(queue)

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