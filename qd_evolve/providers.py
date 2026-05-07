from __future__ import annotations

from typing import Any

from loguru import logger

from qd_evolve.config import ProviderConfig, Settings


# Map provider-level api field to model-level api_type
API_TYPE_MAP = {
    "openai-completions": "openai_completion",
    "openai-response": "openai_response",
    "anthropic": "anthropic",
}


class Provider:
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self._key_index = config.default_api_key

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def api_key(self) -> str:
        if not self.config.api_keys:
            raise ValueError(f"No API keys configured for provider {self.name}")
        return self.config.api_keys[self._key_index % len(self.config.api_keys)]

    def rotate_key(self) -> str:
        if len(self.config.api_keys) <= 1:
            return self.api_key
        self._key_index = (self._key_index + 1) % len(self.config.api_keys)
        logger.debug("Rotated to API key index {} for {}", self._key_index, self.name)
        return self.api_key

    @property
    def api_type(self) -> str:
        return API_TYPE_MAP.get(self.config.api, "openai_completion")

    def create_client(self) -> Any:
        key = self.api_key
        if self.api_type == "anthropic":
            from anthropic import Anthropic
            kwargs: dict[str, Any] = {"api_key": key}
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            return Anthropic(**kwargs)
        else:
            from openai import OpenAI
            kwargs = {"api_key": key}
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            return OpenAI(**kwargs)

    def get_model_names(self) -> list[str]:
        return [m.display_name for m in self.config.models]

    def get_max_tokens(self, model: str) -> int:
        m = self._find_model(model)
        return m.max_tokens if m else 4096

    def get_context_window(self, model: str) -> int:
        m = self._find_model(model)
        return m.context_window if m else 0

    def get_api_type(self, model: str) -> str:
        return self.api_type

    def get_model_cost(self, model: str) -> dict[str, float]:
        m = self._find_model(model)
        if m:
            return m.cost.model_dump()
        return {}

    def _find_model(self, model: str) -> Any:
        for m in self.config.models:
            if m.name == model or m.id == model:
                return m
        return None


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._providers: dict[str, Provider] = {}
        for pc in settings.providers:
            self._providers[pc.name] = Provider(pc)

    def get(self, name: str | None = None) -> Provider:
        target = name or self.settings.default_provider
        if target not in self._providers:
            raise KeyError(f"Provider not found: {target}")
        return self._providers[target]

    def list_providers(self) -> list[str]:
        return list(self._providers.keys())

    def list_all_models(self) -> dict[str, list[str]]:
        return {name: p.get_model_names() for name, p in self._providers.items()}
