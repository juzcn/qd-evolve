# QD-Evolve

Multi-agent AI framework with A2A protocol support, persistent memory, and extensible tool system.

## Quick Start

```bash
# Install
uv sync

# Configure — edit config.json with your API keys and models

# Single-agent chat
qd-evolve

# Multi-agent A2A chat
qd-evolve a2a

# Run an agent as standalone A2A HTTP server
qd-evolve a2a serve --agent <name>

# Multi-agent MQTT chat (requires Mosquitto v5 broker)
qd-evolve mqtt

# Run an agent as MQTT-accessible server
qd-evolve mqtt serve --agent <name>
```

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Chat CLI   │     │   A2A CLI    │     │  MQTT CLI   │
│  (in-proc)   │     │ (HTTP/SSE)   │     │  (MQTT v5)  │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                     │
       ▼                    ▼                     ▼
┌──────────────────────────────────────────────────────────┐
│                    Agent Layer                            │
│  Agent ← A2AAgent ← MqttAgent  |  HumanAgent (no LLM)   │
│  AgentRegistry  |  TransportRouter  |  EventSubscribers  │
└──────────────────────────┬───────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
  ┌──────────┐      ┌──────────┐      ┌──────────┐
  │ Provider │      │  Memory  │      │  Toolbox │
  │ Registry │      │  Store   │      │ Registry │
  └──────────┘      └──────────┘      └──────────┘
```

### Three Independent Systems

| System | Entry | Transport | Use Case |
|--------|-------|-----------|----------|
| Chat | `qd-evolve` | In-process only | Single-agent, no network |
| A2A | `qd-evolve a2a` | HTTP + in-proc | Multi-agent over HTTP/SSE |
| MQTT | `qd-evolve mqtt` | MQTT v5 + in-proc | Multi-agent over MQTT |

Each system is fully independent — no protocol fallback between them.

### Agent Hierarchy

- **Agent** — Pure LLM loop: call API → execute tools → repeat. Manages messages, memory, heartbeat, callbacks.
- **A2AAgent** — Wraps Agent, adds A2A identity (AgentCard, TaskStore), event subscriber fan-out, heartbeat override with multi-agent template.
- **MqttAgent** — Wraps A2AAgent, adds MQTT v5 lifecycle (connect, LWT, subscribe, publish).
- **HumanAgent** — Implements AgentProtocol directly. No LLM, no tools, no memory. Returns `input_required`, completes asynchronously.

### Transport

`TransportRouter(inproc, remote)` — routes to in-process for local agents, to `HttpTransport` or `MqttTransport` for remote. Never both remote transports simultaneously.

## Configuration

All configuration via `config.json`. No CLI config commands, no `.env` files.

### Provider & Model

```json
{
  "default_provider": "openai",
  "default_model": "gpt-4o",
  "providers": {
    "openai": {
      "api_key": "sk-...",
      "base_url": "https://api.openai.com/v1",
      "api": "openai-completions",
      "models": {
        "gpt-4o": { "context_window": 128000, "input_price": 2.5, "output_price": 10 }
      }
    },
    "anthropic": {
      "api_key": "sk-ant-...",
      "api": "anthropic",
      "models": { "claude-sonnet-4-6": { "context_window": 200000 } }
    },
    "deepseek": {
      "api_key": "...",
      "base_url": "https://api.deepseek.com/v1",
      "api": "openai-completions",
      "models": { "deepseek-r1": { "reasoning": true } }
    }
  }
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
        "system_prompt_template": "planner.j2",
        "memory_db": "planner.db",
        "server": { "host": "127.0.0.1", "port": 8001 },
        "toolbox": { "tools": {}, "bridge": { "oat:boat": "enabled" } }
      },
      {
        "name": "human",
        "description": "Human for approvals",
        "provider": "human",
        "server": { "host": "127.0.0.1", "port": 8002 }
      }
    ],
    "topology": { "relations": [] }
  }
}
```

Per-agent provider/model with global fallback. `provider: "human"` identifies human agents. Each agent has its own memory DB, server config, and toolbox state.

### MQTT Broker

```json
{
  "agents_config": {
    "mqtt_broker": {
      "host": "127.0.0.1",
      "port": 1883,
      "will_delay_interval": 5
    }
  }
}
```

Requires external Mosquitto v5 broker. Per-agent credentials via `MqttConfig` on each `AgentEntry`.

### Toolbox

Tool enable/disable/preload per agent, managed via `qd-evolve toolbox` (Textual TUI) or by editing `config.json` directly.

```json
{
  "agents_config": {
    "agents": [{
      "name": "planner",
      "toolbox": {
        "tools": { "run_shell": "preloaded", "web_search": "enabled" },
        "mcp_servers": { "filesystem": "disabled" },
        "bridge": { "oat:boat": "enabled", "oat:coat": "disabled" },
        "cli": { "git": "preloaded" },
        "skills": { "code-review": "preloaded" }
      }
    }]
  }
}
```

## Tool System

### Three Tool Categories

| Category | Location | Loading | Callable |
|----------|----------|---------|----------|
| System | `qd_evolve/tools/` | Auto-scanned, on-demand schema | Yes |
| A2A | `qd_evolve/agent/a2a_tools.py` | Registered when A2A enabled | Yes |
| Func | `tools/func/` | Add/remove `.py` files | Yes |
| Skills | `tools/skills/` | SKILL.md files | No — instructions only |
| CLI Tools | `tools/cli/` | YAML definitions | No — via `run_shell` |

### On-Demand Loading

Tools start with name+description only. LLM calls `load_func`/`load_skill`/`load_cli` to get full schema/instructions, then the tool activates for subsequent turns. Reduces prompt size.

### Bridge Protocol

Extensible tool source integration. Each bridge type self-registers with `BridgeManager`.

| Bridge | Config | Description |
|--------|--------|-------------|
| MCP | `tools/bridge/*.json` | External: stdio, SSE, StreamableHTTP, WebSocket |
| OAT | `tools/bridge/oat.json` | In-process: boat + coat, no subprocess overhead |

### Hot-Loading

`install_func`/`install_mcp`/`install_skill` register new tools in the current session without restart. Staged in `.qd_evolve/staging/`, persisted via `register_*`.

## Memory

- **Persistent**: SQLite + sqlite-vec for cross-session storage
- **Embeddings**: BGE-M3 via `sentence-transformers` or `llama-cpp-python` backend
- **Auto-recall**: Before each LLM call, user input queries past conversations (configurable top-k)
- **Context compression**: When tokens exceed `compress_threshold` (default 70%), old Q/A pairs are removed until below `target_threshold` (default 50%)

## A2A Protocol

Full [A2A v1.0](https://google.github.io/A2A/) implementation:

- **Agent discovery**: `/.well-known/agent.json`
- **Methods**: `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`, `tasks/resubscribe`, `tasks/pushNotification`, `agent/getExtendedAgentCard`
- **Task lifecycle**: submitted → working → completed / failed / canceled / input_required
- **SSE streaming**: `message/stream` returns `StreamResponse` events with custom metadata (iteration, status, tokens, heartbeat)
- **Push notifications**: Completed tasks trigger webhook callbacks; results injected into heartbeat prompt

### MQTT Topic Structure

| Topic | Purpose |
|-------|---------|
| `$a2a/v1/discovery/{agent}` | Retained AgentCard + LWT |
| `$a2a/v1/request/{agent}` | Task requests (JSON-RPC) |
| `$a2a/v1/response/{agent}/{req_id}` | Responses via MQTT v5 Response Topic |
| `$a2a/v1/event/{agent}` | Streaming events + push notifications |

## Runtime Features

### Slash Commands

| Command | Description |
|---------|-------------|
| `/models` | Switch provider/model |
| `/agents` | Switch agent |
| `/tools` | List tools |
| `/load <tool>` | Load tool schema |
| `/recall <query>` | Search memory |
| `/compress` | Force context compression |
| `/clear` | Clear conversation |
| `/help` | Show help |
| `/quit` | Exit |

### Heartbeat

Agent-managed idle detection via `asyncio.Event.wait(timeout)`. Only triggers on actual timeout (no activity for `heartbeat_idle_seconds`). Configurable per agent — `0` disables. Displays per-agent heartbeat counters.

### Token Stats

Per-turn and cumulative input/output tokens with context window usage percentage.

### Replay Mode

`--replay <file>` feeds pre-recorded inputs for automated testing. `--output <file>` captures output.

## Project Structure

```
qd-evolve/
├── qd_evolve/
│   ├── __main__.py          # CLI entry point (typer)
│   ├── chat_cli.py          # Single-agent chat loop
│   ├── a2a_cli.py           # A2A multi-agent chat loop
│   ├── mqtt_cli.py          # MQTT multi-agent chat loop
│   ├── agent/
│   │   ├── agent.py         # Agent — LLM loop, tools, memory, heartbeat
│   │   ├── a2a_agent.py     # A2AAgent — wraps Agent, adds A2A identity + events
│   │   ├── mqtt_agent.py    # MqttAgent — wraps A2AAgent, MQTT v5 lifecycle
│   │   ├── human_agent.py   # HumanAgent — no LLM, async completion
│   │   ├── mqtt_human_agent.py  # MQTT wrapper for HumanAgent
│   │   ├── server.py        # A2A HTTP server (JSON-RPC + SSE)
│   │   ├── transport.py     # Inproc / Http / Mqtt transport
│   │   ├── registry.py      # AgentRegistry — current agent management
│   │   ├── loader.py        # init_process + create_agent
│   │   ├── a2a_tools.py     # A2A tools (delegate_to, send_task, etc.)
│   │   ├── protocol.py      # AgentProtocol ABC
│   │   └── a2a.py           # A2A models (Task, Message, AgentCard, etc.)
│   ├── core/
│   │   ├── config.py        # Settings, AgentEntry, ServerConfig, MqttConfig
│   │   ├── providers.py     # ProviderRegistry — multi-provider, multi-model
│   │   ├── registry.py      # ToolRegistry — on-demand loading
│   │   ├── memory.py        # MemoryStore + RecalledMemoryRegistry
│   │   ├── prompts.py       # PromptTemplateManager (Jinja2)
│   │   ├── logger.py        # SharedFileHandler
│   │   └── compress.py      # Context compression
│   └── tools/
│       ├── _registry.py     # Tool discovery and dispatch
│       ├── _bridge.py       # BridgeManager
│       ├── run_shell.py     # Shell execution
│       ├── web_search.py    # Web search (Serper)
│       ├── memory_tools.py  # recall_memory, save_memory
│       └── ...              # Other system tools
├── tools/
│   ├── func/                # User function tools (.py)
│   ├── cli/                 # CLI tool definitions (.yaml)
│   ├── skills/              # Skill instructions (SKILL.md)
│   └── bridge/
│       ├── *.json           # Bridge configs (MCP, OAT)
│       └── _*.py            # Bridge implementations
├── templates/               # User Jinja2 templates (override builtins)
├── qd_evolve/_templates/    # Builtin Jinja2 templates (fallback)
├── tests/                   # pytest test suite
├── config.json              # All configuration
└── pyproject.toml           # Dependencies and build config
```

## Requirements

- Python 3.13+
- External Mosquitto v5 broker (MQTT mode only)
- API keys for configured providers

## License

Private
