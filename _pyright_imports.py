"""Extract import-related pyright errors."""
import json
import subprocess
import sys

result = subprocess.run(
    ["uv", "run", "pyright", "qd_evolve/", "tests/", "--outputjson"],
    capture_output=True, text=True, timeout=120
)

data = json.loads(result.stdout)
errs = data.get("generalDiagnostics", [])

# Filter for import/undefined-variable type issues
target_rules = {
    "reportUndefinedVariable",
    "reportUnboundVariable",
    "reportPossiblyUnboundVariable",
    "reportAttributeAccessIssue",
}

import_issues = [e for e in errs if e["rule"] in target_rules]

for e in import_issues:
    fname = e["file"]
    # Keep only the relative part
    needle = "qd-evolve/"
    idx = fname.find(needle)
    if idx >= 0:
        fname = fname[idx + len(needle):]
    fname = fname.replace("\\", "/")
    line = e["range"]["start"]["line"] + 1
    col = e["range"]["start"]["character"] + 1
    msg = e["message"].encode("ascii", errors="replace").decode("ascii")
    print(f"{fname}:{line}:{col}  [{e['rule']}] {msg}")
