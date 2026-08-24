#!/usr/bin/env python3
"""Checks for the file change a chat message carries — no server, no Claude.

A transcript is written by hand into a temp folder and read back through the
panel's own reader, which is the whole of what this asserts: that a change is
attached to the tool call that made it, that a call which changed nothing carries
nothing, that the preview starts where the change does, and that the whole patch
comes back when it is asked for by tool-use id.

    python3 tests/change-check.py

A failure prints the case and exits 1.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from watchtower import transcript as S  # noqa: E402

FAILED = 0


def check(what: str, ok: bool, note: str = "") -> None:
    global FAILED
    print(f"{'  ok  ' if ok else 'FAIL  '}{what}{f'  — {note}' if note else ''}")
    if not ok:
        FAILED += 1


def call(tool_id: str, name: str, args: dict, sidechain: bool = False) -> dict:
    return {"type": "assistant", "timestamp": "2026-08-22T10:00:00Z", "isSidechain": sidechain,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tool_id, "name": name, "input": args}]}}


def result(tool_id: str, payload: object, sidechain: bool = False) -> dict:
    return {"type": "user", "timestamp": "2026-08-22T10:00:01Z", "isSidechain": sidechain,
            "toolUseResult": payload,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}]}}


# A patch as Claude Code writes one: hunks with the line numbers the file has,
# and three lines of context in front of the first thing that changed.
PATCH = [{"oldStart": 40, "oldLines": 6, "newStart": 40, "newLines": 7,
          "lines": [" first", " second", " third",
                    "-was this", "+is this now", "+and this", " fourth"]}]

home = tempfile.mkdtemp()
cwd = "/tmp/a-project"
project = Path(home) / "-tmp-a-project"
project.mkdir(parents=True)
S.PROJECT_DIR = Path(home)
lines = [
    {"type": "user", "timestamp": "2026-08-22T09:59:00Z",
     "message": {"role": "user", "content": "change the thing"}},
    call("toolu_edit", "Edit", {"file_path": "/tmp/a-project/thing.py"}),
    result("toolu_edit", {"filePath": "/tmp/a-project/thing.py", "structuredPatch": PATCH}),
    call("toolu_bash", "Bash", {"command": "ls -la"}),
    result("toolu_bash", {"stdout": "thing.py", "stderr": "", "interrupted": False}),
    call("toolu_write", "Write", {"file_path": "/tmp/a-project/new.txt"}),
    result("toolu_write", {"filePath": "/tmp/a-project/new.txt", "content": "one\ntwo\n"}),
    call("toolu_side", "Edit", {"file_path": "/tmp/a-project/agent.py"}, True),
    result("toolu_side", {"filePath": "/tmp/a-project/agent.py", "structuredPatch": PATCH}, True),
]
(project / "s1.jsonl").write_text("\n".join(json.dumps(one) for one in lines) + "\n")

read = S.read_transcript("s1", cwd, 40)
tools = {t["name"]: t for message in read["messages"] for t in message["tools"]}
check("the reader found every tool call", set(tools) >= {"Edit", "Bash", "Write"}, str(sorted(tools)))

edit = tools.get("Edit", {}).get("change")
check("an edit carries the change it made", bool(edit), str(tools.get("Edit")))
check("counted the way a patch counts",
      edit and edit["added"] == 2 and edit["removed"] == 1, str(edit))
check("and knows which file it was", edit and edit["path"].endswith("thing.py"))
check("the change is filed under the call that made it",
      edit and edit["id"] == "toolu_edit", str(edit and edit["id"]))

# The preview is the part worth previewing: a hunk opens with context, and three
# unchanged lines followed by a fade is a preview of the part you did not want.
check("the preview starts one line above the first thing that changed",
      edit and edit["preview"][:3] == ["@@ -40,6 +40,7 @@", " third", "-was this"],
      str(edit and edit["preview"][:3]))

check("a command that printed something carries no change",
      "change" not in tools.get("Bash", {}), str(tools.get("Bash")))
written = tools.get("Write", {}).get("change")
check("a file written whole is shown as all added",
      written and written["added"] == 2 and written["removed"] == 0, str(written))
check("what a subagent did stays out, as the rest of a sidechain does",
      not any("agent.py" in json.dumps(m) for m in read["messages"]),
      json.dumps(read["messages"])[:120])

# The whole of it, on demand, by the id the preview carries.
whole = S.read_change("s1", cwd, "toolu_edit")
check("the whole change comes back when it is asked for", whole["ok"], str(whole.get("message")))
check("and it is the whole of it",
      whole["text"].splitlines() == ["@@ -40,6 +40,7 @@", " first", " second", " third",
                                     "-was this", "+is this now", "+and this", " fourth"],
      str(whole["text"].splitlines()))
check("counted the same way twice",
      (whole["added"], whole["removed"]) == (edit["added"], edit["removed"]))
check("a change nobody made is not invented",
      S.read_change("s1", cwd, "toolu_nothing")["ok"] is False)

print()
print("all ok" if not FAILED else f"{FAILED} failed")
sys.exit(1 if FAILED else 0)
