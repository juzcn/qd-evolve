# QD-Evolve — AI Agent Project

## Principles

- **Don't reinvent the wheel.** Use battle-tested libraries: anthropic/openai SDK, pydantic, rich, typer, loguru, sqlite-vec.
- **Composition over inheritance.** Tools are callables registered in a registry, not a class hierarchy.
- **Configuration is data.** All config via `qd-evolve.json`. No CLI config commands, no .env files. Edit the file directly.
- **Multi-provider, multi-model.** Each provider has an api_key, base_url, api type, and multiple models with full metadata.
- **Three API types supported.** `openai-completions`, `openai-response`, `anthropic`. Set at provider level via `api` field.
- **Logging is structured.** loguru with file rotation. No print statements for debugging.
- **CLI is minimal.** Just `qd-evolve` to start chat, with `--template`, `--provider`, `--model` options.
- **Agent loop is simple and explicit.** Call API → check stop_reason → execute tools → append results → repeat.
- **Type everything.** Python 3.13 with full type annotations. pydantic models for all data boundaries.

## Design Decisions

- **Tools are auto-discovered.** Builtin tools from `qd_evolve/tools/`, MCP tools from `tools/mcp/*.json`. No manual registration.
- **Skills are non-callable.** SKILL.md files injected into system prompt — the LLM reads instructions and uses callable tools to execute.
- **MCP tools prefixed.** Registered as `{server_name}__{tool_name}` to avoid naming collisions.
- **Templates are Jinja2.** User templates in `templates/` override builtin fallbacks in `qd_evolve/_templates/`.
- **Per-turn token stats.** Track input/output tokens and context window usage each turn.
- **Config is read once at startup.** No runtime config changes — edit `qd-evolve.json` and restart.
- **File paths relative to CWD.** Tools never hardcode paths; everything resolves against current working directory.
- **Don't auto push.** Commit is fine, but never push to remote unless the user explicitly asks.
