"""Core infrastructure shared by all Agent processes."""

from qd_evolve.core.config import (
    CONFIG_PATH, SKILLS_DIR, CLI_TOOLS_DIR, FUNC_TOOLS_DIR,
    MCP_DIR, BRIDGE_DIR, STAGING_DIR, DEFAULT_MEMORY_DB,
    ModelCost, ModelConfig, ProviderConfig, MCPServerConfig,
    EmbeddingsBackend, MemorySearchConfig, UIConfig, LogConfig,
    Settings, load_json, save_json, load_settings,
)
from qd_evolve.core.logger import logger, setup_logging
from qd_evolve.core.providers import Provider, ProviderRegistry, API_TYPE_MAP
from qd_evolve.core.prompts import PromptTemplateManager
from qd_evolve.core.memory import (
    MemoryEntry, RecalledMemoryRegistry, MemoryStore,
    SentenceTransformerEmbedder, LlamaCppEmbedder,
)
from qd_evolve.core.registry import (
    ToolDef, ToolRegistry, get_registry, decode_output,
)
from qd_evolve.core.toolbox import (
    get_state, get_disabled, get_preloaded, set_state, toggle,
    apply_to_tools, apply_to_cli_registry, apply_to_skill_registry,
    get_disabled_bridges, get_default, state_mark,
)