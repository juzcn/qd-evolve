# QD-Evolve

Multi-provider AI agent with tool use, skills, MCP integration, and CLI interface.

## Features

- **Multi-provider, multi-model** — OpenAI Chat Completions, OpenAI Responses API, Anthropic Messages API
- **Tool system** — Builtin tools + MCP (external processes) + OAT bridge (in-process boat + coat)
- **Bridge protocol** — qd-evolve's own protocol for tool source integration; new bridge types never touch `cli.py`
- **OAT bridge** — basic-open-agent-tools (boat) + coding-open-agent-tools (coat) loaded in-process, no subprocess latency
- **Toolbox TUI** — `qd-evolve toolbox` (Textual) for interactive enable/disable/preload management across all tool types
- **Toolbox config** — `toolbox.json` manages which tools/skills/CLI/bridges/MCP are enabled, disabled, or preloaded
- **On-demand tool loading** — Tools start with name+description only; full schema loaded via `load_tool_detail`
- **Skill system** — SKILL.md files from `tools/skills/`, injected into system prompt; `load_skill_detail` for full content
- **CLI tools** — YAML definitions in `tools/cli/`, loaded via `load_cli_detail`
- **MCP integration** — Stdio-based MCP servers; clean tool names (no prefix); disabled servers skipped entirely
- **Jinja2 prompt templates** — `.j2` files in `templates/`, with builtin fallbacks
- **Persistent memory** — SQLite + sqlite-vec with BGE-M3 semantic + keyword hybrid search
- **Context compression** — Auto Q/A removal when tokens exceed threshold
- **Auto recall** — Relevant past conversations auto-injected into system prompt
- **Per-turn token stats** — Input/output tracking with context window usage
- **Dual embedder support** — sentence-transformers or llama-cpp-python
- **Structured logging** — Standard library logging with SharedFileHandler for real-time log visibility
- **Rich CLI** — Interactive prompt with tab completion, spinner, and slash commands

## Quick Start

```bash
pip install -e .
```

Create `qd-evolve.json` in the project root (see `qd-evolve.json.example`):

```json
{
  "default_provider": "my-provider",
  "default_model": "my-model",
  "log": {
    "level": "INFO"
  },
  "env_vars": {
    "SERPER_API_KEY": "sk-xxx",
    "BAIDU_API_KEY": "sk-xxx"
  },
  "providers": [
    {
      "name": "my-provider",
      "api_key": "sk-xxx",
      "base_url": "https://api.example.com/v1",
      "api": "openai-completions",
      "models": [
        {
          "name": "my-model",
          "reasoning": false,
          "input": ["text"],
          "cost": { "input": 0.0025, "output": 0.01, "cache_read": 0, "cache_write": 0 },
          "context_window": 128000,
          "max_tokens": 4096
        }
      ]
    }
  ],
  "memory_search": {
    "default_embeddings_backend": "default",
    "compress_threshold": 0.7,
    "target_threshold": 0.5,
    "auto_recall": true,
    "auto_recall_top_k": 1,
    "recall_memory_limit": 5,
    "search_by_time_limit": 20,
    "list_all_limit": 50
  },
  "embeddings_backends": {
    "default": {
      "model_path": "BAAI/bge-m3",
      "dim": 1024,
      "backend": "sentence-transformers"
    }
  }
}
```

Run:

```bash
qd-evolve                  # start chat with defaults
qd-evolve toolbox          # interactive tool manager (Textual TUI)
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
| `/status` | Show runtime status (loaded tools, skills, CLI) |
| `/models` | Switch model interactively |
| `/memory` | List saved memories |

## Builtin Tools

| Tool | Description |
|------|-------------|
| `run_shell` | Execute shell commands with timeout |
| `read_file` | Read file contents |
| `write_file` | Write content to a file (creates parent dirs) |
| `list_directory` | List directory contents |
| `fetch` | Fetch URL content via HTTP GET/POST |
| `serper_search` | Web search via Serper API (general/images/news) |
| `serper_scrape` | Scrape webpage content |
| `load_tool_detail` | Load full schema for a tool on demand |
| `load_skill_detail` | Load full SKILL.md content for a skill on demand |
| `load_cli_detail` | Load full definition for a CLI tool on demand |
| `recall_memory` | Search past conversations by query, keywords, and time range |

## Bridge Protocol

qd-evolve's own generic tool integration protocol. Each bridge type self-registers with `BridgeManager` via three functions: `discover` → `connect` → `disconnect`. Adding a new bridge type only requires a `_*.py` module in `tools/bridge/` — `cli.py` never changes.

### MCP Bridge (external processes)

Place JSON config files in `tools/mcp/`. Supported formats:

```json
// Format 1: Claude Desktop style
{ "mcpServers": { "name": { "command": "...", "args": [...] } } }

// Format 2: Nested mcp.servers style
{ "mcp": { "servers": { "name": { "command": "...", "args": [...] } } } }

// Format 3: Bare server config (filename used as name)
{ "command": "...", "args": [...] }
```

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

`toolbox.json` `bridge` section controls bridge enable/disable:

```json
{
  "bridge": {
    "oat:boat": "enabled",
    "mcp:github-fetcher": "disabled"
  }
}
```

## CLI Tools

CLI tools are YAML definitions in `tools/cli/` that describe how to use command-line programs. They are **not** tool calls — the LLM calls `load_cli_detail` to get usage info and examples, then executes via `run_shell`.

```yaml
# tools/cli/pandoc.yaml
name: pandoc
command: pandoc
description: "Universal document converter"
help_summary: |
  Usage: pandoc [OPTIONS] [FILES]
  Key options:
    -f/--from=FORMAT    Input format
    -t/--to=FORMAT      Output format
    -o/--output=FILE    Output file
examples:
  - "pandoc input.md -o output.pdf"
  - "pandoc input.docx -t markdown"
```

Use the `cli-register` skill to generate YAML from `--help` output automatically.

## Skills

Skills are directories under `tools/skills/` containing a `SKILL.md` file. They are **not** tool calls — the LLM reads the summary in the system prompt and uses `load_skill_detail` to get full instructions when needed. Skill state (enabled/disabled/preloaded) is managed via `toolbox.json` or `qd-evolve toolbox`.

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
| `self-improvement` | Capture learnings and corrections for continuous improvement |

## Prompt Templates

Jinja2 templates in `templates/` (user) or `qd_evolve/_templates/` (builtin fallback). The default template receives these variables:

| Variable | Description |
|----------|-------------|
| `unloaded_skills` | Skill summaries not yet loaded (use `load_skill_detail`) |
| `unloaded_cli` | CLI tool summaries not yet loaded (use `load_cli_detail`) |
| `unloaded_tools` | Tool name + description list not yet loaded (use `load_tool_detail`) |
| `loaded_skills` | Full content of preloaded or previously loaded skills |
| `loaded_cli` | Full content of preloaded or previously loaded CLI tools |
| `memory_section` | Auto-recalled relevant past conversations (if any) |
| `os_name` | Platform name (e.g. Windows, Linux) |
| `python_cmd` | Detected python command |
| `cwd` | Current working directory |
| `skills_dir` | Skills directory path |

## Configuration

All config via `qd-evolve.json`. Key fields:

| Field | Description |
|-------|-------------|
| `default_provider` | Default provider name |
| `default_model` | Default model name |
| `log.level` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `log.truncation` | Max chars per log entry, 0 to disable (default: 500) |
| `max_iterations` | Maximum tool-calling iterations per turn (default: 20) |
| `tool_output_limit` | Max characters per tool response before truncation (default: 50000) |
| `env_vars` | Environment variables to inject at startup |
| `memory_search.default_embeddings_backend` | Name of the embeddings backend to use |
| `memory_search.compress_threshold` | Token ratio to trigger context compression (default: 0.7) |
| `memory_search.target_threshold` | Token ratio to compress down to (default: 0.5) |
| `memory_search.auto_recall` | Enable automatic memory recall before each LLM call (default: true) |
| `memory_search.auto_recall_top_k` | Number of memory entries to retrieve per auto recall (default: 1) |
| `memory_search.recall_memory_limit` | Default limit for the recall_memory tool (default: 5) |
| `memory_search.search_by_time_limit` | Default limit for time-based memory search (default: 20) |
| `memory_search.list_all_limit` | Default limit for listing all memories (default: 50) |
| `embeddings_backends` | Dict of named backends with `model_path`, `dim`, `backend`, `llama_n_ctx`, `llama_n_batch` |
| `providers[]` | Provider list with api_key, base_url, api type, models |

Provider `api` field: `openai-completions` | `openai-response` | `anthropic`

## Project Structure

```
qd_evolve/
  config.py          — Settings, ProviderConfig, ModelConfig, load/save
  logger.py          — Standard logging with SharedFileHandler
  prompts.py         — Jinja2 template manager (user + builtin fallback)
  providers.py       — Provider/ProviderRegistry, client creation
  memory.py          — SQLite + sqlite-vec persistent memory store
  skills.py          — SkillRegistry, SKILL.md discovery, active skill injection
  cli_tools.py       — CLIRegistry, YAML-based CLI tool definitions
  utils/
    adk_schema.py    — Google ADK → OpenAI JSON Schema converter
    adk_output.py    — Output normalizer + handler factory
  tools/
    __init__.py      — ToolRegistry, tool registration, on-demand loading
    shell.py         — run_shell (auto-detects system encoding)
    file_rw.py       — read_file, write_file, list_directory
    fetch.py         — fetch (httpx)
    search.py        — serper_search, serper_scrape
    tool_loader.py   — load_tool_detail (on-demand schema loading)
    skill_loader.py  — load_skill_detail (on-demand skill content)
    cli_loader.py    — load_cli_detail (on-demand CLI tool info)
    recall_memory.py — recall_memory (semantic + keyword search)
  agent.py           — Agent loop (openai_completion, openai_response, anthropic)
  cli.py             — typer CLI with slash commands, toolbox subcommand
  toolbox.py         — Toolbox state management (toolbox.json)
  toolbox_tui.py     — Textual TUI for interactive tool management
  _templates/        — builtin Jinja2 templates
tools/
  mcp/               — MCP server configs (*.json)
  cli/               — CLI tool definitions (*.yaml)
  skills/            — SKILL.md skills
  bridge/            — Bridge protocol modules
    __init__.py      — BridgeManager (discover/connect/reload/list_all)
    _mcp.py          — MCP bridge (external subprocess)
    _oat.py          — OAT bridge (in-process boat + coat)
    oat.json         — OAT bridge config
main.py              — thin launcher
```

## Tech Stack

| Concern | Library |
|---------|---------|
| LLM API | anthropic + openai |
| Config | JSON + pydantic |
| Templates | Jinja2 |
| CLI | typer + rich + prompt-toolkit + textual |
| Logging | standard library (logging) |
| HTTP | httpx |
| Search | serper-toolkit |
| MCP | mcp (Model Context Protocol) |
| Bridge | qd-evolve Bridge Protocol |
| OAT | basic-open-agent-tools + coding-open-agent-tools |
