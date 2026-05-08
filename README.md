# QD-Evolve

Multi-provider AI agent with tool use, skills, MCP integration, and CLI interface.

## Features

- **Multi-provider, multi-model** — OpenAI Chat Completions, OpenAI Responses API, Anthropic Messages API
- **Tool system** — Builtin tools auto-discovered from `qd_evolve/tools/`, MCP tools from `tools/mcp/*.json`
- **Skill system** — Non-callable prompt instructions from `skills/*/SKILL.md`, injected into system prompt
- **Jinja2 prompt templates** — `.j2` files in `templates/`, with builtin fallbacks
- **Vector store** — sqlite-vec + sentence-transformers for semantic search
- **Per-turn token stats** — Input/output token tracking with context window usage
- **Structured logging** — loguru with file rotation

## Quick Start

```bash
pip install -e .
```

Create `qd-evolve.json` in the project root:

```json
{
  "default_provider": "my-provider",
  "default_model": "my-model",
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
| `/loglevel` | Set log level (e.g. `/loglevel DEBUG`) |

## Builtin Tools

| Tool | Description |
|------|-------------|
| `run_shell` | Execute shell commands |
| `read_file` | Read file contents |
| `write_file` | Write content to a file |
| `list_directory` | List directory contents |
| `fetch` | Fetch URL content (GET/POST) |
| `serper_search` | Web search via Serper API |
| `serper_scrape` | Scrape webpage content |

## MCP Integration

Place JSON config files in `tools/mcp/`. Supported formats:

```json
// Format 1: Claude Desktop style
{ "mcpServers": { "name": { "command": "...", "args": [...] } } }

// Format 2: Bare server config (filename used as name)
{ "command": "...", "args": [...] }
```

MCP tools are registered with prefix `{server_name}__{tool_name}`.

## Skills

Skills are directories under `skills/` containing a `SKILL.md` file. They are **not** tool calls — the LLM reads the instructions and uses other callable tools to execute them.

```
skills/
  my-skill/
    SKILL.md       # instructions injected into system prompt
    _meta.json     # optional: {"slug": "my-skill", "version": "1.0.0"}
```

## Prompt Templates

Jinja2 templates in `templates/` (user) or `qd_evolve/_templates/` (builtin fallback). The default template receives `{{ skills }}` for skill injection.

## Configuration

All config via `qd-evolve.json`. Key fields:

| Field | Description |
|-------|-------------|
| `default_provider` | Default provider name |
| `default_model` | Default model ID |
| `log_level` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `default_system_prompt` | Fallback system prompt |
| `skills_dir` | Skills directory path (default: `skills`) |
| `mcp_servers` | Inline MCP server configs |
| `providers[]` | Provider list with api_key, base_url, api type, models |

Provider `api` field: `openai-completions` | `openai-response` | `anthropic`

## Project Structure

```
qd_evolve/
  config.py          — Settings, ProviderConfig, ModelConfig, load/save
  logger.py          — loguru setup with file rotation
  prompts.py         — Jinja2 template manager
  providers.py       — Provider/ProviderRegistry, client creation
  skills.py          — SkillRegistry, SKILL.md discovery
  tools/
    __init__.py      — ToolRegistry, tool registration
    shell.py         — run_shell
    file_rw.py       — read_file, write_file, list_directory
    fetch.py         — fetch (httpx)
    search.py        — serper_search, serper_scrape
    find_tools.py    — find_and_load_tools (semantic)
    _mcp_client.py   — MCP server discovery and bridge
  agent.py           — Agent loop (openai_completion, openai_response, anthropic)
  vector.py          — VectorStore with sqlite-vec
  cli.py             — typer CLI with slash commands
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
| Vector Store | sqlite-vec + sentence-transformers |
| HTTP | httpx |
| Search | serper-toolkit |
