# A2A over MQTT — Group 模式设计方案

**状态**: 讨论中，待确认

## 需求

group 模式下，所有 agent 能看到别的 agent 的输出，用 A2A over MQTT 实现。

## 现有架构

- **Transport**: InprocTransport（同进程零延迟）+ HttpTransport（跨进程 aiohttp JSON-RPC + SSE）
- **TransportRouter._pick()**: 本地注册表有 → inproc，否则 → http
- **事件流**: Agent → `_on_event` → A2AAgent `_push_event` → subscribers → CLI `Live(Group)` 渲染
- **当前 A2A 是点对点**: `delegate_to` / `send_task` 是一个 agent 直接调另一个 agent，没有广播机制

## MQTT 库选型

- **aiomqtt 2.x** — 基于 paho-mqtt 的 async 封装，活跃维护，支持 Python 3.13，最佳客户端选择
- **内嵌 broker** — amqtt 是唯一还在维护的纯 Python MQTT broker，但质量一般
- **务实方案** — 默认连 `localhost:1883`，开发时用 Docker 起一个 Mosquitto

## Config 设计

```json
{
  "agents_config": {
    "group": "default",
    "mqtt": {
      "host": "127.0.0.1",
      "port": 1883,
      "prefix": "qd-evolve"
    },
    "agents": [...]
  }
}
```

- `group` 为空或缺失 → 现有行为，不变
- `group` 有值 → 启用 MQTT transport，加入该 group

**待确认**: group 放在 `agents_config` 级别（所有 agent 同 group）还是每个 `AgentEntry` 单独配 group（允许跨 group）？

## Topic 设计

```
qd-evolve/{group}/agents/{agent_name}/card      — AgentCard 广播（保留）
qd-evolve/{group}/agents/{agent_name}/events     — 事件流（新增核心）
qd-evolve/{group}/tasks/{task_id}/messages       — 任务消息（A2A 标准）
```

关键 topic 是 `events`：每个 agent 把自己的事件发布到这里，同 group 的其他 agent 订阅后注入到自己的消息流中。

## 事件广播机制

- Agent 运行时，`_push_event` 除了推给本地 subscribers，还 publish 到 MQTT `events` topic
- 同 group 的其他 agent 订阅该 topic，收到后作为**观察者消息**注入（类似 `[Agent-B] 正在执行 run_shell...`）
- **待确认**: 只广播最终回复 + 状态摘要，不广播 reasoning 和 tool 原始输出（避免噪音）？

## Transport 扩展

在 `TransportRouter._pick()` 中增加 MQTT 分支：
- 本地注册表有 → inproc
- 同 group → MQTT（通过 broker 转发 A2A 消息）
- 否则 → http

## Broker 策略

- 默认连 `127.0.0.1:1883`
- 文档建议开发时 `docker run -p 1883:1883 eclipse-mosquitto`
- **不内嵌 broker** — 保持零外部依赖的原则，MQTT 是可选功能

**待确认**: 不内嵌 broker，要求用户自备 Mosquitto，可接受吗？

## 待确认问题

1. 广播范围：只广播回复+状态（非全部输出）是否合适？
2. Broker：不内嵌，要求用户自备 Mosquitto，可接受吗？
3. Group 粒度：`agents_config` 级别（全局同 group）还是 `AgentEntry` 级别（允许跨 group）？
