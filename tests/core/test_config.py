"""Tests for qd_evolve.core.config — Settings, AgentEntry, load/save."""

import json

import pytest

from qd_evolve.core.config import (
    AgentEntry,
    EmbeddingsBackend,
    MCPServerConfig,
    ModelConfig,
    ProviderConfig,
    ServerConfig,
    Settings,
    TopologyConfig,
    AgentsConfig,
    MemorySearchConfig,
        load_json,
    load_settings,
    save_json,
    save_settings,
    A2ACLIConfig,
    MqttBrokerConfig,
    MqttConfig,
    GChatConfig,
)


# ── Pydantic model tests ────────────────────────────────────────────

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
        assert sc.host == ""
        assert sc.port == 0

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
        assert ae.memory_db == "memory.db"
        assert ae.server.host == ""

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
        assert ae.server.host == ""
        assert ae.server.port == 0

    def test_is_human(self):
        assert AgentEntry(name="h", provider="human").is_human is True
        assert AgentEntry(name="w", provider="wechat-human").is_human is True
        assert AgentEntry(name="a", provider="openai").is_human is False

    def test_is_wechat_human(self):
        assert AgentEntry(name="w", provider="wechat-human").is_wechat_human is True
        assert AgentEntry(name="h", provider="human").is_wechat_human is False

    def test_mqtt_config_defaults(self):
        ae = AgentEntry(name="mqtt_agent")
        assert ae.mqtt.username == ""
        assert ae.mqtt.keepalive == 60

    def test_wechat_session(self):
        ae = AgentEntry(name="wx", wechat_session={"room_id": "test"})
        assert ae.wechat_session == {"room_id": "test"}


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
            agents=[AgentEntry(name="default", provider="test", model="test-model", server=ServerConfig(port=8002))],
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
                agents=[AgentEntry(name="default", provider="test", server=ServerConfig(port=8002))],
            ),
        )
        assert s.is_configured is False

    def test_is_configured_false_no_agent(self, minimal_settings):
        minimal_settings.agents_config = AgentsConfig(chat_agent="nonexistent", agents=[])
        assert minimal_settings.is_configured is False

    def test_is_configured_human_agent(self):
        """Human agents don't need api_key — is_configured is True."""
        s = Settings(
            max_iterations=5,
            tool_output_limit=2000,
            providers=[ProviderConfig(name="test", api_key="")],
            default_provider="test",
            default_model="test-model",
            agents_config=AgentsConfig(
                chat_agent="human",
                agents=[AgentEntry(name="human", provider="human")],
            ),
        )
        assert s.is_configured is True

    def test_is_configured_missing_provider(self):
        """Agent references nonexistent provider — is_configured is False."""
        s = Settings(
            max_iterations=5,
            tool_output_limit=2000,
            providers=[ProviderConfig(name="test", api_key="sk-key")],
            default_provider="test",
            default_model="test-model",
            agents_config=AgentsConfig(
                chat_agent="default",
                agents=[AgentEntry(name="default", provider="nonexistent_provider")],
            ),
        )
        assert s.is_configured is False

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
        assert msc.auto_recall is False
        assert msc.auto_recall_top_k == 1
        assert msc.recall_memory_limit == 5




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

    def test_missing_file_uses_defaults(self, tmp_path):
        settings = load_settings(tmp_path / "nonexistent.json")
        assert settings.max_iterations == 20
        assert settings.tool_output_limit == 50000

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
        assert eb.llama_n_ctx == 8192
        assert eb.llama_n_batch == 512

    def test_llama_backend(self):
        eb = EmbeddingsBackend(model_path="model.bin", dim=384, backend="llama-cpp-python", llama_n_ctx=512, llama_n_batch=256)
        assert eb.backend == "llama-cpp-python"
        assert eb.llama_n_ctx == 512


class TestAgentsConfigPortValidation:
    def test_duplicate_agent_ports_raises(self):
        with pytest.raises(ValueError, match="Duplicate server ports"):
            AgentsConfig(
                chat_agent="default",
                agents=[
                    AgentEntry(name="a1", server=ServerConfig(port=8000)),
                    AgentEntry(name="a2", server=ServerConfig(port=8000)),
                ],
            )

    def test_agent_cli_port_conflict_raises(self):
        with pytest.raises(ValueError, match="Duplicate server ports"):
            AgentsConfig(
                chat_agent="default",
                agents=[AgentEntry(name="a1", server=ServerConfig(port=8000))],
                a2a_cli=A2ACLIConfig(server=ServerConfig(port=8000)),
            )

    def test_no_duplicate_ports_passes(self):
        ac = AgentsConfig(
            chat_agent="default",
            agents=[
                AgentEntry(name="a1", server=ServerConfig(port=8000)),
                AgentEntry(name="a2", server=ServerConfig(port=8001)),
            ],
        )
        assert len(ac.agents) == 2

    def test_zero_ports_ignored(self):
        ac = AgentsConfig(
            chat_agent="default",
            agents=[
                AgentEntry(name="a1", server=ServerConfig(port=0)),
                AgentEntry(name="a2", server=ServerConfig(port=0)),
            ],
        )
        assert len(ac.agents) == 2


class TestSaveSettings:
    def test_save_creates_file(self, tmp_path):
        s = Settings(max_iterations=5, tool_output_limit=1000)
        path = tmp_path / "out_config.json"
        save_settings(s, path)
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["max_iterations"] == 5

    def test_save_default_path(self, monkeypatch, tmp_path):
        s = Settings(max_iterations=5, tool_output_limit=1000)
        import qd_evolve.core.config as config_mod
        monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "default_out.json")
        save_settings(s)
        assert (tmp_path / "default_out.json").exists()


class TestMqttBrokerConfig:
    def test_defaults(self):
        mc = MqttBrokerConfig()
        assert mc.host == ""
        assert mc.port == 0

    def test_custom(self):
        mc = MqttBrokerConfig(host="mqtt.local", port=1883, will_delay_interval=5)
        assert mc.host == "mqtt.local"
        assert mc.port == 1883


class TestMqttConfig:
    def test_defaults(self):
        mc = MqttConfig()
        assert mc.keepalive == 60

    def test_tls_config(self):
        mc = MqttConfig(ca_certs="/path/ca.pem", certfile="/path/cert.pem", keyfile="/path/key.pem")
        assert mc.ca_certs == "/path/ca.pem"
        assert mc.certfile == "/path/cert.pem"


class TestGChatConfig:
    def test_defaults(self):
        gc = GChatConfig()
        assert gc.reply_delay_min == 0.0
        assert gc.reply_delay_max == 0.0

    def test_custom(self):
        gc = GChatConfig(reply_delay_min=0.5, reply_delay_max=2.0)
        assert gc.reply_delay_min == 0.5
        assert gc.reply_delay_max == 2.0


class TestA2ACLIConfig:
    def test_defaults(self):
        ac = A2ACLIConfig()
        assert ac.server.port == 0
        assert ac.resubscribe_retry_seconds == 15