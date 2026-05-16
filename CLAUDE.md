# QD-Evolve — AI Agent Project

## Principles

- **Don't reinvent the wheel.** Use battle-tested libraries: anthropic/openai SDK, pydantic, rich, typer, standard logging.
- **Composition over inheritance.** Tools are callables registered in a registry, not a class hierarchy.
- **Configuration is data.** All config via `config.json`. No CLI config commands, no .env files. Edit the file directly.
- **Multi-provider, multi-model.** Each provider has an api_key, base_url, api type, and multiple models with full metadata.
- **Three API types supported.** `openai-completions`, `openai-response`, `anthropic`. Set at provider level via `api` field.
- **Logging is structured.** Standard library logging with custom SharedFileHandler (per-write open/flush/close) for real-time log visibility. No print statements for debugging.
- **CLI is minimal.** Just `qd-evolve` to start chat. `--replay` for automated testing, `--output` to capture. No CLI options for provider/model/agent — edit `config.json` or use `/models` or `/agents` at runtime.
- **Agent loop is simple and explicit.** Call API → check stop_reason → execute tools → append results → repeat. Capped at `max_iterations` (default 20) to prevent infinite loops.
- **Heartbeat is agent-owned.** Agent creates heartbeat coroutine that sleeps then calls LLM. CLI is purely event-driven — it just awaits and displays. Template-driven heartbeat message via `heartbeat.j2`. Configurable via `heartbeat_idle_seconds` (0 = disabled).
- **Streaming is global.** `stream` is a top-level settings field, not per-model. OpenAI-compatible providers stream tokens to the terminal.
- **Reasoning/thinking is per-model.** `reasoning: true` on a model enables reasoning_content passthrough (DeepSeek, etc.). Reasoning text is displayed in the terminal with a "Reasoning:" label.
- **Type everything.** Python 3.13 with full type annotations. pydantic models for all data boundaries.

## Design Decisions

- **Multi-agent architecture.** Each agent is a fully isolated process with independent messages, memory db, system prompt, and toolbox config. Public code (Settings, ProviderRegistry, ToolRegistry) is shared. A2A protocol for inter-agent communication with dual transport (inproc for same-machine, http for cross-machine). Agent config is centralized in `config.json` — `agents` list with `AgentEntry` (name, description, provider, model, a2a_tools, memory, server). `/agents` command switches agent at runtime (like `/models`).
- **A2A protocol (v1.0 spec).** Full implementation: AgentCard, Task lifecycle (submitted→working→completed/failed/canceled), Message/Part/Artifact, JSON-RPC over HTTP, SSE streaming. Agent discovery via `/.well-known/agent.json`.
- **Dual transport.** `InprocTransport` (asyncio.to_thread + AgentCore.run, zero latency for same-machine) and `HttpTransport` (aiohttp JSON-RPC for cross-machine). `TransportRouter` auto-selects based on topology.json. HTTP server always runs for cross-machine access, but same-machine agents bypass it.
- **Shared toolbox, per-agent config.** ToolRegistry is shared infrastructure — all tool definitions come from the same registry. But each agent has its own `agents/<name>/toolbox.json` for preload/enable/disable. If no per-agent toolbox.json exists, global `toolbox.json` is used.
- **Agent config in config.json.** `agents` list defines all agents with `AgentEntry` fields (name, description, provider, model, system_prompt_template, a2a_tools, memory, server). `default_agent` selects the startup agent. `/agents` command switches at runtime and saves to config.json. No `--agent` CLI option or `serve` command — agent selection is runtime only.
- **Topology config.** `agents/topology.json` defines agent relationships (peer/master-worker) and transport mode per agent pair. Default peer-to-peer, configurable master-worker. Cross-machine agents auto-detected as http.
- **Tools are auto-discovered.** Builtin tools from `qd_evolve/tools/`, MCP tools from `tools/mcp/*.json`, CLI tools from `tools/cli/*.yaml`, OAT tools from `tools/bridge/oat.json`. No manual registration.
- **Bridge protocol.** qd-evolve's own protocol for tool source integration. Each bridge type self-registers with `BridgeManager` (discover/connect/disconnect). `cli.py` only talks to `BridgeManager` — adding a new bridge type never touches `cli.py` or `toolbox.py`. Current bridges: `mcp` (external: stdio/SSE/StreamableHTTP/WebSocket), `oat` (in-process boat + coat). Bridge modules live in `tools/bridge/_*.py`, config files in `tools/bridge/*.json`.
- **OAT bridge (in-process).** `basic-open-agent-tools` (boat) and `coding-open-agent-tools` (coat) are loaded in-process via `tools/bridge/_oat.py`. No subprocess, no MCP serialization overhead. Wrapped functions get Google ADK → OpenAI JSON Schema conversion via `qd_evolve/utils/adk_schema.py`, output normalization via `qd_evolve/utils/adk_output.py`. Config in `tools/bridge/oat.json` — edit loadout per package without touching Settings model.
- **On-demand tool loading.** Tools start with name+description only. The LLM calls `load_func` to get the full schema, then the tool is activated for subsequent turns. This reduces prompt size.
- **Hot-loading.** `install_func/install_mcp/install_skill` register new tools in the current session without restart. Staged in `.qd-evolve/staging/`, persisted via `register_func/register_mcp/register_skill`. Staging cleaned on session exit.
- **Dynamic system prompt sections.** The system prompt has sections for preloaded content: "Preloaded Skills SKILL.md", "Preloaded CLI Tools Usage", and "Func Tools" (schemas via API). When `load_skill`, `load_cli`, or `load_func` is called, the content is delivered via tool message (not injected into system prompt), and the item is removed from the unloaded summary.
- **Toolbox manages tool state.** `toolbox.json` controls enable/disable/preload for tools, skills, CLI tools, MCP servers, and bridges. All toolbox functions accept optional `agent_name` parameter for per-agent config. Managed interactively via `qd-evolve toolbox` (Textual TUI).
- **Skills are non-callable.** SKILL.md files injected into system prompt as summaries — the LLM calls `load_skill` to get full instructions and uses callable tools to execute.
- **CLI tools are non-callable definitions.** YAML files in `tools/cli/` describe CLI commands (name, command, help_summary, examples). The LLM calls `load_cli` to get usage info, then executes via `run_shell`.
- **MCP multi-transport.** MCP bridge supports 4 transport types via `type` field: `stdio` (local subprocess), `sse` (Server-Sent Events), `http`/`streamable-http` (Streamable HTTP POST), `ws`/`websocket` (WebSocket). All string config values support `$VAR`/`${VAR}` env var expansion. `headers` field allows API key injection for remote transports.
- **MCP tools use original names.** No prefix on tool names. Disabled MCP servers are skipped entirely (no subprocess spawned, no HTTP connection). Disabled bridges are skipped by BridgeManager.
- **Templates are Jinja2.** User templates in `templates/` override builtin fallbacks in `qd_evolve/agent/_templates/`. System prompt is rendered from template and passed to Agent at startup.
- **Per-turn and cumulative token stats.** Track input/output tokens and context window usage each turn, plus running session total.
- **Persistent memory.** SQLite + sqlite-vec for cross-session memory storage. Each user+assistant message pair is auto-saved with BGE-M3 embedding. Supports semantic + keyword hybrid search, time-range filtering, and session exclusion. Per-agent memory db for full isolation.
- **Context compression.** When input tokens exceed `compress_threshold` (default 70%) of context window, old Q/A pairs are removed from the message list until tokens drop below `target_threshold` (default 50%). A new memory session is created after compression so recall_memory can distinguish pre/post compression context.
- **Auto recall.** Before each LLM call, user input is used as query to automatically retrieve relevant past conversations from MemoryStore. Results are injected into a dedicated memory section in the system prompt, with deduplication via `RecalledMemoryRegistry` (keyed by memory id). All recall queries exclude the current session. Configurable via `auto_recall` (on/off) and `auto_recall_top_k`.
- **Embedder selection by config.** `embeddings_backends` section in `config.json` defines named backends with `backend` field (`sentence-transformers` or `llama-cpp-python`). `memory_search.default_embeddings_backend` selects which to use. No file-suffix auto-detection.
- **Env vars from config.** `env_vars` in `config.json` are injected into `os.environ` at startup, so tools can access API keys without .env files.
- **Clean shutdown.** On `/quit` exit, bridges disconnect with `shutdown=True`, closing remote sessions and killing subprocesses while skipping unnecessary in-memory registry cleanup.
- **Config is read once at startup.** No runtime config changes — edit `config.json` and restart. However, registries (skills, CLI tools, bridges) are reloaded after each conversation turn to pick up new files without restart. Hot-loaded tools via `install_*` are immediately available without reload.
- **Staging area for hot-loaded tools.** `.qd-evolve/staging/` holds func/mcp/skill files before user confirms permanent registration. Cleaned on session exit. Registries scan both permanent and staging directories during discovery.
- **File paths relative to CWD.** Tools never hardcode paths; everything resolves against current working directory.
- **Shell encoding.** `run_shell` uses `locale.getpreferredencoding()` to decode subprocess output, handling Windows GBK/cp936 correctly.
- **BOAT via OAT bridge.** basic-open-agent-tools loaded in-process, no subprocess latency. Config in `tools/bridge/oat.json` — set `loadout` per package (coder, python, all, etc.).
- **COAT via OAT bridge.** coding-open-agent-tools (485 code analysis functions) loaded in-process alongside boat. Same Google ADK format, same bridge, same schema/output converters.
- **Bridge toolbox.** `toolbox.json` `bridge` section controls bridge enable/disable (e.g. `"oat:boat": "disabled"`). Bridges don't support preload — too many tools.
- **Backward-compatible shims.** Old import paths (`from qd_evolve.config import Settings`) still work via re-export shims. Core code moved to `qd_evolve/core/`, agent to `qd_evolve/agent/`, multi-agent management in `qd_evolve/agents/`.

## Rules

- **Don't auto push.** Commit is fine, but never push to remote unless the user explicitly asks.
- **Replay mode for testing.** `--replay <file>` feeds pre-recorded inputs instead of interactive prompt, with optional `--output` to capture. Used for automated CLI testing without an LLM dependency.