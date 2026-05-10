# QD-Evolve

Multi-provider AI agent with tool use, skills, MCP integration, and CLI interface.

## Features

- **Multi-provider, multi-model** — OpenAI Chat Completions, OpenAI Responses API, Anthropic Messages API
- **Tool system** — Builtin tools auto-discovered from `qd_evolve/tools/`, MCP tools from `tools/mcp/*.json`
- **CLI tool registry** — YAML-based CLI tool definitions in `tools/cli/*.yaml`, loaded on demand via `load_cli_detail`
- **On-demand tool loading** — Tools start with name+description only; full schema loaded via `load_tool_detail` when the LLM calls it, reducing prompt size
- **Skill system** — Non-callable prompt instructions from `skills/*/SKILL.md`, injected into system prompt; full content loaded via `load_skill_detail` on demand
- **MCP integration** — Connect to external MCP servers via stdio; tools registered with `{server_name}__{tool_name}` prefix; incremental reload (only start new servers, disconnect removed ones)
- **Registry reload** — Skills, CLI tools, and MCP servers are reloaded after each conversation turn, so new definitions take effect without restart
- **Jinja2 prompt templates** — `.j2` files in `templates/`, with builtin fallbacks in `qd_evolve/_templates/`
- **Per-turn token stats** — Input/output token tracking with context window usage percentage
- **Cumulative token tracking** — Running total of tokens used across the session
- **Persistent memory** — SQLite + sqlite-vec for cross-session memory with semantic (BGE-M3) + keyword hybrid search
- **Context compression** — Automatic Q/A pair removal when tokens exceed threshold, with configurable compress/target ratios and session switching
- **Auto recall** — Automatically retrieves relevant past conversations from memory before each LLM call, injected into system prompt with deduplication via RecalledMemoryRegistry
- **Memory recall tool** — `recall_memory` tool for LLM to search past conversations by query, keywords, and time range
- **Dual embedder support** — sentence-transformers or llama-cpp-python, selected by config `embeddings_backends.*.backend`
- **Env vars injection** — Define `env_vars` in config to inject API keys into environment at startup
- **Preload tool/skill configuration** — `preload_tools`, `preload_skills`, `preload_cli` in `qd-evolve.json` control which items are preloaded (no on-demand loading needed)
- **Structured logging** — loguru with file rotation to `logs/` directory
- **Rich CLI** — Interactive prompt with spinner status, slash commands, and tab completion

## Quick Start

```bash
pip install -e .
```

Create `qd-evolve.json` in the project root (see `qd-evolve.json.example`):

```json
{
  "default_provider": "my-provider",
  "default_model": "my-model",
  "log_level": "INFO",
  "skills_dir": "tools/skills",
  "cli_tools_dir": "tools/cli",
  "preload_skills": ["cli-register"],
  "preload_tools": ["load_tool_detail", "load_skill_detail", "load_cli_detail", "recall_memory"],
  "preload_cli": [],
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
    "auto_recall_top_k": 5
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
qd-evolve    # start chat with defaults
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

## MCP Integration

Place JSON config files in `tools/mcp/`. Supported formats:

```json
// Format 1: Claude Desktop style
{ "mcpServers": { "name": { "command": "...", "args": [...] } } }

// Format 2: Nested mcp.servers style
{ "mcp": { "servers": { "name": { "command": "...", "args": [...] } } } }

// Format 3: Bare server config (filename used as name)
{ "command": "...", "args": [...] }
```

MCP tools are registered with prefix `{server_name}__{tool_name}`.

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

Skills are directories under `tools/skills/` containing a `SKILL.md` file. They are **not** tool calls — the LLM reads the summary in the system prompt and uses `load_skill_detail` to get full instructions when needed. Preloaded skills (configured in `preload_skills`) have their full content injected into the system prompt.

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
| `skills` | Formatted skill summary list |
| `cli_tools` | Formatted CLI tools summary list |
| `tools_summary` | Formatted tool name + description list |
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
| `log_level` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `skills_dir` | Skills directory path (default: `skills`) |
| `cli_tools_dir` | CLI tools directory path (default: `tools/cli`) |
| `preload_skills` | List of skill names to always inject into system prompt |
| `preload_tools` | List of tool names that are preloaded (no on-demand loading) |
| `preload_cli` | List of CLI tool names to always inject into system prompt |
| `env_vars` | Environment variables to inject at startup |
| `memory_search.default_embeddings_backend` | Name of the embeddings backend to use |
| `memory_search.compress_threshold` | Token ratio to trigger context compression (default: 0.7) |
| `memory_search.target_threshold` | Token ratio to compress down to (default: 0.5) |
| `memory_search.auto_recall` | Enable automatic memory recall before each LLM call (default: true) |
| `memory_search.auto_recall_top_k` | Number of memory entries to retrieve per auto recall (default: 5) |
| `embeddings_backends` | Dict of named backends with `model_path`, `dim`, `backend`, `llama_n_ctx`, `llama_n_batch` |
| `providers[]` | Provider list with api_key, base_url, api type, models |

Provider `api` field: `openai-completions` | `openai-response` | `anthropic`

## Project Structure

```
qd_evolve/
  config.py          — Settings, ProviderConfig, ModelConfig, load/save
  logger.py          — loguru setup with file rotation
  prompts.py         — Jinja2 template manager (user + builtin fallback)
  providers.py       — Provider/ProviderRegistry, client creation
  skills.py          — SkillRegistry, SKILL.md discovery, active skill injection
  cli_tools.py       — CLIRegistry, YAML-based CLI tool definitions
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
    _mcp_client.py   — MCP server discovery, bridge, incremental reload
  agent.py           — Agent loop (openai_completion, openai_response, anthropic)
  cli.py             — typer CLI with slash commands and Rich UI
  _templates/        — builtin Jinja2 templates
main.py              — thin launcher
```

## Tech Stack

| Concern | Library |
|---------|---------|
| LLM API | anthropic + openai |
| Config | JSON + pydantic |
| Templates | Jinja2 |
| CLI | typer + rich + prompt-toolkit |
| Logging | loguru |
| HTTP | httpx |
| Search | serper-toolkit |
| MCP | mcp (Model Context Protocol) |
