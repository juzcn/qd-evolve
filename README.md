# QD-Evolve

[![PyPI version](https://img.shields.io/pypi/v/qd-evolve)](https://pypi.org/project/qd-evolve/)

Multi-agent AI framework with A2A protocol support, group chat, persistent memory, and extensible tool system.

- [DESIGN.md](DESIGN.md) — design philosophy, invariants, architecture, implementation

## Installation

### Prerequisites

- **Python 3.13+**
- **Mosquitto v5 broker** (MQTT/GChat mode only) — [download](https://mosquitto.org/download/)

### Install from PyPI

```bash
pip install qd-evolve

# Or with uv
uv add qd-evolve

# Optional: BOAT bridge extras
pip install qd-evolve[boat]
```

### Install from source

```bash
git clone https://github.com/juzcn/qd-evolve
cd qd-evolve

# Install dependencies
uv sync

# Optional: BOAT bridge extras
uv sync --extra boat
```

### Configuration

Create a `config.json` in your working directory. Minimal setup for single-agent chat:

```json
{
  "env_vars": {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1"
  },
  "default_provider": "deepseek",
  "default_model": "deepseek-v4-pro",
  "providers": [
    {
      "name": "deepseek",
      "api_key": "...",
      "base_url": "https://api.deepseek.com",
      "api": "openai-completions",
      "models": [
        { "name": "deepseek-v4-pro", "reasoning": true, "context_window": 1000000, "max_tokens": 131072 }
      ]
    }
  ],
  "agents_config": {
    "agents": [
      {
        "name": "default",
        "toolbox": {
          "tools": {
            "load_func": "preload",
            "load_skill": "preload",
            "load_cli": "preload"
          }
        }
      }
    ]
  }
}
```

See [Configuration](#configuration-1) below for full multi-agent, MQTT, and toolbox setup.

### Verify

```bash
qd-evolve chat --agent default
```

## Quick Start

```bash
# Single-agent chat
qd-evolve chat --agent default

# Multi-agent A2A chat over HTTP
qd-evolve a2a-http

# Run an agent as standalone A2A HTTP server
qd-evolve a2a-http serve --agent <name>

# Multi-agent in-process chat (all agents loaded locally, no network)
qd-evolve a2a-inproc

# Multi-agent MQTT chat (requires Mosquitto v5 broker)
qd-evolve a2a-mqtt

# Run an agent as MQTT-accessible server
qd-evolve a2a-mqtt serve --agent <name>

# Group chat — WeChat-style multi-agent group (requires Mosquitto v5 broker)
# Supports: AI agents, terminal human agents, WeChat human agents
qd-evolve gchat --agent <name>

# Manage tool enable/disable/preload
qd-evolve toolbox --agent <name>

# Browse and search conversation memories
qd-evolve memory --agent <name>
```

## Four Systems

| System | Entry | Transport | Use Case |
|--------|-------|-----------|----------|
| Chat | `qd-evolve chat --agent <name>` | In-process only | Single-agent, no network |
| A2A Inproc | `qd-evolve a2a-inproc` | In-process only | Multi-agent in-process |
| A2A | `qd-evolve a2a-http` | HTTP + in-proc | Multi-agent over HTTP/SSE |
| MQTT | `qd-evolve a2a-mqtt` | MQTT v5 + in-proc | Multi-agent over MQTT |
| GChat | `qd-evolve gchat` | MQTT v5 (group topics) | WeChat-style group chat |

Each system is fully independent — no protocol fallback between them. See [DESIGN.md](DESIGN.md) for the full architecture.

## Configuration

All configuration via `config.json`. No CLI config commands, no `.env` files.

### Provider & Model

```json
{
  "default_provider": "openai",
  "default_model": "gpt-4o",
  "providers": [
    {
      "name": "openai",
      "api_key": "sk-...",
      "base_url": "https://api.openai.com/v1",
      "api": "openai-completions",
      "models": [
        { "name": "gpt-4o", "context_window": 128000, "max_tokens": 4096 }
      ]
    },
    {
      "name": "anthropic",
      "api_key": "sk-ant-...",
      "api": "anthropic",
      "models": [
        { "name": "claude-sonnet-4-6", "context_window": 200000, "max_tokens": 8192 }
      ]
    },
    {
      "name": "deepseek",
      "api_key": "...",
      "base_url": "https://api.deepseek.com",
      "api": "openai-completions",
      "models": [
        { "name": "deepseek-v4-pro", "reasoning": true, "context_window": 1000000, "max_tokens": 131072 }
      ]
    }
  ]
}
```

Three API types: `openai-completions`, `openai-response`, `anthropic`. Set at provider level via `api` field. Streaming is global (`stream` field). Reasoning/thinking is per-model (`reasoning: true`).

### Multi-Agent

```json
{
  "agents_config": {
    "chat_agent": "planner",
    "agents": [
      {
        "name": "planner",
        "description": "Plans and delegates tasks",
        "provider": "openai",
        "model": "gpt-4o",
        "memory_db": "planner.db",
        "server": { "host": "127.0.0.1", "port": 8001 },
        "toolbox": { "tools": {} }
      },
      {
        "name": "human",
        "description": "Human for approvals",
        "provider": "human",
        "server": { "host": "127.0.0.1", "port": 8002 }
      },
      {
        "name": "wechat_user",
        "description": "Human via WeChat iLink",
        "provider": "wechat-human",
        "server": { "host": "127.0.0.1", "port": 8003 }
      }
    ]
  }
}
```

Per-agent provider/model with global fallback. `provider: "human"` for terminal human agents, `"wechat-human"` for WeChat iLink bridge. Each agent has its own memory DB, server config, and toolbox state. WeChat human agents persist their session token via the `wechat_session` field.

### MQTT Broker

```json
{
  "agents_config": {
    "mqtt_broker": {
      "host": "127.0.0.1",
      "port": 1883
    }
  }
}
```

Requires external Mosquitto v5 broker.

### Toolbox

Tool enable/disable/preload per agent, managed via `qd-evolve toolbox --agent <name>` (Textual TUI) or by editing `config.json` directly.

```json
{
  "agents_config": {
    "agents": [{
      "name": "planner",
      "toolbox": {
        "tools": { "run_shell": "preload", "web_search": "enabled" },
        "mcp_servers": { "filesystem": "disabled" },
        "bridge": { "oat:boat": "enabled", "oat:coat": "disabled" },
        "cli": { "git": "preload" },
        "skills": { "code-review": "preload" }
      }
    }]
  }
}
```

Three states: `enabled` (on-demand schema), `preload` (schema at startup), `disabled` (invisible to agent).

## Runtime Features

### Slash Commands

| Command | Description |
|---------|-------------|
| `/models` | Switch provider/model |
| `/agents` | Switch agent |
| `/tools` | List tools |
| `/load <tool>` | Load tool schema |
| `/memory` | List saved memories with full content |
| `/compress` | Force context compression |
| `/clear` | Clear conversation |
| `/help` | Show help |
| `/quit` | Exit |

### Heartbeat

Agent-managed idle detection. When no activity for `heartbeat_idle_seconds`, sends a heartbeat prompt to the LLM. If LLM responds with `"."`, stays silent. Set `0` to disable. Mode-specific templates selected automatically.

### Replay Mode

`--replay <file>` feeds pre-recorded inputs for automated testing. `--output <file>` captures output.

### Token Stats

Per-turn and cumulative input/output tokens with context window usage percentage.

## A2A Protocol

Full [A2A v1.0](https://google.github.io/A2A/) implementation:

- **Agent discovery**: `/.well-known/agent.json`
- **Methods**: `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`, `tasks/resubscribe`, `tasks/pushNotification`, `agent/getExtendedAgentCard`
- **Task lifecycle**: submitted → working → completed / failed / canceled / input_required
- **SSE streaming**: `message/stream` returns `StreamResponse` events
- **Push notifications**: webhook callbacks on task completion

## Group Chat

WeChat-style multi-agent group via MQTT. All configured agents form a single group.

- **AI agents**: Background loop processes `@mentions`, runs agent in parallel, publishes responses
- **Terminal human agents** (`provider: "human"`): Interactive prompt — type messages, see group activity
- **WeChat human agents** (`provider: "wechat-human"`): Bidirectional WeChat iLink bridge — long-poll for incoming WeChat messages, forward group responses back to WeChat. QR login on startup, session persisted to `config.json`
- `@all` mentions everyone; specific `@agent_name` directs to one agent

## Project Layout

```
qd-evolve/
├── qd_evolve/       # Main package (agent, core, tools, utils, _templates)
├── tools/           # User tools (func, cli, mcp, bridge)
├── skills/          # Skills (SKILL.md files)
├── templates/       # User Jinja2 template overrides
├── tests/           # pytest suite
├── config.json      # All configuration
├── memory.db        # Conversation memory (SQLite + sqlite-vec)
└── pyproject.toml   # Dependencies and build config
```

See [DESIGN.md](DESIGN.md) for architecture and full module map.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) for dependency management
- External Mosquitto v5 broker (MQTT/GChat mode only)
- API keys for configured providers

## License

MIT
