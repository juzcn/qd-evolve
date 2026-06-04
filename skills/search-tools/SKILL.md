---
name: search-tools
description: "Use this when none of your existing tools can handle the current task. Don't guess package names or blindly pip install — search the web first to find the right tool for this ecosystem."
---

# Search Tools

**When to use this skill — read these conditions carefully:**

You've checked your Functions and Skills. You've tried `activate_func` on promising candidates. Nothing works. You're about to reach for a Python library from memory or type `pip install` — **stop. Open this skill instead.**

Concrete triggers (any one is enough):
- You just discovered that no activated tool can do the job — there's a genuine gap
- The user asked for a tool or capability you don't have, with no specific URL
- You caught yourself about to `pip install` a package you "remember" from training data
- A tool you tried failed and there's no obvious alternative in your toolbox

## 1. Clarify the gap

What capability do you need? What should go in and come out? State it in one sentence.

## 2. Search the web for current tools

Don't rely on training data — tools change, new ones appear, old ones die. Search in this order:

1. **MCP server** — search for `<capability> MCP server`
2. **Skill / Agent tool** — search for `<capability> AI agent tool` or `<capability> Claude skill`
3. **CLI tool** — search for `<capability> CLI tool` or `<capability> command line`
4. **Python/Node library** — last resort, requires writing wrapper code

## 3. Present findings

For each candidate, fill this table:

| Field | Description |
|-------|-------------|
| **Name** | Tool name |
| **Type** | mcp / skill / cli / library |
| **Developer** | Who maintains it (individual, org, company) |
| **Popularity** | GitHub stars, PyPI downloads, or community size |
| **URL** | Repository, docs, or homepage |
| **Why it fits** | How it addresses the gap you identified in step 1 |

Present the candidates and let the user decide. Don't install anything — hand off to `install-and-register-tools` after the user confirms.
