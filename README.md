# QD-Evolve

Multi-agent AI framework with A2A protocol support, group chat, persistent memory, and extensible tool system.

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

# Group chat — WeChat-style multi-agent group (requires Mosquitto v5 broker)
qd-evolve gchat --agent <name>

# Manage tool enable/disable/preload
qd-evolve toolbox --agent <name>
```

## Architecture

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│   Chat CLI   │  │   A2A CLI    │  │  MQTT CLI   │  │  GChat CLI  │
│  (in-proc)   │  │ (HTTP/SSE)   │  │  (MQTT v5)  │  │ (MQTT v5)   │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                  │                  │
       ▼                 ▼                  ▼                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│                          Agent Layer                                  │
│  Agent ← A2AAgent ← MqttAgent ← GroupChatAgent  |  HumanAgent       │
│  AgentRegistry  |  TransportRouter  |  EventSubscribers             │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
  ┌──────────┐      ┌──────────┐      ┌──────────┐
  │ Provider │      │  Memory  │      │  Toolbox │
  │ Registry │      │  Store   │      │ Registry │
  └──────────┘      └──────────┘      └──────────┘
```

### Four Independent Systems

| System | Entry | Transport | Use Case |
|--------|-------|-----------|----------|
| Chat | `qd-evolve` | In-process only | Single-agent, no network |
| A2A | `qd-evolve a2a` | HTTP + in-proc | Multi-agent over HTTP/SSE |
| MQTT | `qd-evolve mqtt` | MQTT v5 + in-proc | Multi-agent over MQTT |
| GChat | `qd-evolve gchat` | MQTT v5 (group topics) | WeChat-style group chat |

Each system is fully independent — no protocol fallback between them.

### Agent Hierarchy

- **Agent** — Pure LLM loop: call API → execute tools → repeat. Manages messages, memory, heartbeat, callbacks. Serialized with `threading.Lock` to prevent concurrent corruption.
- **A2AAgent** — Wraps Agent, adds A2A identity (AgentCard, TaskStore), event subscriber fan-out, heartbeat override with multi-agent template.
- **MqttAgent** — Wraps A2AAgent, adds MQTT v5 lifecycle (connect, LWT, subscribe, publish).
- **GroupChatAgent** — Wraps MqttAgent, adds group chat behavior: subscribes to `/chat` topics, deduplication, parallel `agent.run()`, group message publishing. Owns heartbeat loop using `group-heartbeat.j2` template.
- **HumanAgent** — Implements AgentProtocol directly. No LLM, no tools, no memory. Returns `input_required`, completes asynchronously.

### Transport

`TransportRouter(inproc, remote)` — routes to in-process for local agents, to `HttpTransport` or `MqttTransport` for remote. Never both remote transports simultaneously. Group chat uses a separate `GroupChatTransport` with its own MQTT connection on `$a2a/v1/group/+/chat` topics, keeping the MqttTransport sole-consumer design intact.

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
        "gpt-4o": { "context_window": 128000, "cost": { "input": 2.5, "output": 10 } }
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

Three API types: `openai-completions`, `openai-response`, `anthropic`. Set at provider level via `api` field. Streaming is global (`stream` field). Reasoning/thinking is per-model (`reasoning: true`). Per-model cost tracking via `ModelCost`.

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
        "system_prompt_template": "default",
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

Tool enable/disable/preload per agent, managed via `qd-evolve toolbox --agent <name>` (Textual TUI) or by editing `config.json` directly.

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
| MCP | `tools/mcp/*.json` | External: stdio, SSE, StreamableHTTP, WebSocket |
| OAT | `tools/bridge/oat.json` | In-process: boat + coat, no subprocess overhead |

Schema conversion: Google ADK → OpenAI JSON Schema via `adk_schema.py`, output normalization via `adk_output.py`.

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
| `$a2a/v1/group/{name}/chat` | Group chat messages |

## Group Chat

WeChat-style multi-agent group chat via MQTT. All configured agents form a single group.

- **AI agents**: Background loop — subscribes to group topics, processes `@mentions`, runs `agent.run()` in parallel, publishes responses
- **Human agents**: Interactive terminal — displays incoming group messages, publishes keyboard input
- **Deduplication**: Message IDs tracked in `_seen_msg_ids` set
- **Independent transport**: `GroupChatTransport` uses its own MQTT connection, keeping MqttTransport's sole-consumer design intact
- **Templates**: `group-default.j2` (system prompt), `group-heartbeat.j2` (idle check), `group-message.j2` (incoming message format)

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

Agent-managed idle detection via `asyncio.Event.wait(timeout)`. Only triggers on actual timeout (no activity for `heartbeat_idle_seconds`). Configurable per agent — `0` disables. Displays per-agent heartbeat counters. Template selection: single-agent → `heartbeat.j2`, A2A → `a2a-heartbeat.j2`, MQTT → `mqtt-heartbeat.j2`, Group → `group-heartbeat.j2`.

### Token Stats

Per-turn and cumulative input/output tokens with context window usage percentage.

### Replay Mode

`--replay <file>` feeds pre-recorded inputs for automated testing. `--output <file>` captures output.

## Project Structure

```
qd-evolve/
├── qd_evolve/
│   ├── __main__.py          # CLI entry point (typer) — registers subcommands
│   ├── chat_cli.py          # Single-agent chat loop
│   ├── a2a_cli.py           # A2A multi-agent chat loop
│   ├── mqtt_cli.py          # MQTT multi-agent chat loop
│   ├── gchat_cli.py         # Group chat CLI loop
│   ├── cli_utils.py         # Shared CLI utilities (ReplayInput, TeeWriter, AGENT_COLORS)
│   ├── skills.py            # SkillRegistry
│   ├── cli_tools.py         # CLIRegistry
│   ├── toolbox_tui.py       # Toolbox manager (Textual TUI + CLI command)
│   ├── agent/
│   │   ├── agent.py         # Agent — LLM loop, tools, memory, heartbeat, compression
│   │   ├── a2a_agent.py     # A2AAgent — wraps Agent, adds A2A identity + events
│   │   ├── mqtt_agent.py    # MqttAgent — wraps A2AAgent, MQTT v5 lifecycle
│   │   ├── group_chat_agent.py  # GroupChatAgent — wraps MqttAgent, group chat behavior
│   │   ├── group_chat_human.py  # GroupChatHuman — wraps MqttHumanAgent for group chat
│   │   ├── group_chat_transport.py  # GroupChatTransport — independent MQTT for /chat topics
│   │   ├── human_agent.py   # HumanAgent — no LLM, async completion
│   │   ├── mqtt_human_agent.py  # MQTT wrapper for HumanAgent
│   │   ├── server.py        # A2A HTTP server (JSON-RPC + SSE)
│   │   ├── transport.py     # Inproc / Http / Mqtt transport
│   │   ├── mqtt_transport.py    # MqttTransport — sole-consumer MQTT v5
│   │   ├── registry.py      # AgentRegistry — current agent management
│   │   ├── loader.py        # init_process + create_agent
│   │   ├── a2a_tools.py     # A2A tools (delegate_to, send_task, etc.)
│   │   ├── protocol.py      # AgentProtocol ABC
│   │   └── a2a.py           # A2A models (Task, Message, AgentCard, etc.)
│   ├── core/
│   │   ├── config.py        # Settings, AgentEntry, ServerConfig, MqttConfig, constants
│   │   ├── providers.py     # ProviderRegistry — multi-provider, multi-model
│   │   ├── registry.py      # ToolRegistry — on-demand loading
│   │   ├── memory.py        # MemoryStore + RecalledMemoryRegistry
│   │   ├── prompts.py       # PromptTemplateManager (Jinja2)
│   │   ├── logger.py        # SharedFileHandler
│   │   └── toolbox.py       # Toolbox state management, migration, apply helpers
│   ├── tools/
│   │   ├── tool_loader.py   # Tool preloading
│   │   ├── skill_loader.py  # Skill loading
│   │   ├── cli_loader.py    # CLI tool loading
│   │   ├── install_func.py  # Install func tools
│   │   ├── install_mcp.py   # Install MCP servers
│   │   ├── install_skill.py # Install skills
│   │   ├── register_func.py # Register func tools
│   │   ├── register_mcp.py  # Register MCP servers
│   │   ├── register_skill.py # Register skills
│   │   ├── recall_memory.py # Recall memory tool
│   │   ├── staging.py       # Staging area for hot-loading
│   │   └── ...              # Other system tools
│   ├── utils/
│   │   ├── adk_schema.py    # Google ADK → OpenAI JSON Schema conversion
│   │   └── adk_output.py    # ADK output normalization
│   └── _templates/          # Builtin Jinja2 templates (fallback)
│       ├── default.j2       # Single-agent system prompt
│       ├── a2a-default.j2   # A2A system prompt
│       ├── mqtt-default.j2  # MQTT system prompt
│       ├── group-default.j2 # Group chat system prompt
│       ├── group-heartbeat.j2 # Group chat heartbeat
│       ├── group-message.j2 # Group chat incoming message format
│       ├── heartbeat.j2     # Single-agent heartbeat
│       ├── a2a-heartbeat.j2 # A2A heartbeat
│       ├── mqtt-heartbeat.j2 # MQTT heartbeat
│       └── _system_tail.j2  # Shared tail included by all templates
├── tools/
│   ├── func/                # User function tools (.py)
│   ├── cli/                 # CLI tool definitions (.yaml)
│   ├── skills/              # Skill instructions (SKILL.md)
│   ├── mcp/                 # MCP server configs (*.json)
│   └── bridge/
│       ├── oat.json         # OAT bridge config
│       └── _*.py            # Bridge implementations (MCP, OAT)
├── templates/               # User Jinja2 templates (override builtins)
├── tests/                   # pytest test suite
├── config.json              # All configuration
└── pyproject.toml           # Dependencies and build config
```

## Requirements

- Python 3.13+
- External Mosquitto v5 broker (MQTT/GChat mode only)
- API keys for configured providers

## License

MIT
