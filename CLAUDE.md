# QD-Evolve — AI Agent Project

## Principles

- **Don't reinvent the wheel.** Use battle-tested libraries: anthropic/openai SDK, pydantic, rich, typer, loguru.
- **Composition over inheritance.** Tools are callables registered in a registry, not a class hierarchy.
- **Configuration is data.** All config via `qd-evolve.json`. No CLI config commands, no .env files. Edit the file directly.
- **Multi-provider, multi-model.** Each provider has an api_key, base_url, api type, and multiple models with full metadata.
- **Three API types supported.** `openai-completions`, `openai-response`, `anthropic`. Set at provider level via `api` field.
- **Logging is structured.** loguru with file rotation. No print statements for debugging.
- **CLI is minimal.** Just `qd-evolve` to start chat. No CLI options for provider/model — edit `qd-evolve.json` or use `/models` at runtime.
- **Agent loop is simple and explicit.** Call API → check stop_reason → execute tools → append results → repeat.
- **Type everything.** Python 3.13 with full type annotations. pydantic models for all data boundaries.

## Design Decisions

- **Tools are auto-discovered.** Builtin tools from `qd_evolve/tools/`, MCP tools from `tools/mcp/*.json`, CLI tools from `tools/cli/*.yaml`. No manual registration.
- **On-demand tool loading.** Tools start with name+description only. The LLM calls `load_tool_detail` to get the full schema, then the tool is activated for subsequent turns. This reduces prompt size.
- **Dynamic system prompt sections.** The system prompt has three dynamic injection sections: "Loaded SKILL.md", "Loaded Cli Tools help", and "Loaded Tools Schemas". When `load_skill_detail`, `load_cli_detail`, or `load_tool_detail` is called, the returned content is injected into the corresponding section on the next iteration, so the LLM has the full context without re-fetching.
- **Preloaded tools.** Configured via `preload_tools` in `qd-evolve.json`. By default: `load_tool_detail`, `load_skill_detail`, `load_cli_detail`, and `recall_memory` — no need to load their schema first.
- **Skills are non-callable.** SKILL.md files injected into system prompt as summaries — the LLM calls `load_skill_detail` to get full instructions and uses callable tools to execute.
- **Preloaded skills.** Configured via `preload_skills` in `qd-evolve.json`. Preloaded skills have their full content injected into the system prompt instead of just a summary line.
- **CLI tools are non-callable definitions.** YAML files in `tools/cli/` describe CLI commands (name, command, help_summary, examples). The LLM calls `load_cli_detail` to get usage info, then executes via `run_shell`. Preloaded CLI tools are configured via `preload_cli`.
- **MCP tools prefixed.** Registered as `{server_name}__{tool_name}` to avoid naming collisions.
- **Templates are Jinja2.** User templates in `templates/` override builtin fallbacks in `qd_evolve/_templates/`. System prompt is rendered from template and passed to Agent at startup.
- **Per-turn and cumulative token stats.** Track input/output tokens and context window usage each turn, plus running session total.
- **Persistent memory.** SQLite + sqlite-vec for cross-session memory storage. Each user+assistant message pair is auto-saved with BGE-M3 embedding. Supports semantic + keyword hybrid search, time-range filtering, and session exclusion.
- **Context compression.** When input tokens exceed `compress_threshold` (default 70%) of context window, old Q/A pairs are removed from the message list until tokens drop below `target_threshold` (default 50%). A new memory session is created after compression so recall_memory can distinguish pre/post compression context.
- **Auto recall.** Before each LLM call, user input is used as query to automatically retrieve relevant past conversations from MemoryStore. Results are injected into a dedicated memory section in the system prompt, with deduplication via `RecalledMemoryRegistry` (keyed by memory id). All recall queries exclude the current session. Configurable via `auto_recall` (on/off) and `auto_recall_top_k`.
- **Embedder selection by config.** `embeddings_backends` section in `qd-evolve.json` defines named backends with `backend` field (`sentence-transformers` or `llama-cpp-python`). `memory_search.default_embeddings_backend` selects which to use. No file-suffix auto-detection.
- **Env vars from config.** `env_vars` in `qd-evolve.json` are injected into `os.environ` at startup, so tools can access API keys without .env files.
- **Config is read once at startup.** No runtime config changes — edit `qd-evolve.json` and restart. However, registries (skills, CLI tools, MCP) are reloaded after each conversation turn to pick up new files without restart.
- **File paths relative to CWD.** Tools never hardcode paths; everything resolves against current working directory.
- **Shell encoding.** `run_shell` uses `locale.getpreferredencoding()` to decode subprocess output, handling Windows GBK/cp936 correctly.
- **Don't auto push.** Commit is fine, but never push to remote unless the user explicitly asks.
