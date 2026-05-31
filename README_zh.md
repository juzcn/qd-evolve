# QD-Evolve

[![PyPI version](https://img.shields.io/pypi/v/qd-evolve)](https://pypi.org/project/qd-evolve/)

支持 A2A 协议、群聊、持久化记忆和可扩展工具系统的多智能体 AI 框架。

- [DESIGN.md](DESIGN.md) — 设计哲学、不变量、架构与实现

## 安装

### 前置条件

- **Python 3.13+**
- **Mosquitto v5 代理**（仅 MQTT/GChat 模式需要）— [下载](https://mosquitto.org/download/)

### 从 PyPI 安装

```bash
pip install qd-evolve

# 或使用 uv
uv add qd-evolve

# 可选：BOAT 桥接扩展
pip install qd-evolve[boat]
```

### 从源码安装

```bash
git clone https://github.com/juzcn/qd-evolve
cd qd-evolve

# 安装依赖
uv sync

# 可选：BOAT 桥接扩展
uv sync --extra boat
```

### 配置

在工作目录中创建 `config.json`。单智能体对话的最小配置：

```json
{
  "env_vars": {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1"
  },
  "default_provider": "deepseek",
  "default_model": "deepseek-v4-pro",
  "providers": [
    {
      "name": "deepseek",
      "api_key": "...",
      "base_url": "https://api.deepseek.com",
      "api": "openai-completions",
      "models": [
        { "name": "deepseek-v4-pro", "reasoning": true, "context_window": 1000000, "max_tokens": 131072 }
      ]
    }
  ],
  "agents_config": {
    "agents": [
      {
        "name": "default",
        "toolbox": {
          "tools": {
            "load_func": "preload",
            "load_skill": "preload",
            "load_cli": "preload"
          }
        }
      }
    ]
  }
}
```

完整的多智能体、MQTT 和工具箱配置请参见下方[配置](#配置-1)章节。

### 验证

```bash
qd-evolve chat --agent default
```

## 快速开始

```bash
# 单智能体对话
qd-evolve chat --agent default

# 基于 HTTP 的多智能体 A2A 对话
qd-evolve a2a-http

# 将智能体作为独立的 A2A HTTP 服务器运行
qd-evolve a2a-http serve --agent <name>

# 多智能体进程内对话（所有智能体本地加载，无需网络）
qd-evolve a2a-inproc

# 基于 MQTT 的多智能体对话（需要 Mosquitto v5 代理）
qd-evolve a2a-mqtt

# 将智能体作为 MQTT 可访问的服务器运行
qd-evolve a2a-mqtt serve --agent <name>

# 群聊 — 微信风格多智能体群组（需要 Mosquitto v5 代理）
# 支持：AI 智能体、终端人类智能体、微信人类智能体
qd-evolve gchat --agent <name>

# 管理工具的启用/禁用/预加载
qd-evolve toolbox --agent <name>

# 浏览和搜索对话记忆
qd-evolve memory --agent <name>
```

## 五大系统

| 系统 | 入口 | 传输方式 | 适用场景 |
|------|------|----------|----------|
| Chat | `qd-evolve chat --agent <name>` | 仅进程内 | 单智能体，无需网络 |
| A2A Inproc | `qd-evolve a2a-inproc` | 仅进程内 | 多智能体进程内通信 |
| A2A | `qd-evolve a2a-http` | HTTP + 进程内 | 通过 HTTP/SSE 的多智能体通信 |
| MQTT | `qd-evolve a2a-mqtt` | MQTT v5 + 进程内 | 通过 MQTT 的多智能体通信 |
| GChat | `qd-evolve gchat` | MQTT v5（群组主题） | 微信风格群聊 |

每个系统完全独立 — 彼此之间没有协议回退。完整架构见 [DESIGN.md](DESIGN.md)。

## 配置

所有配置通过 `config.json` 完成。没有 CLI 配置命令，也不需要 `.env` 文件。

### 提供商与模型

```json
{
  "default_provider": "openai",
  "default_model": "gpt-4o",
  "providers": [
    {
      "name": "openai",
      "api_key": "sk-...",
      "base_url": "https://api.openai.com/v1",
      "api": "openai-completions",
      "models": [
        { "name": "gpt-4o", "context_window": 128000, "max_tokens": 4096 }
      ]
    },
    {
      "name": "anthropic",
      "api_key": "sk-ant-...",
      "api": "anthropic",
      "models": [
        { "name": "claude-sonnet-4-6", "context_window": 200000, "max_tokens": 8192 }
      ]
    },
    {
      "name": "deepseek",
      "api_key": "...",
      "base_url": "https://api.deepseek.com",
      "api": "openai-completions",
      "models": [
        { "name": "deepseek-v4-pro", "reasoning": true, "context_window": 1000000, "max_tokens": 131072 }
      ]
    }
  ]
}
```

三种 API 类型：`openai-completions`、`openai-response`、`anthropic`。通过提供商级别的 `api` 字段设置。流式输出为全局设置（`stream` 字段）。推理/思考为模型级别设置（`reasoning: true`）。

### 多智能体

```json
{
  "agents_config": {
    "chat_agent": "planner",
    "agents": [
      {
        "name": "planner",
        "description": "规划并委派任务",
        "provider": "openai",
        "model": "gpt-4o",
        "memory_db": "planner.db",
        "server": { "host": "127.0.0.1", "port": 8001 },
        "toolbox": { "tools": {} }
      },
      {
        "name": "human",
        "description": "用于审批的人类",
        "provider": "human",
        "server": { "host": "127.0.0.1", "port": 8002 }
      },
      {
        "name": "wechat_user",
        "description": "通过微信 iLink 接入的人类",
        "provider": "wechat-human",
        "server": { "host": "127.0.0.1", "port": 8003 }
      }
    ]
  }
}
```

每个智能体可单独设置提供商/模型，未设置时使用全局回退。`provider: "human"` 表示终端人类智能体，`"wechat-human"` 表示微信 iLink 桥接。每个智能体拥有独立的记忆数据库、服务器配置和工具箱状态。微信人类智能体通过 `wechat_session` 字段持久化其会话令牌。

### MQTT 代理

```json
{
  "agents_config": {
    "mqtt_broker": {
      "host": "127.0.0.1",
      "port": 1883
    }
  }
}
```

需要外部 Mosquitto v5 代理。

### 工具箱

每个智能体的工具启用/禁用/预加载，通过 `qd-evolve toolbox --agent <name>`（Textual TUI）或直接编辑 `config.json` 管理。

```json
{
  "agents_config": {
    "agents": [{
      "name": "planner",
      "toolbox": {
        "tools": { "run_shell": "preload", "web_search": "enabled" },
        "mcp_servers": { "filesystem": "disabled" },
        "bridge": { "oat:boat": "enabled", "oat:coat": "disabled" },
        "cli": { "git": "preload" },
        "skills": { "code-review": "preload" }
      }
    }]
  }
}
```

三种状态：`enabled`（按需加载 schema）、`preload`（启动时加载 schema）、`disabled`（智能体不可见）。

## 运行时功能

### 斜杠命令

| 命令 | 描述 |
|------|------|
| `/models` | 切换提供商/模型 |
| `/agents` | 切换智能体 |
| `/tools` | 列出工具 |
| `/load <tool>` | 加载工具 schema |
| `/memory` | 列出已保存的记忆及完整内容 |
| `/compress` | 强制压缩上下文 |
| `/clear` | 清空对话 |
| `/help` | 显示帮助 |
| `/quit` | 退出 |

### 心跳检测

智能体管理的空闲检测。当 `heartbeat_idle_seconds` 时间内无活动时，向 LLM 发送心跳提示。如果 LLM 回复 `"."`，则保持静默。设为 `0` 可禁用。模式相关的提示模板会自动选择。

### 重放模式

`--replay <file>` 输入预录制的输入用于自动化测试。`--output <file>` 捕获输出。

### Token 统计

每轮和累计的输入/输出 token 数量，以及上下文窗口使用百分比。

## A2A 协议

完整的 [A2A v1.0](https://google.github.io/A2A/) 实现：

- **智能体发现**：`/.well-known/agent.json`
- **方法**：`message/send`、`message/stream`、`tasks/get`、`tasks/cancel`、`tasks/resubscribe`、`tasks/pushNotification`、`agent/getExtendedAgentCard`
- **任务生命周期**：submitted → working → completed / failed / canceled / input_required
- **SSE 流式传输**：`message/stream` 返回 `StreamResponse` 事件
- **推送通知**：任务完成时通过 webhook 回调

## 群聊

基于 MQTT 的微信风格多智能体群组。所有已配置的智能体组成一个群组。

- **AI 智能体**：后台循环处理 `@提及`，并行运行智能体，发布回复
- **终端人类智能体**（`provider: "human"`）：交互式提示 — 输入消息，查看群组动态
- **微信人类智能体**（`provider: "wechat-human"`）：双向微信 iLink 桥接 — 长轮询接收微信消息，将群组回复转发回微信。启动时扫码登录，会话持久化到 `config.json`
- `@all` 提及所有人；`@agent_name` 指定某个智能体

## 项目结构

```
qd-evolve/
├── qd_evolve/       # 主包（agent、core、tools、utils、_templates）
├── tools/           # 用户工具（func、cli、mcp、bridge）
├── skills/          # 技能（SKILL.md 文件）
├── templates/       # 用户 Jinja2 模板覆盖
├── tests/           # pytest 测试套件
├── config.json      # 全部配置
├── memory.db        # 对话记忆（SQLite + sqlite-vec）
└── pyproject.toml   # 依赖与构建配置
```

架构和完整模块映射见 [DESIGN.md](DESIGN.md)。

## 运行要求

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) 用于依赖管理
- 外部 Mosquitto v5 代理（仅 MQTT/GChat 模式）
- 所配置提供商的 API 密钥

## 许可证

MIT
