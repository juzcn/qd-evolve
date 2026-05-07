# QD-Evolve

AI Agent with multi-provider, multi-model support and persistent tool management.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Start chat (default provider/model from qd-evolve.json)
qd-evolve

# Start chat with specific provider/model
qd-evolve chat --provider openai --model gpt-4o
```

## CLI Commands

```bash
qd-evolve                          # Start interactive chat (default)
qd-evolve chat                     # Same as above
qd-evolve tools list               # List all tools in the toolbox
qd-evolve tools mcp add <path>     # Register an MCP server from JSON config
qd-evolve tools mcp list           # List registered MCP servers
```

### In-Chat Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/tools` | List tools loaded in current session |
| `/model` | Show current model |
| `/provider` | Show current provider |
| `/enable <tool>` | Enable a tool |
| `/disable <tool>` | Disable a tool |
| `/clear` | Clear conversation |
| `/quit` | Exit |

## Configuration

All config in `qd-evolve.json`. No CLI config commands — edit the file directly.

```json
{
  "default_provider": "openai",
  "default_model": "gpt-4o",
  "log_level": "INFO",
  "default_system_prompt": "You are a helpful AI assistant with access to tools.",
  "embedding_model": "text-embedding-3-small",
  "embedding_dimensions": 256,
  "db_path": "qd_tools.db",
  "skills_dir": "skills",
  "skill_config": {
    "BAIDU_API_KEY": "sk-xxx"
  },
  "mcp_servers": [
    {
      "name": "open-meteo",
      "command": "npx",
      "args": ["-y", "-p", "open-meteo-mcp-server", "open-meteo-mcp-server"]
    }
  ],
  "providers": [...]
}
```

## Tool System

Three tool sources, all persisted to sqlite-vec (`qd_tools.db`):

| Source | Description | Registration |
|--------|-------------|--------------|
| **builtin** | Shell, file I/O, fetch, search, etc. | Auto-discovered from `qd_evolve/tools/*.py` |
| **skill** | Prompt-injected tools (SKILL.md + scripts) | Auto-discovered from `skills/*/SKILL.md` |
| **mcp** | MCP protocol servers | Via `qd-evolve tools mcp add` or config |

### Tool Persistence

- Tools are saved to sqlite-vec on registration (metadata + embeddings)
- Hybrid search: name exact → keyword → semantic similarity
- Enable/disable state persists across sessions
- MCP server configs reference the original JSON file path

### Adding MCP Servers

```bash
# From a JSON config file (supports Claude Code format)
qd-evolve tools mcp add mcp/open-meteo-mcp-server.json
```

JSON format (either style works):

```json
// Nested (Claude Code format)
{
  "mcpServers": {
    "open-meteo": {
      "command": "npx",
      "args": ["-y", "-p", "open-meteo-mcp-server", "open-meteo-mcp-server"]
    }
  }
}

// Flat
{
  "name": "open-meteo",
  "command": "npx",
  "args": ["-y", "-p", "open-meteo-mcp-server", "open-meteo-mcp-server"]
}
```

### Adding Skills

Create a directory under `skills/` with a `SKILL.md` and `scripts/`:

```
skills/
  baidu-search/
    SKILL.md           # Prompt injected into system prompt
    scripts/
      search.py        # Executed via subprocess
```

## Tech Stack

| Concern | Library |
|---------|---------|
| LLM API | anthropic + openai |
| Config | JSON + pydantic |
| CLI | typer + rich |
| Logging | loguru |
| Vector Store | sqlite-vec |
| Validation | pydantic |

## Project Structure

```
qd_evolve/
  config.py          — JSON config, Settings/ProviderConfig/ModelConfig
  logger.py          — loguru setup
  prompts.py         — prompt template management
  providers.py       — Provider/ProviderRegistry, client creation
  tools/
    __init__.py      — ToolRegistry, tool registration
    shell.py         — shell command execution
    file_rw.py       — file read/write/list
    fetch.py         — HTTP fetch
    search.py        — Serper web search
    find_tools.py    — tool search and loading
    _mcp_client.py   — MCP server connection
  skills.py          — Skill loader (SKILL.md + scripts)
  toolbox.py         — ToolBox persistence (sqlite-vec)
  vector.py          — VectorStore with sqlite-vec + embeddings
  agent.py           — agent loop (openai-completions/openai-response/anthropic)
  cli.py             — typer CLI
main.py              — thin launcher
skills/              — skill modules
mcp/                 — MCP server JSON configs
templates/           — prompt templates
qd-evolve.json       — configuration (not committed)
```
