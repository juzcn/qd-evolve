# QD-Evolve

Multi-agent AI system with A2A protocol, MQTT transport, human agents, push notifications, dual transport, tool use, skills, MCP integration, and CLI interface.

## Quick Start

```bash
uv tool install -e .          # local development (editable)
qd-evolve                     # start chat with default agent

# Or remote — no clone needed
uvx --from git+https://github.com/juzcn/qd-evolve qd-evolve
```

Create `config.json` in your working directory (copy from `config.json.example`, then add your API keys). At minimum you need a provider with `api_key`, `base_url`, `api`, and at least one model.

```bash
qd-evolve                     # start chat (in-process)
qd-evolve a2a                  # start A2A chat client+server (remote agents)
qd-evolve a2a serve --agent test  # run agent as standalone A2A HTTP server
qd-evolve mqtt                 # start MQTT chat client (pure client, no agent loading)
qd-evolve mqtt serve --agent test  # run agent as MQTT-accessible server
qd-evolve mqtt broker          # start embedded MQTT broker (type="embedded" in config)
qd-evolve toolbox             # interactive tool manager (Textual TUI)
qd-evolve toolbox --agent test   # per-agent tool management
qd-evolve --replay in.txt     # replay inputs (automated testing)
qd-evolve --replay in.txt --output out.txt   # replay + capture
```

## Chat Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/quit` | Quit the session |
| `/reset` | Reset conversation history |
| `/tools` | List available tools |
| `/skills` | List available skills |
| `/cli` | List registered CLI tools |
| `/agents` | List agents, switch current agent |
| `/status` | Runtime status (loaded tools, skills, CLI) |
| `/models` | Switch model interactively |
| `/memory` | List saved memories |

## Multi-Agent Architecture

### Agent Configuration

All agent config in `config.json` under `agents_config`:

```json
{
  "agents_config": {
    "chat_agent": "default",
    "agents": [
      {
        "name": "default",
        "description": "Default agent",
        "provider": "",
        "model": "",
        "server": {"host": "127.0.0.1", "port": 8002}
      },
      {
        "name": "remote",
        "description": "Remote agent",
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "memory_db": "remote_memory.db",
        "server": {"host": "127.0.0.1", "port": 8003}
      },
      {
        "name": "human",
        "friendly_name": "You",
        "description": "Human operator",
        "provider": "human",
        "server": {"host": "127.0.0.1", "port": 8004}
      }
    ],
    "topology": {
      "relations": [{"from": "default", "to": "remote", "mode": "peer"}]
    },
    "a2a_cli": {
      "server": {"host": "127.0.0.1", "port": 8001}
    }
  }
}
```

- `provider`/`model`: empty = use global defaults; `"human"` = human agent
- `friendly_name`: display name in CLI prompts and heartbeat (falls back to `name`)
- `memory_db`: independent SQLite per agent; `""` or `null` disables memory
- `server.host`: connect address (default `127.0.0.1`); server binds `0.0.0.0` to accept all interfaces
- `a2a_cli.server.port`: CLI's own A2A server port (for webhook callbacks)

### Per-Agent Toolbox

Each agent has its own `toolbox` section:

```json
{
  "name": "default",
  "toolbox": {
    "tools": {"load_cli": "preload", "fetch": "disabled"},
    "mcp_servers": {},
    "bridge": {"oat:boat": "enabled", "mcp:github-fetcher": "disabled"},
    "cli": {},
    "skills": {}
  }
}
```

States: `"enabled"` (default), `"disabled"`, `"preload"` (load schema into system prompt at startup).

### Topology

`agents_config.topology.relations` defines agent relationships. Transport between agents is auto-derived: if target is in local registry → inproc, otherwise → http.

```json
{"relations": [
  {"from": "coordinator", "to": "coder", "mode": "master-worker"},
  {"from": "coder", "to": "reviewer", "mode": "peer"}
]}
```

### A2A Protocol (v1.0)

Full A2A v1.0 spec implementation. All agents and CLI are independent A2A servers — they may be distributed across different machines.

| Method | Description | Blocking? |
|--------|-------------|-----------|
| `message/send` | Create task, wait for completion | Yes |
| `message/stream` | Create task, SSE stream `StreamResponse` events | No |
| `tasks/get` | Query task status | No |
| `tasks/cancel` | Cancel a task | No |
| `tasks/resubscribe` | Subscribe to events for an existing task via SSE | No |
| `tasks/pushNotification` | Webhook callback: receive completed task from remote agent | No |
| `agent/getExtendedAgentCard` | Extended AgentCard with runtime status | No |
| `/.well-known/agent.json` | Agent discovery | — |

SSE events use A2A v1.0 `StreamResponse` format. Custom events (iteration, status, print, tokens, heartbeat) carried in `TaskStatusUpdateEvent.metadata`.

### Human Agent

`provider: "human"` creates a human agent — a full A2A participant whose "inference engine" is a person. Human agents have their own server, AgentCard, TaskStore, event subscribers, and heartbeat.

Communication pattern (async callback via push notification):
1. AI agent calls `send_task("human", ...)` → human agent returns `Task(input_required)`
2. Human responds asynchronously → `complete_task()` fires webhook callback (`tasks/pushNotification`)
3. Calling agent's server receives webhook → updates task store → pushes event

`delegate_to` rejects human agents — they require async `send_task` because `delegate_to` is blocking.

CLI uses `send_task` (non-blocking) for human agents because humans may leave; uses `send_stream` (blocking) for AI agents.

### Dual Transport

- **InprocTransport** — Direct `AgentCore.run()` via `asyncio.to_thread`. Zero latency for same-machine agents.
- **HttpTransport** — aiohttp JSON-RPC client for cross-machine agents. Standard A2A protocol.
- **MqttTransport** — aiomqtt pub/sub for A2A over MQTT broker. Topic-based communication.
- **TransportRouter** — Auto-selects: local registry → inproc, otherwise → http.

`send_task` includes `callback_url` and `from_agent` in message metadata so push notifications route back to the sender's own A2A server (not the CLI's).

### MQTT Transport

MQTT provides an alternative transport layer using a message broker. All agents and the CLI connect to the same MQTT broker.

**Broker config** in `config.json` under `agents_config.mqtt_broker`:

```json
{
  "agents_config": {
    "mqtt_broker": {
      "type": "mosquitto",
      "host": "127.0.0.1",
      "port": 1883
    }
  }
}
```

| Field | Description |
|-------|-------------|
| `type` | `"mosquitto"` (default, external) or `"embedded"` (amqtt, dev only) |
| `host` | Broker connect address (default: `127.0.0.1`) |
| `port` | Broker port (default: `1883`) |

**Per-agent MQTT credentials** on each agent entry:

```json
{
  "name": "default",
  "mqtt": {
    "username": "",
    "password": "",
    "keepalive": 60
  }
}
```

**Topic structure:**

| Topic | Direction | Description |
|-------|-----------|-------------|
| `qd/agents/{name}/online` | Agent → Broker | Retained presence message |
| `qd/agents/{name}/tasks/{id}/request` | Client → Agent | Task submission |
| `qd/agents/{name}/tasks/{id}/response` | Agent → Client | Task result |
| `qd/agents/{name}/events` | Agent → Client | Streaming events |

**MQTT CLI** is a pure client — no tools, skills, or agent loading. It connects via MqttTransport and provides the same interactive experience as the A2A CLI (prompt_toolkit, slash commands, heartbeat, streaming, token stats).

**Broker types:**
- **mosquitto** (recommended) — Install [Eclipse Mosquitto](https://mosquitto.org/) and start it before running MQTT commands. Production-ready, works on Windows.
- **embedded** — `qd-evolve mqtt broker` starts an amqtt broker in-process. Convenient for development but has known issues on Windows.

### Event Stream

All agent events flow through `_push_event()` to subscriber queues:

| Event | Fields | Description |
|-------|--------|-------------|
| `iteration` | `num`, `provider`, `model` | New iteration started |
| `status` | `text` | Tool call / step progress |
| `print` | `text` | Reasoning content, tool output |
| `tokens` | `input`, `output`, `total_in`, `total_out` | Token usage |
| `completed` | `content` | Agent finished |
| `error` | `content` | API error |
| `heartbeat` | `content` | Speaking heartbeat (LLM replied with non-"." content) |
| `heartbeat_silent` | — | Silent heartbeat (LLM replied ".") |

### Push Notifications

When `A2AServer._tasks_push_notification` receives a completed task, it updates `_task_store` via `on_push_notification()` so `get_task()` returns the result. `send_task` maps the remote `task_id` to the local entry so push notifications can find it.

A2AAgent's `heartbeat_check` reads pending completed results from `_task_store` and injects them into the heartbeat prompt via `a2a-heartbeat.j2`, enabling the agent to act on asynchronously received responses (e.g. human agent replies).

## System Tools

Bundled in `qd_evolve/tools/` — core infrastructure, not user-replaceable:

| Tool | Description |
|------|-------------|
| `load_func` | Load full schema for a func tool on demand |
| `load_skill` | Load full SKILL.md content for a skill on demand |
| `load_cli` | Load full definition for a CLI tool on demand |
| `recall_memory` | Search past conversations |
| `install_func` | Install + hot-load a func tool |
| `register_func` | Persist a staged func tool to `tools/func/` |
| `install_mcp` | Install + hot-load an MCP server |
| `register_mcp` | Persist a staged MCP config |
| `install_skill` | Install + hot-load a skill from GitHub |
| `register_skill` | Persist a staged skill |
| `delegate_to` | Call another agent, wait for response (blocking A2A; rejects human agents) |
| `send_task` | Submit task to another agent, return task_id (non-blocking) |
| `get_task` | Query task status/result |
| `cancel_task` | Cancel a pending task |

## Func Tools

In `tools/func/` — add/delete .py files to add/remove tools:

| Tool | Description |
|------|-------------|
| `run_shell` | Execute shell commands with timeout |
| `run_python` | Execute Python code in isolated subprocess |
| `read_file` | Read file contents |
| `write_file` | Write content to a file |
| `list_directory` | List directory contents |
| `fetch` | Fetch URL content via HTTP GET/POST |
| `serper_search` | Web search via Serper API |
| `serper_scrape` | Scrape webpage content |

## Bridge Protocol

Generic tool integration protocol. Each bridge type self-registers with `BridgeManager` via `discover` → `connect` → `disconnect`. Adding a new bridge type only requires a `_*.py` module in `tools/bridge/`.

### MCP Bridge (external)

Place JSON config files in `tools/mcp/`. Supports 4 transport types:

**stdio** (default):
```json
{"mcpServers": {"name": {"command": "npx", "args": ["-y", "server"]}}}
```

**sse** / **http** / **ws**:
```json
{"mcpServers": {"name": {
  "type": "sse", "url": "https://example.com/sse",
  "headers": {"Authorization": "Bearer $API_KEY"}
}}}
```

All string values support `$VAR`/`${VAR}` env var expansion. API keys should be in `config.json` `env_vars`.

### OAT Bridge (in-process)

Loads `basic-open-agent-tools` (boat) and `coding-open-agent-tools` (coat) directly in-process — no subprocess, no MCP overhead.

Config in `tools/bridge/oat.json`:
```json
{"boat": {"package": "basic_open_agent_tools", "loadout": "coder"},
 "coat": {"package": "coding_open_agent_tools", "loadout": "python"}}
```

Schema: Google ADK → OpenAI JSON Schema via `qd_evolve/utils/adk_schema.py`. Output normalized via `qd_evolve/utils/adk_output.py`.

## Skills

SKILL.md files in `tools/skills/` — non-callable instructions. The LLM reads summaries in the system prompt and calls `load_skill` for full content when needed.

| Skill | Description |
|-------|-------------|
| `baidu-search` | Web search via Baidu AI Search Engine |
| `cli-register` | Register CLI tools from `--help` output |
| `find-skills` | Discover and install new skills |
| `find-tools` | Search and register new CLI/Python tools |
| `self-improvement` | Capture learnings for continuous improvement |
| `skill-creator` | Create and validate new skills |

## CLI Tools

YAML definitions in `tools/cli/` — non-callable. The LLM calls `load_cli` for usage info, then executes via `run_shell`.

```yaml
name: pandoc
command: pandoc
description: "Universal document converter"
help_summary: |
  Usage: pandoc [OPTIONS] [FILES]
    -f/--from=FORMAT    Input format
    -t/--to=FORMAT      Output format
    -o/--output=FILE    Output file
examples:
  - "pandoc input.md -o output.pdf"
```

## Prompt Templates

Jinja2 templates in `templates/` (user) or `qd_evolve/_templates/` (builtin fallback). `a2a-heartbeat.j2` is used for multi-agent heartbeat (includes pending task results and timestamp). Default template variables:

| Variable | Description |
|----------|-------------|
| `unpreloaded_skills` | Skill summaries not yet loaded |
| `unpreloaded_cli` | CLI tool summaries not yet loaded |
| `unloaded_tools` | Func tool name+description list not yet loaded |
| `preloaded_skills` | Full content of preloaded skills |
| `preloaded_cli` | Full usage of preloaded CLI tools |
| `memory_section` | Auto-recalled past conversations |
| `os_name` | Platform name |
| `python_cmd` | Detected python command |
| `cwd` | Current working directory |

## Configuration

All config via `config.json`. Key fields:

| Field | Description |
|-------|-------------|
| `default_provider` | Default provider (fallback when agent has no provider) |
| `default_model` | Default model (fallback when agent has no model) |
| `providers[].api` | `openai-completions` \| `openai-response` \| `anthropic` |
| `providers[].models[].context_window` | Context window in tokens; 0 disables compression |
| `providers[].models[].max_tokens` | Max output tokens (required) |
| `providers[].models[].reasoning` | Enable reasoning_content passthrough |
| `log.level` | DEBUG/INFO/WARNING/ERROR |
| `max_iterations` | Max tool-calling iterations per turn (default: 20) |
| `tool_output_limit` | Max chars per tool response before truncation |
| `stream` | Token-by-token streaming to terminal |
| `heartbeat_idle_seconds` | Idle seconds before heartbeat; 0 disables |
| `env_vars` | Environment variables injected at startup |
| `compress_threshold` | Token ratio to trigger compression (default: 0.7) |
| `target_threshold` | Token ratio to compress down to (default: 0.5) |
| `memory_search.auto_recall` | Auto memory recall before each LLM call |
| `memory_search.embeddings_backend` | Name of embeddings backend |
| `agents_config.chat_agent` | Currently active agent |
| `agents_config.agents[].server.host` | Connect address (default: `127.0.0.1`) |
| `agents_config.agents[].friendly_name` | Display name (falls back to `name`) |
| `agents_config.agents[].memory_db` | Per-agent SQLite file; `""`/`null` disables |
| `agents_config.mqtt_broker.type` | `"mosquitto"` (default) or `"embedded"` |
| `agents_config.mqtt_broker.host` | MQTT broker connect address (default: `127.0.0.1`) |
| `agents_config.mqtt_broker.port` | MQTT broker port (default: `1883`) |
| `agents_config.agents[].mqtt.username` | Per-agent MQTT username |
| `agents_config.agents[].mqtt.password` | Per-agent MQTT password |
| `agents_config.agents[].mqtt.keepalive` | MQTT keepalive interval in seconds (default: 60) |
| `toolbox_defaults.timeout` | Default tool timeout in seconds |

## Project Structure

```
qd_evolve/
  core/                    # Shared infrastructure
    config.py              — Settings, ProviderConfig, ModelConfig
    logger.py              — Standard logging with SharedFileHandler
    prompts.py             — Jinja2 template manager
    providers.py           — Provider/ProviderRegistry, client creation
    memory.py              — SQLite + sqlite-vec persistent memory
    registry.py            — ToolRegistry, on-demand loading
    toolbox.py             — Per-agent toolbox state management
  agent/                   # Agent implementation
    agent.py               — AgentCore loop (openai_completion, openai_response, anthropic)
    a2a.py                 — A2A v1.0 data models
    transport.py           — InprocTransport, HttpTransport, TransportRouter
    mqtt_transport.py      — MqttTransport (aiomqtt pub/sub)
    mqtt_agent.py          — MqttAgent wrapper (subscribes, runs, publishes)
    mqtt_broker.py         — Embedded amqtt broker
    server.py              — A2A HTTP server (aiohttp JSON-RPC)
    registry.py            — AgentRegistry + Topology
    loader.py              — init_process, create_agent_core
  _templates/              — Builtin Jinja2 templates (default.j2, heartbeat.j2, a2a-heartbeat.j2)
  utils/
    adk_schema.py          — Google ADK → OpenAI JSON Schema converter
    adk_output.py          — Output normalizer
  tools/                   # System tools (bundled)
    tool_loader.py         — load_func
    skill_loader.py        — load_skill
    cli_loader.py          — load_cli
    recall_memory.py       — recall_memory
    install_func.py        — install_func + hot-load
    register_func.py       — register_func (staging → tools/func/)
    install_mcp.py         — install_mcp + hot-load
    register_mcp.py        — register_mcp (staging → permanent)
    install_skill.py       — install_skill + hot-load
    register_skill.py      — register_skill (staging → permanent)
    a2a.py                 — delegate_to, send_task, get_task, cancel_task
    staging.py             — staging directory paths and cleanup
  skills.py                — SkillRegistry, SKILL.md discovery
  cli_tools.py             — CLIRegistry, YAML definitions
  cli.py                   — typer CLI, slash commands, event-driven display
  mqtt_cli.py              — MQTT CLI (pure client, interactive chat)
  toolbox_tui.py           — Textual TUI for tool management

tools/                       # Project root tool directories
  func/                      — Func tools (add/delete .py to add/remove)
  mcp/                       — MCP server configs (*.json)
  cli/                       — CLI tool definitions (*.yaml)
  skills/                    — SKILL.md skills
  bridge/                    — Bridge protocol modules
    __init__.py             — BridgeManager
    _mcp.py                 — MCP bridge (external)
    _oat.py                 — OAT bridge (in-process)
    oat.json                — OAT config
```

## Tech Stack

| Concern | Library |
|---------|---------|
| LLM API | anthropic + openai |
| A2A Protocol | aiohttp (JSON-RPC + SSE) |
| MQTT Transport | aiomqtt (pub/sub over MQTT broker) |
| Config | JSON + pydantic |
| Templates | Jinja2 |
| CLI | typer + rich + prompt-toolkit + textual |
| Logging | standard library |
| HTTP | httpx |
| Search | serper-toolkit |
| MCP | mcp (Model Context Protocol) |
| OAT | basic-open-agent-tools + coding-open-agent-tools |
| Memory | sqlite-vec + sentence-transformers |
