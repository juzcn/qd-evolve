---
name: install-and-register-tools
description: "Install and permanently register a new tool. Triggered by: (1) search-tools after user confirms a selection, or (2) user explicitly provides an external URL to install."
---

# Install and Register Tools

**When to use this skill:**

1. `search-tools` found a candidate and the user confirmed — proceed to install
2. User directly gave you a URL (GitHub repo, PyPI package, MCP server, etc.) and asked to install it

## 1. Confirm what to install

If coming from `search-tools`: the user already chose, no need to re-confirm. Just say what you're installing.

If the user gave you a URL directly: look at it, tell the user what type of tool it appears to be, and confirm before proceeding.

## 2. Check for duplicates

Check your context — the tool schemas, skill summaries, and CLI tool list you already see. If a name matches or is highly similar to the candidate, mention it to the user and ask whether to proceed. Otherwise skip this step.

## 3. Install

**Before installing, check for name conflicts:**

- **Skill** → check if `skills/<name>/` already exists
- **MCP server** → check if `tools/mcp/<name>.json` already exists
- **CLI tool** → check if `tools/cli/<name>.yaml` already exists
- **Python library** → check if already listed in `pyproject.toml`

If a conflict exists, ask the user whether to overwrite or abort. Do not proceed without confirmation.

By type:

- **Skill** → fetch the SKILL.md from the URL first. Check for required dependencies and install them as needed. Then clone the repository into `skills/`.
- **MCP server** → fetch the URL, read the setup guide. Extract the server config JSON and write it to `tools/mcp/<name>.json`. Install any required dependencies. Then call `hot_loading_mcp` with name and config to hot-load the server — this spawns the process, discovers tools, and registers them for immediate use.
- **CLI tool** → fetch the URL, read the installation guide. For Python-based CLI tools, use `uv pip install` and add to `pyproject.toml`. For system binaries, use the appropriate package manager. After installing, use the register-cli skill to create the YAML definition.
- **Python library** → fetch the URL, read the installation guide. For Python dependencies, use `uv pip install` and add them to `pyproject.toml`. For other dependencies, follow the guide.

After installation, for all types, report the result to the user: what was installed, whether it succeeded, and any follow-up actions needed (restart, env vars, API keys, etc.).

## 4. Done

- **Skill** → already in `skills/` directory
- **MCP server** → config already in `tools/mcp/`
- **CLI tool** → YAML already created
- **Python library** → already in `pyproject.toml`

The tool is ready to use immediately and across sessions.
