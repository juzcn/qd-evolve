# Design

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

No environment variables, no CLI config, no database-backed settings. The trade-off: less flexible for containerized deployment where env vars are idiomatic. The bet: simplicity and discoverability matter more for a framework meant to be understood and modified.

### Physical isolation over software security

No sandbox, no permission system, no content filter. The trade-off: the model can do dangerous things if given dangerous tools. The design response: don't give it dangerous tools. The security boundary is what the model can physically reach — network access, filesystem access, process execution. A human presses the last button.

## Invariants

These are the constraints that every change must preserve:

1. **The agent loop is `reason → act → observe`.** No phases, no templates, no planning steps.
2. **Agents compose by wrapping, not inheritance.** Each layer adds exactly one concern.
3. **No more than one remote transport at a time.** In-process + HTTP, or in-process + MQTT, never both.
4. **MQTT transport is sole-consumer.** Group chat gets its own transport connection.
5. **Human and AI agents share the same protocol.** The transport layer doesn't distinguish them.
6. **Memory is save + recall only.** No forgetting curves, no episodic structures, no automatic categorization.
7. **Configuration is one file.** No env vars, no scattered config.
8. **Security is physical, not digital.** No software permission system that the model could reason past.
