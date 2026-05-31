# Design & Implementation

QD-Evolve is a multi-agent AI framework built on the belief that intelligence emerges, it isn't engineered. The design follows from the [manifesto](manifesto.md): give the model a minimal loop, a messy toolbox, and the ability to grow its own capabilities — then get out of the way.

## Core Philosophy

**The model knows best.** Every design decision starts from the premise that the LLM — not the framework — should decide what to do, when to do it, and how. The framework's job is to provide capabilities, not prescribe strategies. We don't encode ReAct, Plan-and-Execute, or any other reasoning template. The loop is: reason → call tools → observe → repeat. That's it.

**Emergence over engineering.** Multi-agent collaboration has no preset roles, no voting protocols, no orchestrator. Agents discover each other, send messages, and self-organize. Memory has no forgetting curves or episodic structures — just save and recall. The model learns what to keep.

**Physical isolation as the security boundary.** Software permissions, sandboxes, and content filters are all guardrails that a sufficiently capable model can talk its way past. The only meaningful security boundary is whether the model can physically affect the world without a human in the loop.

## Design Decisions and Trade-offs

### No orchestration layer

There is no Planner, no Executor, no Critic. Agents are peers. The trade-off: emergent coordination is less predictable than scripted workflows. The bet: as models improve, emergent coordination outperforms hand-coded protocols, and the framework won't need to be rewritten to keep up.

### No memory architecture

Save and recall is the entire memory surface. The trade-off: the model might miss important context that a sophisticated memory system would surface. The bet: the model's own attention mechanism is a better retrieval algorithm than any forgetting curve or episodic structure we could hard-code.

### Thread-locked agent loop

Agents serialize concurrent calls with a lock rather than supporting parallel execution. The trade-off: slower under concurrent load. The bet: correctness matters more than throughput, and concurrent LLM calls to the same agent would corrupt shared state (message list, tool registrations, memory).

### On-demand tool schemas

Tools start invisible to the model, revealed only when needed. The trade-off: extra round-trips when the model discovers it needs a tool. The bet: the prompt size savings (hundreds of tools × thousands of schema tokens) outweigh the latency of an extra `load_func` call.

### One config file

Framework settings live in `config.json` with sensible defaults — only overrides need to be specified. Tool API keys (Serper, Baidu, Tavily) go in its `env_vars` section. No CLI config commands, no `.env` files. The trade-off: less flexible for containerized deployment where env vars are idiomatic. The bet: simplicity and discoverability matter more for a framework meant to be understood and modified.

### Physical isolation over software security

No sandbox, no permission system, no content filter. The trade-off: the model can do dangerous things if given dangerous tools. The design response: don't give it dangerous tools. The security boundary is what the model can physically reach — network access, filesystem access, process execution. A human presses the last button.

## Invariants

These are the constraints that every change must preserve:

1. **The agent loop is `reason → act → observe`.** No phases, no templates, no planning steps.
2. **Agents compose by wrapping, not inheritance.** Each layer adds exactly one concern.
3. **No more than one remote transport at a time.** In-process + HTTP, or in-process + MQTT, never both.
4. **MQTT transport is sole-consumer.** Group chat gets its own transport connection.
5. **Human and AI agents share the same protocol.** The transport layer doesn't distinguish them.
6. **Memory is save + recall + process capture.** Each save records the full Q/A along with the tool call process (name, parameters, success/failure). No forgetting curves, no episodic structures, no automatic categorization.
7. **Configuration is one file.** Framework settings live in config.json. Tool API keys go in its `env_vars` section.
8. **Security is physical, not digital.** No software permission system that the model could reason past.

## Architecture

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│   Chat CLI   │  │   A2A CLI    │  │  MQTT CLI   │  │  GChat CLI  │
│  (in-proc)   │  │ (HTTP/SSE)   │  │  (MQTT v5)  │  │ (MQTT v5)   │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                  │                  │
       ▼                 ▼                  ▼                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│                          Agent Layer                                  │
│  Agent ← A2AAgent ← MqttAgent ← GroupChatAgent  |  HumanAgent       │
│  GroupChatWechatHuman (bridge)                                         │
│  AgentRegistry  |  TransportRouter  |  EventSubscribers             │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
  ┌──────────┐      ┌──────────┐      ┌──────────┐
  │ Provider │      │  Memory  │      │  Toolbox │
  │ Registry │      │  Store   │      │ Registry │
  └──────────┘      └──────────┘      └──────────┘
```

### Agent Loop

The central abstraction: `reason → act → observe`, repeated until the model produces a text response with no tool calls. No planning phase, no reflection phase. The loop is guarded by a single `threading.Lock` — one invocation per agent at a time to prevent concurrent corruption of messages and tool state.

### Agent Hierarchy

Composition via wrapping, not inheritance:

- **Agent** (`agent/agent.py`): Pure LLM loop. Manages messages, memory recall, context compression, heartbeat, tool execution. Knows nothing of networks or other agents.
- **A2AAgent** (`agent/a2a_agent.py`): Wraps Agent, adds A2A identity (AgentCard, TaskStore), event subscriber fan-out.
- **MqttAgent** (`agent/mqtt_agent.py`): Wraps A2AAgent, adds MQTT v5 lifecycle (connect, LWT, subscribe, publish).
- **GroupChatAgent** (`agent/group_chat_agent.py`): Wraps MqttAgent, adds group chat — subscribes to `/chat` topics, deduplication, parallel `agent.run()`, group message publishing.
- **HumanAgent** (`agent/human_agent.py`): Implements AgentProtocol directly. No LLM, no tools, no memory. Returns `input_required`, completes asynchronously via webhook.
- **MqttHumanAgent** (`agent/mqtt_human_agent.py`): MQTT wrapper for HumanAgent.
- **GroupChatHuman** (`agent/group_chat_human.py`): Wraps MqttHumanAgent, adds terminal-based group chat UI.
- **GroupChatWechatHuman** (`agent/group_chat_wechat_human.py`): Wraps MqttHumanAgent, bridges WeChat iLink to MQTT group chat. Polls WeChat for incoming messages, forwards group responses back via WeChat.

### Transport

`TransportRouter(inproc, remote)` — holds exactly two transports. Routes locally registered agents to in-process, unknown agents to remote (HTTP or MQTT, never both). Group chat uses an independent `GroupChatTransport` connection to keep the MqttTransport sole-consumer design intact.

### Tool System

Five tool categories:

| Category | Location | Callable | Loading |
|----------|----------|----------|---------|
| System | `qd_evolve/tools/` | Yes | Auto-discovered, schema on demand |
| A2A | `qd_evolve/agent/a2a_tools.py` | Yes | Registered when A2A enabled |
| Func | `tools/func/` | Yes | Hot-loadable `.py` files |
| Skills | `skills/` | No | SKILL.md, injected into prompt |
| CLI | `tools/cli/` | No | YAML definitions, via `run_shell` |

**On-demand loading**: tools start with name + description. Full schema loaded only when the model calls `load_func`/`load_skill`/`load_cli`. Tools move from unloaded to active for subsequent turns.

**Hot-loading**: install tools at runtime via `install_func`/`install_mcp`/`install_skill`. Staged in `.qd_evolve/staging/`, persisted via `register_*` to `config.json`.

**Bridge protocol**: `BridgeManager` auto-discovers bridge modules in `tools/bridge/_*.py`. Each bridge self-registers with discover/connect/disconnect functions. MCP bridge spawns subprocesses; OAT bridge imports packages in-process.

### Memory

SQLite + `sqlite-vec` with BGE-M3 embeddings. Two operations: `save` (insert + embed) and `recall` (embed query → cosine similarity → top-k). Auto-recall queries memory before each LLM call, injecting results into the system prompt. Deduplicated across turns via `RecalledMemoryRegistry`. Context compression truncates old Q/A pairs when tokens exceed threshold.

**Process capture**: `save()` accepts an optional `process` string recording each tool call in the iteration chain — name, parameters, and success/failure (tool results excluded). This enriches the `content` field for semantic recall without schema changes.

### Multi-Agent Communication

Two mechanisms: **direct tasking** (send task → lifecycle: submitted→working→completed/failed/canceled/input_required) and **group chat** (all agents subscribe to `/chat` topics, `@mentions` direct attention, no coordinator). Built on A2A v1.0: agent discovery, task management, SSE streaming, push notifications.

### Configuration

Single `config.json` file. Most fields have sensible defaults in the Pydantic models — config.json only needs to specify overrides. Each agent gets its own provider, model, memory DB, server binding, and toolbox state. Global defaults as fallback. `provider: "human"` identifies terminal human agents; `provider: "wechat-human"` identifies WeChat iLink bridge human agents. WeChat human agents persist their session token in the `wechat_session` field. `env_vars` maps environment variables for tool API keys (Serper, Baidu, Tavily).

### Templates

Jinja2 system prompts with two-tier fallback: `templates/` (user) overrides `_templates/` (builtin). Mode-specific templates (single-agent, A2A, MQTT, group chat), each including a shared `_system_tail.j2`.

### Heartbeat

`asyncio.Event.wait(timeout)` — only fires on genuine idle. `touch_heartbeat()` resets the timer on activity. LLM response `"."` means stay silent. Mode-specific templates. `0` disables.

## Module Map

```
qd_evolve/
├── __main__.py              # CLI entry (typer), subcommand registration
├── chat_cli.py              # Single-agent chat loop
├── a2a_cli.py               # A2A multi-agent chat + serve
├── mqtt_cli.py              # MQTT multi-agent chat + serve
├── gchat_cli.py             # Group chat
├── cli_utils.py             # ReplayInput, TeeWriter, AGENT_COLORS
├── skills.py                # SkillRegistry
├── cli_tools.py             # CLIRegistry
├── toolbox_tui.py           # Textual TUI for toolbox management
├── memory_tui.py            # Textual TUI for memory browsing and search
├── core/
│   ├── config.py            # Settings, AgentEntry, ServerConfig, MqttBrokerConfig (pydantic)
│   ├── providers.py         # Provider + ProviderRegistry
│   ├── registry.py          # ToolRegistry + ToolDef (on-demand loading)
│   ├── memory.py            # MemoryStore (SQLite + sqlite-vec), RecalledMemoryRegistry
│   ├── prompts.py           # PromptTemplateManager (Jinja2, two-tier fallback)
│   ├── toolbox.py           # Per-agent tool state (enabled/preload/disabled)
│   └── logger.py            # SharedFileHandler
├── agent/
│   ├── agent.py             # Agent — LLM loop, tool exec, memory, compression, heartbeat
│   ├── a2a_agent.py         # A2AAgent — wraps Agent, adds card + task_store + event fan-out
│   ├── mqtt_agent.py        # MqttAgent — wraps A2AAgent, MQTT v5 lifecycle
│   ├── group_chat_agent.py  # GroupChatAgent — wraps MqttAgent, group chat behavior
│   ├── group_chat_human.py  # GroupChatHuman — wraps MqttHumanAgent, terminal group UI
│   ├── group_chat_wechat_human.py  # GroupChatWechatHuman — WeChat iLink ↔ MQTT bridge
│   ├── group_chat_transport.py  # GroupChatTransport — independent MQTT for /chat topics
│   ├── human_agent.py       # HumanAgent — implements AgentProtocol directly, no LLM
│   ├── mqtt_human_agent.py  # MQTT wrapper for HumanAgent
│   ├── server.py            # A2AServer — aiohttp JSON-RPC + SSE endpoint
│   ├── transport.py         # InprocTransport, HttpTransport, TransportRouter
│   ├── mqtt_transport.py    # MqttTransport — sole-consumer MQTT v5
│   ├── registry.py          # AgentRegistry — singleton, local agent lookup
│   ├── loader.py            # init_process + create_agent factory
│   ├── a2a_tools.py         # delegate_to, send_task, get_task, cancel_task
│   ├── protocol.py          # AgentProtocol ABC (card, task_store, run, subscribe_events)
│   └── a2a.py               # A2A v1.0 data models (Task, Message, AgentCard, etc.)
├── tools/
│   ├── __init__.py          # Re-exports ToolRegistry
│   ├── tool_loader.py       # load_func — on-demand func tool schema loading
│   ├── skill_loader.py      # load_skill — on-demand skill content loading
│   ├── cli_loader.py        # load_cli — on-demand CLI tool detail loading
│   ├── install_func.py      # install_func — hot-load func tool
│   ├── install_mcp.py       # install_mcp — hot-load MCP server
│   ├── install_skill.py     # install_skill — hot-load skill
│   ├── register_func.py     # register_func — persist func tool
│   ├── register_mcp.py      # register_mcp — persist MCP server
│   ├── register_skill.py    # register_skill — persist skill
│   ├── recall_memory.py     # recall_memory — search memory
│   └── staging.py           # .qd_evolve/staging/ directory helpers
├── bridge/
│   ├── __init__.py
│   └── wechat_clawbot_client.py  # WeChatClawbotClient — iLink ClawBot protocol
├── utils/
│   ├── adk_schema.py        # Google ADK → OpenAI JSON Schema
│   └── adk_output.py        # ADK output normalization
└── _templates/              # Builtin Jinja2 templates
    ├── default.j2           # Single-agent system prompt
    ├── a2a-default.j2       # A2A system prompt
    ├── mqtt-default.j2      # MQTT system prompt
    ├── group-default.j2     # Group chat system prompt
    ├── group-heartbeat.j2   # Group chat heartbeat
    ├── group-message.j2     # Group chat incoming message format
    ├── heartbeat.j2         # Single-agent heartbeat
    ├── a2a-heartbeat.j2     # A2A heartbeat
    ├── mqtt-heartbeat.j2    # MQTT heartbeat
    └── _system_tail.j2      # Shared tail (included by all templates)
```

## Class Hierarchy

```
AgentProtocol (Protocol)
├── A2AAgent — wraps Agent, adds A2A identity + event fan-out
│   └── MqttAgent — wraps A2AAgent, adds MQTT v5 lifecycle
│       └── GroupChatAgent — wraps MqttAgent, adds group chat behavior
└── HumanAgent — no LLM, no tools, no memory; async completion via webhook
    └── MqttHumanAgent — MQTT wrapper for HumanAgent

Group chat wrappers (composition over MqttHumanAgent):
├── GroupChatHuman — terminal-based group chat for human agents
└── GroupChatWechatHuman — WeChat iLink ↔ MQTT bidirectional bridge
```

Composition, not inheritance. Each wrapper delegates to the inner agent and adds exactly one concern.

## Model Layer (pydantic)

All config models live in `qd_evolve/core/config.py`:

| Model | Purpose |
|-------|---------|
| `Settings` | Root config. Providers, agents, memory, stream, heartbeat. Most fields have defaults — config.json only needs overrides. |
| `AgentEntry` | Per-agent: name, description, provider, model, memory_db, server, toolbox. `is_human` is `provider == "human"`. |
| `ProviderConfig` | API key, base URL, api type (openai-completions/openai-response/anthropic), models. |
| `ModelConfig` | Context window, max_tokens, reasoning flag. |
| `ServerConfig` | host, port. |
| `MqttBrokerConfig` | Broker host, port. |
| `ToolboxSection` | Per-agent tool state: five dicts mapping name→state. |
| `MCPServerConfig` | MCP server: command, args, env, type (stdio/sse/http/ws), url, headers, timeout. |
| `EmbeddingsBackend` | model_path, dim, backend (sentence-transformers/llama-cpp-python), llama_n_ctx, llama_n_batch. |
| `MemorySearchConfig` | auto_recall, auto_recall_top_k, recall_memory_limit. |

Validation: `AgentsConfig._validate_ports` rejects duplicate ports at model init.

A2A data models in `qd_evolve/agent/a2a.py`: `AgentCard`, `Task`, `TaskStatus`, `TaskState` (enum), `Message`, `Part`, `StreamResponse`, `AgentCapabilities`, `AgentSkill`, `FileContent`, `AgentExtension`.

## Agent Loop (`Agent._run_inner`)

```
_run_inner(user_input, system, provider, model):
    1. Resolve provider/model (arg → instance → config default)
    2. Append user message to self.messages
    3. Auto-recall: query memory, inject into system prompt
    4. Loop:
       a. Create API client (openai or anthropic)
       b. Build tool definitions from registry (active + preload)
       c. Call LLM (dispatch by api_type)
       d. If text response → save to memory (with process capture), compress, return
       e. If tool calls → record name/params/success via _record_tool_call(), execute via ToolRegistry.call(), append results, continue
       f. If max_iterations exceeded → return error
```

The entire loop is guarded by `threading.Lock` (`_run_lock`). Only one `run()` per agent at a time.

### API Dispatch

Three code paths in `Agent`:

- `_run_anthropic()` — `client.messages.create()` with Anthropic SDK. Tool use via `stop_reason == "tool_use"`. Content blocks contain `tool_use` items.
- `_run_openai_completion()` — `client.chat.completions.create()` with OpenAI SDK. Tool calls via `msg.tool_calls`. Supports streaming (`stream=True`) with reasoning content for reasoning models.
- `_run_openai_response()` — OpenAI Responses API. Separate code path for the newer API shape.

Each path recursively calls itself for tool turns, incrementing an `_iter` counter checked against `max_iterations`.

`api_type` is mapped from config's `api` field: `openai-completions` → `openai_completion`, `openai-response` → `openai_response`, `anthropic` → `anthropic`.

### Tool Execution

`ToolRegistry.call(name, **kwargs)` spawns a daemon thread with a `DEFAULT_TOOL_TIMEOUT` (60s) timeout. If the thread is still alive after timeout, returns an error string. Exceptions are caught and formatted into error messages. `ImportError` is re-raised (not caught) to surface missing dependencies.

## Initialization Flow

### Per-process (`init_process`)

Called once. Sets up module-level singletons:

1. `SkillRegistry` — scans `skills/` for SKILL.md files
2. `CLIRegistry` — scans `tools/cli/` for YAML definitions
3. `BridgeManager.connect_all()` — auto-discovers bridge modules in `tools/bridge/_*.py`, calls each bridge's `discover()` then `connect()`
4. Injects registries into loader tools (`skill_loader`, `install_skill`, `cli_loader`)

### Per-agent (`create_agent`)

Called for each agent. Returns a fully initialized agent:

1. Lookup `AgentEntry` from config by name
2. **Human short-circuit**: if `entry.is_human`, create `HumanAgent` (or `MqttHumanAgent` if MQTT mode), return immediately
3. Resolve singletons: `ToolRegistry`, `ProviderRegistry`, `SkillRegistry`, `CLIRegistry`
4. Apply per-agent toolbox state (enabled/preload/disabled)
5. Register A2A tools if >1 agent and not group chat
6. Build system prompt via `PromptTemplateManager.render()` with template variable injection
7. Create `MemoryStore` if `memory_db` is configured
8. Create `Agent` instance
9. Resolve provider/model (agent-specific → global default)
10. Wrap with `A2AAgent` if multi-agent, then `MqttAgent` if MQTT mode
11. Return agent

### Template Resolution

`create_agent` chains template name lookups:

1. If group chat: try `group-{template}`
2. If MQTT: try `mqtt-{template}` → fallback to `a2a-{template}` if multi-agent
3. If A2A only: try `a2a-{template}`
4. GChat fallback: try `gchat-{template}`
5. Default: `{template}` (usually `default`)

`PromptTemplateManager` uses a `_CombinedLoader` that checks `templates/` first (user overrides), then `_templates/` (builtins). Jinja2 with `trim_blocks=True`, `lstrip_blocks=True`.

## Transport

### TransportRouter

`TransportRouter(inproc, remote)` — holds exactly two transports. Routes to `inproc` for locally registered agents, to `remote` for unknown agents. `remote` is either `HttpTransport` or `MqttTransport`, never both.

### InprocTransport

Direct call to `Agent.run()` via thread pool (`asyncio.to_thread`). For human agents, uses async path: `receive_task()` → returns `input_required` immediately.

### HttpTransport

A2A JSON-RPC over HTTP. `POST /` with JSON-RPC body. Connects to remote agent's `A2AServer`. Supports SSE streaming for `message/stream`.

### MqttTransport

Implements `A2ATransport` over MQTT v5. Key features:

- **Request-response correlation**: MQTT v5 Response Topic + Correlation Data. Caller sets response topic, callee publishes result there.
- **Discovery**: Retained `AgentCard` on `$a2a/v1/discovery/{name}`. LWT clears it on disconnect.
- **Sole consumer**: `_listen_all()` subscribes to `$a2a/v1/response/{self}/+` and `$a2a/v1/event/{self}`. Only one consumer per connection.
- **QoS**: Task requests at QoS 1, events at QoS 0, discovery at QoS 1.

Topic structure:

| Topic | QoS | Retained | Purpose |
|-------|-----|----------|---------|
| `$a2a/v1/discovery/{agent}` | 1 | Yes + LWT | AgentCard, online/offline |
| `$a2a/v1/request/{agent}` | 1 | No | JSON-RPC requests |
| `$a2a/v1/response/{agent}/{req_id}` | 1 | No | MQTT v5 Response Topic |
| `$a2a/v1/event/{agent}` | 0 | No | Streaming + push notifications |
| `$a2a/v1/group/{name}/chat` | 0 | No | Group chat messages |

## Tool System

### ToolRegistry (`qd_evolve/core/registry.py`)

Central registry of `ToolDef` objects. Each `ToolDef` has: `name`, `description`, `handler` (callable), `input_schema` (JSON Schema dict), `enabled` (bool).

`definitions(api_format, active_tools)` produces tool schemas in the target API format. Only returns schemas for tools whose names are in `active_tools` — this is the on-demand loading mechanism.

### On-Demand Loading

Three loader tools implement the pattern:

1. **Func tools** (`tool_loader.py`): `load_func(name)` — imports the module, extracts `get_input_schema()` + `run()`, registers full schema in ToolRegistry, adds name to `_active_tools`
2. **Skills** (`skill_loader.py`): `load_skill(name)` — reads SKILL.md content, injects into system prompt by appending to messages, adds to `_loaded_skill_names`
3. **CLI tools** (`cli_loader.py`): `load_cli(name)` — reads YAML, formats as system prompt injection, adds to `_loaded_cli_names`

### Bridge System (`tools/bridge/`)

Self-registering plugin architecture:

- `BridgeManager` scans `tools/bridge/_*.py` for bridge modules
- Each module calls `BridgeManager.register(name, discover, connect, disconnect)`
- Each bridge's `discover()` returns config objects; `connect()` creates `Bridge` instances and registers tools

**MCP bridge** (`_mcp.py`): Scans `tools/mcp/*.json` + `.qd_evolve/staging/mcp/*.json`. Spawns subprocess via `mcp` SDK, discovers tools via `list_tools`, registers in ToolRegistry. Supports stdio, SSE, StreamableHTTP, WebSocket transports.

**OAT bridge** (`_oat.py`): Reads `tools/bridge/oat.json`. Imports Python packages directly, wraps functions as ToolRegistry handlers. No subprocess overhead. Schema conversion via `adk_schema.py` (Google ADK → OpenAI JSON Schema) and output normalization via `adk_output.py`.

### Hot-Loading

Five install/register pairs:

| Install (staging) | Register (persist) | Target |
|-------------------|-------------------|--------|
| `install_func` | `register_func` | `tools/func/*.py` |
| `install_mcp` | `register_mcp` | `tools/mcp/*.json` |
| `install_skill` | `register_skill` | `skills/*/SKILL.md` |

Install writes to `.qd_evolve/staging/`, register copies from staging to the target directory and updates `config.json` toolbox state. Both work at runtime without restart.

### Toolbox State

`qd_evolve/core/toolbox.py` manages per-agent tool state in `config.json`:

- **enabled**: Tool is callable, schema starts unloaded
- **preload**: Tool is callable, schema loaded at startup
- **disabled**: Tool is invisible to the agent

Five sections: `tools`, `mcp_servers`, `bridge`, `cli`, `skills`. Bridge uses binary enabled/disabled (no preload). Managed via `qd-evolve toolbox` (Textual TUI) or direct `config.json` editing.

## Memory

### MemoryStore (`qd_evolve/core/memory.py`)

SQLite + `sqlite-vec` extension. Two tables:

- `conversations`: session_id, user_msg, assistant_msg, content (combined), accessed_at, access_count
- Vector index on `content` via `sqlite-vec` with BGE-M3 embeddings

`save(user_msg, assistant_msg)`: Inserts row, creates vector embedding.

`recall(query, limit)`: Embeds query, cosine similarity search, returns top-k `MemoryEntry` objects.

`new_session()`: Generates new session_id.

### Auto-Recall

Before each LLM call, `Agent._auto_recall()` queries memory with the user input. Results are deduplicated via `RecalledMemoryRegistry` (tracks seen IDs across turns). Deduped entries are injected into the system prompt under a `## Relevant Past Conversations` section.

### Context Compression

`Agent._compress_messages()` fires when `last_input_tokens / context_window > compress_threshold` (default 0.7). Removes oldest user/assistant/tool triples from the front of `self.messages` until estimated tokens drop below `target_threshold * context_window` (default 0.5). Simple truncation, no summarization.

### Embeddings Backend

Two backends: `sentence-transformers` (BGE-M3 via `SentenceTransformer`) and `llama-cpp-python` (local GGUF models). Configured per-backend in `embeddings_backends` config section.

## Event System

### Agent Events

`Agent._on_event` callback fires on: iteration start, status update, print output, error, completion, heartbeat, heartbeat_silent. Event dict has `type` + relevant fields.

`A2AAgent._push_event()` fans out to all subscribers (list of `asyncio.Queue`). Subscribers get events via `subscribe_events() → Queue`.

### SSE Streaming

`A2AServer` converts events to SSE `StreamResponse` objects. `message/stream` returns an async generator of SSE events. Custom metadata includes iteration number, status, token counts, heartbeat.

### MQTT Event Publishing

`MqttAgent` runs an `_event_pusher_task` that drains the event queue and publishes each event to `$a2a/v1/event/{agent_name}` as JSON.

## Heartbeat

`Agent.start_heartbeat_loop()` creates an asyncio task that:

1. `await asyncio.wait_for(self._hb_event.wait(), timeout=idle_seconds)`
2. If event was set → activity occurred, reset, don't fire
3. If timeout → genuine idle, call `heartbeat_check()` via thread pool

`touch_heartbeat()` sets the event. Called on user input before each LLM call.

Heartbeat response handling: if LLM returns `"."`, stays silent. Otherwise, the response is pushed as a heartbeat event. Configurable `heartbeat_idle_seconds` per agent; `0` disables.

## Group Chat

### GroupChatAgent

Wraps `MqttAgent`. Adds:

- **Group listener**: background asyncio task subscribes to `$a2a/v1/group/{member}/chat` for each member via `GroupChatTransport`
- **Deduplication**: `_seen_msg_ids` set, tracks message IDs to skip duplicates
- **Parallel processing**: incoming messages trigger `agent.run()` in thread pool; multiple messages can process concurrently (locking handled by Agent's `_run_lock`)
- **Response publishing**: formatted responses published to the group topic
- **Heartbeat override**: uses `group-heartbeat.j2` template; fires only when no recent group activity

### GroupChatTransport

Independent `aiomqtt.Client` connection for `/chat` topics. Keeps `MqttTransport`'s sole-consumer `_listen_all()` design intact by not adding subscriptions to the original client.

### Message Format

Incoming group messages formatted via `group-message.j2` template. `@mentions` parsed to determine if the message is addressed to this agent. `@all` matches everyone.

### GroupChatHuman

Wraps `MqttHumanAgent`. Provides an interactive terminal UI: incoming group messages appear above the prompt line, preserving partial input. Keyboard input is published to the group via `publish_human_input()`.

### GroupChatWechatHuman

Wraps `MqttHumanAgent`. Replaces terminal I/O with a WeChat iLink bidirectional bridge:

- **WeChat → MQTT**: Long-polls WeChat for new messages via `WechatClawbotClient.poll_updates()`, extracts text, parses `@mentions`, publishes to group MQTT topic
- **MQTT → WeChat**: Listens to all group messages, forwards to the WeChat user via `WechatClawbotClient.send_message()`, using the last `context_token` from the polled message

QR login on startup. Session token is persisted to `config.json` (`wechat_session` field) for reuse across restarts (valid for ~23 hours).

### WechatClawbotClient (`qd_evolve/bridge/wechat_clawbot_client.py`)

Standalone async client for the WeChat iLink ClawBot protocol. Extracted from SiverKing/weixin-ClawBot-API (MIT License). Handles: QR code login flow (terminal rendering), session persistence (`get_session_dict` / `try_restore_session`), message polling (`/ilink/bot/getupdates`), message sending (`/ilink/bot/sendmessage`), typing indicators. No dependencies on external WeChat libraries — uses only `aiohttp` + stdlib.

## CLI Layer

### Entry Point

`qd_evolve/chat_cli.py` registers typer subcommands: default (chat), `a2a-http`, `a2a-inproc`, `a2a-mqtt`, `gchat`, `toolbox`, `memory`.

### Chat Loop Pattern (all CLIs)

```
while True:
    read user input (prompt_toolkit / replay)
    if slash command → handle locally
    if EOF / /quit → break
    touch heartbeat
    agent.run(input) → display response
```

### Slash Commands

Parsed in CLI layer, never sent to LLM. Each command is a string match before `agent.run()`. Commands: `/models`, `/agents`, `/tools`, `/skills`, `/cli`, `/status`, `/memory`, `/recall`, `/compress`, `/load`, `/reset`, `/clear`, `/help`, `/quit`.

### Replay Mode

`ReplayInput` reads lines from a file, feeds them as user input. `TeeWriter` captures output to a file. Used for automated testing.

## Dependencies

Core runtime: `anthropic`, `openai`, `pydantic`, `typer`, `rich`, `jinja2`, `pyyaml`, `sqlite-vec`, `prompt-toolkit`, `sentence-transformers` (or `llama-cpp-python`), `numpy`, `aiohttp`, `aiomqtt`, `mcp`, `textual`, `basic-open-agent-tools`, `coding-open-agent-tools`.

Bridge extras: `defusedxml` (coding-open-agent-tools dep), `tomlkit`, `markdown-pdf`.

Test: `pytest`, `pytest-asyncio`, `pytest-aiohttp`, `pytest-cov`, `pytest-mock`, `pytest-timeout`, `pytest-xdist`, `aioresponses`.

Python ≥ 3.13 required.

## Key Patterns

### Singleton via Module-Level Variable

Used by: `AgentRegistry`, `SkillRegistry`, `CLIRegistry`, `BridgeManager`, A2A `_transport`/`_task_store`.

Pattern: `_instance: T | None = None` at module level, `get_*()` returns it or raises, `set_*()` injects it. Avoids DI framework overhead.

### Callback Injection

Agent has three callbacks: `_on_status` (status bar), `_on_print` (output), `_on_event` (structured events). Set via setter methods. A2AAgent hooks `_on_event` to fan out to subscribers. CLIs set callbacks after agent creation.

### Tool Timeout via Daemon Thread

`ToolRegistry.call()` spawns a `threading.Thread(daemon=True)`, joins with timeout. If thread is alive after timeout, returns error. Avoids blocking the agent loop on hung tools.

### Lazy Import at Call Site

Common pattern: imports inside function bodies rather than at module top. Avoids circular imports between `agent/` and `core/`. Seen in: `InprocTransport._get_registry()`, `Agent._create_memory()`, `create_agent()`.

### Pydantic for All Config

Every config structure is a `pydantic.BaseModel`. No `dict.get()` with fallbacks — fields have explicit defaults in the model definition. Validation at model init catches config errors early.

### Jinja2 Two-Tier Templates

`PromptTemplateManager` checks `templates/` (user) first, falls back to `_templates/` (builtin). Templates are `.j2` files. Render context includes agent metadata, tool summaries, OS info, available agents, topology.
