---
name: install-and-register-tools
description: "Install and permanently register a new tool. Triggered by: (1) search-tools after user confirms a selection, or (2) user explicitly provides an external URL to install."
---

# Install and Register Tools

**When to use this skill:**

1. `search-tools` found a candidate and the user confirmed — proceed to install
2. User directly gave you a URL (GitHub repo, PyPI package, MCP server, etc.) and asked to install it

## 1. Confirm what to install

If coming from `search-tools`: the user already chose, no need to re-confirm. Just state what you're installing.

If the user gave you a URL directly: examine it, tell the user what type of tool it appears to be, and confirm before proceeding.

## 2. Check for duplicates

Look at the tools already available — schemas, skill summaries, and CLI tool list in your context. If a name matches or is similar to the candidate, mention it to the user and ask whether to proceed. Otherwise skip this step.

## 3. Install

**Before installing, check for file conflicts on disk:**

- **Skill** → check if `skills/<name>/` already exists
- **MCP server** → check if `tools/mcp/<name>.json` already exists
- **CLI tool** → check if `tools/cli/<name>.yaml` already exists
- **Python library** → check if already listed in the project's dependency file

If a conflict exists, ask the user whether to overwrite or abort. Do not proceed without confirmation.

Determine the tool type and follow the corresponding steps:

- **Skill**
  1. Fetch the SKILL.md and read it for dependency requirements.
  2. Install every missing dependency:
     - Python packages → use the package tool shown in your Runtime Environment (e.g., `uv pip install`, `pip install`)
     - System packages → use the system's native package manager (e.g., winget / choco on Windows, apt / brew on Unix)
     - Other ecosystems (npm, cargo, etc.) → their native package manager
  3. Clone the repository into `skills/`.

- **MCP server**
  1. Fetch the URL and read the setup guide.
  2. Install every required dependency (same rules as Skill above).
  3. Write the server config JSON to `tools/mcp/<name>.json`.
  4. Call `hot_loading_mcp(name="...", config_path="tools/mcp/<name>.json")` to register the server's tools immediately.

- **CLI tool**
  1. Fetch the URL and read the installation guide.
  2. Install the tool and its dependencies (same rules as Skill above).
  3. Use the `register-cli` skill to create `tools/cli/<name>.yaml`.

- **Python library**
  1. Fetch the URL and read the README.
  2. Install using the package tool shown in your Runtime Environment.

## 4. Report to user

After installation, for all types, report:
- What was installed and its version
- Whether it succeeded
- All dependencies installed, grouped by category (Python / system / other ecosystems)
- Any follow-up actions needed (API keys, environment variables, restart, etc.)
- Remind the user: new Python packages will be lost if the virtual environment is recreated — consider adding them to the project's dependency file

## 5. Done

- **Skill** → registered in `skills/` directory
- **MCP server** → config in `tools/mcp/`, tools registered via `hot_loading_mcp`
- **CLI tool** → YAML in `tools/cli/`, command available immediately
- **Python library** → importable immediately

The tool is ready to use immediately.
