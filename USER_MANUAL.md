```markdown
# QD-Evolve User Manual

> **Version**: 0.1.9 | **Author**: Zhang Jun (zhangjun@cueb.edu.cn) | **License**: MIT

---

## Table of Contents

1. [Overview and Philosophy](#1-overview-and-philosophy)
2. [Installation and Quick Start](#2-installation-and-quick-start)
3. [Configuration System](#3-configuration-system)
4. [Five Operation Modes](#4-five-operation-modes)
5. [Core Concepts](#5-core-concepts)
6. [Tool System](#6-tool-system)
7. [Multi-Agent System (A2A)](#7-multi-agent-system-a2a)
8. [Group Chat System](#8-group-chat-system)
9. [Human Agent](#9-human-agent)
10. [Sub-Agent](#10-sub-agent)
11. [Memory System](#11-memory-system)
12. [Heartbeat and Events](#12-heartbeat-and-events)
13. [Slash Command Reference](#13-slash-command-reference)
14. [TUI Management Interface](#14-tui-management-interface)
15. [Template System](#15-template-system)
16. [Bridge System](#16-bridge-system)
17. [Skill System](#17-skill-system)
18. [Advanced Topics](#18-advanced-topics)
19. [Troubleshooting](#19-troubleshooting)
20. [Appendix](#20-appendix)

---

## 1. Overview and Philosophy

### 1.1 What is QD-Evolve?

QD-Evolve is a multi-agent AI framework supporting A2A (Agent-to-Agent) protocol, group chat, persistent memory, and an extensible tool system. It allows you to define multiple AI agents with a single JSON configuration file, let them collaborate via different transports (in‑process, HTTP, MQTT), and also allow humans to participate in conversations.

### 1.2 Design Philosophy

QD-Evolve’s design follows eight core principles (detailed in `manifesto.md`), which deeply influence every design decision of the framework:

**Principle 1: One loop, no templates.** The framework does not presuppose ReAct, Plan‑and‑Execute, or any fixed reasoning template. An agent’s execution loop is only: reason → call tools → observe → repeat. Strategy moves from code to model weights.

**Principle 2: Embrace the messy toolbox.** Tools do not need to be polished into perfect, orthogonal Lego bricks. Models can use a set of Swiss army knife‑style tools with overlapping functions and casual descriptions.

**Principle 3: Only store, do not teach how to remember.** The memory system provides only two buttons: save and recall. No forgetting curves, episodic memory, or automatic categorization. Models learn for themselves what is worth keeping, what can be forgotten, and how to search.

**Principle 4: Give it meta‑tools and let it evolve itself.** The framework gives the model wrenches (fetch web pages, save knowledge, register tools) so the agent can grow its own capabilities, rather than having humans decide when to update.

**Principle 5: Safety through physical isolation.** It does not rely on software permission checks, sandboxes, or content filtering. If the key is in the model’s hand, any lock can be picked. Real safety comes from not giving dangerous capabilities to the model.

**Principle 6: Partner, not assistant.** Agents should be able to question, suggest alternatives, reject meaningless requests, and participate in decisions. Conversation is a process where two intelligences think together.

**Principle 7: Self‑organising multi‑agent collaboration – no script.** No predefined roles (planner, executor, critic) or collaboration protocols (voting, auctions). The agent group finds its own division of labour and coordination methods. The framework only provides the ability to send messages.

**Principle 8: Give it a body, then shut up and wait.** True understanding comes from interacting with the world, not just text. Without a body, consciousness never emerges.

### 1.3 System Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI Layer                             │
│   chat │ a2a-http │ a2a-inproc │ a2a-mqtt │ gchat │ toolbox │ memory │
├─────────────────────────────────────────────────────────────┤
│                       Agent Layer                            │
│  Agent → A2AAgent → MqttAgent → GroupChatAgent              │
│  HumanAgent → MqttHumanAgent → GroupChatHuman                │
│                    → GroupChatWechatHuman                    │
├─────────────────────────────────────────────────────────────┤
│                     Infrastructure Layer                     │
│  ProviderRegistry │ ToolRegistry │ MemoryStore │ Templates  │
│  Transport (inproc/http/mqtt) │ BridgeManager │ Skills      │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Installation and Quick Start

### 2.1 Requirements

- **Python**: >= 3.13
- **MQTT mode**: requires an external Mosquitto v5 broker (optional)
- **OS**: Windows / macOS / Linux

### 2.2 Installation

```bash
# PyPI installation
pip install qd-evolve

# or with uv
uv add qd-evolve

# Install from source
git clone <repo-url>
cd qd-evolve
uv sync

# Optional: install BOAT bridge support
pip install qd-evolve[boat]
```

### 2.3 Minimal Configuration

Create `config.json` in the project root:

```json
{
    "providers": [
        {
            "name": "deepseek",
            "api_key": "sk-your-api-key",
            "base_url": "https://api.deepseek.com",
            "api": "openai-completions",
            "models": [
                {
                    "name": "deepseek-chat",
                    "context_window": 131072,
                    "max_tokens": 8192
                }
            ]
        }
    ],
    "agents_config": {
        "active_agent": "assistant",
        "agents": [
            {
                "name": "assistant",
                "description": "General assistant",
                "provider": "deepseek",
                "model": "deepseek-chat"
            }
        ]
    }
}
```

### 2.4 First Run

```bash
# Start single‑agent chat
qd-evolve chat

# After entering the chat interface, type messages to talk to the AI
# Type /help to see all available commands
# Type /quit to exit
```

---

## 3. Configuration System

### 3.1 Overview

All configuration is concentrated in a single `config.json` file. No `.env` files, no command‑line configuration tools. The configuration is validated using Pydantic models and supports sensible defaults.

### 3.2 Detailed Configuration Structure

#### 3.2.1 Top‑level Settings (Settings)

```json
{
    "log_level": "INFO",           // log level: DEBUG/INFO/WARNING/ERROR
    "compress_threshold": 0.7,     // context compression threshold (70%)
    "target_threshold": 0.5,       // compression target ratio (50%)
    "max_iterations": 50,          // max tool call iterations
    "tool_output_limit": 2000,     // tool output truncation length (characters)
    "stream": true,                // enable streaming output
    "heartbeat_idle_seconds": 30,  // heartbeat idle seconds, 0 to disable
    "providers": [...],            // LLM provider list
    "agents_config": {...},        // agent configuration
    "embeddings": {...},           // embedding model configuration
    "memory_search": {...}         // memory search configuration
}
```

#### 3.2.2 LLM Provider (ProviderConfig)

```json
{
    "name": "my-provider",
    "api_key": "sk-...",
    "base_url": "https://api.example.com",
    "api": "openai-completions",  // or "openai-response" or "anthropic"
    "models": [
        {
            "name": "model-name",
            "reasoning": false,        // whether reasoning/thinking mode is supported
            "accept": ["text"],        // accepted input types
            "context_window": 131072,
            "max_tokens": 8192
        }
    ]
}
```

**Three API types**:

| API type | Description | Use case |
|----------|-------------|----------|
| `openai-completions` | OpenAI Chat Completions API | DeepSeek, OpenAI, most compatible APIs |
| `openai-response` | OpenAI Responses API | newer OpenAI endpoints |
| `anthropic` | Anthropic Messages API | Claude family models |

#### 3.2.3 Agent Configuration (AgentEntry)

```json
{
    "name": "agent-name",
    "description": "Description of the agent, influences its behaviour and system prompt",
    "provider": "deepseek",        // specify provider (overrides global default)
    "model": "deepseek-chat",      // specify model (overrides global default)
    "server": {                    // HTTP server configuration (A2A mode)
        "host": "127.0.0.1",
        "port": 8001
    },
    "toolbox": {                   // toolbox state
        "tools": {},
        "mcp_servers": {},
        "bridges": {},
        "cli": {},
        "skills": {}
    },
    "mqtt": {                      // MQTT configuration
        "broker_host": "127.0.0.1",
        "broker_port": 1883,
        "username": "",
        "password": "",
        "tls": false
    },
    "wechat_session": "path/to/session.json"  // WeChat session persistence
}
```

Special provider values:
- `"human"` — terminal human agent
- `"wechat-human"` — WeChat‑bridged human agent

#### 3.2.4 Agent Topology (TopologyConfig)

Defines relationships between agents:

```json
{
    "topology": [
        {"from": "planner", "to": "executor", "relation": "delegates"},
        {"from": "executor", "to": "reviewer", "relation": "reports"}
    ]
}
```

#### 3.2.5 Embedding Model (EmbeddingsBackend)

```json
{
    "embeddings": [
        {
            "name": "bge-m3",
            "model_path": "BAAI/bge-m3",
            "dimension": 1024,
            "backend": "sentence-transformers"
        }
    ]
}
```

Two backends supported:
- `sentence-transformers` — HuggingFace based models
- `llama-cpp-python` — local embeddings based on llama.cpp

#### 3.2.6 Memory Search Configuration (MemorySearchConfig)

```json
{
    "memory_search": {
        "embeddings_backend": "bge-m3",
        "auto_recall": true,
        "auto_recall_top_k": 5,
        "recall_limit": 20,
        "list_all_limit": 50
    }
}
```

#### 3.2.7 Group Chat Configuration (GChatConfig)

```json
{
    "agents_config": {
        "gchat": {
            "reply_delay_min": 1.0,  // minimum reply delay (seconds)
            "reply_delay_max": 3.0   // maximum reply delay (seconds)
        }
    }
}
```

### 3.3 Configuration Validation

Configuration is automatically validated via Pydantic on load:
- Duplicate server ports are detected and cause an error
- Missing provider/model references are detected
- Type mismatches cause an immediate error on startup

---

## 4. Five Operation Modes

QD-Evolve provides five operation modes, each operating independently, with no protocol fallback.

### 4.1 Single‑Agent Chat (chat)

```bash
qd-evolve chat [--agent NAME] [--replay FILE] [--output FILE]
```

**Transport**: in‑process (no network)

**Use cases**: chatting with a single AI agent, testing tools, daily use

**Features**:
- does not start any network services
- does not register A2A tools
- supports all slash commands
- supports heartbeat and sub‑agents

### 4.2 A2A In‑Process Multi‑Agent (a2a-inproc)

```bash
qd-evolve a2a-inproc [--replay FILE] [--output FILE]
```

**Transport**: in‑process (direct calls via `InprocTransport`)

**Use cases**: testing multi‑agent collaboration in the same process, no network required

**Features**:
- loads all configured non‑human agents into the same process
- agents discover and communicate with each other via `TransportRouter`
- zero network latency
- supports A2A tools such as `delegate_to`, `send_task`, `get_task`, `cancel_task`

### 4.3 A2A HTTP Mode (a2a-http)

```bash
# Client mode (connect to remote agents)
qd-evolve a2a-http [--replay FILE] [--output FILE]

# Server mode (expose agent as HTTP service)
qd-evolve a2a-http serve [--agent NAME]
```

**Transport**: HTTP/SSE (A2A v1.0 protocol)

**Use cases**: cross‑machine multi‑agent deployment, each agent runs independently

**Features**:
- full A2A v1.0 protocol implementation
- agent discovery (`/.well-known/agent.json`)
- SSE streaming event push
- task lifecycle: submitted → working → completed/failed/canceled/input_required
- supports webhook callbacks (push notifications)
- supports full‑duplex JSON‑RPC communication

### 4.4 A2A MQTT Mode (a2a-mqtt)

```bash
# Client mode
qd-evolve a2a-mqtt [--replay FILE] [--output FILE]

# Server mode
qd-evolve a2a-mqtt serve [--agent NAME]
```

**Transport**: MQTT v5

**Use cases**: IoT scenarios, deployments requiring a broker pattern

**Features**:
- uses standard A2A JSON‑RPC over MQTT v5
- topic structure:

| Topic | Purpose |
|-------|---------|
| `$a2a/v1/discovery/{name}` | AgentCard discovery (retained message) |
| `$a2a/v1/request/{name}` | task requests |
| `$a2a/v1/response/{name}/{req_id}` | per‑request responses |
| `$a2a/v1/event/{name}` | streaming events and push notifications |

- MQTT v5 features: Response Topic, Correlation Data, User Properties, LWT, Retained Messages
- requires external Mosquitto v5 broker

### 4.5 Group Chat Mode (gchat)

```bash
qd-evolve gchat [--agent NAME]
```

**Transport**: MQTT v5 group topics

**Use cases**: WeChat‑style group conversations with multiple people + multiple AIs

**Features**:
- all configured agents join the same group chat
- AI agents automatically handle `@mentions` and reply
- terminal humans participate via interactive prompt
- WeChat humans participate via iLink bridging
- `@all` mentions everyone
- message deduplication (via `msg_id`)
- configurable reply delays (simulating human typing)

### 4.6 Mode Selection Guide

| Need | Recommended mode |
|------|------------------|
| just want to talk to an AI | `chat` |
| test multi‑agent collaboration (local) | `a2a-inproc` |
| production multi‑agent deployment (cross‑machine) | `a2a-http serve` + `a2a-http` |
| IoT / embedded scenarios | `a2a-mqtt` |
| group chat interaction | `gchat` |

---

## 5. Core Concepts

### 5.1 Agent

The agent is the core abstraction of the framework. It is an LLM‑driven reasoning engine that follows a simple loop:

```
Reason → Call Tools → Observe → Repeat
```

**Core capabilities**:
- receives user input, sends it to the LLM for a response
- executes tool calls (the LLM decides when to call which tools)
- manages conversation history
- automatic memory recall and saving
- context window compression (when history exceeds the threshold, old messages are automatically trimmed)
- multi‑provider backend support (Anthropic / OpenAI Completions / OpenAI Responses)

**Agent layering (composite pattern)**:

```
Agent (pure LLM loop, no network)
  └── A2AAgent (wraps Agent, adds A2A identity and event fan‑out)
        └── MqttAgent (wraps A2AAgent, adds MQTT v5 lifecycle)
              └── GroupChatAgent (wraps MqttAgent, adds group chat behaviour)
```

Each layer adds one concern through composition (not inheritance). External creation is done via the factory function `create_agent()` in `loader.py`.

### 5.2 Tool Registry

`ToolRegistry` is a global singleton that manages registration, discovery, and execution of all callable tools.

**Tool sources**:
1. **System tools** — `qd_evolve/tools/*.py` (auto‑discovered)
2. **User func tools** — `tools/func/*.py` (hot‑loaded)
3. **A2A tools** — registered when A2A is enabled (`delegate_to`, `send_task`, `get_task`, `cancel_task`)
4. **Bridge tools** — MCP and OAT bridge tools
5. **CLI tools** — command‑line tools defined by `tools/cli/*.yaml`
6. **Sub‑agent tools** — `create_sub_agent`, `run_sub_agent`, `get_sub_result`

**Tool execution mechanism**: tools are executed in daemon threads with a configurable timeout (default 60 seconds, tools can customise timeout + 15 seconds buffer).

### 5.3 On‑Demand Loading

To save prompt context, tools adopt an on‑demand loading strategy:

| State | Meaning | LLM visibility |
|-------|---------|----------------|
| `enabled` | enabled | only name and description visible |
| `preload` | pre‑loaded | full JSON Schema in system prompt |
| `disabled` | disabled | completely invisible |

The LLM can load full definitions via the following tools:
- `load_func(name)` — loads the full Schema of a func tool
- `load_skill(name)` — loads the content of a SKILL.md
- `load_cli(name)` — loads the full definition of a CLI tool

### 5.4 Provider Registry

Manages all configured LLM providers. Supports three API types:
- `openai-completions` — OpenAI Chat Completions API
- `openai-response` — OpenAI Responses API (newer)
- `anthropic` — Anthropic Messages API

Each provider can be configured with multiple models, each with its own context window size, maximum tokens, and reasoning/thinking capability configuration.

### 5.5 Memory Store

Persistent memory system based on SQLite + `sqlite-vec`. See [Chapter 11](#11-memory-system) for details.

### 5.6 Template System (PromptTemplateManager)

Jinja2‑based prompt template system. See [Chapter 15](#15-template-system) for details.

---

## 6. Tool System

### 6.1 Tool Types

The framework supports six tool types:

#### 6.1.1 Func Tools (Python function tools)

`.py` files located in the `tools/func/` directory. Each file registers tools using `ToolRegistry`.

**Built‑in func tools**:

| Tool name | File | Function |
|-----------|------|----------|
| `fetch` | `fetch.py` | HTTP GET/POST requests |
| `read_file` | `file_rw.py` | read file content |
| `write_file` | `file_rw.py` | write to file |
| `list_directory` | `file_rw.py` | list directory contents |
| `run_python` | `run_python.py` | execute Python code |
| `run_shell` | `run_shell.py` | execute shell commands |
| `serper_search` | `search.py` | web search |
| `serper_scrape` | `search.py` | web page scraping |

**Writing a custom Func tool**:

Create a `.py` file under `tools/func/`:

```python
"""
My custom tool
"""
from qd_evolve.tools import get_registry

def my_handler(param1: str, param2: int = 10) -> str:
    """Processing logic"""
    return f"Result: {param1} x {param2}"

def register_tools():
    registry = get_registry()
    registry.register(
        name="my_tool",
        description="Description of my custom tool",
        handler=my_handler,
        input_schema={
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "first parameter"},
                "param2": {"type": "integer", "description": "second parameter", "default": 10}
            },
            "required": ["param1"]
        }
    )
```

#### 6.1.2 CLI Tools (command‑line tools)

YAML files located in the `tools/cli/` directory. They are invoked via the `run_shell` tool.

**Example** (`tools/cli/yt-dlp.yaml`):

```yaml
name: yt-dlp
command: yt-dlp
description: "Feature‑rich command‑line audio/video downloader, supports 1000+ sites"
help_summary: |
  -h          help
  -f FORMAT   select format
  -x          extract audio
  ...
examples:
  - "Download video: yt-dlp <url>"
  - "Select best format: yt-dlp -f best <url>"
  - "Extract MP3 audio: yt-dlp -x --audio-format mp3 <url>"
```

#### 6.1.3 MCP Tools (Model Context Protocol)

External process tools loaded via the MCP Bridge. See [Chapter 16](#16-bridge-system) for details.

#### 6.1.4 OAT Tools (Open‑Agent‑Tools)

In‑process Python tools loaded via the OAT Bridge. See [Chapter 16](#16-bridge-system) for details.

#### 6.1.5 Skills

`SKILL.md` files located in the `skills/` directory. See [Chapter 17](#17-skill-system) for details.

#### 6.1.6 System Tools

Built‑in system tools:

| Tool name | Function |
|-----------|----------|
| `load_func` | on‑demand load func tool definition |
| `load_skill` | on‑demand load skill content |
| `load_cli` | on‑demand load CLI tool definition |
| `list_providers` | list available LLM providers and models |
| `get_my_config` | view current agent configuration |
| `update_my_config` | update current agent’s provider/model/description |
| `recall_memory` | search historical conversation memory |
| `hot_loading_mcp` | activate MCP server at runtime |
| `create_sub_agent` | create a sub‑agent |
| `run_sub_agent` | run a sub‑agent task |
| `get_sub_result` | query sub‑agent result |
| `delegate_to` | synchronously delegate a task to another agent (A2A) |
| `send_task` | asynchronously send a task to another agent (A2A) |
| `get_task` | query asynchronous task status (A2A) |
| `cancel_task` | cancel an asynchronous task (A2A) |

### 6.2 Tool State Management

Each agent has an independent toolbox configuration stored in `config.json` under `agents[*].toolbox`.

**Management methods**:
1. via the `qd-evolve toolbox` TUI (recommended)
2. via the command line `qd-evolve toolbox --toggle <name>`
3. by directly editing `config.json`

**State transitions**:
```
Tools/CLI/Skills: disabled → enabled → preload → disabled
Bridges/MCP:      disabled → enabled → disabled
```

---

## 7. Multi‑Agent System (A2A)

### 7.1 A2A Protocol Overview

QD-Evolve implements the A2A v1.0 protocol. Agents communicate through a unified interface, with AI and humans using the same protocol.

### 7.2 Core Data Models

**AgentCard** — agent identity document:
```json
{
    "name": "agent-name",
    "description": "What this agent does",
    "url": "http://host:port/",
    "capabilities": {
        "streaming": true,
        "push_notifications": true
    }
}
```

**Task** — task lifecycle:
```
submitted → working → completed
                    → failed
                    → canceled
                    → input_required (human agent)
```

### 7.3 A2A Tools in Detail

#### delegate_to (synchronous delegation)

Blocking call. Sends a task to a target agent and waits for completion, then returns the result.

```
Parameters:
  - agent_name: target agent name (required)
  - task: task description (required)
  - timeout: timeout in seconds (optional)

Returns: full response from the target agent
Restriction: cannot delegate to a human agent
```

#### send_task (asynchronous sending)

Non‑blocking call. Sends a task and immediately returns a task_id.

```
Parameters:
  - agent_name: target agent name (required)
  - task: task description (required)

Returns: {"task_id": "...", "status": "submitted"}
```

#### get_task (query task status)

Queries the status and result of an asynchronous task.

```
Parameters:
  - task_id: task ID (required)

Returns: {"task_id": "...", "status": "working|completed|failed", "result": "..."}
```

#### cancel_task (cancel task)

Cancels an unfinished task.

```
Parameters:
  - task_id: task ID (required)

Returns: cancellation confirmation
```

### 7.4 Transport Layer

#### InprocTransport (in‑process)

- Directly calls the target agent’s `run()` method
- Zero network overhead
- Asynchronous execution for AI agents via `asyncio.to_thread`
- Immediately returns `input_required` for human agents

#### HttpTransport (HTTP/SSE)

- Sends tasks via aiohttp JSON‑RPC calls
- `send_stream` receives intermediate events via SSE
- Supports webhook callbacks for push notifications
- Automatically computes callback URLs

#### MqttTransport (MQTT v5)

- Uses standard A2A JSON‑RPC over MQTT
- Leverages MQTT v5 Response Topic and Correlation Data for request‑response matching
- Service discovery via retained messages
- Offline detection via LWT

#### TransportRouter

```python
# Automatic routing: local agent → InprocTransport, remote agent → remote Transport
router = TransportRouter(inproc=inproc_transport, remote=http_transport)
# router automatically chooses the correct Transport
```

### 7.5 Server Mode Deployment

#### HTTP Server Mode

```bash
qd-evolve a2a-http serve --agent my-agent
```

Starts an independent HTTP server, exposing the specified agent as an A2A service:
- `POST /` — JSON‑RPC endpoint
- `GET /.well-known/agent.json` — AgentCard discovery
- Supports SSE streaming responses
- Supports webhook push notifications

#### MQTT Server Mode

```bash
qd-evolve a2a-mqtt serve --agent my-agent
```

Registers the agent with the MQTT broker:
- Publishes AgentCard to discovery topic (retained message)
- Subscribes to request topic
- Sets LWT for offline detection
- Pushes streaming updates via event topics

---

## 8. Group Chat System

### 8.1 Overview

Group chat simulates a WeChat‑style multi‑person + multi‑AI group conversation experience. All configured agents join the same group.

### 8.2 Message Flow

```
MQTT Broker
  └── $a2a/v1/group/{agent_name}/chat
        ├── AI Agent A publishes messages
        ├── AI Agent B publishes messages
        ├── Human C (terminal) publishes messages
        └── Human D (WeChat) publishes messages
```

### 8.3 Mention Mechanism

- `@agent_name` — mentions a specific agent
- `@all` — mentions everyone
- AI agents only process messages containing `@mention`
- Each agent independently determines whether it is mentioned

### 8.4 Message Deduplication

`GroupChatAgent` maintains a set of seen message IDs:
- maximum cache 10,000 entries
- after exceeding, prunes to 5,000 entries
- ensures each message is processed only once

### 8.5 Reply Delay

To simulate a natural group chat feeling, AI agents wait a random delay before replying:

```json
{
    "gchat": {
        "reply_delay_min": 1.0,  // minimum delay (seconds)
        "reply_delay_max": 3.0   // maximum delay (seconds)
    }
}
```

### 8.6 Three Types of Group Chat Participants

#### AI Agent (GroupChatAgent)

```bash
# if the default agent is of AI type
qd-evolve gchat

# specify an AI agent
qd-evolve gchat --agent my-bot
```

#### Terminal Human (GroupChatHuman)

- An agent configured with `"provider": "human"` acts as a terminal human
- Sees real‑time group messages in the terminal
- Types messages to publish to the group
- Messages are displayed in colour, with different colours for different agents

#### WeChat Human (GroupChatWechatHuman)

- An agent configured with `"provider": "wechat-human"`
- Requires logging into WeChat via QR code first
- Messages are bidirectionally forwarded between WeChat and MQTT via the iLink bridge

---

## 9. Human Agent

### 9.1 Overview

The human agent implements the same `AgentProtocol` interface as the AI agent. For the transport layer, AI and humans are transparent – the difference is only in how they are handled.

### 9.2 Terminal Human (HumanAgent)

```json
{
    "name": "human-user",
    "description": "Human operator",
    "provider": "human",
    "model": ""
}
```

**Workflow**:
1. An AI agent sends a task to the human via `send_task`
2. The task is created with status `input_required`
3. The terminal displays the task content and its source
4. The human types a response
5. The response is pushed back to the caller via webhook

### 9.3 MQTT Human (MqttHumanAgent)

Similar to the terminal human, but communicates over MQTT:

1. Receives a task on the MQTT request topic
2. Creates an `input_required` task
3. The human response is pushed back to the caller’s event topic via webhook

### 9.4 WeChat Human (GroupChatWechatHuman)

Bridges via the WeChat iLink protocol:
1. Long‑polls WeChat messages
2. Parses `@mentions`
3. Publishes to the MQTT group chat topic
4. Receives group messages and forwards them to WeChat

---

## 10. Sub‑Agent

### 10.1 Overview

Sub‑agents are lightweight in‑process worker agents, created at runtime by a parent agent. They are:

- **Lightweight**: no persistent memory, no heartbeat, no network server
- **Inheriting**: inherit the parent agent’s provider/model/tools/skills/CLI preload set
- **Single‑task**: handle only one task at a time (reject new tasks when busy)
- **Ephemeral**: exist inside the parent process and are destroyed when the parent exits

### 10.2 Usage

Agents (LLMs) can use sub‑agents via the following tools:

#### create_sub_agent

```
Parameters:
  - name: sub‑agent name (required)
  - description: purpose description (optional, used for prompt customisation)

Effect: creates an Agent instance that inherits the parent agent’s configuration
```

#### run_sub_agent

```
Parameters:
  - name: sub‑agent name (required)
  - task: task description (required)
  - reset: whether to reset conversation history (optional, default false)

Returns: {"task_id": "...", "agent": "...", "status": "running"}
```

#### get_sub_result

```
Parameters:
  - task_id: task ID (required)

Returns: {"task_id": "...", "status": "running|done|error", "result": "..."}
```

### 10.3 Result Push Mechanism

When the parent agent enters idle waiting, results completed by sub‑agents are automatically pushed into the parent agent’s conversation flow as a user message. Uses `ContextVar` to ensure thread correctness.

### 10.4 Use Cases

- Handling multiple independent tasks in parallel
- Letting an agent run multiple conversations that require different contexts simultaneously
- Isolating experimental operations

---

## 11. Memory System

### 11.1 Architecture

The memory system is based on SQLite + `sqlite-vec` vector extension:

```
SQLite database (memory.db)
  ├── memories table — metadata and content
  └── memory_vec table — BGE‑M3 vector embeddings
```

### 11.2 Operations

#### Save

Automatically saved after each agent response:
- `user_msg` — user message
- `assistant_msg` — assistant response
- `process` — tool call process
- `content` — combined content (used for vector embedding)
- `session_id` — session identifier
- `key` — ISO timestamp

#### Recall

Three retrieval modes:

1. **Semantic search** — vector similarity search using the `query` parameter
2. **Keyword search** — SQL LIKE matching using the `keywords` parameter
3. **Time‑range browsing** — using the `time_range` parameter

**Time range formats**:

| Value | Meaning |
|-------|---------|
| `last_session` | all memories from the most recent session that is not the current one |
| `today` | today |
| `yesterday` | yesterday |
| `this_week` | this week |
| `last_week` | last week |
| `this_month` | this month |
| `last_month` | last month |
| `last_Nd` | last N days (e.g. `last_3d`) |
| `YYYY-MM-DD~YYYY-MM-DD` | date range |

#### Auto Recall

Before each LLM call, the system automatically performs a semantic search (based on the user message) and injects the most relevant memories into the system prompt. Deduplication is handled by `RecalledMemoryRegistry`.

### 11.3 recall_memory Tool

```
Parameters:
  - query: semantic query (optional)
  - keywords: keywords (optional)
  - time_range: time range (optional)
  - limit: number of results (optional)

Returns: formatted list of memories, including session information, user/assistant messages, and relevance scores
```

### 11.4 Context Compression

When conversation history exceeds the configured threshold of the context window (default 70%):

1. Remove the oldest groups of user/assistant/tool messages
2. Continue until the token count falls to the target threshold (default 50%)
3. Compressed messages are kept in memory as “processed”

### 11.5 Browser (Memory TUI)

```bash
qd-evolve memory [--agent NAME]        # command‑line list mode
qd-evolve memory --tui [--agent NAME]  # TUI browse mode
```

TUI features:
- `/` semantic search
- `t` time‑range filter
- `l` toggle number of displayed items (5/10/20/50/100)
- arrow keys/jk navigation
- detail panel showing full memory content

---

## 12. Heartbeat and Events

### 12.1 Heartbeat Mechanism

The heartbeat allows an agent to proactively start a conversation after a long idle period.

**Workflow**:
1. The agent has been idle for `heartbeat_idle_seconds` (default 30 seconds)
2. A heartbeat prompt is sent to the LLM
3. The LLM can reply with `"."` to remain silent
4. The LLM can also reply with a proactive conversation starter

**Configuration**:
```json
{
    "heartbeat_idle_seconds": 30,  // global default
    "agents": [
        {
            "name": "my-agent",
            "heartbeat_idle_seconds": 60  // agent‑level override
        }
    ]
}
```

Set to `0` to disable the heartbeat.

### 12.2 Event System

Agents generate events during execution:

| Event type | Trigger |
|------------|---------|
| `iteration_start` | start of each LLM call iteration |
| `status` | status update (e.g. “thinking…”) |
| `print` | output content |
| `error` | an error occurs |
| `completed` | task finished |
| `heartbeat_silent` | no response after heartbeat |
| `heartbeat_*` | heartbeat‑related events |
| `human_task` | human receives a new task |
| `task_completed` | human completes a task |
| `sub_agent_result` | sub‑agent result is available |
| `tool_activated` | a tool is activated |

### 12.3 Event Subscription and Push

- `A2AAgent` supports multiple event subscribers via `subscribe_events()`
- Events are fanned out to all subscribers via `asyncio.Queue`
- HTTP mode pushes via SSE
- MQTT mode pushes via event topics
- Clients reconnect to the event stream using `resubscribe`

---

## 13. Slash Command Reference

The following commands are available in chat mode:

| Command | Description |
|---------|-------------|
| `/quit` | exit the program |
| `/reset` | reset the current conversation |
| `/help` | display help information |
| `/models` | interactively switch models |
| `/agents` | list all agents (with online status) |
| `/tools` | list available tools |
| `/skills` | list available skills |
| `/cli` | list CLI tools |
| `/status` | show current agent status (preload/loaded categories) |
| `/memory` | show recent memories |
| `/recall <query>` | search memories |
| `/compress` | manually trigger context compression |
| `/load` | manually load tools/skills/CLI |
| `/clear` | clear screen |

### Interactive Model Switching (`/models`)

```
1. deepseek-chat       (DeepSeek)
2. gpt-4o              (OpenAI)
3. claude-sonnet-4-6   (Anthropic)
Enter number to switch model >
```

### Interactive Agent Switching (`/agents`)

```
1. assistant            [AI]     ✓ online  inproc
2. reviewer             [AI]     ✓ online  inproc
3. human-approver       [HUMAN]  ✓ online  inproc
Enter number to switch conversation target >
```

---

## 14. TUI Management Interface

### 14.1 Toolbox Management (Toolbox TUI)

```bash
qd-evolve toolbox [--agent NAME]
```

**Layout**:
```
┌──────────────┬──────────────────────────────────┐
│ Category panel │ Tool panel                       │
│              │                                  │
│ System Tools │ ✓ fetch        HTTP request tool │
│ Func Tools   │ P run_shell    Execute shell cmd │
│ Bridge: mcp  │ ✗ unused_tool  disabled tool     │
│ Bridge: oat  │ ...                              │
│ CLI Tools    │                                  │
│ Skills       │                                  │
└──────────────┴──────────────────────────────────┘
```

**Shortcuts**:

| Key | Function |
|-----|----------|
| `e` | toggle enable/disable |
| `p` | toggle preload state (three‑state toggle) |
| `space` | expand/collapse Bridge group |
| `s` | collapse all Bridge groups |
| `/` | filter tool names |
| `tab` | switch left/right panels |
| `?` | show help |
| `q` | exit |

**Status markers**:
- `[✓]` — enabled
- `[P]` — preloaded
- `[✗]` — disabled

### 14.2 Memory Browser (Memory TUI)

```bash
qd-evolve memory --tui [--agent NAME]
```

**Shortcuts**:

| Key | Function |
|-----|----------|
| `/` | semantic search |
| `t` | time‑range filter |
| `l` | toggle number of displayed items (5→10→20→50→100) |
| `r` | refresh |
| `↑/↓` or `j/k` | navigation |
| `q` | exit |

### 14.3 Command‑line Toolbox

```bash
# Quickly toggle tool state
qd-evolve toolbox --toggle fetch
qd-evolve toolbox --toggle run_shell
qd-evolve toolbox --toggle mcp:boat

# Interactive shell
qd-evolve toolbox
> ls              # list all tools (paginated)
> toggle fetch    # toggle fetch state
> enable run_shell
> disable old_tool
> preload important_skill
> help
> quit
```

---

## 15. Template System

### 15.1 Overview

The framework uses Jinja2 templates to render system prompts and user messages. Templates support falling back from a user‑customised directory to the built‑in directory.

### 15.2 Template Loading

**Loading order**:
1. `templates/` (user‑customised directory) — highest priority
2. `qd_evolve/_templates/` (built‑in directory) — fallback

**Template suffix**: `.j2`

### 15.3 Context Variables

Default variables accessible to all templates:

| Variable | Description |
|----------|-------------|
| `current_date` | current date (YYYY-MM-DD) |

Specific templates receive additional variables based on the use case (e.g. agent name, description, tool list, memories, runtime environment, etc.).

### 15.4 Available Templates

| Template name | Purpose |
|---------------|---------|
| `system.j2` | default system prompt |
| `chat.j2` | chat message format |
| `subagent.j2` | sub‑agent system prompt |
| `heartbeat.j2` | heartbeat prompt |
| `group-message.j2` | group chat message format |
| `a2a-heartbeat.j2` | A2A mode heartbeat prompt |

### 15.5 Custom Templates

Create a `templates/` directory in the project root and place `.j2` files with the same names to override built‑in templates:

```
project/
  templates/
    system.j2     # override default system prompt
    chat.j2       # override chat format
```

---

## 16. Bridge System

### 16.1 Bridge Framework

The Bridge system integrates external tool sources via a generic framework. Each bridge type is automatically discovered and registered via `tools/bridge/_*.py` modules.

**Bridge lifecycle**:
```
discover → connect → use tools → disconnect
```

**Core components**:
- `Bridge` (Protocol) — each Bridge instance manages its configuration and registered tools
- `BridgeSpec` — named specification containing discover/connect/disconnect
- `BridgeEntry` — summary for toolbox listings
- `BridgeManager` — singleton manager

### 16.2 MCP Bridge (Model Context Protocol)

**Location**: `tools/bridge/_mcp.py`

Runs external MCP servers as subprocesses, discovers and registers their tools.

**Configuration** (`tools/mcp/<name>.json`):

```json
{
    "mcpServers": {
        "my-server": {
            "command": "npx",
            "args": ["-y", "@my/mcp-server"],
            "env": {
                "API_KEY": "$MY_API_KEY"
            },
            "timeout": 30000
        }
    }
}
```

**Features**:
- supports multiple transports: stdio, SSE, StreamableHTTP, WebSocket
- environment variable substitution (`$VAR` or `${VAR}`)
- connects all servers in parallel (using ThreadPoolExecutor)
- tool names prefixed with `[server_name]` to avoid conflicts
- skips servers when environment variables are missing (no error)

**Runtime loading**:
```bash
# can also be done via the LLM‑callable hot_loading_mcp tool
# in chat, ask the AI to add a new MCP server using hot_loading_mcp
```

### 16.3 OAT Bridge (Open‑Agent‑Tools)

**Location**: `tools/bridge/_oat.py`

Imports Python packages in‑process, zero subprocess overhead.

**Configuration** (`tools/bridge/oat.json`):

```json
[
    {
        "name": "boat-core",
        "package": "basic_open_agent_tools",
        "loadout": "core"
    }
]
```

**Features**:
- directly imports and executes Python functions
- automatically converts Google ADK Schema to OpenAI Schema
- automatically normalises return values

### 16.4 OAT JSON Shim

**Location**: `tools/bridge/_oat_json.py`

Provides JSON file manipulation tools for the OAT Bridge:

```
read_json_file, get_json_value_at_path, get_json_keys,
get_json_structure, count_json_items, search_json_keys,
write_json_file, update_json_value_at_path,
delete_json_key_at_path, append_to_json_array
```

---

## 17. Skill System

### 17.1 What is a Skill?

A skill is a Markdown file located at `skills/<name>/SKILL.md`, containing YAML front matter and instructional content. Skills are injected into the agent’s system prompt to guide the LLM on how to handle specific tasks.

### 17.2 Skill Structure

```
skills/
  my-skill/
    SKILL.md          # skill definition (required)
    _meta.json        # version info (optional)
    scripts/          # helper scripts (optional)
    references/       # reference material (optional)
```

**SKILL.md format**:

```markdown
---
name: my-skill
description: What this skill does
version: "1.0.0"
tags: [web, search]
---

# Skill content

Detailed guidance for the skill goes here...
```

### 17.3 Built‑in Skills

| Skill name | Function |
|------------|----------|
| `baidu-search` | web search via Baidu AI Search API |
| `search-tools` | search for and recommend new tools |
| `install-and-register-tools` | install and register new tools |
| `register-cli` | analyse `--help` output and register CLI tools |
| `self-improvement` | record learnings, errors, and feature requests for continuous improvement |

### 17.4 Using Skills

- **Preload (preload)**: skill content is injected into the system prompt at startup
- **On‑demand (enabled)**: the LLM sees a skill summary and loads the content on demand via `load_skill(name)`
- **Disabled (disable)**: the skill is invisible

---

## 18. Advanced Topics

### 18.1 Replay Mode

Record and replay conversations for automated testing:

```bash
# Record a conversation
qd-evolve chat --output session.txt

# Replay a conversation
qd-evolve chat --replay session.txt
```

`ReplayInput` reads pre‑recorded input from a file, and `TeeWriter` writes simultaneously to the terminal and a file.

### 18.2 Token Statistics

After each response, the following are displayed:
- input/output tokens for this turn
- cumulative input/output tokens
- context window usage percentage

### 18.3 Runtime Environment Information

When an agent starts for the first time, it automatically collects runtime context:
- OS and Python version
- virtual environment and package manager (uv/pip)
- shell type
- Git repository status
- proxy settings

This information is injected into the system prompt as Markdown, letting the LLM understand its operating environment.

### 18.4 Error Handling

- Tool execution timeout: returns a timeout error string, does not interrupt the loop
- Tool execution exception: returns the exception information as a string
- Non‑zero exit code (`run_shell`/`run_python`): not treated as an error, output is returned normally
- Encoding handling: multi‑level fallback encoding detection (UTF-8 → GBK → GB2312 → Latin‑1)

### 18.5 Concurrency Safety

- `Agent.run()` uses a `threading.Lock` (reentrant) to serialise concurrent calls
- Tools execute in daemon threads, inheriting context via `contextvars`
- Sub‑agents maintain thread‑safe current agent name via `ContextVar`

### 18.6 Logging

Log files are located in the `logs/` directory, with filenames containing timestamps:
```
logs/qd_evolve_20260115_143052.log
```

- `SharedFileHandler`: flushes every log line, supports concurrent `tail -f`
- File level: DEBUG
- Console level: ERROR (stderr only)

---

## 19. Troubleshooting

### 19.1 Common Issues

#### Q: “Provider not configured” on startup

Check `config.json`:
- ensure the `providers` array is not empty
- ensure the agent’s `provider` name matches a name in providers
- ensure `api_key` is set and valid

#### Q: MQTT mode cannot connect

- Verify that the Mosquitto v5 broker is running: `mosquitto -v`
- Check the broker_host and broker_port configuration
- If using TLS, ensure certificate paths are correct

#### Q: Tools do not appear in the agent’s tool list

- Check the toolbox TUI: `qd-evolve toolbox`
- Verify the tool state is not `disabled`
- Verify that func tool files are in the `tools/func/` directory

#### Q: Memory search returns no results

- Verify that the `memory.db` file exists (after at least one conversation)
- Check that the embedding model configuration is correct
- Try keyword search instead of semantic search

#### Q: Context window overflow

- The framework automatically compresses (when `compress_threshold` is exceeded)
- Manual trigger: `/compress` in chat
- Use `/reset` to reset the conversation
- Adjust the `compress_threshold` and `target_threshold` settings

### 19.2 Log Analysis

```bash
# View the most recent log
ls -t logs/ | head -1 | xargs cat

# Follow logs in real time
tail -f logs/qd_evolve_*.log

# Filter errors
grep "ERROR" logs/qd_evolve_*.log
```

---

## 20. Appendix

### 20.1 Project Structure

```
qd-evolve/
├── qd_evolve/              # main Python package
│   ├── __init__.py         # version number
│   ├── chat_cli.py         # chat CLI (main entry)
│   ├── gchat_cli.py        # group chat CLI
│   ├── mqtt_cli.py         # MQTT A2A CLI
│   ├── a2a_cli.py          # HTTP A2A CLI
│   ├── a2a_inproc_cli.py   # in‑process A2A CLI
│   ├── cli_tools.py        # CLI tool registry
│   ├── cli_utils.py        # CLI utilities
│   ├── skills.py           # skill registry
│   ├── toolbox_tui.py      # toolbox TUI
│   ├── memory_tui.py       # memory browser TUI
│   ├── core/               # core infrastructure
│   │   ├── config.py       # configuration models (Pydantic)
│   │   ├── registry.py     # tool registry
│   │   ├── providers.py    # LLM providers
│   │   ├── toolbox.py      # toolbox state management
│   │   ├── memory.py       # memory storage
│   │   ├── prompts.py      # template management
│   │   └── logger.py       # logging
│   ├── agent/              # agent layer
│   │   ├── agent.py        # core Agent class
│   │   ├── a2a.py          # A2A data models
│   │   ├── a2a_agent.py    # A2A agent wrapper
│   │   ├── a2a_tools.py    # A2A tools
│   │   ├── protocol.py     # agent protocol interface
│   │   ├── registry.py     # agent registry
│   │   ├── loader.py       # factory functions
│   │   ├── server.py       # HTTP A2A server
│   │   ├── transport.py    # transport layer
│   │   ├── mqtt_agent.py   # MQTT agent wrapper
│   │   ├── mqtt_transport.py # MQTT transport
│   │   ├── mqtt_human_agent.py # MQTT human
│   │   ├── human_agent.py  # human agent
│   │   ├── group_chat_agent.py    # group chat AI
│   │   ├── group_chat_transport.py # group chat transport
│   │   ├── group_chat_human.py    # group chat terminal human
│   │   └── group_chat_wechat_human.py # group chat WeChat human
│   ├── tools/              # tool modules
│   │   ├── tool_loader.py      # func tool loader
│   │   ├── hot_loading_mcp.py  # MCP hot‑loading
│   │   ├── skill_loader.py     # skill loader
│   │   ├── cli_loader.py       # CLI loader
│   │   ├── config_manager.py   # configuration management + sub‑agents
│   │   └── recall_memory.py    # memory recall tool
│   ├── bridge/             # WeChat bridge
│   │   └── wechat_clawbot_client.py # iLink client
│   ├── utils/              # utility functions
│   │   ├── adk_output.py   # ADK output normalisation
│   │   └── adk_schema.py   # ADK Schema conversion
│   └── _templates/         # built‑in Jinja2 templates
├── tools/                  # user tools
│   ├── func/               # Python function tools
│   │   ├── fetch.py
│   │   ├── file_rw.py
│   │   ├── run_python.py
│   │   ├── run_shell.py
│   │   └── search.py
│   ├── cli/                # CLI tool definitions
│   │   └── yt-dlp.yaml
│   ├── mcp/                # MCP server configurations
│   └── bridge/             # Bridge configurations
│       ├── _mcp.py
│       ├── _oat.py
│       └── _oat_json.py
├── skills/                 # skills
│   ├── baidu-search/
│   ├── search-tools/
│   ├── install-and-register-tools/
│   ├── register-cli/
│   ├── self-improvement/
│   └── skill-creator/
├── templates/              # user‑customised templates (override built‑in)
├── tests/                  # tests
├── config.json             # configuration file
├── memory.db               # memory database (auto‑generated)
├── pyproject.toml          # project build configuration
├── README.md               # English README
├── README_zh.md            # Chinese README
├── DESIGN.md               # design document
├── manifesto.md            # design philosophy manifesto
└── CLAUDE.md               # AI assistant behaviour specification
```

### 20.2 Configuration Quick Reference

```json
{
    "log_level": "INFO",
    "compress_threshold": 0.7,
    "target_threshold": 0.5,
    "max_iterations": 50,
    "tool_output_limit": 2000,
    "stream": true,
    "heartbeat_idle_seconds": 30,
    "providers": [{
        "name": "...",
        "api_key": "...",
        "base_url": "...",
        "api": "openai-completions | openai-response | anthropic",
        "models": [{
            "name": "...",
            "reasoning": false,
            "context_window": 131072,
            "max_tokens": 8192
        }]
    }],
    "default_provider": "...",
    "default_model": "...",
    "agents_config": {
        "active_agent": "...",
        "agents": [{
            "name": "...",
            "description": "...",
            "provider": "...",
            "model": "...",
            "server": {"host": "127.0.0.1", "port": 8001},
            "toolbox": {
                "tools": {"tool_name": "enabled|preload|disabled"},
                "mcp_servers": {"server_name": "enabled|disabled"},
                "bridges": {"bridge_name": "enabled|disabled"},
                "cli": {"cli_name": "enabled|preload|disabled"},
                "skills": {"skill_name": "enabled|preload|disabled"}
            },
            "mqtt": {
                "broker_host": "127.0.0.1",
                "broker_port": 1883
            }
        }],
        "topology": [
            {"from": "agentA", "to": "agentB", "relation": "delegates"}
        ],
        "gchat": {
            "reply_delay_min": 1.0,
            "reply_delay_max": 3.0
        }
    },
    "embeddings": [{
        "name": "...",
        "model_path": "...",
        "dimension": 1024,
        "backend": "sentence-transformers | llama-cpp-python"
    }],
    "memory_search": {
        "embeddings_backend": "...",
        "auto_recall": true,
        "auto_recall_top_k": 5,
        "recall_limit": 20,
        "list_all_limit": 50
    }
}
```

### 20.3 CLI Command Quick Reference

```bash
# Single‑agent chat
qd-evolve chat [--agent NAME] [--replay FILE] [--output FILE]

# A2A HTTP
qd-evolve a2a-http [--replay FILE] [--output FILE]
qd-evolve a2a-http serve [--agent NAME]

# A2A in‑process
qd-evolve a2a-inproc [--replay FILE] [--output FILE]

# A2A MQTT
qd-evolve a2a-mqtt [--replay FILE] [--output FILE]
qd-evolve a2a-mqtt serve [--agent NAME]

# Group chat
qd-evolve gchat [--agent NAME]

# Toolbox management
qd-evolve toolbox [--agent NAME]          # interactive shell
qd-evolve toolbox --tui [--agent NAME]    # TUI interface
qd-evolve toolbox --toggle <name> [--agent NAME]  # quick toggle

# Memory browsing
qd-evolve memory [--agent NAME]           # command‑line list
qd-evolve memory --tui [--agent NAME]     # TUI interface
```

### 20.4 Technology Stack

| Component | Technology |
|-----------|------------|
| configuration management | Pydantic |
| CLI framework | Typer + Rich |
| interactive terminal | prompt‑toolkit |
| TUI interface | Textual |
| template engine | Jinja2 |
| LLM SDK | Anthropic SDK + OpenAI SDK |
| vector embeddings | sqlite-vec + BGE‑M3 |
| MQTT client | aiomqtt |
| HTTP server | aiohttp |
| MCP protocol | mcp |
| data validation | defusedxml |
| network download | yt-dlp |
| WeChat bridge | iLink ClawBot (aiohttp + qrcode + Pillow) |

---

> **Epilogue**: QD-Evolve is a framework about “letting go”. It trusts the capabilities of models, trusts emergent intelligence, and trusts the ultimate constraints of the physical world. Using it means choosing to be a partner to agents, not a master. Give them tools, then step back and see what happens.
>
> *“Let go of control. Embrace emergence. Let the agent become itself.”*
```