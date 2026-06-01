
from __future__ import annotations

import json
from pathlib import Path

from qd_evolve.core.logger import logger
from pydantic import BaseModel, model_validator

CONFIG_PATH = Path("config.json")

# Project directory constants — not user-configurable
SKILLS_DIR = "skills"
CLI_TOOLS_DIR = "tools/cli"
FUNC_TOOLS_DIR = "tools/func"
MCP_DIR = "tools/mcp"
DEFAULT_MEMORY_DB = "memory.db"
LOG_DIR = "logs"
DEFAULT_BIND_HOST = "0.0.0.0"


class ModelConfig(BaseModel):
    name: str = ""
    reasoning: bool = False
    input: list[str] = ["text"]
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
    dim: int = 1024
    backend: str = "sentence-transformers"  # sentence-transformers | llama-cpp-python
    llama_n_ctx: int = 8192
    llama_n_batch: int = 512


class MemorySearchConfig(BaseModel):
    embeddings_backend: str = ""
    auto_recall: bool = False
    auto_recall_top_k: int = 1
    recall_memory_limit: int = 5
    list_all_limit: int = 50




LOG_TRUNCATION = 500


class ServerConfig(BaseModel):
    host: str = ""
    port: int = 0


class ToolboxSection(BaseModel):
    tools: dict[str, str] = {}
    mcp_servers: dict[str, str] = {}
    bridge: dict[str, str] = {}
    cli: dict[str, str] = {}
    skills: dict[str, str] = {}


DEFAULT_TOOL_TIMEOUT = 60


class MqttConfig(BaseModel):
    """Per-agent MQTT transport configuration."""
    username: str = ""
    password: str = ""
    keepalive: int = 60
    ca_certs: str = ""
    certfile: str = ""
    keyfile: str = ""


class MqttBrokerConfig(BaseModel):
    """MQTT broker configuration. host/port are where clients connect. MQTT v5 only."""
    host: str = ""
    port: int = 0
    will_delay_interval: int = 0


class AgentEntry(BaseModel):
    name: str
    description: str = ""
    provider: str = ""
    model: str = ""
    memory_db: str | None = DEFAULT_MEMORY_DB
    server: ServerConfig = ServerConfig()
    toolbox: ToolboxSection = ToolboxSection()
    mqtt: MqttConfig = MqttConfig()
    wechat_session: dict | None = None

    @property
    def is_human(self) -> bool:
        return self.provider in ("human", "wechat-human")

    @property
    def is_wechat_human(self) -> bool:
        return self.provider == "wechat-human"

    def effective_provider(self, settings: Settings) -> str:
        return self.provider or settings.default_provider

    def effective_model(self, settings: Settings) -> str:
        return self.model or settings.default_model



class TopologyConfig(BaseModel):
    relations: list[dict[str, str]] = []


class GChatConfig(BaseModel):
    """Group chat configuration."""
    reply_delay_min: float = 0.0
    reply_delay_max: float = 0.0


class A2ACLIConfig(BaseModel):
    """A2A chat client configuration — server port for webhook callbacks."""
    server: ServerConfig = ServerConfig(port=0)
    resubscribe_retry_seconds: int = 15


class AgentsConfig(BaseModel):
    chat_agent: str = "default"
    agents: list[AgentEntry] = []
    topology: TopologyConfig = TopologyConfig()
    a2a_cli: A2ACLIConfig = A2ACLIConfig()
    mqtt_broker: MqttBrokerConfig = MqttBrokerConfig()
    gchat: GChatConfig = GChatConfig()

    @model_validator(mode="after")
    def _validate_ports(self) -> "AgentsConfig":
        ports: dict[int, list[str]] = {}
        for agent in self.agents:
            if agent.server.port:
                ports.setdefault(agent.server.port, []).append(f"agent '{agent.name}'")

        if self.a2a_cli.server.port:
            ports.setdefault(self.a2a_cli.server.port, []).append("a2a_cli server")

        dupes = {p: owners for p, owners in ports.items() if len(owners) > 1}
        if dupes:
            lines = [f"  port {p} shared by: {', '.join(owners)}" for p, owners in dupes.items()]
            raise ValueError("Duplicate server ports in config:\n" + "\n".join(lines))
        return self


class Settings(BaseModel):
    log_level: str = "INFO"
    env_vars: dict[str, str] = {}
    providers: list[ProviderConfig] = []
    default_provider: str = ""
    default_model: str = ""
    agents_config: AgentsConfig = AgentsConfig()
    embeddings_backends: dict[str, EmbeddingsBackend] = {}
    memory_search: MemorySearchConfig = MemorySearchConfig()
    compress_threshold: float = 0.7
    target_threshold: float = 0.5
    max_iterations: int = 20
    tool_output_limit: int = 50000
    stream: bool = False
    heartbeat_idle_seconds: int = 0

    def get_provider(self, name: str) -> ProviderConfig | None:
        for p in self.providers:
            if p.name == name:
                return p
        return None

    @property
    def is_configured(self) -> bool:
        current = self.agents_config.chat_agent
        for a in self.agents_config.agents:
            if a.name == current:
                if a.is_human:
                    return True
                prov = a.effective_provider(self)
                p = self.get_provider(prov)
                return bool(p and p.api_key)
        return False


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_settings(path: Path | str | None = None) -> Settings:
    from qd_evolve.core.toolbox import migrate_toolbox_to_config
    migrate_toolbox_to_config()

    p = Path(path) if path else CONFIG_PATH
    if p.exists():
        data = load_json(p)
        logger.debug("Config: loaded from %s", p)
        return Settings.model_validate(data)
    logger.debug("Config: file %s not found, using defaults", p)
    return Settings()


def save_settings(settings: Settings, path: Path | str | None = None) -> None:
    p = Path(path) if path else CONFIG_PATH
    data = settings.model_dump(mode="json", exclude_none=False)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.debug("Config: saved to %s", p)