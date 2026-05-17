"""Tests for qd_evolve.core.providers — Provider, ProviderRegistry."""

import pytest

from qd_evolve.core.config import ModelConfig, ProviderConfig, Settings
from qd_evolve.core.providers import Provider, ProviderRegistry


class TestProvider:
    def test_name(self, minimal_settings):
        pc = minimal_settings.providers[0]
        p = Provider(pc)
        assert p.name == "test"

    def test_api_key(self, minimal_settings):
        pc = minimal_settings.providers[0]
        p = Provider(pc)
        assert p.api_key == "sk-test-key"

    def test_api_key_missing_raises(self):
        pc = ProviderConfig(name="nokey", api_key="")
        p = Provider(pc)
        with pytest.raises(ValueError, match="No API key"):
            _ = p.api_key

    def test_api_type(self, minimal_settings):
        pc = minimal_settings.providers[0]
        p = Provider(pc)
        assert p.api_type == "openai_completion"

    def test_api_type_anthropic(self):
        pc = ProviderConfig(name="anthro", api_key="sk-ant", api="anthropic")
        p = Provider(pc)
        assert p.api_type == "anthropic"

    def test_api_type_openai_response(self):
        pc = ProviderConfig(name="resp", api_key="sk", api="openai-response")
        p = Provider(pc)
        assert p.api_type == "openai_response"

    def test_unknown_api_type_raises(self):
        pc = ProviderConfig(name="bad", api_key="sk", api="unknown")
        p = Provider(pc)
        with pytest.raises(ValueError, match="Unknown api type"):
            _ = p.api_type

    def test_get_max_tokens(self, minimal_settings):
        pc = minimal_settings.providers[0]
        p = Provider(pc)
        assert p.get_max_tokens("test-model") == 100

    def test_get_max_tokens_not_found(self, minimal_settings):
        pc = minimal_settings.providers[0]
        p = Provider(pc)
        with pytest.raises(KeyError, match="Model not found"):
            p.get_max_tokens("nonexistent")

    def test_get_context_window(self, minimal_settings):
        pc = minimal_settings.providers[0]
        p = Provider(pc)
        assert p.get_context_window("test-model") == 4000

    def test_get_context_window_not_found(self, minimal_settings):
        pc = minimal_settings.providers[0]
        p = Provider(pc)
        with pytest.raises(KeyError):
            p.get_context_window("nonexistent")

    def test_get_reasoning(self):
        pc = ProviderConfig(
            name="deepseek",
            api_key="sk",
            models=[ModelConfig(name="r1", reasoning=True, max_tokens=100, context_window=4000)],
        )
        p = Provider(pc)
        assert p.get_reasoning("r1") is True

    def test_get_reasoning_not_found(self):
        pc = ProviderConfig(name="test", api_key="sk", models=[])
        p = Provider(pc)
        with pytest.raises(KeyError):
            p.get_reasoning("nonexistent")

    def test_create_client_anthropic(self):
        pc = ProviderConfig(name="anthro", api_key="sk-ant", api="anthropic")
        p = Provider(pc)
        client = p.create_client()
        assert client is not None

    def test_create_client_openai(self):
        pc = ProviderConfig(name="openai", api_key="sk-oai", api="openai-completions")
        p = Provider(pc)
        client = p.create_client()
        assert client is not None

    def test_create_client_with_base_url(self):
        pc = ProviderConfig(name="deepseek", api_key="sk", base_url="https://api.deepseek.com")
        p = Provider(pc)
        client = p.create_client()
        assert client is not None


class TestProviderRegistry:
    def test_get_by_name(self, providers):
        p = providers.get("test")
        assert p.name == "test"

    def test_get_default(self, providers):
        p = providers.get("test")
        assert p.name == "test"

    def test_get_not_found(self, providers):
        with pytest.raises(KeyError, match="Provider not found"):
            providers.get("nonexistent")

    def test_get_empty_name(self, providers):
        with pytest.raises(KeyError):
            providers.get("")

    def test_multiple_providers(self):
        s = Settings(
            max_iterations=5,
            tool_output_limit=2000,
            providers=[
                ProviderConfig(name="openai", api_key="sk-oai", models=[ModelConfig(name="gpt-4o", max_tokens=100, context_window=4000)]),
                ProviderConfig(name="anthropic", api_key="sk-ant", api="anthropic", models=[ModelConfig(name="claude-3", max_tokens=200, context_window=8000)]),
            ],
            default_provider="openai",
            default_model="gpt-4o",
        )
        reg = ProviderRegistry(s)
        assert reg.get("openai").api_type == "openai_completion"
        assert reg.get("anthropic").api_type == "anthropic"