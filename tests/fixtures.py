#!/usr/bin/env python3
"""Stand up a fixture session directory showing every state at once.

The UI checks want a panel with one session in each state, which a real machine
will not reliably have. The panel only believes a session file whose pid is a
process that is actually running — see SessionStore._read_files — so a directory
of hand-written JSON is not enough on its own: this holds a real process open
behind each one.

A sleeping process is not enough either, and that is the part worth knowing. An
active status is only believed while something backs it up: after CPU_WINDOW +
LIVENESS_GRACE — about fifty seconds — a `busy` fixture behind an idle process
correctly settles to `idle`, because that is exactly what the panel is for. So
the fixtures that need to *stay* active are held open by a process burning a few
percent of a core, which is what SessionStore._burning_cpu reads.

Until roughly fifty seconds in, every session reads as working regardless: a
session first seen gets the benefit of the doubt until there are two CPU
readings to compare. Give the panel a minute before believing what you see.

    python3 tests/fixtures.py                  # hold them open until Ctrl-C
    python3 tests/fixtures.py --dir /tmp/fix   # somewhere of your choosing

It prints the two commands to run next. Everything it made goes away on Ctrl-C.

Standard library only, in keeping with the rest of the project.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

# One entry per state the panel draws, plus the two cases that are read rather
# than reported: a session that writes no status at all (the VS Code extension),
# and an active status old enough that nothing backs it up any more.
#
# `busy` says whether the process behind the fixture burns CPU. An active status
# needs one to hold past the first minute; a resting state must not have one, or
# a session that writes no status would be inferred as working.
FIXTURES = [
    {"name": "Working now", "status": "busy", "age": 0.0, "busy": True},
    {"name": "Asking you something", "status": "waiting", "age": 0.0, "busy": False},
    {"name": "Running a command", "status": "shell", "age": 0.0, "busy": True},
    {"name": "Ready", "status": "idle", "age": 0.0, "busy": False},
    # Older than STATUS_TTL with nothing backing it: the panel stops believing
    # the `busy` and settles this one to ready, which is the behaviour that
    # keeps a session that wrote one status at startup from reading as working
    # for the rest of its life.
    {"name": "Stale busy reading", "status": "busy", "age": 600.0, "busy": False},
    # Writes a file once and never a status, so it is read from liveness alone —
    # working here, because the process behind it is.
    {"name": "VS Code extension", "status": None, "age": 0.0, "busy": True,
     "entrypoint": "claude-vscode"},
]

# A few percent of a core, which clears WORKING_CPU with room to spare while
# leaving the machine alone. Sleeping between bursts rather than spinning flat
# out matters: a fixture set should not heat up a laptop for an afternoon.
BURN = (
    "import time\n"
    "while True:\n"
    "    end = time.time() + 0.01\n"
    "    while time.time() < end: pass\n"
    "    time.sleep(0.19)\n"
)


def slug_of(cwd: str) -> str:
    """The folder Claude Code keeps a transcript in, named after the cwd."""
    return "-" + re.sub(r"[^A-Za-z0-9]+", "-", cwd.lstrip("/"))


def write_transcript(project_dir: Path, session_id: str) -> None:
    """A conversation with an Agent call in it, so the drill-in is reachable.

    The tool-use id matches the meta written beside the running subagent below:
    that is the whole join, and without it the tool row is a row like any other.
    """
    project_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "user", "message": {"role": "user",
                                     "content": [{"type": "text", "text": "Look into the rows."}]}},
        {"type": "assistant", "message": {"role": "assistant", "stop_reason": "tool_use",
                                          "content": [
             {"type": "tool_use", "id": "toolu_a1111111111111111", "name": "Task",
              "input": {"subagent_type": "Explore",
                        "description": "Find where the rows are painted"}},
             {"type": "tool_use", "id": "toolu_a2222222222222222", "name": "Task",
              "input": {"subagent_type": "general-purpose",
                        "description": "Summarise the changelog"}}]}},
    ]
    (project_dir / f"{session_id}.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines))


def write_subagents(project_dir: Path, session_id: str) -> None:
    """One running agent and one finished, so the row badge and the drill-in
    have something to show.

    The running one is left mid-tool with a fresh mtime, which is what
    agents.agent_state reads as running — see AGENT_IDLE_SECONDS. It will go
    quiet and read as stopped a couple of minutes in, which is correct and worth
    knowing when a fixture panel is left open.
    """
    folder = project_dir / session_id / "subagents"
    folder.mkdir(parents=True, exist_ok=True)
    agents = [
        ("a1111111111111111", "Explore", "Find where the rows are painted",
         [{"type": "assistant", "isSidechain": True,
           "message": {"role": "assistant", "stop_reason": "tool_use",
                       "content": [{"type": "tool_use", "id": "toolu_f1", "name": "Grep",
                                    "input": {"pattern": "paintListItem"}}]}}]),
        ("a2222222222222222", "general-purpose", "Summarise the changelog",
         [{"type": "assistant", "isSidechain": True,
           "message": {"role": "assistant", "stop_reason": "end_turn",
                       "content": [{"type": "text",
                                    "text": "The changelog covers eight releases."}]}}]),
    ]
    for agent_id, kind, what, lines in agents:
        (folder / f"agent-{agent_id}.meta.json").write_text(json.dumps({
            "agentType": kind, "description": what,
            "toolUseId": f"toolu_{agent_id}", "spawnDepth": 1, "model": "sonnet"}))
        (folder / f"agent-{agent_id}.jsonl").write_text(
            "".join(json.dumps(line) + "\n" for line in lines))


def proc_start(pid: int) -> str | None:
    """Field 22 of /proc/<pid>/stat — the same identity check the panel makes."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # The comm field is parenthesised and may contain spaces, so read after it.
    tail = text.rsplit(")", 1)[-1].split()
    return tail[19] if len(tail) > 19 else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", help="where to write the session files")
    parser.add_argument("--cwd", default=str(Path(__file__).resolve().parents[1]),
                        help="the working folder the fixture sessions claim to be in")
    parser.add_argument("--port", type=int, default=8788, help="port to suggest for the panel")
    args = parser.parse_args()

    if not Path("/proc").is_dir():
        print("fixtures need /proc to make a session file the panel will believe", file=sys.stderr)
        return 1

    made_dir = args.dir is None
    session_dir = Path(args.dir or tempfile.mkdtemp(prefix="watchtower-fixtures-"))
    session_dir.mkdir(parents=True, exist_ok=True)

    children: list[subprocess.Popen] = []
    # What was written outside the fixture directory, to take away again.
    planted: list[Path] = []
    now = time.time()
    for fixture in FIXTURES:
        # The panel reads liveness from CPU and transcript growth. A fixture
        # that must keep an active state needs a process burning some CPU; one
        # at rest needs a process burning none, which a sleep gives exactly.
        command = [sys.executable, "-c", BURN] if fixture["busy"] else ["sleep", "86400"]
        child = subprocess.Popen(command,
                                 stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        children.append(child)
        session_id = str(uuid.uuid4())
        written = (now - fixture["age"]) * 1000
        data = {
            "sessionId": session_id,
            "pid": child.pid,
            "procStart": proc_start(child.pid),
            "cwd": args.cwd,
            "name": fixture["name"],
            "startedAt": int((now - 3600) * 1000),
            "updatedAt": int(written),
        }
        if fixture["status"]:
            data["status"] = fixture["status"]
            data["statusUpdatedAt"] = int(written)
        if fixture.get("entrypoint"):
            data["entrypoint"] = fixture["entrypoint"]
        (session_dir / f"{session_id}.json").write_text(json.dumps(data, indent=2))
        # The first fixture — the working one — is given a conversation with two
        # Agent calls in it and the subagents to match, so the row's agent count
        # and the drill-in behind it have something to show. It goes where
        # Claude Code would have written it: the panel reads transcripts out of
        # your home folder and takes no override for it, so this is written
        # there and taken away again on the way out.
        if fixture is FIXTURES[0]:
            project_dir = Path.home() / ".claude" / "projects" / slug_of(args.cwd)
            write_transcript(project_dir, session_id)
            write_subagents(project_dir, session_id)
            planted += [project_dir / f"{session_id}.jsonl", project_dir / session_id]
        # Backdate the file too: it is the last resort for judging freshness.
        stamp = now - fixture["age"]
        os.utime(session_dir / f"{session_id}.json", (stamp, stamp))

    print(f"{len(FIXTURES)} fixture sessions in {session_dir}\n")
    print("  start the panel against them:")
    print(f"    CLAUDE_WATCHTOWER_SESSION_DIR={session_dir} python3 server.py --port {args.port}\n")
    print("  then, in another terminal:")
    print(f"    PANEL_URL=http://127.0.0.1:{args.port} node tests/ui-check.mjs\n")
    print("Ctrl-C to take them down.")

    def clean(*_) -> None:
        for child in children:
            child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
        if made_dir:
            shutil.rmtree(session_dir, ignore_errors=True)
        for path in planted:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        print("\nfixtures taken down")
        sys.exit(0)

    signal.signal(signal.SIGINT, clean)
    signal.signal(signal.SIGTERM, clean)
    signal.pause()
    return 0


if __name__ == "__main__":
    sys.exit(main())
