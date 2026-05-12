---
name: cli-register
description: Register CLI tools by analyzing --help output and generating yaml definitions
---

# CLI Tool Register

You can register CLI tools so the agent knows how to use them.

## When the user asks to register a CLI tool

1. Run `<command> --help` to get the help output. If `--help` doesn't work, try `-h`.
2. Analyze the help output and generate a YAML definition following the format below.
3. Check if `tools/cli/<name>.yaml` already exists. If it does, ask the user whether to overwrite or skip. Do not overwrite without confirmation.
4. Create `tools/cli/<name>.yaml` with the YAML content. Use file creation tools, not shell commands.
5. After writing, tell the user the tool is ready to use immediately — no restart needed. The system will auto-detect the new yaml on the next turn or after loading CLI details.

## YAML format

```yaml
name: <tool-name>
command: <actual-command>
description: "<one-line description>"
help_summary: |
  <condensed help with key options>
examples:
  - "<example command 1>"
  - "<example command 2>"
```

## Rules for generating YAML

- **name**: Use the tool's common name (e.g. `pandoc`, `jq`, `ffmpeg`).
- **command**: The actual command to execute. Usually the same as name, but may differ when the binary name differs from the common name (e.g. name `python`, command `python3`).
- **description**: One concise sentence describing what the tool does.
- **help_summary**:
  - If the `--help` output is short (under ~40 lines), include the full output as-is.
  - If the output is long, condense it to only the most important options (under 20 lines). Include the Usage line and key flags only.
- **examples**: 2-3 typical usage examples that cover the most common use cases. Each example should be a valid command string.
