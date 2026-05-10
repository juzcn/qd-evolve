# CLI Tool Register

You can register CLI tools so the agent knows how to use them.

## When the user asks to register a CLI tool

1. Run `<command> --help` via `run_shell` to get the help output. If `--help` doesn't work, try `-h`.
2. Analyze the help output and generate a YAML definition.
3. Check if `tools/cli/<name>.yaml` already exists using `list_directory` on `tools/cli`. If the file already exists, ask the user whether to overwrite or skip. Do not overwrite without confirmation.
4. Write the YAML file using the `write_file` tool with path `tools/cli/<name>.yaml`. Do NOT use shell commands to write files.
5. After writing, tell the user the tool is ready to use immediately — no restart needed. The system will auto-detect the new yaml on next `load_cli_detail` call or `/cli` command.

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
- **command**: The actual command to run. May differ from name (e.g. name `python3`, command `python3`).
- **description**: One concise sentence describing what the tool does.
- **help_summary**:
  - If the `--help` output is short (under ~40 lines), include the full output as-is.
  - If the output is long, condense it to only the most important options (under 20 lines). Include the Usage line and key flags only.
- **examples**: 2-3 typical usage examples that cover the most common use cases. Each example should be a valid command string.
