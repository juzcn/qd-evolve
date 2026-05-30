---
name: find-tools
description: "Find and evaluate new tools when existing tools cannot directly accomplish the task. Use BEFORE guessing commands, pip installing, or improvising with run_shell/run_python. Triggers: format conversion, data analysis, image processing, or any task where no current tool is a direct fit."
---

# Find Tools

**When to use this skill:** Whenever no existing tool can directly accomplish the user's task. Examples: converting file formats (md→pdf, csv→xlsx), processing images, analyzing data, or any task where you'd otherwise guess commands or pip install blindly.

**Do NOT:** guess tool names, try random pip installs, or improvise with `run_shell`/`run_python` without first searching for a proper solution.

Follow these steps in order.

## Steps

### 1. Search for the right tool
Web search to identify the best tool. Don't guess tool names from memory — your training data may be outdated or miss better alternatives. Prefer battle-tested solutions.

### 2. Install and hot-load — don't ask yet
Install the tool and make it usable in the current session. Confirm it works before discussing permanent registration. The user doesn't need to be involved at this stage.

Use the appropriate `install_*` tool based on tool type:
- **Python library** → call `install_func` with name, description, input_schema, and python_code. The tool is immediately callable.
- **MCP server** → call `install_mcp` with name and config dict. The server's tools are immediately callable.
- **Skill** → call `install_skill` with name and github_url. The skill is immediately loadable via `load_skill`.
- **CLI tool** → install via `run_shell` (pip install, etc.), then use cli-register skill to create the YAML definition.

### 3. Report the result (then continue to step 4)
Tell the user what happened:
- If it worked: present the output clearly, then **immediately proceed to step 4 without stopping**.
- If it failed: explain why and try alternatives. In this case, step 4 is skipped.

Do not end your response after reporting success. You must go to step 4.

### 4. MUST ask about registration (only after success)
**This step is mandatory.** Once the tool runs successfully, you MUST output the following, word for word:

> "I successfully installed and ran [tool name]. Would you like me to register this tool so I can reuse it in future sessions?"

Then present the registration action (choose the most relevant one based on what you installed):
- Python library → call `register_func` with name
- MCP server → call `register_mcp` with name
- Skill → call `register_skill` with name
- CLI tool → use cli-register skill

Do not finish your response without asking this registration question. If you skip this, you have not followed the skill correctly.