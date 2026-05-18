"""Tests for qd_evolve.core.config — Settings, AgentEntry, load/save."""

import json
from pathlib import Path

import pytest

from qd_evolve.core.config import (
    AgentEntry,
    EmbeddingsBackend,
    LogConfig,
    MCPServerConfig,
    ModelConfig,
    ModelCost,
    ProviderConfig,
    ServerConfig,
    Settings,
    TopologyConfig,
    AgentsConfig,
    MemorySearchConfig,
    UIConfig,
    load_json,
    load_settings,
    save_json,
)


# ── Pydantic model tests ────────────────────────────────────────────

class TestModelCost:
    def test_defaults(self):
        mc = ModelCost()
        assert mc.input == 0.0
        assert mc.output == 0.0
        assert mc.cache_read == 0.0
        assert mc.cache_write == 0.0

    def test_custom_values(self):
        mc = ModelCost(input=0.01, output=0.03)
        assert mc.input == 0.01
        assert mc.output == 0.03


class TestModelConfig:
    def test_required_field(self):
        mc = ModelConfig(max_tokens=100)
        assert mc.max_tokens == 100
        assert mc.name == ""
        assert mc.reasoning is False
        assert mc.input == ["text"]
        assert mc.context_window == 0

    def test_with_all_fields(self):
        mc = ModelConfig(
            name="gpt-4o",
            reasoning=True,
            input=["text", "image"],
            cost=ModelCost(input=0.01, output=0.03),
            context_window=128000,
            max_tokens=4096,
        )
        assert mc.name == "gpt-4o"
        assert mc.reasoning is True
        assert mc.context_window == 128000

    def test_missing_max_tokens_raises(self):
        with pytest.raises(Exception):
            ModelConfig()


class TestProviderConfig:
    def test_defaults(self):
        pc = ProviderConfig(name="test")
        assert pc.api_key == ""
        assert pc.base_url is None
        assert pc.api == "openai-completions"
        assert pc.models == []

    def test_custom_base_url(self):
        pc = ProviderConfig(name="deepseek", api_key="sk-xxx", base_url="https://api.deepseek.com")
        assert pc.base_url == "https://api.deepseek.com"

    def test_api_types(self):
        for api_type in ("openai-completions", "openai-response", "anthropic"):
            pc = ProviderConfig(name="test", api=api_type)
            assert pc.api == api_type


class TestMCPServerConfig:
    def test_defaults(self):
        mc = MCPServerConfig(name="test")
        assert mc.command == ""
        assert mc.type == "stdio"
        assert mc.timeout == 30.0
        assert mc.terminate_on_close is True

    def test_sse_type(self):
        mc = MCPServerConfig(name="remote", type="sse", url="http://example.com/sse")
        assert mc.type == "sse"
        assert mc.url == "http://example.com/sse"


class TestServerConfig:
    def test_defaults(self):
        sc = ServerConfig()
        assert sc.host == "127.0.0.1"
        assert sc.port == 8001

    def test_custom(self):
        sc = ServerConfig(host="localhost", port=9000)
        assert sc.host == "localhost"
        assert sc.port == 9000


class TestAgentEntry:
    def test_defaults(self):
        ae = AgentEntry(name="default")
        assert ae.description == ""
        assert ae.provider == ""
        assert ae.model == ""
        assert ae.system_prompt_template == "default"
        assert ae.memory_db == "memory.db"
        assert ae.server.host == "127.0.0.1"

    def test_effective_provider_fallback(self, minimal_settings):
        ae = AgentEntry(name="test")
        assert ae.effective_provider(minimal_settings) == "test"

    def test_effective_provider_explicit(self, minimal_settings):
        ae = AgentEntry(name="test", provider="other")
        assert ae.effective_provider(minimal_settings) == "other"

    def test_effective_model_fallback(self, minimal_settings):
        ae = AgentEntry(name="test")
        assert ae.effective_model(minimal_settings) == "test-model"

    def test_effective_model_explicit(self, minimal_settings):
        ae = AgentEntry(name="test", model="gpt-4o")
        assert ae.effective_model(minimal_settings) == "gpt-4o"

    def test_memory_db_empty_disables(self):
        ae = AgentEntry(name="test", memory_db="")
        assert ae.memory_db == ""

    def test_memory_db_none_disables(self):
        ae = AgentEntry(name="test", memory_db=None)
        assert ae.memory_db is None

    def test_server_config_defaults(self):
        ae = AgentEntry(name="remote")
        assert ae.server.host == "127.0.0.1"
        assert ae.server.port == 8001


class TestSettings:
    def test_required_fields(self, minimal_settings):
        assert minimal_settings.max_iterations == 5
        assert minimal_settings.tool_output_limit == 2000

    def test_get_provider_found(self, minimal_settings):
        p = minimal_settings.get_provider("test")
        assert p is not None
        assert p.name == "test"

    def test_get_provider_not_found(self, minimal_settings):
        p = minimal_settings.get_provider("nonexistent")
        assert p is None

    def test_is_configured_true(self, minimal_settings):
        minimal_settings.agents_config = AgentsConfig(
            chat_agent="default",
            agents=[AgentEntry(name="default", provider="test", model="test-model")],
        )
        assert minimal_settings.is_configured is True

    def test_is_configured_false_no_key(self):
        s = Settings(
            max_iterations=5,
            tool_output_limit=2000,
            providers=[ProviderConfig(name="test", api_key="")],
            default_provider="test",
            default_model="test-model",
            agents_config=AgentsConfig(
                chat_agent="default",
                agents=[AgentEntry(name="default", provider="test")],
            ),
        )
        assert s.is_configured is False

    def test_is_configured_false_no_agent(self, minimal_settings):
        minimal_settings.agents_config = AgentsConfig(chat_agent="nonexistent", agents=[])
        assert minimal_settings.is_configured is False

    def test_stream_default(self, minimal_settings):
        assert minimal_settings.stream is False

    def test_heartbeat_default(self, minimal_settings):
        assert minimal_settings.heartbeat_idle_seconds == 0

    def test_compress_thresholds(self, minimal_settings):
        assert minimal_settings.compress_threshold == 0.7
        assert minimal_settings.target_threshold == 0.5


class TestMemorySearchConfig:
    def test_defaults(self):
        msc = MemorySearchConfig()
        assert msc.auto_recall is True
        assert msc.auto_recall_top_k == 1
        assert msc.recall_memory_limit == 5


class TestUIConfig:
    def test_defaults(self):
        uic = UIConfig()
        assert uic.page_size == 20
        assert uic.refresh_per_second == 10


class TestLogConfig:
    def test_defaults(self):
        lc = LogConfig()
        assert lc.level == "INFO"
        assert lc.truncation == 500


# ── load/save tests ─────────────────────────────────────────────────

class TestLoadJson:
    def test_load_valid(self, config_json):
        data = load_json(config_json)
        assert data["default_provider"] == "test"
        assert len(data["providers"]) == 1

    def test_load_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_json(tmp_path / "nonexistent.json")


class TestSaveJson:
    def test_save_creates_file(self, tmp_path):
        data = {"key": "value"}
        path = tmp_path / "subdir" / "output.json"
        save_json(data, path)
        assert path.exists()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["key"] == "value"

    def test_save_creates_parent_dirs(self, tmp_path):
        data = {"key": "value"}
        path = tmp_path / "deep" / "nested" / "dir" / "output.json"
        save_json(data, path)
        assert path.exists()

    def test_save_unicode(self, tmp_path):
        data = {"name": "中文测试"}
        path = tmp_path / "unicode.json"
        save_json(data, path)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["name"] == "中文测试"


class TestLoadSettings:
    def test_from_file(self, config_json):
        settings = load_settings(config_json)
        assert settings.default_provider == "test"
        assert settings.default_model == "gpt-4o-mini"

    def test_missing_file_raises(self, tmp_path):
        # Settings requires max_iterations and tool_output_limit — no defaults
        with pytest.raises(Exception):
            load_settings(tmp_path / "nonexistent.json")

    def test_from_dict_data(self, config_json):
        data = load_json(config_json)
        settings = Settings.model_validate(data)
        assert settings.default_provider == "test"


class TestTopologyConfig:
    def test_defaults(self):
        tc = TopologyConfig()
        assert tc.relations == []

    def test_with_relations(self):
        tc = TopologyConfig(relations=[{"from": "a", "to": "b", "mode": "peer"}])
        assert len(tc.relations) == 1
        assert tc.relations[0]["from"] == "a"


class TestEmbeddingsBackend:
    def test_defaults(self):
        eb = EmbeddingsBackend(model_path="model.bin", dim=384)
        assert eb.backend == "sentence-transformers"
        assert eb.llama_n_ctx == 0

    def test_llama_backend(self):
        eb = EmbeddingsBackend(model_path="model.bin", dim=384, backend="llama-cpp-python", llama_n_ctx=512, llama_n_batch=256)
        assert eb.backend == "llama-cpp-python"
        assert eb.llama_n_ctx == 512