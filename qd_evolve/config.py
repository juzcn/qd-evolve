"""Backward-compatible re-export shim — implementation moved to qd_evolve.core.config."""

from qd_evolve.core.config import (  # noqa: F401
    CONFIG_PATH, SKILLS_DIR, CLI_TOOLS_DIR, FUNC_TOOLS_DIR,
    MCP_DIR, BRIDGE_DIR, STAGING_DIR, MEMORY_DB,
    ModelCost, ModelConfig, ProviderConfig, MCPServerConfig,
    EmbeddingsBackend, MemorySearchConfig, UIConfig, LogConfig,
    Settings, load_json, save_json, load_settings,
)