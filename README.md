# QD-Evolve

Multi-provider AI agent with tool use, skills, MCP integration, and CLI interface.

## Features

- **Multi-provider, multi-model** — OpenAI Chat Completions, OpenAI Responses API, Anthropic Messages API
- **Tool system** — Builtin tools auto-discovered from `qd_evolve/tools/`, MCP tools from `tools/mcp/*.json`
- **On-demand tool loading** — Tools start with name+description only; full schema loaded via `load_tool_detail` when the LLM calls it, reducing prompt size
- **Skill system** — Non-callable prompt instructions from `skills/*/SKILL.md`, injected into system prompt; full content loaded via `load_skill_detail` on demand
- **MCP integration** — Connect to external MCP servers via stdio; tools registered with `{server_name}__{tool_name}` prefix
- **Jinja2 prompt templates** — `.j2` files in `templates/`, with builtin fallbacks in `qd_evolve/_templates/`
- **Per-turn token stats** — Input/output token tracking with context window usage percentage
- **Cumulative token tracking** — Running total of tokens used across the session
- **Persistent memory** — SQLite + sqlite-vec for cross-session memory with semantic (BGE-M3) + keyword hybrid search
- **Memory recall tool** — `recall_memory` tool for LLM to search past conversations by query, keywords, and time range
- **Dual embedder support** — sentence-transformers or llama-cpp-python (auto-detected by `.gguf` extension)
- **Env vars injection** — Define `env_vars` in config to inject API keys into environment at startup
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
  "default_system_prompt": "You are a helpful AI assistant with access to tools.",
  "skills_dir": "tools/skills",
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
          "id": "my-model",
          "name": "My Model",
          "reasoning": false,
          "input": ["text"],
          "cost": { "input": 0.0025, "output": 0.01, "cache_read": 0, "cache_write": 0 },
          "context_window": 128000,
          "max_tokens": 4096
        }
      ]
    }
  ]
}
```

Run:

```bash
qd-evolve                    # start chat with defaults
qd-evolve -p provider-name   # override provider
qd-evolve -m model-name      # override model
```

## Chat Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/quit` | Quit the session |
| `/reset` | Reset conversation history |
| `/tools` | List available tools |
| `/skills` | List loaded skills |
| `/config` | Show current configuration |
| `/models` | Switch model interactively |
| `/memory` | List saved memories |
| `/loglevel` | Set log level (e.g. `/loglevel DEBUG`) |

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

## Skills

Skills are directories under `tools/skills/` containing a `SKILL.md` file. They are **not** tool calls — the LLM reads the summary in the system prompt and uses `load_skill_detail` to get full instructions when needed.

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
| `find-skills` | Discover and install new agent skills |
| `self-improvement` | Capture learnings and corrections for continuous improvement |

## Prompt Templates

Jinja2 templates in `templates/` (user) or `qd_evolve/_templates/` (builtin fallback). The default template receives these variables:

| Variable | Description |
|----------|-------------|
| `skills` | Formatted skill summary list |
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
| `default_model` | Default model ID |
| `log_level` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `default_system_prompt` | Fallback system prompt |
| `skills_dir` | Skills directory path (default: `skills`) |
| `env_vars` | Environment variables to inject at startup |
| `providers[]` | Provider list with api_key, base_url, api type, models |

Provider `api` field: `openai-completions` | `openai-response` | `anthropic`

## Project Structure

```
qd_evolve/
  config.py          — Settings, ProviderConfig, ModelConfig, load/save
  logger.py          — loguru setup with file rotation
  prompts.py         — Jinja2 template manager (user + builtin fallback)
  providers.py       — Provider/ProviderRegistry, client creation
  skills.py          — SkillRegistry, SKILL.md discovery
  tools/
    __init__.py      — ToolRegistry, tool registration, on-demand loading
    shell.py         — run_shell
    file_rw.py       — read_file, write_file, list_directory
    fetch.py         — fetch (httpx)
    search.py        — serper_search, serper_scrape
    tool_loader.py   — load_tool_detail (on-demand schema loading)
    skill_loader.py  — load_skill_detail (on-demand skill content)
    _mcp_client.py   — MCP server discovery and bridge
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
