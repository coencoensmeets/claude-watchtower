"""The subagents a session spawned, and which of them are still going.

Claude Code does not write a subagent into the session's transcript. It gives
each one a file of its own, in a folder named after the session:

    <project>/<session-id>.jsonl              the session
    <project>/<session-id>/subagents/
        agent-<agentId>.jsonl                 the subagent, same shape
        agent-<agentId>.meta.json             what it is

The meta is the useful part. It names the agent, says what it was asked to do,
and carries the `toolUseId` of the `Task` call that spawned it — so a tool row in
the conversation and a subagent transcript can be tied together exactly rather
than guessed at.

transcript.py means this session's own transcript. This module means the agents
it spawned; nothing here reads the session's file.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from watchtower.transcript import reverse_lines, transcript_paths

# An id off the wire is a path component, and this panel answers to a phone over
# the network. Anything that is not this shape never reaches the filesystem.
AGENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# How long a subagent may go without writing before it is presumed gone. A
# subagent between turns is quiet for seconds; one that was killed is quiet
# forever, and would otherwise read as running until the panel restarts.
AGENT_IDLE_SECONDS = 120

# How far back to look for the last thing a subagent said. Its final message is
# the last line or very near it; a handful of attachment entries can follow.
STATE_PATIENCE = 40


def subagent_dir(session_id: str, cwd: str) -> Path | None:
    """The folder holding this session's subagents, if it has one.

    Derived from the transcript rather than rebuilt from the cwd, so the
    direct-hit-then-glob fallback in transcript_paths keeps working for a session
    whose folder no longer matches its slug.
    """
    for path in transcript_paths(session_id, cwd):
        found = path.parent / session_id / "subagents"
        if found.is_dir():
            return found
    return None


def _last_spoken(path: Path) -> dict | None:
    """The newest assistant entry in a subagent's transcript."""
    seen = 0
    for line in reverse_lines(path):
        seen += 1
        if seen > STATE_PATIENCE:
            return None
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") == "assistant":
            return entry
    return None


def agent_state(path: Path, now: float | None = None) -> str:
    """Whether a subagent is running, finished, or gone.

    The authoritative answer is in the parent transcript — a `tool_result` for
    the spawning call means the agent came back — but on a long conversation that
    result is far behind the tail the panel reads, and a finished agent would
    read as running forever. So the answer is taken from the agent's own file,
    which is the one being opened anyway: a final message that ended the turn
    without calling a tool is an agent that reported back.

    A file mid-tool is running while it is still being written to, and stopped
    once it has gone quiet. Read a moment too early this says "done" of an agent
    that is only thinking, and the next poll corrects it — which is the right way
    round for a count on a row.
    """
    try:
        quiet = (now or time.time()) - path.stat().st_mtime
    except OSError:
        return "stopped"
    spoken = _last_spoken(path)
    if spoken:
        message = spoken.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        called = isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") == "tool_use"
            for block in content)
        if not called and message.get("stop_reason") == "end_turn":
            return "done"
    return "stopped" if quiet > AGENT_IDLE_SECONDS else "running"


def list_subagents(session_id: str, cwd: str) -> list[dict]:
    """Every subagent this session spawned, newest and busiest first.

    Reads the metas, which are a couple of hundred bytes each, and one stat per
    agent. The state costs a read of the tail of each transcript; callers that
    poll are expected to remember which agents were already finished, since a
    finished one does not change its mind.
    """
    folder = subagent_dir(session_id, cwd)
    if folder is None:
        return []
    found = []
    try:
        metas = sorted(folder.glob("agent-*.meta.json"))
    except OSError:
        return []
    for meta_path in metas:
        agent_id = meta_path.name[len("agent-"):-len(".meta.json")]
        if not AGENT_ID.match(agent_id):
            continue
        path = folder / f"agent-{agent_id}.jsonl"
        try:
            at = path.stat().st_mtime
        except OSError:
            continue          # the meta is written first; the transcript follows
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        item = {
            "agentId": agent_id,
            "agentType": str(meta.get("agentType") or "agent")[:60],
            "description": str(meta.get("description") or "")[:200],
            "toolUseId": str(meta.get("toolUseId") or ""),
            "spawnDepth": int(meta.get("spawnDepth") or 1),
            "state": agent_state(path),
            "at": at,
        }
        # Only where the file has one: older metas carry no model, and a null on
        # every poll says less than an absent key.
        if isinstance(meta.get("model"), str) and meta["model"]:
            item["model"] = meta["model"][:60]
        found.append(item)
    found.sort(key=lambda item: (item["state"] != "running", -item["at"]))
    return found


def read_subagent(session_id: str, cwd: str, agent_id: str, limit: int = 60) -> dict:
    """One subagent's conversation, whole. Filled in by Task 2."""
    raise NotImplementedError
