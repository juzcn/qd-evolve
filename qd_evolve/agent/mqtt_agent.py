"""A2A over MQTT v5 Agent — wraps A2AAgent with MQTT server-side pub/sub.

Subscribes to $a2a/v1/request/{agent_name}, processes JSON-RPC requests,
publishes responses to the caller's response topic (via MQTT v5 Response Topic
+ Correlation Data), and publishes events to $a2a/v1/event/{agent_name}.

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
    make_task_with_text,
)
from qd_evolve.agent.a2a_agent import A2AAgent
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
from qd_evolve.core.config import MqttConfig
from qd_evolve.core.logger import logger


class MqttAgent:
    """Wraps A2AAgent with MQTT v5 server — subscribes to request topic, publishes responses.

    Implements AgentProtocol so it can be registered in AgentRegistry.
    """

    def __init__(self, a2a_agent: A2AAgent, broker_host: str, broker_port: int, mqtt_config: MqttConfig, will_delay_interval: int = 0) -> None:
        self.agent = a2a_agent
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._config = mqtt_config
        self._will_delay_interval = will_delay_interval
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
        self.agent._push_event(event)

    # ── Delegate key A2AAgent methods/attributes ────────────────────

    def heartbeat_check(self, idle_seconds: int) -> str:
        if self.agent._running:
            logger.debug("MQTT Heartbeat: skipped, agent is busy")
            return None

        from datetime import datetime

        if self.agent._template_mgr is not None:
            msg = self.agent._template_mgr.render("heartbeat",
                                                   idle_seconds=idle_seconds,
                                                   now=datetime.now(),
                                                   agent_name=self.card.name,
                                                   mqtt_broker_host=self._broker_host,
                                                   mqtt_broker_port=self._broker_port)
        else:
            msg = f"[MQTT Heartbeat: idle {idle_seconds}s. Broker: {self._broker_host}:{self._broker_port}.]"

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
                        logger.debug("MQTT Heartbeat loop error: %s", e)

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

    # ── MQTT v5 lifecycle ──────────────────────────────────────────

    async def start(self) -> None:
        """Connect to MQTT v5 broker, publish AgentCard (retained), set LWT, subscribe to request topic."""
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
            logger.error("MqttAgent: '%s' failed to connect to %s:%s — %s",
                         agent_name, self._broker_host, self._broker_port, exc)
            self._client = None
            raise
        self._connected = True

        # Subscribe to request topic
        await self._client.subscribe(_request_topic(agent_name), qos=QOS_TASK)

        # Subscribe to own event topic to receive push notifications
        await self._client.subscribe(_event_topic(agent_name), qos=QOS_EVENT)

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

        logger.info("MqttAgent: '%s' connected to %s:%s (v5), LWT set, AgentCard published",
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
                    b"",  # empty payload clears retained
                    qos=QOS_DISCOVERY,
                    retain=True,
                    properties=disc_props,
                )
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

        # Disconnect via __aexit__ to properly clean up aiomqtt internals
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
            self._client = None
        self._connected = False
        logger.info("MqttAgent: '%s' disconnected", agent_name)

    # ── Request listener ────────────────────────────────────────────

    async def _listen_requests(self) -> None:
        if self._client is None:
            return
        agent_name = self.card.name
        request_prefix = _request_topic(agent_name)
        event_prefix = _event_topic(agent_name)

        try:
            async for message in self._client.messages:
                topic = str(message.topic)
                payload = message.payload.decode("utf-8") if isinstance(message.payload, bytes) else str(message.payload)

                # ── Event topic: handle push notifications ──────────
                if topic == event_prefix:
                    try:
                        rpc = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    method = rpc.get("method", "")
                    params = rpc.get("params", {})

                    if method == "pushNotification":
                        task_id = params.get("task_id", "")
                        state = params.get("state", "completed")
                        content = params.get("content", "")
                        from qd_evolve.agent.a2a_tools import on_push_notification
                        on_push_notification(task_id, state, content)
                        logger.info("MqttAgent: push notification received — task=%s, state=%s",
                                    task_id[:8] if task_id else "?", state)
                        # Process push notification as a normal input — agent.run() resets heartbeat
                        # If agent is busy, wait for it to finish before running
                        if self.agent._running:
                            logger.debug("MqttAgent: agent busy, waiting to process push notification")
                            while self.agent._running:
                                await asyncio.sleep(0.5)
                        pending = self.agent._check_pending_task_results()
                        if pending:
                            try:
                                await asyncio.to_thread(self.agent.run, pending)
                            except Exception as e:
                                logger.debug("MqttAgent: push-triggered run error: %s", e)
                    continue

                # ── Request topic: handle JSON-RPC ──────────────────
                if topic != request_prefix:
                    continue

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
            logger.warning("MqttAgent: listener error: %s", e)

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
                logger.warning("MqttAgent: unknown method '%s' from '%s'", method, from_agent)
        except Exception as e:
            logger.exception("MqttAgent: error handling '%s' from '%s': %s", method, from_agent, e)
            if req_id:
                await self._publish_error_response(message, req_id, str(e))

    # ── Request handlers ────────────────────────────────────────────

    async def _on_message_send(self, params: dict, req_id: str, from_agent: str, message: Any) -> None:
        message_data = params.get("message", {})
        msg = Message.model_validate(message_data) if message_data else make_text_message("user", "")
        task_text = self._extract_text(msg)
        logger.info("MqttAgent: message/send from '%s' — %s chars", from_agent, len(task_text))

        task = make_task_with_text(task_text)
        task.status.state = TaskState.working
        self.task_store.put(task)

        # Temporarily hook _on_event to skip "completed" — result goes via response topic.
        base_agent = self.agent.agent
        original_on_event = base_agent._on_event
        def _filter_completed(event: dict) -> None:
            if event.get("type") != "completed":
                original_on_event(event)
        base_agent._on_event = _filter_completed
        try:
            result = await asyncio.to_thread(self.agent.run, task_text)
            logger.info("MqttAgent: message/send from '%s' done — %s chars", from_agent, len(result))
            task.status = TaskStatus(
                state=TaskState.completed,
                message=make_text_message("agent", result),
            )
        except Exception as e:
            logger.exception("MqttAgent: message/send from '%s' failed: %s", from_agent, e)
            task.status = TaskStatus(
                state=TaskState.failed,
                message=make_text_message("agent", f"{type(e).__name__}: {e}"),
            )
        finally:
            base_agent._on_event = original_on_event
        self.task_store.put(task)

        if req_id:
            await self._publish_rpc_response(message, req_id, task.model_dump())

    async def _on_message_stream(self, params: dict, req_id: str, from_agent: str, message: Any) -> None:
        message_data = params.get("message", {})
        msg = Message.model_validate(message_data) if message_data else make_text_message("user", "")
        task_text = self._extract_text(msg)
        logger.info("MqttAgent: message/stream from '%s' — %s chars", from_agent, len(task_text))

        task = make_task_with_text(task_text)
        task.status.state = TaskState.working
        self.task_store.put(task)

        # Run agent — _push_events() handles intermediate events.
        # Hook _on_event to skip "completed" broadcast: result is returned via response topic.
        base_agent = self.agent.agent
        original_on_event = base_agent._on_event
        def _filter_completed(event: dict) -> None:
            if event.get("type") != "completed":
                original_on_event(event)
        base_agent._on_event = _filter_completed
        try:
            result = await asyncio.to_thread(self.agent.run, task_text)
            final_state = TaskState.completed
        except Exception as e:
            logger.exception("MqttAgent: message/stream from '%s' failed: %s", from_agent, e)
            result = f"{type(e).__name__}: {e}"
            final_state = TaskState.failed
        finally:
            base_agent._on_event = original_on_event

        logger.info("MqttAgent: message/stream from '%s' done — %s chars, state=%s",
                     from_agent, len(result), final_state.value)

        # Update task store
        task.status = TaskStatus(state=final_state, message=make_text_message("agent", result))
        self.task_store.put(task)

        # Publish response for req_id-based waiting
        if req_id:
            await self._publish_rpc_response(message, req_id, task.model_dump())

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
        from qd_evolve.agent.server import A2AServer
        server = A2AServer(self.agent)
        card = server._get_extended_agent_card()
        task = Task(
            status=TaskStatus(state=TaskState.completed, message=make_text_message("agent", "agent_card")),
            metadata={"agent_card": card.model_dump()},
        )
        if req_id:
            await self._publish_rpc_response(message, req_id, task.model_dump())

    # ── Event pusher ────────────────────────────────────────────────

    async def _push_events(self) -> None:
        """Background task: subscribe to A2AAgent events and push to MQTT event topic."""
        queue = self.agent.subscribe_events()
        agent_name = self.card.name
        events_topic = _event_topic(agent_name)

        try:
            while True:
                event = await queue.get()
                etype = event.get("type", "")
                content_len = len(str(event.get("content", "")))
                logger.debug("MqttAgent: _push_events PUBLISH type=%s content_len=%d",
                           etype, content_len)
                try:
                    rpc = json.dumps({"jsonrpc": "2.0", "method": "event", "params": event}, ensure_ascii=False)
                    await self._client.publish(events_topic, rpc.encode("utf-8"), qos=QOS_EVENT)
                except Exception as e:
                    logger.debug("MqttAgent: event push failed: %s", e)
        except asyncio.CancelledError:
            pass
        finally:
            self.agent.unsubscribe_events(queue)

    # ── Publish helpers ─────────────────────────────────────────────

    async def _publish_rpc_response(self, original_message: Any, req_id: str, data: dict) -> None:
        """Publish JSON-RPC response using MQTT v5 Response Topic + Correlation Data from the request."""
        if self._client is None:
            return

        rpc = {"jsonrpc": "2.0", "result": data, "id": req_id}
        payload = json.dumps(rpc, ensure_ascii=False).encode("utf-8")

        # Use Response Topic + Correlation Data from the original request's properties
        if original_message.properties and original_message.properties.ResponseTopic:
            resp_topic = original_message.properties.ResponseTopic
            resp_props = Properties(PacketTypes.PUBLISH)
            if original_message.properties.CorrelationData:
                resp_props.CorrelationData = original_message.properties.CorrelationData
            await self._client.publish(resp_topic, payload, qos=QOS_TASK, properties=resp_props)
        else:
            # Fallback: no v5 properties — shouldn't happen with compliant clients
            logger.warning("MqttAgent: no ResponseTopic in request — cannot reply")

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