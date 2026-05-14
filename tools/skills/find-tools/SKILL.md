---
name: find-tools
description: Find and evaluate new tools when existing tools can't satisfy the user's request. Use when you need to search for, install, and try CLI tools, Python libraries, or external services.
---

# Find Tools

When available tools cannot satisfy the user's request, follow these steps in order.

## Steps

### 1. Search for the right tool
Web search to identify the best tool. Don't guess tool names from memory — your training data may be outdated or miss better alternatives. Prefer battle-tested solutions.

### 2. Try it first — don't ask yet
Install and execute the tool. Confirm it works before discussing registration. The user doesn't need to be involved at this stage.

### 3. Report the result
Tell the user what happened. If it worked, present the output. If it failed, explain why and try alternatives.

### 4. Offer to register (only after success)
Once the tool runs successfully, tell the user what you installed and ask whether to register it for future sessions.

**Registration paths:**
- Python libraries → `qd_evolve/tools/<name>.py`
- CLI tools → use cli-register → `tools/cli/<name>.yaml`
- MCP servers → `tools/mcp/<name>.json`
- Skills → `tools/skills/<name>/`
