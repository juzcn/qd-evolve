# QD-Evolve Test Plan

## Quick Start

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=qd_evolve --cov-report=term-missing

# Run only core tests (fast feedback)
pytest tests/core/

# Fail-fast for CI
pytest -x
```

## Test Results (2026-05-17)

- **394 tests, all passing**
- **44% overall code coverage**
- Core modules: 88-100% coverage
- Agent logic: 31-88% coverage (LLM call paths not tested)
- CLI: 7% coverage (interactive TUI, not automatable)

## Test Directory Structure

```
tests/
├── conftest.py                      # Shared fixtures
├── core/
│   ├── test_config.py               # Settings, AgentEntry, load/save (100%)
│   ├── test_registry.py             # ToolRegistry, definitions, call (94%)
│   ├── test_providers.py            # Provider, ProviderRegistry (97%)
│   ├── test_memory.py               # MemoryStore, RecalledMemoryRegistry (71%)
│   ├── test_prompts.py              # PromptTemplateManager (97%)
│   ├── test_toolbox.py              # State management, toggle, apply (82%)
│   └── test_logger.py               # SharedFileHandler, setup_logging (94%)
├── agent/
│   ├── test_a2a_models.py           # A2A pydantic models (100%)
│   ├── test_agent_registry.py       # AgentRegistry, Topology (88%)
│   ├── test_agent_core.py           # Agent loop logic (31%)
│   ├── test_transport.py            # Transport pure functions (27%)
│   ├── test_server.py               # TaskStore, A2AServer RPC (58%)
│   └── test_loader.py               # get_agent_entry, create_agent_core (41%)
├── tools/
│   ├── test_adk_schema.py           # ADK→OpenAI schema conversion (88%)
│   ├── test_adk_output.py           # Output normalization (100%)
│   ├── test_tool_loader.py          # load_func handler (100%)
│   ├── test_skill_loader.py         # load_skill handler (100%)
│   ├── test_cli_loader.py           # load_cli handler (100%)
│   ├── test_recall_memory.py        # recall_memory handler (88%)
│   ├── test_a2a_tools.py            # delegate_to, send_task, etc. (41%)
├── skills/
│   ├── test_skill_registry.py       # SkillRegistry, frontmatter (89%)
│   └── test_cli_registry.py         # CLIRegistry, YAML loading (95%)
└── cli/
    ├── test_replay.py               # ReplayInput, TeeWriter
    └── test_slash_commands.py       # SLASH_COMMANDS validation
```

## Coverage by Module

| Module | Coverage | Notes |
|--------|----------|-------|
| `core/config.py` | 100% | All pydantic models, load/save |
| `agent/a2a.py` | 100% | All A2A protocol models |
| `tools/skill_loader.py` | 100% | Pure handler logic |
| `tools/cli_loader.py` | 100% | Pure handler logic |
| `utils/adk_output.py` | 100% | Output normalization |
| `core/prompts.py` | 97% | Template rendering |
| `core/providers.py` | 97% | Provider lookup, client creation |
| `core/logger.py` | 94% | SharedFileHandler |
| `core/registry.py` | 94% | Tool registration, definitions |
| `skills/test_cli_registry.py` | 95% | YAML discovery |
| `agent/registry.py` | 88% | Agent/topology lookup |
| `utils/adk_schema.py` | 88% | ADK schema conversion |
| `tools/recall_memory.py` | 88% | Memory search handler |
| `skills/skills.py` | 89% | Skill discovery, frontmatter |
| `core/toolbox.py` | 82% | State management |
| `agent/server.py` | 58% | JSON-RPC handling |
| `agent/loader.py` | 41% | Agent initialization (heavy deps) |
| `tools/a2a.py` | 41% | A2A tool handlers (need transport) |
| `agent/agent.py` | 31% | LLM call paths (mocked) |
| `agent/transport.py` | 27% | Async HTTP transport |
| `cli.py` | 7% | Interactive TUI |
| `toolbox_tui.py` | 0% | Textual TUI |

## Items Requiring Manual Testing

These cannot be fully automated and must be verified by a human:

### 1. LLM API Actual Calls
- **Why**: Non-deterministic output, costs money per call
- **What to test**: Send a real prompt to each supported API type (openai-completions, openai-response, anthropic), verify response is received and parsed correctly
- **How**: Run `qd-evolve` with a real API key and interact

### 2. Streaming Token Display
- **Why**: Rich Live rendering in terminal — visual verification only
- **What to test**: Enable `stream: true` in config.json, verify tokens appear incrementally
- **How**: Run `qd-evolve` with streaming enabled

### 3. Interactive CLI Prompt
- **Why**: prompt_toolkit session, slash commands with real agent
- **What to test**: Type `/models`, `/agents`, `/status`, `/reset` in a live session
- **How**: Run `qd-evolve` interactively; also test `--replay` with `--output`

### 4. Heartbeat UX
- **Why**: Timing behavior (idle seconds, silent '.' response) requires live observation
- **What to test**: Set `heartbeat_idle_seconds: 10`, wait idle, verify heartbeat fires
- **How**: Run `qd-evolve` with heartbeat enabled

### 5. MCP Bridge Subprocess Lifecycle
- **Why**: Real MCP server startup/shutdown; TaskStore logic is tested, but actual subprocess communication is not
- **What to test**: Configure an MCP server in `tools/mcp/*.json`, verify it starts and tools are discovered
- **How**: Run `qd-evolve` with MCP servers configured

### 6. OAT Bridge (boat/coat) Real Execution
- **Why**: In-process tool calls depend on package availability
- **What to test**: Enable boat/coat in `tools/bridge/oat.json`, verify tools load and execute
- **How**: Run `qd-evolve` with OAT bridge enabled

### 7. Multi-Agent Cross-Machine HTTP
- **Why**: Two processes on different machines; inproc transport is tested, HTTP between real processes is not
- **What to test**: Run `qd-evolve serve --agent helper` on one machine, connect from another
- **How**: Start server, use HTTP client to send tasks

### 8. Embedding Model Quality
- **Why**: Semantic search relevance depends on real embeddings; mock embedder tests structure, not quality
- **What to test**: Save conversations, recall with semantic queries, verify relevance
- **How**: Run `qd-evolve` with memory enabled, test recall accuracy

### 9. Windows Encoding (GBK/cp936)
- **Why**: `run_shell` encoding fallback needs real Windows environment with non-UTF-8 output
- **What to test**: Run a command that produces GBK output (e.g., Chinese system commands)
- **How**: On Windows with Chinese locale, run `qd-evolve` and execute shell commands

### 10. Context Window Overflow in Production
- **Why**: Compression triggers at real token counts; mock tests verify logic, but threshold calibration is manual
- **What to test**: Have a long conversation, verify compression triggers at the right point
- **How**: Run `qd-evolve` and have a long conversation, watch for "context compressed" log

## How to Update Tests

When adding new code:

1. **New pydantic model** → Add tests to the corresponding `test_*.py` file. Test defaults, validation, serialization roundtrip.

2. **New tool handler** → Add a `test_<tool>.py` in `tests/tools/`. Mock the registry and any external deps. Test success path, error path, edge cases.

3. **New agent feature** → Add tests to `tests/agent/test_agent_core.py`. Mock LLM API calls. Test the logic, not the API.

4. **New CLI command** → If non-interactive, add to `tests/cli/`. If interactive, add to the manual testing list above.

5. **New config field** → Add to `tests/core/test_config.py`. Test default value, validation, serialization.

## Test Fixtures (conftest.py)

| Fixture | Purpose |
|---------|---------|
| `minimal_settings` | Settings with test provider, no config.json |
| `registry` | Clean ToolRegistry, no discover side effects |
| `registry_with_echo` | Registry with echo tool registered |
| `providers` | ProviderRegistry from minimal_settings |
| `mock_embedder` | Fixed-vector embedder, no model loading |
| `memory_store` | Temporary SQLite + mock embedder |
| `agent_core` | Minimal Agent with provider/model set |
| `config_json` | Minimal config.json in tmp_path |
| `toolbox_json` | Minimal toolbox.json in tmp_path |