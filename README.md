# QD-Evolve

Multi-agent AI system with A2A protocol, dual transport, tool use, skills, MCP integration, and CLI interface.

## Quick Start

```bash
uv tool install -e .          # local development (editable)
qd-evolve                     # start chat with default agent

# Or remote — no clone needed
uvx --from git+https://github.com/juzcn/qd-evolve qd-evolve
```

Create `config.json` in your working directory (copy from `config.json.example`, then add your API keys). At minimum you need a provider with `api_key`, `base_url`, `api`, and at least one model.

```bash
qd-evolve                     # start chat
qd-evolve toolbox             # interactive tool manager (Textual TUI)
qd-evolve toolbox --agent test   # per-agent tool management
qd-evolve serve --agent test  # run agent as standalone A2A HTTP server
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
        "server": {"host": "127.0.0.1", "port": 8001},
        "transport": "inproc"
      },
      {
        "name": "remote",
        "description": "Remote agent",
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "memory_db": "remote_memory.db",
        "server": {"host": "127.0.0.1", "port": 8002},
        "transport": "http"
      }
    ],
    "topology": {
      "relations": [{"from": "default", "to": "remote", "mode": "peer"}]
    }
  }
}
```

- `provider`/`model`: empty = use global defaults
- `memory_db`: independent SQLite per agent; `""` or `null` disables memory
- `server.host`: connect address (default `127.0.0.1`); server binds `0.0.0.0` to accept all interfaces
- `transport`: `"inproc"` = runs in CLI process; `"http"` = runs remotely, CLI is HTTP client

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

`agents_config.topology.relations` defines agent relationships. Transport is auto-derived from `AgentEntry.transport`: both inproc → inproc, either http → http.

```json
{"relations": [
  {"from": "coordinator", "to": "coder", "mode": "master-worker"},
  {"from": "coder", "to": "reviewer", "mode": "peer"}
]}
```

### A2A Protocol (v1.0)

Full A2A v1.0 spec implementation:

| Method | Description | Blocking? |
|--------|-------------|-----------|
| `message/send` | Create task, wait for completion | Yes |
| `message/stream` | Create task, SSE stream `StreamResponse` events | No |
| `tasks/get` | Query task status | No |
| `tasks/cancel` | Cancel a task | No |
| `tasks/resubscribe` | Subscribe to events for an existing task via SSE | No |
| `agent/getExtendedAgentCard` | Extended AgentCard with runtime status | No |
| `/.well-known/agent.json` | Agent discovery | — |

SSE events use A2A v1.0 `StreamResponse` format. Custom events (iteration, status, print, tokens, heartbeat) carried in `TaskStatusUpdateEvent.metadata`.

### Dual Transport

- **InprocTransport** — Direct `AgentCore.run()` via `asyncio.to_thread`. Zero latency for same-machine agents.
- **HttpTransport** — aiohttp JSON-RPC client for cross-machine agents. Standard A2A protocol.
- **TransportRouter** — Auto-selects based on `AgentEntry.transport`.

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
| `delegate_to` | Call another agent, wait for response (blocking A2A) |
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

Jinja2 templates in `templates/` (user) or `qd_evolve/_templates/` (builtin fallback). Default template variables:

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
| `agents_config.agents[].memory_db` | Per-agent SQLite file; `""`/`null` disables |
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
    server.py              — A2A HTTP server (aiohttp JSON-RPC)
    registry.py            — AgentRegistry + Topology
    loader.py              — init_process, create_agent_core
  _templates/              — Builtin Jinja2 templates (default.j2, heartbeat.j2)
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
| Config | JSON + pydantic |
| Templates | Jinja2 |
| CLI | typer + rich + prompt-toolkit + textual |
| Logging | standard library |
| HTTP | httpx |
| Search | serper-toolkit |
| MCP | mcp (Model Context Protocol) |
| OAT | basic-open-agent-tools + coding-open-agent-tools |
| Memory | sqlite-vec + sentence-transformers |
