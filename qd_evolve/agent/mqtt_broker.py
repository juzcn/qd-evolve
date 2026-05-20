"""Embedded MQTT broker using amqtt (formerly hbmqtt)."""

from __future__ import annotations

import asyncio
import logging

from qd_evolve.core.config import MqttBrokerConfig

logger = logging.getLogger(__name__)

_broker_instance: MqttBroker | None = None


class MqttBroker:
    """Embedded MQTT broker — runs amqtt in-process as an asyncio task."""

    def __init__(self, config: MqttBrokerConfig) -> None:
        self._config = config
        self._broker = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the embedded MQTT broker."""
        try:
            from amqtt.broker import Broker
        except ImportError:
            logger.error("amqtt not installed — run: pip install amqtt")
            raise

        if self._broker is not None:
            return

        host = self._config.host
        port = self._config.port
        listener = f"{host}:{port}"

        self._broker = Broker(
            config=None,  # use amqtt defaults
            plugin_namespace="amqtt.broker.plugins",
        )
        # amqtt Broker.start() takes a list of listener URIs
        self._task = asyncio.create_task(self._broker.start([f"mqtt://{listener}"]))
        logger.info("MQTT broker started on %s", listener)

    async def stop(self) -> None:
        """Gracefully shut down the broker."""
        if self._broker is None:
            return
        self._broker.shutdown()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._broker = None
        self._task = None
        logger.info("MQTT broker stopped")

    @property
    def running(self) -> bool:
        return self._broker is not None


def get_broker(config: MqttBrokerConfig | None = None) -> MqttBroker | None:
    """Get or create the global broker singleton."""
    global _broker_instance
    if _broker_instance is None and config is not None:
        _broker_instance = MqttBroker(config)
    return _broker_instance


async def ensure_broker(config: MqttBrokerConfig) -> MqttBroker:
    """Ensure the global broker is running, starting it if needed."""
    broker = get_broker(config)
    assert broker is not None
    if not broker.running:
        await broker.start()
    return broker


async def shutdown_broker() -> None:
    """Shut down the global broker if running."""
    global _broker_instance
    if _broker_instance is not None:
        await _broker_instance.stop()
        _broker_instance = None
