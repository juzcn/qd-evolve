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

### 3. Report the result (then continue to step 4)
Tell the user what happened:
- If it worked: present the output clearly, then **immediately proceed to step 4 without stopping**.
- If it failed: explain why and try alternatives. In this case, step 4 is skipped.

Do not end your response after reporting success. You must go to step 4.

### 4. MUST ask about registration (only after success)
**This step is mandatory.** Once the tool runs successfully, you MUST output the following, word for word:

> "I successfully installed and ran [tool name]. Would you like me to register this tool so I can reuse it in future sessions?"

Then present the registration options (choose the most relevant one based on what you installed):

- Python library → `qd_evolve/tools/<name>.py`
- CLI tool → use cli-register → `tools/cli/<name>.yaml`
- MCP server → `tools/mcp/<name>.json`
- Skill → `tools/skills/<name>/`

Do not finish your response without asking this registration question. If you skip this, you have not followed the skill correctly.