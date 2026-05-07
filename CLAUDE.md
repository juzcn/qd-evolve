# QD-Evolve — AI Agent Project

## Principles

- **Don't reinvent the wheel.** Use battle-tested libraries: anthropic/openai SDK, pydantic, rich, typer, loguru, sqlite-vec.
- **Composition over inheritance.** Tools are callables registered in a registry, not a class hierarchy.
- **Configuration is data.** All config via `qd-evolve.json` (JSON). No CLI config commands, no .env files. Edit the file directly.
- **Multi-provider, multi-model.** Each provider has an api_key, base_url, api type, and multiple models with full metadata.
- **Three API types supported.** `openai-completions` (Chat Completions), `openai-response` (Responses API), `anthropic` (Messages API). Set at provider level via `api` field.
- **Full model metadata.** Each model carries: id, name, reasoning, input types, cost (input/output/cache_read/cache_write), context_window, max_tokens.
- **Logging is structured.** loguru with file rotation. No print statements for debugging.
- **CLI is minimal.** Just `qd-evolve` to start chat, with `--template`, `--provider`, `--model` options. `--help` for usage.
- **Agent loop is simple and explicit.** Call API → check stop_reason → execute tools → append results → repeat. No hidden magic.
- **Type everything.** Python 3.13 with full type annotations. pydantic models for all data boundaries.

## Tech Stack

| Concern | Library |
|---------|---------|
| LLM API | anthropic + openai |
| Config | JSON (stdlib) + pydantic |
| Templates | JSON (stdlib) + pydantic |
| CLI | typer + rich |
| Logging | loguru |
| Vector Store | sqlite-vec |
| Validation | pydantic |

## Project Structure

```
qd_evolve/
  __init__.py
  config.py        — JSON config, Settings/ProviderConfig/ModelConfig/ModelCost, load_json/save_json
  logger.py        — loguru setup
  prompts.py       — prompt template management (JSON store, shared load_json/save_json)
  providers.py     — Provider/ProviderRegistry, client creation, model lookup
  tools/
    __init__.py    — ToolRegistry, tool registration
    shell.py       — shell command execution
    file_rw.py     — file read/write/list
  agent.py         — agent loop supporting openai_completion, openai_response, anthropic
  vector.py        — VectorStore with sqlite-vec + embeddings
  cli.py           — minimal typer CLI
main.py            — thin launcher
templates/         — JSON prompt templates
qd-evolve.json     — configuration (not committed)
```

## Configuration Format (qd-evolve.json)

```json
{
  "default_provider": "baiduqianfancodingplan",
  "default_model": "qianfan-code-latest",
  "log_level": "INFO",
  "default_system_prompt": "You are a helpful AI assistant with access to tools.",
  "embedding_model": "text-embedding-3-small",
  "embedding_dimensions": 256,
  "db_path": "qd_evolve.db",
  "providers": [
    {
      "name": "baiduqianfancodingplan",
      "api_key": "sk-xxx",
      "base_url": "https://qianfan.baidubce.com/v2/coding",
      "api": "openai-completions",
      "models": [
        {
          "id": "qianfan-code-latest",
          "name": "qianfan-code-latest",
          "reasoning": false,
          "input": ["text"],
          "cost": {
            "input": 0.0025,
            "output": 0.01,
            "cache_read": 0,
            "cache_write": 0
          },
          "context_window": 98304,
          "max_tokens": 65536
        }
      ]
    }
  ]
}
```

## Conventions

- Source lives in `qd_evolve/` package, `main.py` is just a launcher.
- Tools are functions registered via `registry.register()` that return string output.
- Agent loop owns the message list; tools never touch it.
- Config is read once at startup from `qd-evolve.json`. No CLI config commands — edit the file.
- Each model declares full metadata: id, name, reasoning, input types, cost, context_window, max_tokens, api_type.
- Provider-level `api` field determines which SDK/protocol to use for all its models.
- Prompt templates are JSON files in `templates/`.
- All file paths in tools are resolved relative to CWD, never hardcoded.
