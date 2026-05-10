from __future__ import annotations

import json
from pathlib import Path

from loguru import logger
from pydantic import BaseModel

CONFIG_PATH = Path("qd-evolve.json")


class ModelCost(BaseModel):
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


class ModelConfig(BaseModel):
    id: str = ""
    name: str = ""
    reasoning: bool = False
    input: list[str] = ["text"]
    cost: ModelCost = ModelCost()
    context_window: int = 0
    max_tokens: int = 4096

    @property
    def display_name(self) -> str:
        return self.name or self.id


class ProviderConfig(BaseModel):
    name: str
    api_key: str = ""
    base_url: str | None = None
    api: str = "openai-completions"  # openai-completions | openai-response | anthropic
    models: list[ModelConfig] = []


class MCPServerConfig(BaseModel):
    name: str
    command: str
    args: list[str] = []
    env: dict[str, str] = {}


class EmbeddingsBackend(BaseModel):
    model_path: str = "BAAI/bge-m3"
    dim: int = 1024
    backend: str = "sentence-transformers"  # sentence-transformers | llama-cpp-python
    llama_n_ctx: int = 8192
    llama_n_batch: int = 512


class MemorySearchConfig(BaseModel):
    default_embeddings_backend: str = "default"
    compress_threshold: float = 0.7
    target_threshold: float = 0.5
    auto_recall: bool = True
    auto_recall_top_k: int = 5


class Settings(BaseModel):
    log_level: str = "INFO"
    env_vars: dict[str, str] = {}
    providers: list[ProviderConfig] = []
    default_provider: str = ""
    default_model: str = ""
    skills_dir: str = "skills"
    memory_db: str = "memory.db"
    embeddings_backends: dict[str, EmbeddingsBackend] = {"default": EmbeddingsBackend()}
    memory_search: MemorySearchConfig = MemorySearchConfig()
    max_iterations: int = 20

    def get_provider(self, name: str | None = None) -> ProviderConfig | None:
        target = name or self.default_provider
        for p in self.providers:
            if p.name == target:
                return p
        return None

    def get_model_config(self, model_name: str) -> ModelConfig | None:
        for p in self.providers:
            for m in p.models:
                if m.name == model_name or m.id == model_name:
                    return m
        return None

    @property
    def is_configured(self) -> bool:
        p = self.get_provider()
        return bool(p and p.api_key)


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_settings(path: Path | str | None = None) -> Settings:
    p = Path(path) if path else CONFIG_PATH
    if p.exists():
        data = load_json(p)
        logger.debug("Loaded config from {}", p)
        return Settings.model_validate(data)
    logger.debug("Config file {} not found, using defaults", p)
    return Settings()


def save_settings(settings: Settings, path: Path | str | None = None) -> None:
    p = Path(path) if path else CONFIG_PATH
    data = settings.model_dump(exclude={"providers"})
    providers_data = []
    for prov in settings.providers:
        pd = prov.model_dump(exclude={"api_key"})
        providers_data.append(pd)
    data["providers"] = providers_data
    save_json(data, p)
    logger.info("Saved config to {}", p)