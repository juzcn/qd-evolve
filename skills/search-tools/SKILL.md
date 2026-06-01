---
name: search-tools
description: "Search for new tools via web search. Triggered by: (1) user asks to find a tool but has no specific URL, or (2) you discover a tool gap and no existing tool can handle the task."
---

# Search Tools

**When to use this skill:**

1. User asks to search for a certain kind of tool, but doesn't have a specific URL
2. You discover a tool gap — no existing tool can directly handle the task. Don't guess, don't pip install blindly. Search first.

## 1. Clarify the gap

What capability do you need? What should go in and come out?

## 2. Search

Use web search — don't rely on training data. Search in this priority order:

1. **Skill** — search for `SKILL.md <capability>` or check GitHub topics
2. **MCP server** — search for `<capability> MCP server`
3. **CLI tool** — search for `<capability> CLI tool` or `<capability> command line`
4. **Python library** — last resort, requires writing wrapper code

## 3. Share findings

For each candidate, present:

| Field | Description |
|-------|-------------|
| **Name** | Tool name |
| **Type** | skill / mcp / cli / python library |
| **Developer** | Who maintains it (individual, org, company) |
| **Popularity** | GitHub stars, PyPI downloads, or community size |
| **URL** | Repository, docs, or homepage |
| **Notes** | Highlights, concerns, or relevant user feedback |

List the candidates and discuss with the user to decide. Don't install anything yet — that's what `install-and-register-tools` is for.

## 4. Hand off

Once the user confirms one or more choices, load the `install-and-register-tools` skill to install and register them.
