"""Shared test fixtures for qd_evolve test suite."""

import json
from pathlib import Path
from unittest.mock import MagicMock
from typing import Any

import pytest

from qd_evolve.core.config import (
    AgentEntry,
    EmbeddingsBackend,
    ModelConfig,
    ProviderConfig,
    ServerConfig,
    Settings,
)
from qd_evolve.core.memory import MemoryStore, RecalledMemoryRegistry
from qd_evolve.core.providers import ProviderRegistry
from qd_evolve.core.registry import ToolRegistry


# ── Settings fixtures ──────────────────────────────────────────────

@pytest.fixture
def minimal_settings() -> Settings:
    """Minimal Settings with test provider — no config.json dependency."""
    return Settings(
        max_iterations=5,
        tool_output_limit=2000,
        providers=[
            ProviderConfig(
                name="test",
                api_key="sk-test-key",
                models=[
                    ModelConfig(
                        name="test-model",
                        max_tokens=100,
                        context_window=4000,
                    ),
                ],
            ),
        ],
        default_provider="test",
        default_model="test-model",
    )


@pytest.fixture
def multi_agent_settings(minimal_settings: Settings) -> Settings:
    """Settings with 2 agents for A2A testing."""
    minimal_settings.agents_config = AgentEntry(
        name="default",
        description="Default agent",
        provider="test",
        model="test-model",
    )
    return Settings(
        max_iterations=5,
        tool_output_limit=2000,
        providers=[
            ProviderConfig(
                name="test",
                api_key="sk-test-key",
                models=[
                    ModelConfig(name="test-model", max_tokens=100, context_window=4000),
                ],
            ),
        ],
        default_provider="test",
        default_model="test-model",
        agents_config={
            "chat_agent": "default",
            "agents": [
                AgentEntry(name="default", description="Default agent", provider="test", model="test-model"),
                AgentEntry(name="helper", description="Helper agent", provider="test", model="test-model"),
            ],
            "topology": {"relations": [{"from": "default", "to": "helper", "mode": "peer"}]},
        },
    )


# ── Registry fixtures ──────────────────────────────────────────────

@pytest.fixture
def registry() -> ToolRegistry:
    """Clean ToolRegistry — no discover_tools side effects."""
    return ToolRegistry()


@pytest.fixture
def registry_with_echo(registry: ToolRegistry) -> ToolRegistry:
    """Registry with a simple echo tool registered."""
    registry.register(
        "echo",
        "Echo the input string",
        lambda s: s,
        {"type": "object", "properties": {"s": {"type": "string"}}, "required": ["s"]},
    )
    return registry


@pytest.fixture
def providers(minimal_settings: Settings) -> ProviderRegistry:
    """ProviderRegistry from minimal_settings."""
    return ProviderRegistry(minimal_settings)


# ── Memory fixtures ─────────────────────────────────────────────────

@pytest.fixture
def mock_embedder() -> MagicMock:
    """Fixed-vector embedder — no model loading, returns zeros."""
    import numpy as np
    embedder = MagicMock()
    embedder.encode.return_value = np.zeros(384, dtype=np.float32)
    return embedder


@pytest.fixture
def memory_backend() -> EmbeddingsBackend:
    """Minimal EmbeddingsBackend for testing."""
    return EmbeddingsBackend(model_path="fake-model", dim=384)


@pytest.fixture
def memory_store(tmp_path: Path, mock_embedder: MagicMock) -> MemoryStore:
    """Temporary SQLite + mock embedder — full save/recall/delete lifecycle."""
    from unittest.mock import patch
    db_path = str(tmp_path / "test_memory.db")
    backend = EmbeddingsBackend(model_path="fake-model", dim=384)
    # Patch _create_embedder to avoid loading real models
    with patch("qd_evolve.core.memory._create_embedder", return_value=mock_embedder):
        store = MemoryStore(db_path, backend, list_all_limit=50)
    yield store
    store.close()


@pytest.fixture
def recalled_registry() -> RecalledMemoryRegistry:
    """Empty RecalledMemoryRegistry."""
    return RecalledMemoryRegistry()


# ── Agent fixtures ──────────────────────────────────────────────────

@pytest.fixture
def agent_core(minimal_settings: Settings, registry_with_echo: ToolRegistry, providers: ProviderRegistry) -> Any:
    """Minimal AgentCore for logic testing — no LLM calls."""
    from qd_evolve.agent.agent import Agent
    agent = Agent(
        settings=minimal_settings,
        registry=registry_with_echo,
        providers=providers,
        default_system_prompt="You are a test agent.",
    )
    agent._provider_name = "test"
    agent._model = "test-model"
    return agent


# ── File fixtures ───────────────────────────────────────────────────

@pytest.fixture
def config_json(tmp_path: Path) -> Path:
    """Write a minimal config.json to tmp_path."""
    data = {
        "max_iterations": 5,
        "tool_output_limit": 2000,
        "providers": [
            {
                "name": "test",
                "api_key": "sk-test",
                "api": "openai-completions",
                "models": [
                    {"name": "gpt-4o-mini", "max_tokens": 100, "context_window": 4000},
                ],
            },
        ],
        "default_provider": "test",
        "default_model": "gpt-4o-mini",
        "agents_config": {
            "chat_agent": "default",
            "agents": [
                {"name": "default", "description": "Default agent", "server": {"port": 8002}},
            ],
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def toolbox_json(tmp_path: Path) -> Path:
    """Write a config.json with embedded toolbox data to tmp_path."""
    data = {
        "max_iterations": 5,
        "tool_output_limit": 2000,
        "default_provider": "test",
        "default_model": "test-model",
        "agents_config": {
            "chat_agent": "default",
            "agents": [
                {
                    "name": "default",
                    "toolbox": {
                        "tools": {"fetch": "preload", "run_shell": "disabled"},
                        "mcp_servers": {},
                        "bridge": {},
                        "cli": {},
                        "skills": {},
                    },
                },
            ],
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path