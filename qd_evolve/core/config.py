
import json
from pathlib import Path
from typing import Any

from qd_evolve.core.logger import logger
from pydantic import BaseModel

CONFIG_PATH = Path("config.json")

# Project directory constants — not user-configurable
SKILLS_DIR = "tools/skills"
CLI_TOOLS_DIR = "tools/cli"
FUNC_TOOLS_DIR = "tools/func"
MCP_DIR = "tools/mcp"
BRIDGE_DIR = "tools/bridge"
STAGING_DIR = ".qd_evolve/staging"
DEFAULT_MEMORY_DB = "memory.db"
LOG_DIR = "logs"
DEFAULT_SERVER_HOST = "0.0.0.0"
DEFAULT_SERVER_PORT = 8001


class ModelCost(BaseModel):
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


class ModelConfig(BaseModel):
    name: str = ""
    reasoning: bool = False
    input: list[str] = ["text"]
    cost: ModelCost = ModelCost()
    context_window: int = 0
    max_tokens: int


class ProviderConfig(BaseModel):
    name: str
    api_key: str = ""
    base_url: str | None = None
    api: str = "openai-completions"  # openai-completions | openai-response | anthropic
    models: list[ModelConfig] = []


class MCPServerConfig(BaseModel):
    name: str
    command: str = ""
    args: list[str] = []
    env: dict[str, str] = {}
    type: str = "stdio"  # stdio | sse | http | ws
    url: str = ""
    headers: dict[str, str] = {}
    timeout: float = 30.0
    sse_read_timeout: float = 300.0
    terminate_on_close: bool = True


class EmbeddingsBackend(BaseModel):
    model_path: str
    dim: int
    backend: str = "sentence-transformers"  # sentence-transformers | llama-cpp-python
    llama_n_ctx: int = 0
    llama_n_batch: int = 0


class MemorySearchConfig(BaseModel):
    default_embeddings_backend: str = ""
    auto_recall: bool = True
    auto_recall_top_k: int = 1
    recall_memory_limit: int = 5
    list_all_limit: int = 50


class UIConfig(BaseModel):
    page_size: int = 20
    refresh_per_second: int = 10
    prompt_refresh_interval: float = 0.5


class LogConfig(BaseModel):
    level: str = "INFO"
    truncation: int = 500


class ServerConfig(BaseModel):
    host: str = DEFAULT_SERVER_HOST
    port: int = DEFAULT_SERVER_PORT


class AgentEntry(BaseModel):
    name: str
    description: str = ""
    provider: str = ""
    model: str = ""
    system_prompt_template: str = "default"
    a2a_tools: list[str] = []
    memory_db: str = DEFAULT_MEMORY_DB
    server: ServerConfig = ServerConfig()


class TopologyConfig(BaseModel):
    default_mode: str = "peer"
    default_transport: str = "inproc"
    transports: dict[str, str] = {}
    relations: list[dict[str, str]] = []


class AgentsConfig(BaseModel):
    default_agent: str = "default"
    agents: list[AgentEntry] = []
    topology: TopologyConfig = TopologyConfig()


class Settings(BaseModel):
    log: LogConfig = LogConfig()
    ui: UIConfig = UIConfig()
    env_vars: dict[str, str] = {}
    providers: list[ProviderConfig] = []
    default_provider: str = ""
    default_model: str = ""
    agents_config: AgentsConfig = AgentsConfig()
    embeddings_backends: dict[str, EmbeddingsBackend] = {}
    memory_search: MemorySearchConfig = MemorySearchConfig()
    compress_threshold: float = 0.7
    target_threshold: float = 0.5
    max_iterations: int
    tool_output_limit: int
    stream: bool = False
    heartbeat_idle_seconds: int = 0

    def get_provider(self, name: str | None = None) -> ProviderConfig | None:
        target = name or self.default_provider
        for p in self.providers:
            if p.name == target:
                return p
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
        logger.debug("Config: loaded from %s", p)
        return Settings.model_validate(data)
    logger.debug("Config: file %s not found, using defaults", p)
    return Settings()