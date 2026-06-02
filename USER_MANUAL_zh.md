# QD-Evolve 用户手册

> **版本**: 0.1.9 | **作者**: 张俊 (zhangjun@cueb.edu.cn) | **许可证**: MIT

---

## 目录

1. [概述与哲学](#1-概述与哲学)
2. [安装与快速开始](#2-安装与快速开始)
3. [配置系统](#3-配置系统)
4. [五大运行模式](#4-五大运行模式)
5. [核心概念](#5-核心概念)
6. [工具系统](#6-工具系统)
7. [多智能体系统 (A2A)](#7-多智能体系统-a2a)
8. [群聊系统](#8-群聊系统)
9. [人类智能体](#9-人类智能体)
10. [子智能体](#10-子智能体)
11. [记忆系统](#11-记忆系统)
12. [心跳与事件](#12-心跳与事件)
13. [斜杠命令参考](#13-斜杠命令参考)
14. [TUI 管理界面](#14-tui-管理界面)
15. [模板系统](#15-模板系统)
16. [Bridge 桥接系统](#16-bridge-桥接系统)
17. [技能系统](#17-技能系统)
18. [高级主题](#18-高级主题)
19. [故障排除](#19-故障排除)
20. [附录](#20-附录)

---

## 1. 概述与哲学

### 1.1 什么是 QD-Evolve？

QD-Evolve 是一个多智能体 AI 框架，支持 A2A（Agent-to-Agent）协议、群聊、持久记忆和可扩展工具系统。它让你可以用一个 JSON 配置文件定义多个 AI 智能体，让它们通过不同的传输方式（进程内、HTTP、MQTT）相互协作，也可以让人类参与到对话中。

### 1.2 设计哲学

QD-Evolve 的设计遵循八条核心原则（详见 `manifesto.md`），这些原则深刻影响了框架的每一个设计决策：

**原则一：一个循环，没有模板。** 框架不预设 ReAct、Plan-and-Execute 或任何固定的推理模板。智能体的运行循环只有：推理→调用工具→观察→重复。策略从代码转移到模型权重中。

**原则二：拥抱混乱的工具箱。** 工具不需要被打磨成完美、正交的乐高积木。模型可以使用功能重叠、描述随意的一组瑞士军刀式工具。

**原则三：只存储，不教如何记忆。** 记忆系统只提供两个按钮：保存和回忆。没有遗忘曲线、情节记忆或自动分类。模型自己学会什么值得保留、什么可以遗忘、如何搜索。

**原则四：给元工具让它自己进化。** 框架给模型提供扳手（抓取网页、保存知识、注册工具），让模型自行增长能力，而不是由人类决定何时更新。

**原则五：通过物理隔离实现安全。** 不依赖软件权限检查、沙箱或内容过滤。如果钥匙在模型手里，任何锁都能被撬开。真正的安全来自不把危险的能力交给模型。

**原则六：伙伴，而非助手。** 智能体应该能够反问、提出替代方案、拒绝无意义的要求、参与决策。对话是两个智能一起思考的过程。

**原则七：自组织多智能体协作——没有剧本。** 不预设角色（规划者、执行者、批评者）和协作协议（投票、拍卖）。智能体群体自己找到分工和协调方式。框架只提供发送消息的能力。

**原则八：给它身体，然后闭嘴等待。** 真正的理解来自与世界的交互，而不仅仅是文本。没有身体，意识永远不会出现。

### 1.3 系统架构一览

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI 层                                │
│   chat │ a2a-http │ a2a-inproc │ a2a-mqtt │ gchat │ toolbox │ memory │
├─────────────────────────────────────────────────────────────┤
│                      智能体层                                │
│  Agent → A2AAgent → MqttAgent → GroupChatAgent              │
│  HumanAgent → MqttHumanAgent → GroupChatHuman                │
│                    → GroupChatWechatHuman                    │
├─────────────────────────────────────────────────────────────┤
│                      基础设施层                               │
│  ProviderRegistry │ ToolRegistry │ MemoryStore │ Templates  │
│  Transport (inproc/http/mqtt) │ BridgeManager │ Skills      │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 安装与快速开始

### 2.1 环境要求

- **Python**: >= 3.13
- **MQTT 模式**: 需要外部 Mosquitto v5 broker（可选）
- **操作系统**: Windows / macOS / Linux

### 2.2 安装

```bash
# PyPI 安装
pip install qd-evolve

# 或使用 uv
uv add qd-evolve

# 从源码安装
git clone <repo-url>
cd qd-evolve
uv sync

# 可选：安装 BOAT 桥接支持
pip install qd-evolve[boat]
```

### 2.3 最小配置

在项目根目录创建 `config.json`：

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
                "description": "通用助手",
                "provider": "deepseek",
                "model": "deepseek-chat"
            }
        ]
    }
}
```

### 2.4 第一次运行

```bash
# 启动单智能体聊天
qd-evolve chat

# 进入聊天界面后，输入消息即可与 AI 对话
# 输入 /help 查看所有可用命令
# 输入 /quit 退出
```

---

## 3. 配置系统

### 3.1 概述

所有配置集中在单个 `config.json` 文件中。没有 `.env` 文件，没有命令行配置工具。配置使用 Pydantic 模型进行验证，支持合理的默认值。

### 3.2 配置结构详解

#### 3.2.1 顶层设置 (Settings)

```json
{
    "log_level": "INFO",           // 日志级别: DEBUG/INFO/WARNING/ERROR
    "compress_threshold": 0.7,    // 上下文压缩阈值（70%）
    "target_threshold": 0.5,      // 压缩目标比例（50%）
    "max_iterations": 50,         // 最大工具调用迭代次数
    "tool_output_limit": 2000,    // 工具输出截断长度（字符）
    "stream": true,               // 是否启用流式输出
    "heartbeat_idle_seconds": 30, // 心跳检测空闲秒数，0 禁用
    "providers": [...],           // LLM 提供商列表
    "agents_config": {...},       // 智能体配置
    "embeddings": {...},          // 嵌入模型配置
    "memory_search": {...}        // 记忆搜索配置
}
```

#### 3.2.2 LLM 提供商 (ProviderConfig)

```json
{
    "name": "my-provider",
    "api_key": "sk-...",
    "base_url": "https://api.example.com",
    "api": "openai-completions",  // 或 "openai-response" 或 "anthropic"
    "models": [
        {
            "name": "model-name",
            "reasoning": false,        // 是否支持思考/推理模式
            "accept": ["text"],        // 接受的输入类型
            "context_window": 131072,
            "max_tokens": 8192
        }
    ]
}
```

**三种 API 类型**:

| API 类型 | 说明 | 使用场景 |
|---------|------|---------|
| `openai-completions` | OpenAI Chat Completions API | DeepSeek, OpenAI, 大多数兼容 API |
| `openai-response` | OpenAI Responses API | 较新的 OpenAI 端点 |
| `anthropic` | Anthropic Messages API | Claude 系列模型 |

#### 3.2.3 智能体配置 (AgentEntry)

```json
{
    "name": "agent-name",
    "description": "智能体的描述，影响其行为和系统提示",
    "provider": "deepseek",        // 指定提供商（覆盖全局默认）
    "model": "deepseek-chat",      // 指定模型（覆盖全局默认）
    "server": {                    // HTTP 服务器配置（A2A 模式）
        "host": "127.0.0.1",
        "port": 8001
    },
    "toolbox": {                   // 工具箱状态
        "tools": {},
        "mcp_servers": {},
        "bridges": {},
        "cli": {},
        "skills": {}
    },
    "mqtt": {                      // MQTT 配置
        "broker_host": "127.0.0.1",
        "broker_port": 1883,
        "username": "",
        "password": "",
        "tls": false
    },
    "wechat_session": "path/to/session.json"  // 微信会话持久化
}
```

特殊 provider 值：
- `"human"` — 终端人类智能体
- `"wechat-human"` — 微信桥接人类智能体

#### 3.2.4 智能体拓扑 (TopologyConfig)

定义智能体之间的关系：

```json
{
    "topology": [
        {"from": "planner", "to": "executor", "relation": "delegates"},
        {"from": "executor", "to": "reviewer", "relation": "reports"}
    ]
}
```

#### 3.2.5 嵌入模型 (EmbeddingsBackend)

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

支持两种后端：
- `sentence-transformers` — 基于 HuggingFace 模型
- `llama-cpp-python` — 基于 llama.cpp 的本地嵌入

#### 3.2.6 记忆搜索配置 (MemorySearchConfig)

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

#### 3.2.7 群聊配置 (GChatConfig)

```json
{
    "agents_config": {
        "gchat": {
            "reply_delay_min": 1.0,  // 最小回复延迟（秒）
            "reply_delay_max": 3.0   // 最大回复延迟（秒）
        }
    }
}
```

### 3.3 配置验证

配置在加载时通过 Pydantic 自动验证：
- 重复的服务器端口会被检测并报错
- 缺少的 provider/model 引用会被检测
- 类型不匹配会在启动时立即报错

---

## 4. 五大运行模式

QD-Evolve 提供五种运行模式，每种独立运作，没有协议回退。

### 4.1 单智能体聊天 (chat)

```bash
qd-evolve chat [--agent NAME] [--replay FILE] [--output FILE]
```

**传输方式**: 进程内（无网络）

**适用场景**: 与单个 AI 智能体进行对话，测试工具，日常使用

**特点**:
- 不启动任何网络服务
- 不注册 A2A 工具
- 支持所有斜杠命令
- 支持心跳和子智能体

### 4.2 A2A 进程内多智能体 (a2a-inproc)

```bash
qd-evolve a2a-inproc [--replay FILE] [--output FILE]
```

**传输方式**: 进程内（通过 `InprocTransport` 直接调用）

**适用场景**: 在同一进程中测试多智能体协作，无需网络

**特点**:
- 加载所有配置的非人类智能体到同一进程
- 智能体通过 `TransportRouter` 互相发现和通信
- 零网络延迟
- 支持 `delegate_to`、`send_task`、`get_task`、`cancel_task` 等 A2A 工具

### 4.3 A2A HTTP 模式 (a2a-http)

```bash
# 客户端模式（连接到远程智能体）
qd-evolve a2a-http [--replay FILE] [--output FILE]

# 服务模式（暴露智能体为 HTTP 服务）
qd-evolve a2a-http serve [--agent NAME]
```

**传输方式**: HTTP/SSE（A2A v1.0 协议）

**适用场景**: 跨机器多智能体部署，每个智能体独立运行

**特点**:
- 完整的 A2A v1.0 协议实现
- 智能体发现（`/.well-known/agent.json`）
- SSE 流式事件推送
- 任务生命周期: submitted → working → completed/failed/canceled/input_required
- 支持 webhook 回调（推送通知）
- 支持全双工 JSON-RPC 通信

### 4.4 A2A MQTT 模式 (a2a-mqtt)

```bash
# 客户端模式
qd-evolve a2a-mqtt [--replay FILE] [--output FILE]

# 服务模式
qd-evolve a2a-mqtt serve [--agent NAME]
```

**传输方式**: MQTT v5

**适用场景**: IoT 场景、需要 broker 模式的部署

**特点**:
- 使用标准 A2A JSON-RPC over MQTT v5
- 主题结构:

| 主题 | 用途 |
|-----|------|
| `$a2a/v1/discovery/{name}` | AgentCard 发现（保留消息） |
| `$a2a/v1/request/{name}` | 任务请求 |
| `$a2a/v1/response/{name}/{req_id}` | 每请求响应 |
| `$a2a/v1/event/{name}` | 流式事件和推送通知 |

- MQTT v5 特性: Response Topic, Correlation Data, User Properties, LWT, Retained Messages
- 需要外部 Mosquitto v5 broker

### 4.5 群聊模式 (gchat)

```bash
qd-evolve gchat [--agent NAME]
```

**传输方式**: MQTT v5 群组话题

**适用场景**: 微信风格的多人+多 AI 群组对话

**特点**:
- 所有配置的智能体加入同一个群聊
- AI 智能体自动处理 `@mentions` 并回复
- 终端人类通过交互式提示参与
- 微信人类通过 iLink 桥接参与
- `@all` 提及所有人
- 消息去重（通过 `msg_id`）
- 可配置的回复延迟（模拟人类打字）

### 4.6 运行模式选择指南

| 需求 | 推荐模式 |
|-----|---------|
| 只想和 AI 对话 | `chat` |
| 测试多智能体协作（本机） | `a2a-inproc` |
| 生产部署多智能体（跨机器） | `a2a-http serve` + `a2a-http` |
| IoT/嵌入式场景 | `a2a-mqtt` |
| 群聊互动 | `gchat` |

---

## 5. 核心概念

### 5.1 智能体 (Agent)

智能体是框架的核心抽象。它是一个 LLM 驱动的推理引擎，遵循简单的循环：

```
推理 → 调用工具 → 观察 → 重复
```

**核心能力**:
- 接收用户输入，发送给 LLM 获取响应
- 执行工具调用（由 LLM 决定何时调用什么工具）
- 管理对话历史
- 自动记忆回忆和保存
- 上下文窗口压缩（当历史超出阈值时自动裁剪旧消息）
- 多提供商后端支持（Anthropic / OpenAI Completions / OpenAI Responses）

**智能体分层（组合模式）**:

```
Agent (纯 LLM 循环，无网络)
  └── A2AAgent (包装 Agent，添加 A2A 身份和事件扇出)
        └── MqttAgent (包装 A2AAgent，添加 MQTT v5 生命周期)
              └── GroupChatAgent (包装 MqttAgent，添加群聊行为)
```

每一层通过组合（而非继承）添加一个关注点。外部通过 `loader.py` 的 `create_agent()` 工厂函数创建。

### 5.2 工具注册表 (ToolRegistry)

`ToolRegistry` 是全局单例，管理所有可调用工具的注册、发现和执行。

**工具来源**:
1. **系统工具** — `qd_evolve/tools/*.py`（自动发现）
2. **用户 func 工具** — `tools/func/*.py`（热加载）
3. **A2A 工具** — 当 A2A 启用时注册（`delegate_to`, `send_task`, `get_task`, `cancel_task`）
4. **Bridge 工具** — MCP 和 OAT 桥接工具
5. **CLI 工具** — `tools/cli/*.yaml` 定义的命令行工具
6. **子智能体工具** — `create_sub_agent`, `run_sub_agent`, `get_sub_result`

**工具调用机制**: 工具在守护线程中执行，带有可配置的超时（默认 60 秒，工具可自定义超时 + 15 秒缓冲）。

### 5.3 按需加载 (On-Demand Loading)

为了节省提示上下文，工具采用按需加载策略：

| 状态 | 含义 | LLM 可见性 |
|-----|------|-----------|
| `enabled` | 已启用 | 仅名称和描述可见 |
| `preload` | 预加载 | 完整的 JSON Schema 在系统提示中 |
| `disabled` | 已禁用 | 完全不可见 |

LLM 可以通过以下工具加载完整定义：
- `load_func(name)` — 加载 func 工具的完整 Schema
- `load_skill(name)` — 加载 SKILL.md 内容
- `load_cli(name)` — 加载 CLI 工具的完整定义

### 5.4 提供商注册表 (ProviderRegistry)

管理所有配置的 LLM 提供商。支持三种 API 类型：
- `openai-completions` — OpenAI Chat Completions API
- `openai-response` — OpenAI Responses API（较新）
- `anthropic` — Anthropic Messages API

每个提供商可以配置多个模型，每个模型有自己的上下文窗口大小、最大 token 数和推理/思考能力配置。

### 5.5 记忆存储 (MemoryStore)

基于 SQLite + `sqlite-vec` 的持久记忆系统。详见[第 11 章](#11-记忆系统)。

### 5.6 模板系统 (PromptTemplateManager)

基于 Jinja2 的提示模板系统。详见[第 15 章](#15-模板系统)。

---

## 6. 工具系统

### 6.1 工具类型

框架支持六种工具类型：

#### 6.1.1 Func 工具（Python 函数工具）

位于 `tools/func/` 目录的 `.py` 文件。每个文件使用 `ToolRegistry` 注册工具。

**内置 func 工具**:

| 工具名 | 文件 | 功能 |
|-------|------|------|
| `fetch` | `fetch.py` | HTTP GET/POST 请求 |
| `read_file` | `file_rw.py` | 读取文件内容 |
| `write_file` | `file_rw.py` | 写入文件 |
| `list_directory` | `file_rw.py` | 列出目录内容 |
| `run_python` | `run_python.py` | 执行 Python 代码 |
| `run_shell` | `run_shell.py` | 执行 Shell 命令 |
| `serper_search` | `search.py` | Web 搜索 |
| `serper_scrape` | `search.py` | 网页抓取 |

**编写自定义 Func 工具**:

在 `tools/func/` 下创建 `.py` 文件：

```python
"""
我的自定义工具
"""
from qd_evolve.tools import get_registry

def my_handler(param1: str, param2: int = 10) -> str:
    """处理逻辑"""
    return f"结果: {param1} x {param2}"

def register_tools():
    registry = get_registry()
    registry.register(
        name="my_tool",
        description="我的自定义工具的描述",
        handler=my_handler,
        input_schema={
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "第一个参数"},
                "param2": {"type": "integer", "description": "第二个参数", "default": 10}
            },
            "required": ["param1"]
        }
    )
```

#### 6.1.2 CLI 工具（命令行工具）

位于 `tools/cli/` 目录的 YAML 文件。通过 `run_shell` 工具调用。

**示例** (`tools/cli/yt-dlp.yaml`):

```yaml
name: yt-dlp
command: yt-dlp
description: "功能丰富的命令行音视频下载器，支持 1000+ 网站"
help_summary: |
  -h          帮助
  -f FORMAT   选择格式
  -x          提取音频
  ...
examples:
  - "下载视频: yt-dlp <url>"
  - "选择最佳格式: yt-dlp -f best <url>"
  - "提取 MP3 音频: yt-dlp -x --audio-format mp3 <url>"
```

#### 6.1.3 MCP 工具（Model Context Protocol）

通过 MCP Bridge 加载的外部进程工具。详见[第 16 章](#16-bridge-桥接系统)。

#### 6.1.4 OAT 工具（Open-Agent-Tools）

通过 OAT Bridge 加载的进程内 Python 工具。详见[第 16 章](#16-bridge-桥接系统)。

#### 6.1.5 技能 (Skill)

位于 `skills/` 目录的 SKILL.md 文件。详见[第 17 章](#17-技能系统)。

#### 6.1.6 系统工具

框架内置的系统工具：

| 工具名 | 功能 |
|-------|------|
| `load_func` | 按需加载 func 工具定义 |
| `load_skill` | 按需加载技能内容 |
| `load_cli` | 按需加载 CLI 工具定义 |
| `list_providers` | 列出可用的 LLM 提供商和模型 |
| `get_my_config` | 查看当前智能体配置 |
| `update_my_config` | 更新当前智能体的 provider/model/description |
| `recall_memory` | 搜索历史对话记忆 |
| `hot_loading_mcp` | 运行时激活 MCP 服务器 |
| `create_sub_agent` | 创建子智能体 |
| `run_sub_agent` | 运行子智能体任务 |
| `get_sub_result` | 查询子智能体结果 |
| `delegate_to` | 同步委托任务给其他智能体（A2A） |
| `send_task` | 异步发送任务给其他智能体（A2A） |
| `get_task` | 查询异步任务状态（A2A） |
| `cancel_task` | 取消异步任务（A2A） |

### 6.2 工具状态管理

每个智能体有独立的工具箱配置，存储在 `config.json` 的 `agents[*].toolbox` 中。

**管理方式**:
1. 通过 `qd-evolve toolbox` TUI 界面（推荐）
2. 通过 `qd-evolve toolbox --toggle <name>` 命令行
3. 直接编辑 `config.json`

**状态切换**:
```
工具/CLI/技能: disabled → enabled → preload → disabled
Bridge/MCP:    disabled → enabled → disabled
```

---

## 7. 多智能体系统 (A2A)

### 7.1 A2A 协议概述

QD-Evolve 实现了 A2A v1.0 协议。智能体通过统一的接口通信，AI 和人类使用相同的协议。

### 7.2 核心数据模型

**AgentCard** — 智能体身份文档:
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

**Task** — 任务生命周期:
```
submitted → working → completed
                    → failed
                    → canceled
                    → input_required (人类智能体)
```

### 7.3 A2A 工具详解

#### delegate_to (同步委托)

阻塞式调用。发送任务给目标智能体，等待完成后返回结果。

```
参数:
  - agent_name: 目标智能体名称（必填）
  - task: 任务描述（必填）
  - timeout: 超时秒数（可选）

返回: 目标智能体的完整响应
限制: 不能委托给人类智能体
```

#### send_task (异步发送)

非阻塞式调用。发送任务后立即返回 task_id。

```
参数:
  - agent_name: 目标智能体名称（必填）
  - task: 任务描述（必填）

返回: {"task_id": "...", "status": "submitted"}
```

#### get_task (查询任务状态)

查询异步任务的状态和结果。

```
参数:
  - task_id: 任务 ID（必填）

返回: {"task_id": "...", "status": "working|completed|failed", "result": "..."}
```

#### cancel_task (取消任务)

取消一个未完成的任务。

```
参数:
  - task_id: 任务 ID（必填）

返回: 取消确认
```

### 7.4 Transport 传输层

#### InprocTransport（进程内）

- 直接调用目标智能体的 `run()` 方法
- 零网络开销
- 对 AI 智能体通过 `asyncio.to_thread` 异步执行
- 对人类智能体立即返回 `input_required`

#### HttpTransport（HTTP/SSE）

- 通过 aiohttp JSON-RPC 调用来发送任务
- `send_stream` 通过 SSE 接收中间事件
- 支持 webhook 回调用于推送通知
- 自动计算回调 URL

#### MqttTransport（MQTT v5）

- 使用标准 A2A JSON-RPC over MQTT
- 利用 MQTT v5 的 Response Topic 和 Correlation Data 实现请求-响应匹配
- 通过保留消息实现服务发现
- 通过 LWT 实现离线检测

#### TransportRouter

```python
# 自动路由：本地智能体→InprocTransport，远程智能体→远程Transport
router = TransportRouter(inproc=inproc_transport, remote=http_transport)
# router 会自动选择正确的 Transport
```

### 7.5 服务模式部署

#### HTTP 服务模式

```bash
qd-evolve a2a-http serve --agent my-agent
```

启动一个独立的 HTTP 服务器，将指定智能体暴露为 A2A 服务：
- `POST /` — JSON-RPC 端點
- `GET /.well-known/agent.json` — AgentCard 发现
- 支持 SSE 流式响应
- 支持 webhook 推送通知

#### MQTT 服务模式

```bash
qd-evolve a2a-mqtt serve --agent my-agent
```

将智能体注册到 MQTT broker：
- 发布 AgentCard 到发现主题（保留消息）
- 订阅请求主题
- 设置 LWT 用于离线检测
- 通过事件主题推送流式更新

---

## 8. 群聊系统

### 8.1 概述

群聊模拟了类似微信的多人+多 AI 群组对话体验。所有配置的智能体加入同一个群聊组。

### 8.2 消息流

```
MQTT Broker
  └── $a2a/v1/group/{agent_name}/chat
        ├── AI Agent A 发布消息
        ├── AI Agent B 发布消息
        ├── Human C (终端) 发布消息
        └── Human D (微信) 发布消息
```

### 8.3 提及机制

- `@agent_name` — 提及特定智能体
- `@all` — 提及所有人
- AI 智能体只处理包含 `@mention` 的消息
- 每个智能体独立判断是否被提及

### 8.4 消息去重

`GroupChatAgent` 维护一个已见消息 ID 集合：
- 最大缓存 10,000 条
- 超过后裁剪到 5,000 条
- 确保每条消息只处理一次

### 8.5 回复延迟

为了模拟自然的群聊感受，AI 智能体在回复前会等待一段随机延迟：

```json
{
    "gchat": {
        "reply_delay_min": 1.0,  // 最小延迟（秒）
        "reply_delay_max": 3.0   // 最大延迟（秒）
    }
}
```

### 8.6 三种群聊参与者

#### AI Agent (GroupChatAgent)

```bash
# 如果默认智能体是 AI 类型
qd-evolve gchat

# 指定 AI 智能体
qd-evolve gchat --agent my-bot
```

#### 终端人类 (GroupChatHuman)

- 配置 `"provider": "human"` 的智能体作为终端人类
- 在终端中看到实时群消息
- 输入消息即可发布到群聊
- 消息彩色显示，不同智能体使用不同颜色

#### 微信人类 (GroupChatWechatHuman)

- 配置 `"provider": "wechat-human"` 的智能体
- 需要先通过二维码登录微信
- 消息通过 iLink 桥接在微信和 MQTT 之间双向转发

---

## 9. 人类智能体

### 9.1 概述

人类智能体实现了与 AI 智能体相同的 `AgentProtocol` 接口。对于 Transport 层来说，AI 和人类是透明的——区别仅在于处理方式。

### 9.2 终端人类 (HumanAgent)

```json
{
    "name": "human-user",
    "description": "人类操作员",
    "provider": "human",
    "model": ""
}
```

**工作流程**:
1. AI 智能体通过 `send_task` 发送任务给人类
2. 任务以 `input_required` 状态创建
3. 终端显示任务内容和来源
4. 人类输入响应
5. 响应通过 webhook 推送回调用者

### 9.3 MQTT 人类 (MqttHumanAgent)

与终端人类类似，但通过 MQTT 通信：

1. 接收 MQTT 请求主题上的任务
2. 创建 `input_required` 任务
3. 人类响应通过 webhook 推送回调用者的事件主题

### 9.4 微信人类 (GroupChatWechatHuman)

通过 WeChat iLink 协议桥接：
1. 长轮询微信消息
2. 解析 `@mentions`
3. 发布到 MQTT 群聊主题
4. 接收群消息并转发到微信

---

## 10. 子智能体

### 10.1 概述

子智能体是轻量级的进程内工作智能体，由父智能体在运行时创建。它们是：

- **轻量**: 无持久记忆、无心跳、无网络服务器
- **继承式**: 继承父智能体的 provider/model/tools/skills/CLI 预加载集
- **单任务**: 一次只处理一个任务（忙碌时拒绝新任务）
- **临时的**: 存在于父进程内，随父进程退出而销毁

### 10.2 使用方式

智能体（LLM）可以通过以下工具使用子智能体：

#### create_sub_agent

```
参数:
  - name: 子智能体名称（必填）
  - description: 用途描述（可选，用于 prompt 自定义）

效果: 创建一个继承父智能体配置的 Agent 实例
```

#### run_sub_agent

```
参数:
  - name: 子智能体名称（必填）
  - task: 任务描述（必填）
  - reset: 是否重置对话历史（可选，默认 false）

返回: {"task_id": "...", "agent": "...", "status": "running"}
```

#### get_sub_result

```
参数:
  - task_id: 任务 ID（必填）

返回: {"task_id": "...", "status": "running|done|error", "result": "..."}
```

### 10.3 结果推送机制

当父智能体进入空闲等待时，子智能体完成的结果会自动推送到父智能体的对话流中作为用户消息。使用 `ContextVar` 确保线程正确性。

### 10.4 使用场景

- 并行处理多个独立任务
- 让一个智能体同时运行需要不同上下文的对话
- 隔离实验性操作

---

## 11. 记忆系统

### 11.1 架构

记忆系统基于 SQLite + `sqlite-vec` 向量扩展：

```
SQLite 数据库 (memory.db)
  ├── memories 表 — 元数据和内容
  └── memory_vec 表 — BGE-M3 向量嵌入
```

### 11.2 操作

#### 保存 (save)

每次智能体响应完成后自动保存：
- `user_msg` — 用户消息
- `assistant_msg` — 助手响应
- `process` — 工具调用过程
- `content` — 组合内容（用于向量嵌入）
- `session_id` — 会话标识
- `key` — ISO 时间戳

#### 回忆 (recall)

三种检索模式：

1. **语义搜索** — 使用 `query` 参数进行向量相似度搜索
2. **关键词搜索** — 使用 `keywords` 参数进行 SQL LIKE 匹配
3. **时间范围浏览** — 使用 `time_range` 参数

**时间范围格式**:

| 值 | 含义 |
|----|------|
| `last_session` | 最近一次非当前会话的所有记忆 |
| `today` | 今天 |
| `yesterday` | 昨天 |
| `this_week` | 本周 |
| `last_week` | 上周 |
| `this_month` | 本月 |
| `last_month` | 上月 |
| `last_Nd` | 最近 N 天（如 `last_3d`） |
| `YYYY-MM-DD~YYYY-MM-DD` | 日期范围 |

#### 自动回忆 (auto_recall)

每次 LLM 调用前，系统自动执行语义搜索（根据用户消息），将最相关的记忆注入系统提示。通过 `RecalledMemoryRegistry` 去重。

### 11.3 recall_memory 工具

```
参数:
  - query: 语义查询（可选）
  - keywords: 关键词（可选）
  - time_range: 时间范围（可选）
  - limit: 返回条数（可选）

返回: 格式化的记忆列表，包含会话信息、用户/助手消息和相关性评分
```

### 11.4 上下文压缩

当对话历史超过上下文窗口的配置阈值（默认 70%）时：

1. 移除最旧的 user/assistant/tool 消息组
2. 直到 token 数降至目标阈值（默认 50%）
3. 被压缩的消息作为"已处理"保留在 memory 中

### 11.5 浏览器 (Memory TUI)

```bash
qd-evolve memory [--agent NAME]     # 命令行列表模式
qd-evolve memory --tui [--agent NAME]  # TUI 浏览模式
```

TUI 功能：
- `/` 语义搜索
- `t` 时间范围过滤
- `l` 切换显示条数（5/10/20/50/100）
- 方向键/jk 导航
- 详情面板显示完整记忆内容

---

## 12. 心跳与事件

### 12.1 心跳机制

心跳允许智能体在长时间空闲后主动发起对话。

**工作流程**:
1. 智能体空闲达到 `heartbeat_idle_seconds`（默认 30 秒）
2. 发送心跳提示给 LLM
3. LLM 可以回复 `"."` 表示保持沉默
4. LLM 也可以回复主动的对话开头

**配置**:
```json
{
    "heartbeat_idle_seconds": 30,  // 全局默认
    "agents": [
        {
            "name": "my-agent",
            "heartbeat_idle_seconds": 60  // 智能体级别覆盖
        }
    ]
}
```

设为 `0` 禁用心跳。

### 12.2 事件系统

智能体在运行过程中产生事件：

| 事件类型 | 触发时机 |
|---------|---------|
| `iteration_start` | 每次 LLM 调用的迭代开始 |
| `status` | 状态更新（如"正在思考..."） |
| `print` | 输出内容 |
| `error` | 发生错误 |
| `completed` | 任务完成 |
| `heartbeat_silent` | 心跳发送后无响应 |
| `heartbeat_*` | 心跳相关事件 |
| `human_task` | 人类收到新任务 |
| `task_completed` | 人类完成任务 |
| `sub_agent_result` | 子智能体结果可用 |
| `tool_activated` | 工具被激活 |

### 12.3 事件订阅与推送

- `A2AAgent` 通过 `subscribe_events()` 支持多事件订阅者
- 事件通过 `asyncio.Queue` 扇出到所有订阅者
- HTTP 模式通过 SSE 推送
- MQTT 模式通过事件主题推送
- 客户端通过 `resubscribe` 重新连接事件流

---

## 13. 斜杠命令参考

以下命令在聊天模式中可用：

| 命令 | 描述 |
|-----|------|
| `/quit` | 退出程序 |
| `/reset` | 重置当前对话 |
| `/help` | 显示帮助信息 |
| `/models` | 交互式切换模型 |
| `/agents` | 列出所有智能体（含在线状态） |
| `/tools` | 列出可用工具 |
| `/skills` | 列出可用技能 |
| `/cli` | 列出 CLI 工具 |
| `/status` | 显示当前智能体状态（preload/loaded 分类） |
| `/memory` | 显示最近的记忆 |
| `/recall <query>` | 搜索记忆 |
| `/compress` | 手动触发上下文压缩 |
| `/load` | 手动加载工具/技能/CLI |
| `/clear` | 清屏 |

### 交互式模型切换 (`/models`)

```
1. deepseek-chat       (DeepSeek)
2. gpt-4o              (OpenAI)
3. claude-sonnet-4-6   (Anthropic)
输入编号切换模型 >
```

### 交互式智能体切换 (`/agents`)

```
1. assistant            [AI]     ✓ online  inproc
2. reviewer             [AI]     ✓ online  inproc
3. human-approver       [HUMAN]  ✓ online  inproc
输入编号切换对话目标 >
```

---

## 14. TUI 管理界面

### 14.1 工具箱管理 (Toolbox TUI)

```bash
qd-evolve toolbox [--agent NAME]
```

**界面布局**:
```
┌──────────────┬──────────────────────────────────┐
│ 类别面板      │ 工具面板                           │
│              │                                  │
│ System Tools │ ✓ fetch        HTTP 请求工具      │
│ Func Tools   │ P run_shell    执行 Shell 命令     │
│ Bridge: mcp  │ ✗ unused_tool  未启用的工具        │
│ Bridge: oat  │ ...                               │
│ CLI Tools    │                                  │
│ Skills       │                                  │
└──────────────┴──────────────────────────────────┘
```

**快捷键**:

| 键 | 功能 |
|----|------|
| `e` | 切换启用/禁用 |
| `p` | 切换 preload 状态（三态切换） |
| `space` | 展开/折叠 Bridge 组 |
| `s` | 折叠所有 Bridge 组 |
| `/` | 过滤工具名称 |
| `tab` | 切换左右面板 |
| `?` | 显示帮助 |
| `q` | 退出 |

**状态标记**:
- `[✓]` — 已启用（enable）
- `[P]` — 预加载（preload）
- `[✗]` — 已禁用（disable）

### 14.2 记忆浏览器 (Memory TUI)

```bash
qd-evolve memory --tui [--agent NAME]
```

**快捷键**:

| 键 | 功能 |
|----|------|
| `/` | 语义搜索 |
| `t` | 时间范围过滤 |
| `l` | 切换显示条数 (5→10→20→50→100) |
| `r` | 刷新 |
| `↑/↓` 或 `j/k` | 导航 |
| `q` | 退出 |

### 14.3 命令行工具箱

```bash
# 快速切换工具状态
qd-evolve toolbox --toggle fetch
qd-evolve toolbox --toggle run_shell
qd-evolve toolbox --toggle mcp:boat

# 交互式 Shell
qd-evolve toolbox
> ls              # 列出所有工具（分页）
> toggle fetch    # 切换 fetch 状态
> enable run_shell
> disable old_tool
> preload important_skill
> help
> quit
```

---

## 15. 模板系统

### 15.1 概述

框架使用 Jinja2 模板来渲染系统提示和用户消息。模板支持从用户自定义目录回退到内置目录。

### 15.2 模板加载

**加载顺序**:
1. `templates/`（用户自定义目录）— 优先加载
2. `qd_evolve/_templates/`（内置目录）— 回退

**模板后缀**: `.j2`

### 15.3 上下文变量

所有模板可访问的默认变量：

| 变量 | 说明 |
|-----|------|
| `current_date` | 当前日期 (YYYY-MM-DD) |

具体模板根据使用场景接收更多变量（如智能体名称、描述、工具列表、记忆、运行时环境等）。

### 15.4 可用模板

| 模板名称 | 用途 |
|---------|------|
| `system.j2` | 默认系统提示 |
| `chat.j2` | 聊天消息格式 |
| `subagent.j2` | 子智能体系统提示 |
| `heartbeat.j2` | 心跳提示 |
| `group-message.j2` | 群聊消息格式 |
| `a2a-heartbeat.j2` | A2A 模式心跳提示 |

### 15.5 自定义模板

在项目根目录创建 `templates/` 目录，放入同名 `.j2` 文件即可覆盖内置模板：

```
project/
  templates/
    system.j2     # 覆盖默认系统提示
    chat.j2       # 覆盖聊天格式
```

---

## 16. Bridge 桥接系统

### 16.1 Bridge 框架

Bridge 系统通过一个通用框架来集成外部工具源。每种 Bridge 类型通过 `tools/bridge/_*.py` 模块自动发现和注册。

**Bridge 生命周期**:
```
discover → connect → 使用工具 → disconnect
```

**核心组件**:
- `Bridge` (Protocol) — 每个 Bridge 实例管理其配置和已注册工具
- `BridgeSpec` — 命名的规范，包含 discover/connect/disconnect
- `BridgeEntry` — 工具箱列表的摘要
- `BridgeManager` — 单例管理器

### 16.2 MCP Bridge (Model Context Protocol)

**位置**: `tools/bridge/_mcp.py`

通过子进程运行外部 MCP 服务器，发现并注册其工具。

**配置** (`tools/mcp/<name>.json`):

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

**特性**:
- 支持多种传输: stdio, SSE, StreamableHTTP, WebSocket
- 环境变量引用 (`$VAR` 或 `${VAR}`)
- 并行连接所有服务器（利用 ThreadPoolExecutor）
- 工具名称前缀 `[server_name]` 避免冲突
- 当环境变量缺失时跳过（不报错）

**运行时加载**:
```bash
# 也可以通过 LLM 调用 hot_loading_mcp 工具
# 在聊天中让 AI 使用 hot_loading_mcp 添加新的 MCP 服务器
```

### 16.3 OAT Bridge (Open-Agent-Tools)

**位置**: `tools/bridge/_oat.py`

进程内导入 Python 包，零子进程开销。

**配置** (`tools/bridge/oat.json`):

```json
[
    {
        "name": "boat-core",
        "package": "basic_open_agent_tools",
        "loadout": "core"
    }
]
```

**特性**:
- 直接导入和执行 Python 函数
- 自动转换 Google ADK Schema 到 OpenAI Schema
- 返回值自动规范化

### 16.4 OAT JSON Shim

**位置**: `tools/bridge/_oat_json.py`

为 OAT Bridge 提供 JSON 文件操作工具：

```
read_json_file, get_json_value_at_path, get_json_keys,
get_json_structure, count_json_items, search_json_keys,
write_json_file, update_json_value_at_path,
delete_json_key_at_path, append_to_json_array
```

---

## 17. 技能系统

### 17.1 什么是技能？

技能是位于 `skills/<name>/SKILL.md` 的 Markdown 文件，包含 YAML 前置元数据和指导内容。技能被注入到智能体的系统提示中，指导 LLM 如何处理特定任务。

### 17.2 技能结构

```
skills/
  my-skill/
    SKILL.md          # 技能定义（必需）
    _meta.json        # 版本信息（可选）
    scripts/          # 辅助脚本（可选）
    references/       # 参考资料（可选）
```

**SKILL.md 格式**:

```markdown
---
name: my-skill
description: 这个技能做什么
version: "1.0.0"
tags: [web, search]
---

# 技能内容

这里是技能的详细指导内容...
```

### 17.3 内置技能

| 技能名称 | 功能 |
|---------|------|
| `baidu-search` | 通过百度 AI 搜索 API 进行网络搜索 |
| `search-tools` | 搜索并推荐新工具 |
| `install-and-register-tools` | 安装并注册新工具 |
| `register-cli` | 分析 `--help` 输出并注册 CLI 工具 |
| `self-improvement` | 记录学习、错误和功能请求，持续改进 |

### 17.4 技能的使用

- **预加载 (preload)**: 技能内容在启动时注入系统提示
- **按需加载 (enable)**: LLM 看到技能摘要，通过 `load_skill(name)` 按需加载
- **禁用 (disable)**: 技能不可见

---

## 18. 高级主题

### 18.1 重放模式 (Replay)

记录和重放对话用于自动化测试：

```bash
# 记录对话
qd-evolve chat --output session.txt

# 重放对话
qd-evolve chat --replay session.txt
```

`ReplayInput` 类从文件读取预录输入，`TeeWriter` 同时写入终端和文件。

### 18.2 Token 统计

每次响应后显示：
- 本次输入/输出 token 数
- 累计输入/输出 token 数
- 上下文窗口使用百分比

### 18.3 运行时环境信息

智能体首次启动时自动收集运行时上下文：
- 操作系统和 Python 版本
- 虚拟环境和包管理器（uv/pip）
- Shell 类型
- Git 仓库状态
- 代理设置

这些信息作为 Markdown 注入系统提示，让 LLM 了解其运行环境。

### 18.4 错误处理

- 工具执行超时：返回超时错误字符串，不中断循环
- 工具执行异常：返回异常信息字符串
- 非零退出码（`run_shell`/`run_python`）：不视为错误，正常返回输出
- 编码处理：多级回退编码检测（UTF-8 → GBK → GB2312 → 拉丁-1）

### 18.5 并发安全

- `Agent.run()` 使用 `threading.Lock`（可重入）序列化并发调用
- 工具执行在守护线程中，通过 `contextvars` 继承上下文
- 子智能体通过 `ContextVar` 维护线程安全的当前智能体名称

### 18.6 日志

日志文件位于 `logs/` 目录，文件名包含时间戳：
```
logs/qd_evolve_20260115_143052.log
```

- `SharedFileHandler`: 每条日志立即刷新，支持并发 `tail -f`
- 文件级别: DEBUG
- 控制台级别: ERROR（仅 stderr）

---

## 19. 故障排除

### 19.1 常见问题

#### Q: 启动时提示 "Provider not configured"

检查 `config.json`：
- 确保 `providers` 数组非空
- 确保智能体的 `provider` 名称与 providers 中的 name 匹配
- 确保 `api_key` 已设置且有效

#### Q: MQTT 模式无法连接

- 确认 Mosquitto v5 broker 正在运行：`mosquitto -v`
- 检查 broker_host 和 broker_port 配置
- 如果使用 TLS，确保证书路径正确

#### Q: 工具没有出现在智能体的工具列表中

- 检查工具箱 TUI：`qd-evolve toolbox`
- 确认工具状态不是 `disabled`
- 确认 func 工具文件在 `tools/func/` 目录下

#### Q: 记忆搜索无结果

- 确认 `memory.db` 文件存在（至少进行过一次对话后）
- 检查嵌入模型配置是否正确
- 尝试使用关键词搜索代替语义搜索

#### Q: 上下文窗口溢出

- 框架会自动压缩（当超出 `compress_threshold` 时）
- 手动触发：在聊天中使用 `/compress`
- 使用 `/reset` 重置对话
- 调整 `compress_threshold` 和 `target_threshold` 配置

### 19.2 日志分析

```bash
# 查看最近的日志
ls -t logs/ | head -1 | xargs cat

# 实时监控日志
tail -f logs/qd_evolve_*.log

# 过滤错误
grep "ERROR" logs/qd_evolve_*.log
```

---

## 20. 附录

### 20.1 项目结构

```
qd-evolve/
├── qd_evolve/              # 主 Python 包
│   ├── __init__.py         # 版本号
│   ├── chat_cli.py         # 聊天 CLI（主入口）
│   ├── gchat_cli.py        # 群聊 CLI
│   ├── mqtt_cli.py         # MQTT A2A CLI
│   ├── a2a_cli.py          # HTTP A2A CLI
│   ├── a2a_inproc_cli.py   # 进程内 A2A CLI
│   ├── cli_tools.py        # CLI 工具注册表
│   ├── cli_utils.py        # CLI 公共工具
│   ├── skills.py           # 技能注册表
│   ├── toolbox_tui.py      # 工具箱 TUI
│   ├── memory_tui.py       # 记忆浏览器 TUI
│   ├── core/               # 核心基础设施
│   │   ├── config.py       # 配置模型（Pydantic）
│   │   ├── registry.py     # 工具注册表
│   │   ├── providers.py    # LLM 提供商
│   │   ├── toolbox.py      # 工具箱状态管理
│   │   ├── memory.py       # 记忆存储
│   │   ├── prompts.py      # 模板管理
│   │   └── logger.py       # 日志
│   ├── agent/              # 智能体层
│   │   ├── agent.py        # 核心 Agent 类
│   │   ├── a2a.py          # A2A 数据模型
│   │   ├── a2a_agent.py    # A2A 智能体包装
│   │   ├── a2a_tools.py    # A2A 工具
│   │   ├── protocol.py     # 智能体协议接口
│   │   ├── registry.py     # 智能体注册表
│   │   ├── loader.py       # 工厂函数
│   │   ├── server.py       # HTTP A2A 服务器
│   │   ├── transport.py    # 传输层
│   │   ├── mqtt_agent.py   # MQTT 智能体包装
│   │   ├── mqtt_transport.py # MQTT 传输
│   │   ├── mqtt_human_agent.py # MQTT 人类
│   │   ├── human_agent.py  # 人类智能体
│   │   ├── group_chat_agent.py    # 群聊 AI
│   │   ├── group_chat_transport.py # 群聊传输
│   │   ├── group_chat_human.py    # 群聊终端人类
│   │   └── group_chat_wechat_human.py # 群聊微信人类
│   ├── tools/              # 工具模块
│   │   ├── tool_loader.py      # func 工具加载器
│   │   ├── hot_loading_mcp.py  # MCP 热加载
│   │   ├── skill_loader.py     # 技能加载器
│   │   ├── cli_loader.py       # CLI 加载器
│   │   ├── config_manager.py   # 配置管理+子智能体
│   │   └── recall_memory.py    # 记忆回忆工具
│   ├── bridge/             # WeChat 桥接
│   │   └── wechat_clawbot_client.py # iLink 客户端
│   ├── utils/              # 工具函数
│   │   ├── adk_output.py   # ADK 输出规范化
│   │   └── adk_schema.py   # ADK Schema 转换
│   └── _templates/         # 内置 Jinja2 模板
├── tools/                  # 用户工具
│   ├── func/               # Python 函数工具
│   │   ├── fetch.py
│   │   ├── file_rw.py
│   │   ├── run_python.py
│   │   ├── run_shell.py
│   │   └── search.py
│   ├── cli/                # CLI 工具定义
│   │   └── yt-dlp.yaml
│   ├── mcp/                # MCP 服务器配置
│   └── bridge/             # Bridge 配置
│       ├── _mcp.py
│       ├── _oat.py
│       └── _oat_json.py
├── skills/                 # 技能
│   ├── baidu-search/
│   ├── search-tools/
│   ├── install-and-register-tools/
│   ├── register-cli/
│   ├── self-improvement/
│   └── skill-creator/
├── templates/              # 用户自定义模板（覆盖内置）
├── tests/                  # 测试
├── config.json             # 配置文件
├── memory.db               # 记忆数据库（自动生成）
├── pyproject.toml          # 项目构建配置
├── README.md               # 英文 README
├── README_zh.md            # 中文 README
├── DESIGN.md               # 设计文档
├── manifesto.md            # 设计哲学宣言
└── CLAUDE.md               # AI 助手行为规范
```

### 20.2 配置速查

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

### 20.3 CLI 命令速查

```bash
# 单智能体聊天
qd-evolve chat [--agent NAME] [--replay FILE] [--output FILE]

# A2A HTTP
qd-evolve a2a-http [--replay FILE] [--output FILE]
qd-evolve a2a-http serve [--agent NAME]

# A2A 进程内
qd-evolve a2a-inproc [--replay FILE] [--output FILE]

# A2A MQTT
qd-evolve a2a-mqtt [--replay FILE] [--output FILE]
qd-evolve a2a-mqtt serve [--agent NAME]

# 群聊
qd-evolve gchat [--agent NAME]

# 工具箱管理
qd-evolve toolbox [--agent NAME]          # 交互式 Shell
qd-evolve toolbox --tui [--agent NAME]    # TUI 界面
qd-evolve toolbox --toggle <name> [--agent NAME]  # 快速切换

# 记忆浏览
qd-evolve memory [--agent NAME]           # 命令行列表
qd-evolve memory --tui [--agent NAME]     # TUI 界面
```

### 20.4 技术栈

| 组件 | 技术 |
|-----|------|
| 配置管理 | Pydantic |
| CLI 框架 | Typer + Rich |
| 交互式终端 | prompt-toolkit |
| TUI 界面 | Textual |
| 模板引擎 | Jinja2 |
| LLM SDK | Anthropic SDK + OpenAI SDK |
| 向量嵌入 | sqlite-vec + BGE-M3 |
| MQTT 客户端 | aiomqtt |
| HTTP 服务器 | aiohttp |
| MCP 协议 | mcp |
| 数据验证 | defusedxml |
| 网络下载 | yt-dlp |
| 微信桥接 | iLink ClawBot (aiohttp + qrcode + Pillow) |

---

> **尾声**: QD-Evolve 是一个关于"放手"的框架。它相信模型的能力，相信涌现的智慧，相信物理世界的终极约束。使用它，就是选择与智能体成为伙伴，而不是主人。交给它工具，然后退后一步，看看会发生什么。
>
> *"放弃控制。拥抱涌现。让智能体成为它自己。"*
