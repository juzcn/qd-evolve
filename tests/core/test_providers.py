"""Tests for qd_evolve.core.providers — Provider, ProviderRegistry."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from qd_evolve.core.config import ModelConfig, ProviderConfig, Settings
from qd_evolve.core.providers import Provider, ProviderRegistry

# Pre-populate heavy SDK modules so @patch("anthropic.Anthropic") /
# @patch("openai.OpenAI") resolve without triggering slow real imports.
# anthropic: ~0.7s, openai: ~0.6s — shaves ~1.3s off the slowest tests.
if "anthropic" not in sys.modules:
    sys.modules["anthropic"] = SimpleNamespace(Anthropic=None)
if "openai" not in sys.modules:
    sys.modules["openai"] = SimpleNamespace(OpenAI=None)


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

    @pytest.mark.parametrize("method_name,method", [
        ("get_max_tokens", lambda p, m: p.get_max_tokens(m)),
        ("get_context_window", lambda p, m: p.get_context_window(m)),
        ("get_reasoning", lambda p, m: p.get_reasoning(m)),
    ])
    def test_model_not_found_raises_keyerror(self, minimal_settings, method_name, method):
        """All model lookup methods raise KeyError when model is not found."""
        pc = minimal_settings.providers[0]
        p = Provider(pc)
        with pytest.raises(KeyError):
            method(p, "nonexistent")

    def test_get_reasoning(self):
        pc = ProviderConfig(
            name="deepseek",
            api_key="sk",
            models=[ModelConfig(name="r1", reasoning=True, max_tokens=100, context_window=4000)],
        )
        p = Provider(pc)
        assert p.get_reasoning("r1") is True

    @patch("anthropic.Anthropic")
    def test_create_client_anthropic(self, mock_cls):
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        pc = ProviderConfig(name="anthro", api_key="sk-ant", api="anthropic")
        p = Provider(pc)
        client = p.create_client()
        mock_cls.assert_called_once_with(api_key="sk-ant")
        assert client is mock_instance

    @patch("openai.OpenAI")
    def test_create_client_openai(self, mock_cls):
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        pc = ProviderConfig(name="openai", api_key="sk-oai", api="openai-completions")
        p = Provider(pc)
        client = p.create_client()
        mock_cls.assert_called_once_with(api_key="sk-oai")
        assert client is mock_instance

    @patch("openai.OpenAI")
    def test_create_client_with_base_url(self, mock_cls):
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        pc = ProviderConfig(name="deepseek", api_key="sk", base_url="https://api.deepseek.com")
        p = Provider(pc)
        client = p.create_client()
        mock_cls.assert_called_once_with(api_key="sk", base_url="https://api.deepseek.com")
        assert client is mock_instance

    @patch("anthropic.Anthropic")
    def test_create_client_anthropic_with_base_url(self, mock_cls):
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        pc = ProviderConfig(name="anthro", api_key="sk-ant", api="anthropic", base_url="https://api.anthropic.example.com")
        p = Provider(pc)
        client = p.create_client()
        mock_cls.assert_called_once_with(api_key="sk-ant", base_url="https://api.anthropic.example.com")
        assert client is mock_instance

    def test_get_api_type_returns_api_type(self):
        pc = ProviderConfig(name="test", api_key="sk", api="anthropic")
        p = Provider(pc)
        assert p.get_api_type("any-model") == "anthropic"


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