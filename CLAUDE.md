# QD-Evolve — AI Agent Project

## Principles

- **Don't reinvent the wheel.** Use battle-tested libraries: anthropic/openai SDK, pydantic, rich, typer, loguru.
- **Composition over inheritance.** Tools are callables registered in a registry, not a class hierarchy.
- **Configuration is data.** All config via `qd-evolve.json`. No CLI config commands, no .env files. Edit the file directly.
- **Multi-provider, multi-model.** Each provider has an api_key, base_url, api type, and multiple models with full metadata.
- **Three API types supported.** `openai-completions`, `openai-response`, `anthropic`. Set at provider level via `api` field.
- **Logging is structured.** loguru with file rotation. No print statements for debugging.
- **CLI is minimal.** Just `qd-evolve` to start chat, with `--provider`, `--model` options.
- **Agent loop is simple and explicit.** Call API → check stop_reason → execute tools → append results → repeat.
- **Type everything.** Python 3.13 with full type annotations. pydantic models for all data boundaries.

## Design Decisions

- **Tools are auto-discovered.** Builtin tools from `qd_evolve/tools/`, MCP tools from `tools/mcp/*.json`. No manual registration.
- **On-demand tool loading.** Tools start with name+description only. The LLM calls `load_tool_detail` to get the full schema, then the tool is activated for subsequent turns. This reduces prompt size.
- **Always-active tools.** `load_tool_detail`, `load_skill_detail`, and `recall_memory` are always active — no need to load their schema first.
- **Skills are non-callable.** SKILL.md files injected into system prompt as summaries — the LLM calls `load_skill_detail` to get full instructions and uses callable tools to execute.
- **MCP tools prefixed.** Registered as `{server_name}__{tool_name}` to avoid naming collisions.
- **Templates are Jinja2.** User templates in `templates/` override builtin fallbacks in `qd_evolve/_templates/`.
- **Per-turn and cumulative token stats.** Track input/output tokens and context window usage each turn, plus running session total.
- **Persistent memory.** SQLite + sqlite-vec for cross-session memory storage. Each user+assistant message pair is auto-saved with BGE-M3 embedding. Supports semantic + keyword hybrid search, time-range filtering, and session exclusion.
- **Context compression.** When input tokens exceed `compress_threshold` (default 70%) of context window, old Q/A pairs are removed from the message list until tokens drop below `target_threshold` (default 50%). A new memory session is created after compression so recall_memory can distinguish pre/post compression context.
- **Auto recall.** Before each LLM call, user input is used as query to automatically retrieve relevant past conversations from MemoryStore. Results are injected into a dedicated memory section in the system prompt, with deduplication via `RecalledMemoryRegistry` (keyed by memory id). All recall queries exclude the current session. Configurable via `auto_recall` (on/off) and `auto_recall_top_k`.
- **Embedder auto-detection.** `.gguf` files use llama-cpp-python, other paths use sentence-transformers. `embedding_dim` configured in `qd-evolve.json`.
- **Env vars from config.** `env_vars` in `qd-evolve.json` are injected into `os.environ` at startup, so tools can access API keys without .env files.
- **Config is read once at startup.** No runtime config changes — edit `qd-evolve.json` and restart.
- **File paths relative to CWD.** Tools never hardcode paths; everything resolves against current working directory.
- **Don't auto push.** Commit is fine, but never push to remote unless the user explicitly asks.
