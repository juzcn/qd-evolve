# QD-Evolve — AI Agent Project

## Principles

- **Don't reinvent the wheel.** Use battle-tested libraries: anthropic/openai SDK, pydantic, rich, typer, standard logging.
- **Composition over inheritance.** Tools are callables in a registry, not a class hierarchy. Agents wrap via composition: Agent → A2AAgent → MqttAgent → GroupChatAgent.
- **Configuration is data.** All config via `config.json`. No CLI config commands, no `.env` files. Edit the file directly.
- **Three API types.** `openai-completions`, `openai-response`, `anthropic`. Set at provider level via `api` field.
- **Type everything.** Python 3.13 with full type annotations. pydantic models for all data boundaries.

## Design Decisions

### Four Independent Systems — No Protocol Mixing

(1) **Chat** (`qd-evolve`): in-process only, `need_a2a=False`, no A2A tools, no remote transport.
(2) **A2A** (`qd-evolve a2a`): in-proc for local + `HttpTransport` for remote.
(3) **MQTT** (`qd-evolve mqtt`): in-proc for local + `MqttTransport` for remote.
(4) **GChat** (`qd-evolve gchat`): MQTT-based group chat — all agents in one group via `GroupChatTransport` + `MqttTransport`.

`TransportRouter(inproc, remote)` where remote is exclusively HttpTransport or MqttTransport — never both, never falls back between protocols. `_pick()` checks inproc first (local registry), then returns `self._remote`. MQTT mode is determined by entry point, not a config flag — there is no `mqtt.enabled` field.

### Agent Initialization — Centralized in loader.py

`init_process(settings)` does per-process setup (SkillRegistry, CLIRegistry, BridgeManager.connect_all).
`create_agent(name, settings, *, need_a2a, need_mqtt, need_gchat)` does per-agent setup (toolbox, preload, system prompt, memory, provider, A2A identity).

Both `chat()` and `serve()` call `init_process` once then `create_agent` per agent. CLI is decoupled from agent logic — only calls `init_process` + `create_agent`, then handles display/transport. `qd-evolve chat` passes `need_a2a=False` to force-disable A2A in single-agent mode.

### Agent Wrapping — Composition Chain

Agent is the pure LLM loop. A2AAgent wraps Agent, adding card, task_store, event subscribers, and heartbeat override. MqttAgent wraps A2AAgent, adding MQTT lifecycle. GroupChatAgent wraps MqttAgent, adding group chat behavior (subscribe/publish on `/chat` topics, deduplication, parallel `agent.run()`). HumanAgent implements AgentProtocol directly (no wrapping). MqttHumanAgent wraps HumanAgent for MQTT. GroupChatHuman wraps MqttHumanAgent for group chat. Each wrapper delegates all Agent attributes through `.agent`.

A2AAgent hooks `agent._on_event` to `_push_event` for multi-subscriber fan-out. A2AAgent overrides `heartbeat_check()` to use `a2a-heartbeat.j2` template. A2AAgent runs its own heartbeat loop (not Agent's) so the override takes effect. `_hb_event` lives on Agent and is shared by wrappers via `self.agent._hb_event`.

GroupChatAgent owns its own heartbeat loop using `group-heartbeat.j2` template. GroupChatAgent calls `agent.run()` from a background thread — concurrent calls serialized by Agent's `_run_lock`.

### Agent.run() — Thread-Safe

`Agent.run()` is serialized with `threading.Lock` (`_run_lock`) to prevent concurrent corruption. Multiple callers (e.g., GroupChatAgent processing messages from multiple agents simultaneously) will queue and execute sequentially.

### A2A Auto-Enabled by Agent Count

A2A tools and system prompt section auto-enable when >1 agent is configured and `need_a2a` is not explicitly False. `qd-evolve chat` passes `need_a2a=False` regardless of agent count. A2A tools live in `qd_evolve/agent/a2a_tools.py` (outside auto-scanned `qd_evolve/tools/`), registered only via `register_a2a_tools()` when `a2a_on=True`.

### CLI as A2A Client+Server — Event-Driven Architecture

`qd-evolve a2a chat` starts a full A2A server (not just HTTP client) for webhook callbacks. Two distinct code paths:
- **AI agent**: CLI uses `send_stream` (blocking) — SSE event stream for display.
- **Human agent**: CLI uses `send_task` (non-blocking) — returns `input_required`, then waits for webhook callback (`tasks/pushNotification`) which pushes `task_completed` event.

Both paths share a unified `event_queue` for per-agent heartbeat display.

### Push Notifications — Task Store Updates + Heartbeat Injection

`A2AServer._tasks_push_notification` calls `on_push_notification()` to update `_task_store`. `send_task` maps remote `task_id` to local entry so push notifications can find and update it. After updating task store, the server calls `_check_pending_task_results()` and runs the agent with the pending result, forwarding to CLI via `_forward_to_cli()`.

In MQTT mode, `MqttAgent._listen_requests` and `MqttTransport._listen_all` both handle push notifications on the event topic by calling `on_push_notification()` and setting event type to `task_completed`.

### Human Agent — Async Only

`provider == "human"` identifies human agents. HumanAgent implements AgentProtocol directly — no Agent, no LLM, no tools, no memory. Communication: AI agent calls `send_task("human", ...)` → human returns `input_required` → human responds async → `complete_task()` fires webhook/push notification. `delegate_to` rejects human agents — blocking is incompatible with async. MqttHumanAgent wraps HumanAgent for MQTT, publishing push notifications on the caller's event topic.

### Group Chat — WeChat-Style Multi-Agent

All configured agents join a single group via MQTT. `qd-evolve gchat --agent <name>` starts either a GroupChatAgent (AI) or GroupChatHuman (human) depending on agent type.

- **GroupChatAgent**: Subscribes to `$a2a/v1/group/+/chat`, deduplicates via `_seen_msg_ids`, runs `agent.run()` in a background thread for each incoming message, publishes responses to `$a2a/v1/group/{name}/chat`.
- **GroupChatHuman**: Interactive terminal — displays incoming group messages with agent-colored prefixes, publishes keyboard input to group chat topic.
- **GroupChatTransport**: Own MQTT connection on `/chat` topics only. Does NOT consume from MqttTransport's `self._client.messages` stream — keeps MqttTransport's sole-consumer design intact.
- **Template selection**: `gchat-{template}` > `mqtt-{template}` > `a2a-{template}` > `{template}`. New templates: `group-default.j2`, `group-heartbeat.j2`, `group-message.j2`.
- **No A2A tools**: Group chat agents don't use `delegate_to`/`send_task` — they communicate via group topics, not 1:1 task delegation.

### Heartbeat — Agent-Owned, Event-Based

Agent manages heartbeat via `start_heartbeat_loop()`/`stop_heartbeat_loop()`. Uses `asyncio.Event.wait(timeout)` for idle detection — not fixed `asyncio.sleep()`. `Agent.run()` calls `self.touch_heartbeat()` at the start, which sets the event → loop wakes, clears event, resets timer. Heartbeat only triggers on actual timeout. `_hb_event` shared by wrappers. Template selection: single-agent → `heartbeat.j2`, A2A → `a2a-heartbeat.j2`, MQTT → `mqtt-heartbeat.j2`, Group → `group-heartbeat.j2`.

### Transport — Not an Agent Property

Any agent can be started in-process or via A2A/MQTT. Transport determined by entry point, not config field. No `transport` field on `AgentEntry`.

### On-Demand Tool Loading + Dynamic System Prompt

Tools start with name+description only. LLM calls `load_func`/`load_skill`/`load_cli` to get full schema, then the tool activates. System prompt has unloaded summary sections that shrink as tools are loaded — content delivered via tool message, not injected into system prompt.

### Bridge Protocol

Each bridge type self-registers with `BridgeManager` (discover/connect/disconnect). CLI only talks to BridgeManager. Adding a new bridge type only requires a `_*.py` module in `tools/bridge/`. Current bridges: MCP (external: stdio/SSE/StreamableHTTP/WebSocket), OAT (in-process: boat + coat). Schema conversion: Google ADK → OpenAI JSON Schema via `adk_schema.py`, output normalization via `adk_output.py`.

### Context Compression

When input tokens exceed `compress_threshold` (default 0.7) of context window, old Q/A pairs removed until below `target_threshold` (default 0.5). New memory session created after compression. Compression logic lives in `Agent._compress_context()`.

### Memory — Per-Agent, Persistent, Auto-Recall

Each agent has its own SQLite + sqlite-vec DB (configurable via `AgentEntry.memory_db`; `""` or `null` disables). Auto-recall before each LLM call, deduplication via `RecalledMemoryRegistry`. Embeddings backend selected by config name, not file suffix.

### Templates — Jinja2, User Overrides

User templates in `templates/` override builtin fallbacks in `qd_evolve/_templates/`. Selection: `need_a2a=False` → `default.j2`, `a2a_on=True` → `a2a-default.j2`, `mqtt_on=True` → `mqtt-default.j2`, `gchat_on=True` → `group-default.j2`. Shared tail in `_system_tail.j2` included via `{% include %}`.

### MqttTransport — Sole-Consumer Design

`_listen_all()` is the sole consumer of `self._client.messages`. Dispatches via: `_pending` (response futures by CorrelationData), `_event_subscribers` (Queue per agent), `_discovery_subscribers` (Queue list), `_online_subscribers` (one-shot futures). No other code iterates the aiomqtt stream.

### Config — Read Once at Startup

No runtime config changes — edit `config.json` and restart. Exception: registries reload after each turn, hot-loaded tools available immediately, toolbox state mutations write to config.json at runtime.

### Shell Encoding

`run_shell` uses `locale.getpreferredencoding()` to decode subprocess output, handling Windows GBK/cp936 correctly.

### Server Binding

`ServerConfig.host` is the connect address (default `127.0.0.1`). Server binds `0.0.0.0` to accept all interfaces. No hardcoded ports — defaults from `DEFAULT_SERVER_HOST`/`DEFAULT_SERVER_PORT` constants.

### API Errors — Caught, Not Fatal

LLM API call failures return an error string instead of crashing. Heartbeat errors caught silently. User stays in the chat loop.

### No Backward-Compat Shims

All imports use real paths: `qd_evolve.core.config`, `qd_evolve.core.providers`, `qd_evolve.core.logger`.

### MQTT CLI — Pure Client

`qd-evolve mqtt` is a pure client — no init_process, no create_agent, no tools/skills/bridges loading. Connects to agents via MqttTransport only. Chat code is independent copy of a2a_cli.py — avoids cross-dependencies during development.

### GChat CLI — Pure Client

`qd-evolve gchat` is a pure client — no init_process, no create_agent. Connects to agents via GroupChatTransport (for `/chat` topics) and MqttTransport (for A2A request/response). Independent chat loop, avoids cross-dependencies.

## Rules

- **Don't auto push.** Commit is fine, but never push to remote unless the user explicitly asks.
- **Ask before committing.** Confirm with user before creating git commits.
- **Ask before code changes.** Confirm with user before modifying any source code.
- **No revert without permission.** Never revert code changes without asking the user first.
- **Check logs first.** On any bug, read log files before analyzing code.
- **No hardcoded fallbacks.** Read from config via pydantic models, not `.get()` with fallbacks.
