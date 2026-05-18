# QD-Evolve

Multi-agent AI system with A2A protocol, dual transport, tool use, skills, MCP integration, and CLI interface.

## Features

- **Multi-agent architecture** — A2A (Agent-to-Agent) protocol with dual transport: inproc (zero-latency direct call) + HTTP (aiohttp JSON-RPC for cross-machine)
- **Fully isolated agents** — Each agent has independent messages, memory db, system prompt, and toolbox config
- **Per-agent model config** — Each agent has its own `provider`/`model` in `config.json`; `/models` switches the current agent's model and persists; empty strings fall back to global `default_provider`/`default_model`
- **Agent config in config.json** — `agents_config` section with `chat_agent` (currently active agent), `agents` list, and `topology` sub-section
- **AgentEntry.transport** — Each agent has a `transport` field (`"inproc"` | `"http"`, default `"inproc"`); determines whether agent runs in CLI process or remotely
- **ServerConfig** — Structured `host`/`port` per agent (pydantic model, no hardcoded defaults)
- **API error handling** — LLM API errors caught and returned as error messages instead of crashing the session
- **A2A interaction tools** — `delegate_to` (blocking), `send_task` (non-blocking), `get_task`, `cancel_task`
- **Topology config** — `agents_config.topology` defines agent relationships (peer/master-worker) via `relations` list; transport derived from `AgentEntry.transport`
- **Multi-provider, multi-model** — OpenAI Chat Completions, OpenAI Responses API, Anthropic Messages API
- **Tool system** — Builtin tools + MCP (external processes) + OAT bridge (in-process boat + coat)
- **Bridge protocol** — qd-evolve's own protocol for tool source integration; new bridge types never touch `cli.py`
- **OAT bridge** — basic-open-agent-tools (boat) + coding-open-agent-tools (coat) loaded in-process, no subprocess latency
- **Toolbox TUI** — `qd-evolve toolbox` (Textual) for interactive enable/disable/preload management across all tool types
- **Toolbox config** — Per-agent tool state in `config.json` under `agents[].toolbox` (tools, mcp_servers, bridge, cli, skills); global defaults in `toolbox_defaults`
- **Two tool categories** — System tools (load/install/register/a2a, bundled in `qd_evolve/tools/`) and Func tools (run_shell, fetch, etc. in `tools/func/`). Add/delete .py files in `tools/func/` to add/remove tools
- **On-demand loading** — Tools start with name+description only. Call `load_func` to activate a tool's schema, `load_skill` for SKILL.md instructions, or `load_cli` for CLI usage. Loaded content is delivered via tool message, keeping the system prompt lean.
- **Skill system** — SKILL.md files from `tools/skills/`, injected into system prompt; `load_skill` for full content
- **CLI tools** — YAML definitions in `tools/cli/`, loaded via `load_cli`
- **MCP integration** — stdio, SSE, StreamableHTTP, WebSocket; env var expansion in config; clean tool names (no prefix); disabled servers skipped entirely
- **Jinja2 prompt templates** — `.j2` files in `templates/`, with builtin fallbacks
- **Persistent memory** — SQLite + sqlite-vec with BGE-M3 semantic + keyword hybrid search; disable per-agent by setting `memory_db` to `""` or `null`
- **Context compression** — Auto Q/A removal when tokens exceed threshold; set `context_window` to 0 or omit it to disable
- **Auto recall** — Relevant past conversations auto-injected into system prompt
- **Per-turn token stats** — Input/output tracking with context window usage
- **Heartbeat** — Agent-owned heartbeat loop (`start_heartbeat_loop`/`stop_heartbeat_loop`); idle detection with LLM-driven heartbeat messages; per-agent counters displayed as agent name; configurable via `heartbeat_idle_seconds`
- **Hot-loading** — `install_func/install_mcp/install_skill` hot-load new tools into current session without restart; `register_func/register_mcp/register_skill` persist to permanent directories; staging area `.qd_evolve/staging/` for user confirmation before permanent registration
- **Streaming** — Global `stream` setting for token-by-token output to terminal (OpenAI-compatible providers)
- **Reasoning/thinking mode** — Per-model `reasoning` flag for DeepSeek-style reasoning_content passthrough with terminal display
- **Dual embedder support** — sentence-transformers or llama-cpp-python
- **Structured logging** — Standard library logging with SharedFileHandler for real-time log visibility
- **Rich CLI** — Interactive prompt with tab completion, spinner, per-agent heartbeat counters, iteration/tool-call display, and slash commands

## Quick Start

```bash
# Local development (editable)
uv tool install -e .
qd-evolve                  # start chat with default agent

# Or remote — no clone needed
uvx --from git+https://github.com/juzcn/qd-evolve qd-evolve
```

Create `config.json` in your working directory (copy from `config.json.example`, then add your API keys):

```bash
curl -O https://raw.githubusercontent.com/juzcn/qd-evolve/main/config.json.example
mv config.json.example config.json
# edit config.json with your keys
```

See `config.json.example` for the full config structure. At minimum you need a provider with `api_key`, `base_url`, `api`, and at least one model.

Run:

```bash
qd-evolve                  # start chat with default agent
qd-evolve toolbox          # interactive tool manager (Textual TUI)
qd-evolve toolbox --agent test  # per-agent tool management
qd-evolve --replay in.txt  # replay inputs from file (for automated testing)
qd-evolve --replay in.txt --output out.txt  # replay + capture output
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
| `/agents` | List discovered agents |
| `/status` | Show runtime status (loaded tools, skills, CLI) |
| `/models` | Switch model interactively |
| `/memory` | List saved memories |

## Multi-Agent Architecture

### Agent Configuration

All agent config is in `config.json` under `agents_config`:

```json
{
  "agents_config": {
    "chat_agent": "default",
    "agents": [
      {
        "name": "default",
        "description": "Default qd-evolve agent",
        "provider": "",
        "model": "",
        "server": {"host": "0.0.0.0", "port": 8001},
        "transport": "inproc"
      },
      {
        "name": "test",
        "description": "Test agent for cross-process A2A",
        "provider": "baiduqianfancodingplan",
        "model": "qianfan-code-latest",
        "memory_db": "test_memory.db",
        "server": {"host": "0.0.0.0", "port": 8002},
        "transport": "http"
      }
    ],
    "topology": {
      "relations": [{"from": "default", "to": "test", "mode": "peer"}]
    }
  }
}
```

- `provider`/`model`: empty = use global defaults from `config.json`
- `a2a_tools`: auto-derived — enabled when >1 agent, disabled when only 1
- `memory_db`: independent db file per agent (default: `"memory.db"`); `""` or `null` disables memory entirely
- `server.host/port`: HTTP server for cross-machine A2A communication
- `transport`: `"inproc"` = runs in CLI process; `"http"` = runs in remote process, CLI is pure HTTP client

### Per-Agent Toolbox

Each agent has its own `toolbox` section in `config.json` under `agents_config.agents[]`:

```json
{
  "toolbox_defaults": {"timeout": 60},
  "agents_config": {
    "agents": [
      {
        "name": "default",
        "toolbox": {
          "tools": {"load_cli": "preload", "fetch": "disabled"},
          "mcp_servers": {}, "bridge": {}, "cli": {}, "skills": {}
        }
      },
      {
        "name": "test",
        "toolbox": {
          "tools": {}, "mcp_servers": {}, "bridge": {}, "cli": {}, "skills": {}
        }
      }
    ]
  }
}
```

### Topology

`agents_config.topology` in config.json defines agent relationships. Transport is derived from each agent's `transport` field — no manual transport mapping needed.

```json
{
  "relations": [
    {"from": "coordinator", "to": "coder", "mode": "master-worker"},
    {"from": "coder", "to": "reviewer", "mode": "peer"}
  ]
}
```

- Transport is auto-derived: both agents `inproc` → `inproc`, either agent `http` → `http`
- `mode: "peer"` (default) — agents can mutually delegate tasks
- `mode: "master-worker"` — hierarchical delegation

### A2A Protocol

Full A2A v1.0 spec implementation with standard methods:

| Method | Description | Blocking? |
|--------|-------------|-----------|
| `message/send` | Create task, wait for completion | Yes |
| `message/stream` | Create task, SSE stream `StreamResponse` events (intermediate events in `metadata`) | No |
| `tasks/get` | Query task status | No |
| `tasks/cancel` | Cancel a task | No |
| `tasks/resubscribe` | Subscribe to agent events for an existing task via SSE | No |
| `agent/getExtendedAgentCard` | Get extended AgentCard with runtime status (provider, model, loaded tools/skills/CLI) | No |
| AgentCard | `/.well-known/agent.json` — discover agent capabilities | — |

SSE events follow A2A v1.0 `StreamResponse` format: each `data:` line is a JSON-RPC response with `result` containing a `StreamResponse` (one of: `task`, `statusUpdate`, `artifactUpdate`). Custom events (iteration, status, print, tokens, heartbeat) are carried in `TaskStatusUpdateEvent.metadata`.

### Dual Transport

- **InprocTransport** — Direct `AgentCore.run()` call via `asyncio.to_thread`. Zero network latency for same-machine agents. Lazy-loads target agent from config on demand. Agent owns heartbeat via `start_heartbeat_loop()`/`stop_heartbeat_loop()`.
- **HttpTransport** — aiohttp JSON-RPC client for cross-machine agents. Standard A2A protocol. CLI uses SSE event stream from `chat/subscribe` for iteration/heartbeat/tool-call display.
- **TransportRouter** — Auto-selects transport based on `AgentEntry.transport` fields. Both agents `inproc` → inproc transport; either agent `http` → HTTP transport. `delegate_to` and other tools only depend on the transport interface.
- **Unified event display** — Both paths share a `event_queue` for per-agent heartbeat counters (`♡ name:N` in bottom toolbar). Inproc events bridge via `_inproc_event_worker`; HTTP events bridge via `_event_worker`. Same `Live(Group)` iteration display pattern for both modes.

### Event Stream

`chat/subscribe` carries all agent events via SSE, not just heartbeat. Event types:

| Event | Fields | Description |
|-------|--------|-------------|
| `iteration` | `num`, `provider`, `model` | New iteration started |
| `status` | `text` | Tool call / step progress |
| `print` | `text` | Reasoning content, tool output |
| `tokens` | `input`, `output`, `total_in`, `total_out` | Token usage after API call |
| `completed` | `content` | Agent finished, final response |
| `error` | `content` | API error during run |
| `heartbeat` | `content` | Speaking heartbeat response |
| `heartbeat_silent` | — | Silent heartbeat (no content) |
| `ping` | — | SSE keepalive (30s timeout) |

AgentCore pushes events via `_push_event()` to all subscriber queues. `_update_status` and `_print` are wired to also push events, so tool calls and reasoning flow through the same stream.

## System Tools

Bundled in `qd_evolve/tools/` — core infrastructure, not user-replaceable:

| Tool | Description |
|------|-------------|
| `load_func` | Load full schema for a func tool on demand |
| `load_skill` | Load full SKILL.md content for a skill on demand |
| `load_cli` | Load full definition for a CLI tool on demand |
| `recall_memory` | Search past conversations by query, keywords, and time range |
| `install_func` | Install + hot-load a Python-based func tool |
| `register_func` | Persist a staged func tool to `tools/func/` |
| `install_mcp` | Install + hot-load an MCP server |
| `register_mcp` | Persist a staged MCP config to permanent directory |
| `install_skill` | Install + hot-load a skill from a GitHub repo |
| `register_skill` | Persist a staged skill to permanent directory |
| `delegate_to` | Call another Agent and wait for response (blocking A2A) |
| `send_task` | Submit task to another Agent, return task_id (non-blocking A2A) |
| `get_task` | Query status/result of a submitted task |
| `cancel_task` | Cancel a pending task |

## Func Tools

In `tools/func/` — replaceable utilities. Add/delete .py files to add/remove tools:

| Tool | Description |
|------|-------------|
| `run_shell` | Execute shell commands with timeout |
| `run_python` | Execute Python code in isolated subprocess |
| `read_file` | Read file contents |
| `write_file` | Write content to a file (creates parent dirs) |
| `list_directory` | List directory contents |
| `fetch` | Fetch URL content via HTTP GET/POST |
| `serper_search` | Web search via Serper API (general/images/news) |
| `serper_scrape` | Scrape webpage content |

## Bridge Protocol

qd-evolve's own generic tool integration protocol. Each bridge type self-registers with `BridgeManager` via three functions: `discover` → `connect` → `disconnect`. Adding a new bridge type only requires a `_*.py` module in `tools/bridge/` — `cli.py` never changes.

### MCP Bridge (external)

Place JSON config files in `tools/mcp/`. Supports 4 transport types:

**stdio** (default) — local subprocess:
```json
{ "mcpServers": { "name": { "command": "npx", "args": ["-y", "server"] } } }
```

**sse** — Server-Sent Events (GET):
```json
{ "mcpServers": { "name": {
  "type": "sse", "url": "https://example.com/sse",
  "headers": { "Authorization": "Bearer $API_KEY" }
} } }
```

**http** / **streamable-http** — Streamable HTTP (POST):
```json
{ "mcpServers": { "name": {
  "type": "http", "url": "https://example.com/mcp/",
  "headers": { "Authorization": "Bearer $API_KEY" },
  "timeout": 30, "sse_read_timeout": 300
} } }
```

**ws** / **websocket** — WebSocket:
```json
{ "mcpServers": { "name": { "type": "ws", "url": "wss://example.com/mcp" } } }
```

All string values (`command`, `url`, `headers`, `args`) support `$VAR`/`${VAR}` expansion from `os.environ`. API keys should be defined in `config.json` `env_vars` and referenced via `$VAR` in MCP config.

Also supports legacy formats: nested `mcp.servers` and bare `{ "command": "...", "args": [...] }`.

### OAT Bridge (in-process)

Loads `basic-open-agent-tools` (boat) and `coding-open-agent-tools` (coat) directly in-process — no subprocess, no MCP serialization overhead.

Config in `tools/bridge/oat.json`:

```json
{
  "boat": {
    "package": "basic_open_agent_tools",
    "loadout": "coder"
  },
  "coat": {
    "package": "coding_open_agent_tools",
    "loadout": "python"
  }
}
```

- `loadout` selects tool subset per package (coder, python, all, etc.)
- Schema: Google ADK → OpenAI JSON Schema via `qd_evolve/utils/adk_schema.py`
- Output: auto-normalized via `qd_evolve/utils/adk_output.py`
- Remove an entry to disable that package; edit `loadout` to change tool subset

### Bridge Toolbox

`config.json` `agents[].toolbox.bridge` section controls bridge enable/disable:

```json
{
  "bridge": {
    "oat:boat": "enabled",
    "mcp:github-fetcher": "disabled"
  }
}
```

## CLI Tools

CLI tools are YAML definitions in `tools/cli/` that describe how to use command-line programs. They are **not** tool calls — the LLM calls `load_cli` to get usage info and examples, then executes via `run_shell`.

```yaml
# tools/cli/pandoc.yaml
name: pandoc
command: pandoc
description: "Universal document converter"
help_summary: |
  Usage: pandoc [OPTIONS] [FILES]
  Key options:
    -f/--from=FORMAT    Input format
    -t/--to=FORMAT      Output file
    -o/--output=FILE    Output file
examples:
  - "pandoc input.md -o output.pdf"
  - "pandoc input.docx -t markdown"
```

Use the `cli-register` skill to generate YAML from `--help` output automatically.

## Skills

Skills are directories under `tools/skills/` containing a `SKILL.md` file. They are **not** tool calls — the LLM reads the summary in the system prompt and uses `load_skill` to get full instructions when needed. Skill state (enabled/disabled/preloaded) is managed via `config.json` `agents[].toolbox.skills` or `qd-evolve toolbox`.

```
tools/skills/
  my-skill/
    SKILL.md       # instructions (summary shown in prompt, full content loaded on demand)
    _meta.json     # optional: {"slug": "my-skill", "version": "1.0.0", "description": "..."}
```

Included skills:

| Skill | Description |
|-------|-------------|
| `baidu-search` | Search the web using Baidu AI Search Engine |
| `cli-register` | Register CLI tools by analyzing `--help` output and generating YAML definitions |
| `find-skills` | Discover and install new agent skills |
| `find-tools` | Search, try, and register new CLI/Python tools when existing tools fall short |
| `self-improvement` | Capture learnings and corrections for continuous improvement |

## Prompt Templates

Jinja2 templates in `templates/` (user) or `qd_evolve/_templates/` (builtin fallback). The default template receives these variables:

| Variable | Description |
|----------|-------------|
| `unpreloaded_skills` | Skill summaries not yet loaded (use `load_skill`) |
| `unpreloaded_cli` | CLI tool summaries not yet loaded (use `load_cli`) |
| `unloaded_tools` | Func tool name + description list not yet loaded (use `load_func`) |
| `preloaded_skills` | Full content of preloaded skills |
| `preloaded_cli` | Full usage info of preloaded CLI tools |
| `memory_section` | Auto-recalled relevant past conversations (if any) |
| `os_name` | Platform name (e.g. Windows, Linux) |
| `python_cmd` | Detected python command |
| `cwd` | Current working directory |
| `skills_dir` | Skills directory path |

## Configuration

All config via `config.json`. Key fields:

| Field | Description |
|-------|-------------|
| `default_provider` | Default provider name (fallback when agent has no provider set) |
| `default_model` | Default model name (fallback when agent has no model set) |
| `providers[].models[].context_window` | Model context window size in tokens; 0 or omitted disables context compression |
| `providers[].models[].max_tokens` | Model max output tokens (required) |
| `log.level` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `log.truncation` | Max chars per log entry, 0 to disable (default: 500) |
| `max_iterations` | Maximum tool-calling iterations per turn (default: 20) |
| `tool_output_limit` | Max characters per tool response before truncation (default: 50000) |
| `stream` | Enable token-by-token streaming to terminal (default: false) |
| `heartbeat_idle_seconds` | Seconds of user idle before heartbeat message; 0 to disable (default: 0) |
| `env_vars` | Environment variables to inject at startup |
| `memory_search.embeddings_backend` | Name of the embeddings backend to use |
| `compress_threshold` | Token ratio to trigger context compression (default: 0.7) |
| `target_threshold` | Token ratio to compress down to (default: 0.5) |
| `memory_search.auto_recall` | Enable automatic memory recall before each LLM call (default: true) |
| `memory_search.auto_recall_top_k` | Number of memory entries to retrieve per auto recall (default: 1) |
| `memory_search.recall_memory_limit` | Default limit for the recall_memory tool (default: 5) |
| `memory_search.list_all_limit` | Default limit for listing all memories (default: 50) |
| `embeddings_backends` | Dict of named backends with `model_path`, `dim`, `backend`, `llama_n_ctx`, `llama_n_batch` |
| `agents_config.chat_agent` | Name of the currently active agent |
| `agents_config.agents[].provider` | Agent-specific provider (empty = fallback to `default_provider`) |
| `agents_config.agents[].model` | Agent-specific model (empty = fallback to `default_model`) |
| `agents_config.agents[].server` | `{"host": "...", "port": N}` — ServerConfig for A2A HTTP |
| `agents_config.agents[].memory_db` | Per-agent SQLite file for memory (default: `"memory.db"`); `""` or `null` disables memory |
| `providers[]` | Provider list with api_key, base_url, api type, models |

Provider `api` field: `openai-completions` | `openai-response` | `anthropic`

## Project Structure

```
qd_evolve/
  core/                    # Shared infrastructure (all agent processes import this)
    config.py              — Settings, ProviderConfig, ModelConfig, load/save
    logger.py              — Standard logging with SharedFileHandler
    prompts.py             — Jinja2 template manager (user + builtin fallback)
    providers.py           — Provider/ProviderRegistry, client creation
    memory.py              — SQLite + sqlite-vec persistent memory store
    registry.py            — ToolRegistry, tool registration, on-demand loading
    toolbox.py             — Toolbox state management (per-agent config.json support)
  agent/                   # Agent implementation
    agent.py               — AgentCore loop (openai_completion, openai_response, anthropic)
    a2a.py                 — A2A v1.0 data models (AgentCard, Task, Message, Part, TaskState)
    transport.py           — Dual transport: InprocTransport, HttpTransport, TransportRouter
    server.py              — A2A HTTP server (aiohttp JSON-RPC)
    registry.py            — AgentRegistry + Topology
    loader.py              — create_agent_core, get_agent_entry
  _templates/              — Builtin Jinja2 templates (default.j2, heartbeat.j2)
  utils/
    adk_schema.py          — Google ADK → OpenAI JSON Schema converter
    adk_output.py          — Output normalizer + handler factory
  tools/                   # System tools (bundled, not user-replaceable)
    __init__.py            — Re-export shim (ToolRegistry → qd_evolve.core.registry)
    tool_loader.py         — load_func
    skill_loader.py        — load_skill
    cli_loader.py          — load_cli
    recall_memory.py       — recall_memory
    install_func.py        — install_func + hot-load
    register_func.py       — register_func (staging → tools/func/)
    install_mcp.py         — install_mcp + hot-load
    register_mcp.py        — register_mcp (staging → permanent)
    install_skill.py       — install_skill + hot-load from GitHub
    register_skill.py      — register_skill (staging → permanent)
    a2a.py                 — delegate_to, send_task, get_task, cancel_task
    staging.py             — staging directory paths and cleanup
  skills.py                — SkillRegistry, SKILL.md discovery
  cli_tools.py             — CLIRegistry, YAML-based CLI tool definitions
  cli.py                   — typer CLI with slash commands, toolbox
  toolbox_tui.py           — Textual TUI for interactive tool management
```

tools/                       # Project root tool directories
  func/                      — Func tools (add/delete .py to add/remove tools)
    run_shell.py             — run_shell
    run_python.py            — run_python
    file_rw.py               — read_file, write_file, list_directory
    fetch.py                 — fetch (httpx)
    search.py                — serper_search, serper_scrape
  mcp/                      — MCP server configs (*.json)
  cli/                      — CLI tool definitions (*.yaml)
  skills/                   — SKILL.md skills
  bridge/                   — Bridge protocol modules
    __init__.py             — BridgeManager (discover/connect/reload/list_all)
    _mcp.py                 — MCP bridge (external subprocess)
    _oat.py                 — OAT bridge (in-process boat + coat)
    oat.json                — OAT bridge config
```

## Tech Stack

| Concern | Library |
|---------|---------|
| LLM API | anthropic + openai |
| A2A Protocol | aiohttp (JSON-RPC + SSE) |
| Config | JSON + pydantic |
| Templates | Jinja2 |
| CLI | typer + rich + prompt-toolkit + textual |
| Logging | standard library (logging) |
| HTTP | httpx |
| Search | serper-toolkit |
| MCP | mcp (Model Context Protocol) |
| Bridge | qd-evolve Bridge Protocol |
| OAT | basic-open-agent-tools + coding-open-agent-tools |