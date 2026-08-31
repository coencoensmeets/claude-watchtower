# Subagent Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show how many subagents a session is running on its row in the list, and let a `Task`/`Agent` tool row in the conversation be tapped to read that subagent's own conversation — and count subagent tokens, which the panel currently misses entirely.

**Architecture:** Claude Code writes each subagent its own transcript under `<project>/<session-id>/subagents/agent-<agentId>.jsonl`, beside a tiny `.meta.json` whose `toolUseId` ties it to the tool call that spawned it. A new `watchtower/agents.py` owns that directory; `transcript.py`'s parser is extracted so it can read a sidechain file as a conversation; `store.py` puts a count on the session payload; one new `GET /api/subagent` route serves the drill-in, modelled on `/api/change`.

**Tech Stack:** Python 3 standard library only (no dependencies, ever — this project has none). Frontend is hand-written TypeScript compiled by `tools/build.mjs`, no framework. Tests are `unittest`, run with `python3 -m unittest discover -s tests/python`.

**Spec:** `docs/superpowers/specs/2026-08-31-subagent-visibility-design.md`

## Global Constraints

- **Standard library only.** Python has no third-party dependencies and must gain none. Same for the frontend: no packages.
- **Branch:** `feature/subagent-visibility`, off `develop`. Do not commit to `develop` or `main`.
- **Commit messages** are a sentence in the imperative describing the change from the user's side, no `feat:`/`fix:` prefixes and no scope tags. Match the log: `A click on an icon does what the icon says`, `Keep what was typed ahead when the turn is stopped`, `Open a path written into the conversation`.
- **Tests:** `python3 -m unittest discover -s tests/python` from the repo root. Every Python test file starts with `sys.path.insert(0, str(Path(__file__).resolve().parents[2]))` before importing `watchtower`.
- **After any Python change, restart the panel.** A running `server.py` picks up `dist/` rebuilds but never Python edits.
- **After any TypeScript change, run `node tools/build.mjs`.** The server serves `dist/`, not `web/src/`.
- **Comment style:** this codebase explains *why*, in prose, at the point of decision. Match it. Do not add comments that restate the code.
- **Optional payload fields are absent, not null.** A field that does not apply is omitted so it does not travel on every poll. `ToolCall.change` is the precedent.
- Subagent files are named `agent-<agentId>.jsonl`; `agentId` looks like `a42010e9325b4d6fb`.

---

### Task 1: `watchtower/agents.py` — find the subagents and say which are running

**Files:**
- Create: `watchtower/agents.py`
- Test: `tests/python/test_agents.py`

**Interfaces:**
- Consumes: `watchtower.transcript.transcript_paths`, `watchtower.transcript.reverse_lines`.
- Produces:
  - `AGENT_ID: re.Pattern` — `^[A-Za-z0-9_-]{1,64}$`
  - `AGENT_IDLE_SECONDS: int = 120`
  - `subagent_dir(session_id: str, cwd: str) -> Path | None`
  - `agent_state(path: Path, now: float | None = None) -> str` — `"running" | "done" | "stopped"`
  - `list_subagents(session_id: str, cwd: str) -> list[dict]` — each dict has keys `agentId`, `agentType`, `description`, `toolUseId`, `spawnDepth`, `state`, `at`, and `model` only when the meta had one.

- [ ] **Step 1: Write the failing test**

Create `tests/python/test_agents.py`:

```python
"""What the panel can see of a session's subagents.

Claude Code writes each subagent its own transcript under
<project>/<session-id>/subagents/, beside a small meta file naming it. These
tests build that layout by hand and check the reader against it.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower import agents  # noqa: E402
from watchtower import transcript as transcript_module  # noqa: E402
from watchtower import store  # noqa: E402

SESSION = "11111111-2222-3333-4444-555555555555"


def entry(kind="assistant", stop_reason="end_turn", tool=False, text="done") -> str:
    """One line of a subagent transcript, in the shape Claude Code writes."""
    content = [{"type": "text", "text": text}]
    if tool:
        content = [{"type": "tool_use", "id": "toolu_x", "name": "Bash",
                    "input": {"command": "ls"}}]
    return json.dumps({
        "type": kind,
        "isSidechain": True,
        "agentId": "a1234567890abcdef",
        "timestamp": "2026-08-31T10:00:00Z",
        "message": {"role": kind, "stop_reason": stop_reason, "content": content},
    })


class SubagentFixture:
    """The on-disk layout, for the classes below to share.

    Not a TestCase: unittest discovers by subclass, so a TestCase inherited from
    would run every one of its tests again under each child's name.

    PROJECT_DIR is rebound on the transcript module, not on config. transcript.py
    does `from watchtower.config import PROJECT_DIR`, which copies the value into
    its own namespace at import — setting it on config afterwards changes nothing
    that transcript_paths reads. tests/change-check.py:57 does the same thing.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.original = transcript_module.PROJECT_DIR
        transcript_module.PROJECT_DIR = self.root
        self.slug = self.root / "-home-someone-work"
        self.slug.mkdir()
        (self.slug / f"{SESSION}.jsonl").write_text("{}\n")
        self.agents_dir = self.slug / SESSION / "subagents"
        self.agents_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        transcript_module.PROJECT_DIR = self.original
        self.tmp.cleanup()


class SubagentLayout(SubagentFixture, unittest.TestCase):

    def write_agent(self, agent_id, lines, meta=None, age=0.0) -> Path:
        path = self.agents_dir / f"agent-{agent_id}.jsonl"
        path.write_text("".join(line + "\n" for line in lines))
        body = {"agentType": "Explore", "description": "Look around",
                "toolUseId": f"toolu_{agent_id}", "spawnDepth": 1}
        body.update(meta or {})
        (self.agents_dir / f"agent-{agent_id}.meta.json").write_text(json.dumps(body))
        if age:
            when = time.time() - age
            os.utime(path, (when, when))
        return path

    def test_finds_the_directory_beside_the_transcript(self):
        found = agents.subagent_dir(SESSION, "/home/someone/work")
        self.assertEqual(found, self.agents_dir)

    def test_finds_it_by_glob_when_the_folder_does_not_match_the_slug(self):
        # A session whose cwd has moved still has to be findable: transcript_paths
        # falls back to a glob, and the directory hangs off whatever it finds.
        found = agents.subagent_dir(SESSION, "/somewhere/else/entirely")
        self.assertEqual(found, self.agents_dir)

    def test_no_directory_reads_as_nothing(self):
        other = "99999999-2222-3333-4444-555555555555"
        (self.slug / f"{other}.jsonl").write_text("{}\n")
        self.assertIsNone(agents.subagent_dir(other, "/home/someone/work"))

    def test_ending_in_end_turn_is_done(self):
        path = self.write_agent("aaa", [entry()])
        self.assertEqual(agents.agent_state(path), "done")

    def test_stopped_mid_tool_and_still_warm_is_running(self):
        path = self.write_agent("bbb", [entry(tool=True, stop_reason="tool_use")])
        self.assertEqual(agents.agent_state(path), "running")

    def test_stopped_mid_tool_and_gone_quiet_is_stopped(self):
        path = self.write_agent("ccc", [entry(tool=True, stop_reason="tool_use")],
                                age=agents.AGENT_IDLE_SECONDS + 30)
        self.assertEqual(agents.agent_state(path), "stopped")

    def test_a_finished_agent_stays_done_however_old_it_is(self):
        path = self.write_agent("ddd", [entry()], age=99_999)
        self.assertEqual(agents.agent_state(path), "done")

    def test_trailing_non_assistant_entries_do_not_hide_the_verdict(self):
        # A subagent's last written line is not always its last spoken one.
        path = self.write_agent("eee", [entry(), json.dumps({"type": "attachment"})])
        self.assertEqual(agents.agent_state(path), "done")

    def test_lists_every_agent_with_its_meta(self):
        self.write_agent("aaa", [entry()])
        self.write_agent("bbb", [entry(tool=True, stop_reason="tool_use")],
                         meta={"agentType": "general-purpose", "model": "haiku"})
        found = {item["agentId"]: item for item in
                 agents.list_subagents(SESSION, "/home/someone/work")}
        self.assertEqual(set(found), {"aaa", "bbb"})
        self.assertEqual(found["aaa"]["agentType"], "Explore")
        self.assertEqual(found["aaa"]["description"], "Look around")
        self.assertEqual(found["aaa"]["toolUseId"], "toolu_aaa")
        self.assertEqual(found["aaa"]["state"], "done")
        self.assertEqual(found["bbb"]["model"], "haiku")
        self.assertEqual(found["bbb"]["state"], "running")

    def test_a_meta_without_a_model_carries_no_model_key(self):
        # Older files have no model. A null on every poll is worse than absence.
        self.write_agent("aaa", [entry()])
        found = agents.list_subagents(SESSION, "/home/someone/work")
        self.assertNotIn("model", found[0])

    def test_running_agents_are_listed_first(self):
        self.write_agent("done1", [entry()])
        self.write_agent("live1", [entry(tool=True, stop_reason="tool_use")])
        found = agents.list_subagents(SESSION, "/home/someone/work")
        self.assertEqual(found[0]["agentId"], "live1")

    def test_a_meta_with_no_transcript_is_skipped(self):
        # The meta is written first; for a moment there is no .jsonl beside it.
        (self.agents_dir / "agent-orphan.meta.json").write_text('{"agentType": "Explore"}')
        self.assertEqual(agents.list_subagents(SESSION, "/home/someone/work"), [])

    def test_unreadable_meta_does_not_take_the_others_down(self):
        self.write_agent("aaa", [entry()])
        (self.agents_dir / "agent-bad.meta.json").write_text("{ not json")
        (self.agents_dir / "agent-bad.jsonl").write_text(entry() + "\n")
        found = agents.list_subagents(SESSION, "/home/someone/work")
        self.assertEqual([item["agentId"] for item in found], ["aaa"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest discover -s tests/python -k SubagentLayout -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'watchtower.agents'`

- [ ] **Step 3: Write the implementation**

Create `watchtower/agents.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest discover -s tests/python -k SubagentLayout -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Run the whole suite to be sure nothing else moved**

Run: `python3 -m unittest discover -s tests/python`
Expected: PASS, no new failures.

- [ ] **Step 6: Commit**

```bash
git add watchtower/agents.py tests/python/test_agents.py
git commit -m "Find the subagents a session spawned"
```

---

### Task 2: Read a subagent's transcript as a conversation

`read_transcript` finds a path and parses it in one function, and it drops every `isSidechain` entry — which is right for a session's own transcript and exactly wrong for a subagent's, where every entry is one. Split the parse out so it can be pointed at either.

**Files:**
- Modify: `watchtower/transcript.py:553-728` (`read_transcript`)
- Modify: `watchtower/agents.py` (`read_subagent`)
- Test: `tests/python/test_parsing.py` (add a class), `tests/python/test_agents.py` (add a class)

**Interfaces:**
- Consumes: `agents.subagent_dir`, `agents.AGENT_ID`, `agents.list_subagents` from Task 1.
- Produces:
  - `transcript.parse_transcript(path: Path, limit: int = 60, sidechain: bool = False, agents: dict[str, dict] | None = None) -> dict` — returns `{"sessionId": "", "title", "messages", "truncated", "path"}`. The `agents` argument is unused until Task 3; accept and ignore it now so Task 3 does not have to change the signature.
  - `transcript.read_transcript(session_id, cwd, limit=60) -> dict` — unchanged signature and behaviour.
  - `agents.read_subagent(session_id, cwd, agent_id, limit=60) -> dict` — the `parse_transcript` result plus `ok`, `agentId`, `agentType`, `description`, `spawnDepth`, `state`, and `model` when known. `{"ok": False, "message": ...}` when there is no such agent.

- [ ] **Step 1: Write the failing test for the extraction**

Append to `tests/python/test_parsing.py`:

```python
class SidechainParsing(unittest.TestCase):
    """A sidechain file is a conversation when it is the one being read."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "agent-aaa.jsonl"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, *entries) -> None:
        self.path.write_text("".join(json.dumps(item) + "\n" for item in entries))

    def said(self, text, sidechain=True) -> dict:
        return {"type": "assistant", "isSidechain": sidechain,
                "timestamp": "2026-08-31T10:00:00Z",
                "message": {"role": "assistant",
                            "content": [{"type": "text", "text": text}]}}

    def test_a_sidechain_read_as_one_keeps_its_messages(self):
        self.write(self.said("I looked around"))
        found = transcript.parse_transcript(self.path, sidechain=True)
        self.assertEqual([m["text"] for m in found["messages"]], ["I looked around"])

    def test_the_same_file_read_as_a_session_keeps_nothing(self):
        self.write(self.said("I looked around"))
        found = transcript.parse_transcript(self.path, sidechain=False)
        self.assertEqual(found["messages"], [])

    def test_a_plain_entry_is_kept_either_way(self):
        self.write(self.said("plain", sidechain=False))
        for flag in (True, False):
            found = transcript.parse_transcript(self.path, sidechain=flag)
            self.assertEqual([m["text"] for m in found["messages"]], ["plain"])
```

Make sure `json`, `tempfile`, `Path` and `transcript` are imported at the top of the file; add whichever are missing.

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m unittest discover -s tests/python -k SidechainParsing -v`
Expected: FAIL — `AttributeError: module 'watchtower.transcript' has no attribute 'parse_transcript'`

- [ ] **Step 3: Extract the parser**

In `watchtower/transcript.py`, replace the `read_transcript` definition and its `for path in transcript_paths(...)` / `if not path.exists(): continue` wrapper so the body becomes a function of its own. The body is unchanged apart from the sidechain line; only the head, the skip, and the return move.

New head, replacing lines 553-563:

```python
def read_transcript(session_id: str, cwd: str, limit: int = 60) -> dict:
    """The recent conversation: what you said, what Claude said, what it ran.

    Tool results are left out — they are the mechanics of a turn, not the
    conversation — but each tool call is kept so the run reads honestly. Read
    newest-first and stopped as soon as there is a page of it, so a long session
    costs no more than a short one.
    """
    for path in transcript_paths(session_id, cwd):
        if not path.exists():
            continue
        found = parse_transcript(path, limit)
        found["sessionId"] = session_id
        return found
    return {"sessionId": session_id, "title": None, "messages": [],
            "truncated": False, "path": None}


def parse_transcript(path: Path, limit: int = 60, sidechain: bool = False,
                     agents: dict[str, dict] | None = None) -> dict:
    """One transcript file, read as a conversation.

    `sidechain` says which kind of file this is. A session's own transcript holds
    no sidechain entries any more — Claude Code gives each subagent a file of its
    own — but the ones it does hold are another conversation's, and skipping them
    is what keeps a session's transcript the session's. Read a subagent's file
    with the flag set and the same skip would empty it, since every line in it is
    a sidechain.
    """
    title = None
    entries: list[dict] = []
    sent: dict[str, dict] = {}
    changes: dict[str, dict] = {}
    more = False
    seen = 0
```

Then the existing `def keep(...)` through the end of the walk stays exactly as it is, dedented one level. The one behavioural change, at what is currently line 629:

```python
            if entry.get("isSidechain") and not sidechain:
                continue
```

And the return at the end of the body, replacing lines 719-727:

```python
    entries.reverse()
    return {
        "sessionId": "",
        "title": title,
        "messages": entries[-max(1, min(limit, TRANSCRIPT_LIMIT_MAX)):],
        "truncated": more or len(entries) > limit,
        "path": str(path),
    }
```

Leave `read_pending_question`'s skip at line 249 alone. A subagent's question is still not one you can answer, whichever file it is in.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests/python -v`
Expected: PASS. `test_parsing.py`'s existing characterisation tests are the real check here — they describe `read_transcript` as it behaves today, so any of them failing means the extraction changed behaviour and must be corrected, not updated.

- [ ] **Step 5: Commit the extraction on its own**

```bash
git add watchtower/transcript.py tests/python/test_parsing.py
git commit -m "Read a transcript from a path, not only from a session"
```

- [ ] **Step 6: Write the failing test for `read_subagent`**

Append to `tests/python/test_agents.py`, inside the file and after `SubagentLayout`:

```python
class ReadingOne(SubagentFixture, unittest.TestCase):
    """Opening one subagent's conversation."""

    def test_reads_the_conversation_and_names_the_agent(self):
        self.write_agent("aaa", [entry(text="I looked around")],
                         meta={"agentType": "Explore", "model": "haiku"})
        found = agents.read_subagent(SESSION, "/home/someone/work", "aaa")
        self.assertTrue(found["ok"])
        self.assertEqual(found["agentId"], "aaa")
        self.assertEqual(found["agentType"], "Explore")
        self.assertEqual(found["model"], "haiku")
        self.assertEqual(found["state"], "done")
        self.assertEqual([m["text"] for m in found["messages"]], ["I looked around"])

    def test_an_unknown_agent_is_not_found(self):
        found = agents.read_subagent(SESSION, "/home/someone/work", "nosuch")
        self.assertFalse(found["ok"])

    def test_an_id_that_is_not_an_id_is_refused_without_touching_the_disk(self):
        for bad in ("../../etc/passwd", "a/b", "", "a" * 65, "aaa.jsonl"):
            with self.subTest(bad=bad):
                self.assertFalse(
                    agents.read_subagent(SESSION, "/home/someone/work", bad)["ok"])

    def test_a_session_with_no_subagents_finds_none(self):
        other = "99999999-2222-3333-4444-555555555555"
        (self.slug / f"{other}.jsonl").write_text("{}\n")
        self.assertFalse(agents.read_subagent(other, "/home/someone/work", "aaa")["ok"])
```

- [ ] **Step 7: Run it to verify it fails**

Run: `python3 -m unittest discover -s tests/python -k ReadingOne -v`
Expected: FAIL — `NotImplementedError`

- [ ] **Step 8: Implement `read_subagent`**

In `watchtower/agents.py`, add `parse_transcript` to the import from `watchtower.transcript` and replace the stub:

```python
def read_subagent(session_id: str, cwd: str, agent_id: str, limit: int = 60) -> dict:
    """One subagent's conversation, whole.

    The same shape /api/transcript returns, with the meta on top — so the panel
    renders a subagent through the renderer it already has, and the only new part
    is the header saying whose conversation it is.
    """
    missing = {"ok": False, "message": "That subagent is no longer there"}
    if not AGENT_ID.match(agent_id or ""):
        return missing
    folder = subagent_dir(session_id, cwd)
    if folder is None:
        return missing
    path = folder / f"agent-{agent_id}.jsonl"
    # Resolved and checked rather than trusted: AGENT_ID already refuses a
    # separator, and this refuses anything that got past it by another route.
    try:
        inside = path.resolve().parent == folder.resolve()
    except OSError:
        return missing
    if not inside or not path.is_file():
        return missing
    meta_path = folder / f"agent-{agent_id}.meta.json"
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    found = parse_transcript(path, limit, sidechain=True)
    found.update({
        "ok": True,
        "sessionId": session_id,
        "agentId": agent_id,
        "agentType": str(meta.get("agentType") or "agent")[:60],
        "description": str(meta.get("description") or "")[:200],
        "spawnDepth": int(meta.get("spawnDepth") or 1),
        "state": agent_state(path),
    })
    if isinstance(meta.get("model"), str) and meta["model"]:
        found["model"] = meta["model"][:60]
    return found
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests/python`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add watchtower/agents.py tests/python/test_agents.py
git commit -m "Read one subagent's conversation"
```

---

### Task 3: Name the subagent on the tool row that spawned it

**Files:**
- Modify: `watchtower/transcript.py` (`parse_transcript` tool-block branch, `read_transcript`)
- Test: `tests/python/test_agents.py` (add a class)

**Interfaces:**
- Consumes: `agents.list_subagents` (Task 1), `transcript.parse_transcript`'s `agents` argument (Task 2).
- Produces: a `ToolCall` in the transcript payload may now carry
  `"agent": {"agentId": str, "agentType": str, "state": "running"|"done"|"stopped"}`.
  Absent on every tool call that spawned nothing.

Note the import direction: `agents.py` imports from `transcript.py`, so `transcript.py` must **not** import `agents.py` at module level. `read_transcript` takes the mapping from its caller instead — see Step 3.

- [ ] **Step 1: Write the failing test**

Append to `tests/python/test_agents.py`:

```python
class NamingOnTheToolRow(SubagentFixture, unittest.TestCase):
    """The Task call that spawned an agent says which agent it spawned."""

    def spawned(self, tool_id) -> str:
        return json.dumps({
            "type": "assistant", "timestamp": "2026-08-31T10:00:00Z",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tool_id, "name": "Agent",
                 "input": {"description": "Look around", "prompt": "look"}}]},
        })

    def test_the_row_carries_the_agent_it_spawned(self):
        self.write_agent("aaa", [entry()], meta={"toolUseId": "toolu_spawn"})
        (self.slug / f"{SESSION}.jsonl").write_text(self.spawned("toolu_spawn") + "\n")
        found = transcript_module.read_transcript(
            SESSION, "/home/someone/work",
            agents=agents.list_subagents(SESSION, "/home/someone/work"))
        tool = found["messages"][0]["tools"][0]
        self.assertEqual(tool["name"], "Agent")
        self.assertEqual(tool["agent"], {"agentId": "aaa", "agentType": "Explore",
                                         "state": "done"})

    def test_a_row_that_spawned_nothing_carries_no_agent_key(self):
        (self.slug / f"{SESSION}.jsonl").write_text(self.spawned("toolu_other") + "\n")
        found = transcript_module.read_transcript(SESSION, "/home/someone/work")
        self.assertNotIn("agent", found["messages"][0]["tools"][0])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m unittest discover -s tests/python -k NamingOnTheToolRow -v`
Expected: FAIL — `read_transcript() got an unexpected keyword argument 'agents'`

- [ ] **Step 3: Thread the mapping through**

In `watchtower/transcript.py`, give `read_transcript` the argument and build the lookup:

```python
def read_transcript(session_id: str, cwd: str, limit: int = 60,
                    agents: list[dict] | None = None) -> dict:
    """The recent conversation: what you said, what Claude said, what it ran.

    Tool results are left out — they are the mechanics of a turn, not the
    conversation — but each tool call is kept so the run reads honestly. Read
    newest-first and stopped as soon as there is a page of it, so a long session
    costs no more than a short one.

    `agents` is this session's subagents, if the caller has them to hand. They
    are passed in rather than read here because agents.py reads this module, and
    a module cannot be read by what it reads.
    """
    spawned = {item["toolUseId"]: item for item in (agents or []) if item.get("toolUseId")}
    for path in transcript_paths(session_id, cwd):
        if not path.exists():
            continue
        found = parse_transcript(path, limit, agents=spawned)
        found["sessionId"] = session_id
        return found
    return {"sessionId": session_id, "title": None, "messages": [],
            "truncated": False, "path": None}
```

In `parse_transcript`, change the `tool_use` branch to hang the agent off the call. It currently reads:

```python
                elif block_kind == "tool_use":
                    only_results = False
                    made = changes.get(block.get("id") or "")
                    tools.append({"name": block.get("name") or "tool",
                                  "detail": tool_detail(block.get("input")),
                                  **({"change": {**made, "id": block["id"]}} if made else {})})
```

Make it:

```python
                elif block_kind == "tool_use":
                    only_results = False
                    made = changes.get(block.get("id") or "")
                    # A Task call is the one tool whose work is somewhere else
                    # entirely. Naming the agent here is what lets the row be
                    # opened; without it the call reads as a prompt and no
                    # result. See watchtower/agents.py.
                    ran = (agents or {}).get(block.get("id") or "")
                    tools.append({"name": block.get("name") or "tool",
                                  "detail": tool_detail(block.get("input")),
                                  **({"change": {**made, "id": block["id"]}} if made else {}),
                                  **({"agent": {"agentId": ran["agentId"],
                                                "agentType": ran["agentType"],
                                                "state": ran["state"]}} if ran else {})})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests/python`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watchtower/transcript.py tests/python/test_agents.py
git commit -m "Say on the tool row which subagent it started"
```

---

### Task 4: Put the count on the session payload

**Files:**
- Modify: `watchtower/store.py:107-122` (cache fields), after `store.py:243` (`_question`) for the new methods, `store.py:406` and `store.py:515` (payload sites)
- Test: `tests/python/test_agents.py` (add a class)

**Interfaces:**
- Consumes: `agents.list_subagents`, `agents.subagent_dir` (Task 1).
- Produces: `Session.agents` on the state payload —
  `{"running": int, "total": int, "newest": str} | absent`.

- [ ] **Step 1: Write the failing test**

Append to `tests/python/test_agents.py`:

```python
class CountingForTheRow(SubagentFixture, unittest.TestCase):
    """What the session row is told about subagents."""

    def counted(self):
        return store.SessionStore()._agents(SESSION, "/home/someone/work", time.time())

    def test_no_subagents_is_absent_not_empty(self):
        other = "99999999-2222-3333-4444-555555555555"
        (self.slug / f"{other}.jsonl").write_text("{}\n")
        self.assertIsNone(
            store.SessionStore()._agents(other, "/home/someone/work", time.time()))

    def test_counts_the_running_ones_and_all_of_them(self):
        self.write_agent("live1", [entry(tool=True, stop_reason="tool_use")])
        self.write_agent("live2", [entry(tool=True, stop_reason="tool_use")])
        self.write_agent("done1", [entry()])
        found = self.counted()
        self.assertEqual(found["running"], 2)
        self.assertEqual(found["total"], 3)

    def test_newest_names_a_running_agent(self):
        self.write_agent("live1", [entry(tool=True, stop_reason="tool_use")],
                         meta={"agentType": "Explore", "description": "Look around"})
        self.assertEqual(self.counted()["newest"], "Explore: Look around")

    def test_newest_falls_back_to_the_newest_agent_when_none_are_running(self):
        self.write_agent("done1", [entry()],
                         meta={"agentType": "Plan", "description": "Draw it up"})
        found = self.counted()
        self.assertEqual(found["running"], 0)
        self.assertEqual(found["newest"], "Plan: Draw it up")

    def test_a_finished_agent_is_not_re_read(self):
        # A finished file does not reopen, so its verdict is worth remembering.
        self.write_agent("done1", [entry()])
        held = store.SessionStore()
        held._agents(SESSION, "/home/someone/work", time.time())
        (self.agents_dir / "agent-done1.jsonl").write_text("{ ruined")
        found = held._agents(SESSION, "/home/someone/work", time.time() + 600)
        self.assertEqual(found["total"], 1)
        self.assertEqual(found["running"], 0)

    def test_an_agent_finishing_is_noticed_though_the_folder_did_not_change(self):
        # Appending to a subagent's file does not touch the folder's mtime, so a
        # count cached on the folder alone would never move again.
        self.write_agent("live1", [entry(tool=True, stop_reason="tool_use")])
        held = store.SessionStore()
        self.assertEqual(held._agents(SESSION, "/home/someone/work", 1000.0)["running"], 1)
        self.write_agent("live1", [entry()])
        self.assertEqual(held._agents(SESSION, "/home/someone/work", 2000.0)["running"], 0)


if __name__ == "__main__":
    unittest.main()
```

Move the existing `if __name__ == "__main__":` block to the end of the file, so it stays last.

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m unittest discover -s tests/python -k CountingForTheRow -v`
Expected: FAIL — `'SessionStore' object has no attribute '_agents'`

- [ ] **Step 3: Add the caches**

In `watchtower/store.py`, add to the imports:

```python
from watchtower.agents import list_subagents, subagent_dir
```

And beside the other cache fields around line 122:

```python
        # The set of subagents, cached on the folder's mtime, and the verdict on
        # each one, cached by id. They are separate because they go stale for
        # different reasons — see _agents.
        self._agents_dir_cache: dict[str, tuple[float, float | None]] = {}
        self._agents_cache: dict[str, tuple[float, dict | None]] = {}
        self._agents_done: dict[str, str] = {}
```

- [ ] **Step 4: Add the methods**

After `_question` (which ends at `store.py:259`):

```python
    def _agents_touched(self, session_id: str, cwd: str, now: float) -> float | None:
        """When a subagent was last spawned or cleared away."""
        hit = self._agents_dir_cache.get(session_id)
        if hit and now - hit[0] < 4:
            return hit[1]
        folder = subagent_dir(session_id, cwd)
        try:
            at = folder.stat().st_mtime if folder else None
        except OSError:
            at = None
        self._agents_dir_cache[session_id] = (now, at)
        return at

    def _agents(self, session_id: str, cwd: str, now: float) -> dict | None:
        """How many subagents this session is running, for its row.

        Two things go stale here at different rates, and caching them together
        would be wrong in a way that is hard to see. A folder's mtime moves when
        an entry is created, so it says exactly when a subagent was spawned — but
        an agent *finishing* only appends to its own file, and leaves the folder
        untouched. A count held against the folder's mtime would freeze at
        whatever it read when the last agent started.

        So the folder's mtime gates nothing but the short cache below, and the
        verdicts are re-read on the same four-second beat as the activity line.
        What makes that affordable is that `done` is final: a finished agent is
        remembered as finished and never opened again, so the cost is one tail
        read per agent still going.
        """
        touched = self._agents_touched(session_id, cwd, now)
        if touched is None:
            return None
        hit = self._agents_cache.get(session_id)
        if hit and now - hit[0] < 4:
            return hit[1]
        found = []
        for item in list_subagents(session_id, cwd):
            settled = self._agents_done.get(item["agentId"])
            if settled:
                item = {**item, "state": settled}
            elif item["state"] == "done":
                self._agents_done[item["agentId"]] = "done"
            found.append(item)
        value = None
        if found:
            running = [item for item in found if item["state"] == "running"]
            first = (running or found)[0]
            named = ": ".join(part for part in
                              (first["agentType"], first["description"]) if part)
            value = {"running": len(running), "total": len(found), "newest": named}
        self._agents_cache[session_id] = (now, value)
        return value
```

Note `list_subagents` re-reads the state of finished agents before `_agents_done` overrides it. That is one wasted tail read per finished agent per four seconds, which is measurable on a session with dozens. If it shows up, the fix is a `skip` argument on `list_subagents`; do not add it speculatively.

- [ ] **Step 5: Add the field at both payload sites**

At `store.py:406`, after `"activity"`:

```python
                # How many subagents it has going. A session that has fanned out
                # six agents reads as one session doing one thing without this.
                **({"agents": found} if cwd and (found := self._agents(session_id, cwd, now)) else {}),
```

`:=` inside a `**{}` is too clever for this codebase. Use a local instead: before the dict literal is built, in the same scope, add

```python
            agents_now = self._agents(session_id, cwd, now) if cwd else None
```

and then inside the literal:

```python
                **({"agents": agents_now} if agents_now else {}),
```

Do the same at `store.py:515`, where a stopped session's row is built — a stopped session's agents are stopped with it, and the count reads as whatever its files say, which is the truth.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests/python`
Expected: PASS.

- [ ] **Step 7: Restart the panel and confirm the field arrives**

```bash
pkill -f 'python3 .*server.py' || true
python3 server.py &
sleep 3
curl -s localhost:8787/api/state | python3 -c "
import json, sys
for s in json.load(sys.stdin)['sessions']:
    if s.get('agents'):
        print(s['name'], s['agents'])
"
```
Expected: nothing printed unless a session on this machine is running subagents — which is correct, and the field's absence is the point. If a session is fanned out, its count prints. Check the port in `watchtower/config.py` if 8787 is wrong.

- [ ] **Step 8: Commit**

```bash
git add watchtower/store.py tests/python/test_agents.py
git commit -m "Count the subagents a session is running"
```

---

### Task 5: `GET /api/subagent`

**Files:**
- Modify: `watchtower/http.py` (imports, and a route after `_get_change` at `http.py:433-445`)

**Interfaces:**
- Consumes: `agents.read_subagent`, `agents.list_subagents` (Tasks 1-2).
- Produces: `GET /api/subagent?sessionId=…&agentId=…&limit=60`.

- [ ] **Step 1: Add the route**

In `watchtower/http.py`, add to the imports:

```python
from watchtower.agents import list_subagents, read_subagent
```

After `_get_change` (ending at `http.py:445`):

```python
    @route("GET", "/api/subagent")
    def _get_subagent(self) -> None:
        # One subagent's conversation, for a tool row in the chat that was
        # tapped. The same shape as /api/transcript, because a subagent's
        # conversation is a conversation and the panel already draws those.
        query = parse_qs(urlparse(self.path).query)
        session_id = (query.get("sessionId") or [""])[0]
        session = self._session_by_id(session_id)
        if not session:
            self._json({"ok": False, "message": "That session is no longer running"}, 404)
            return
        try:
            limit = max(1, min(TRANSCRIPT_LIMIT_MAX, int((query.get("limit") or ["60"])[0])))
        except ValueError:
            limit = 60
        found = read_subagent(session_id, session["cwd"],
                              (query.get("agentId") or [""])[0], limit)
        self._json(found, 200 if found["ok"] else 404)
```

- [ ] **Step 2: Hand the transcript route its subagents**

`/api/transcript` at `http.py:419` currently ends:

```python
        self._json(read_transcript(session_id, session["cwd"], limit))
```

Make it:

```python
        # The subagents are read here rather than in the transcript reader: that
        # module is what agents.py reads, so it cannot read agents.py back.
        self._json(read_transcript(session_id, session["cwd"], limit,
                                   agents=list_subagents(session_id, session["cwd"])))
```

- [ ] **Step 3: Restart the panel and drive both routes**

```bash
pkill -f 'python3 .*server.py' || true
python3 server.py &
sleep 3
SID=$(curl -s localhost:8787/api/state | python3 -c "
import json,sys
print(next((s['sessionId'] for s in json.load(sys.stdin)['sessions'] if s.get('agents')), ''))")
echo "session with agents: ${SID:-none}"
curl -s "localhost:8787/api/subagent?sessionId=$SID&agentId=nosuch" | head -c 300; echo
```
Expected: `{"ok": false, "message": "That subagent is no longer there"}` with a 404. If `$SID` is empty no session is fanned out; the malformed-id case still exercises the route and must 404 rather than trace back.

- [ ] **Step 4: Confirm a real subagent reads**

Only if `$SID` was found above:

```bash
AID=$(curl -s "localhost:8787/api/transcript?sessionId=$SID&limit=60" | python3 -c "
import json,sys
for m in json.load(sys.stdin)['messages']:
    for t in m.get('tools') or []:
        if t.get('agent'): print(t['agent']['agentId']); break")
curl -s "localhost:8787/api/subagent?sessionId=$SID&agentId=$AID" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(d['agentType'], '|', d['description'], '|', d['state'], '|', len(d['messages']), 'messages')"
```
Expected: the agent's type, description, state, and a non-zero message count.

- [ ] **Step 5: Commit**

```bash
git add watchtower/http.py
git commit -m "Serve one subagent's conversation"
```

---

### Task 6: Count what the subagents spent

**Files:**
- Modify: `watchtower/usage.py:223` (`read_usage`)
- Test: `tests/python/test_usage.py` (add a class)

**Interfaces:**
- Consumes: `agents.subagent_dir` (Task 1).
- Produces: `read_usage(...)["agentModels"]` now holds subagent spend, and `totals`/`cost` include it. No shape change.

- [ ] **Step 1: Write the failing test**

Append to `tests/python/test_usage.py`:

```python
class SubagentSpend(unittest.TestCase):
    """A fanned-out session's real bill.

    Subagents have their own transcripts, so the session's file says nothing
    about what they cost. read_usage has to go and look.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Rebound on the transcript module, not on config: usage.py reads paths
        # through transcript.transcript_paths, and that function reads the name
        # transcript.py copied out of config at import time.
        self.original = transcript_module.PROJECT_DIR
        transcript_module.PROJECT_DIR = self.root
        self.slug = self.root / "-home-someone-work"
        self.slug.mkdir()
        self.session = "11111111-2222-3333-4444-555555555555"
        self.agents_dir = self.slug / self.session / "subagents"
        self.agents_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        transcript_module.PROJECT_DIR = self.original
        self.tmp.cleanup()

    def test_a_subagents_spend_lands_in_the_agent_bucket(self):
        (self.slug / f"{self.session}.jsonl").write_text(
            turn(request_id="main-1", input_tokens=100, output_tokens=10))
        (self.agents_dir / "agent-aaa.jsonl").write_text(
            turn(request_id="side-1", sidechain=True, input_tokens=70, output_tokens=7))
        found = usage.read_usage(self.session, "/home/someone/work")
        self.assertEqual([row["model"] for row in found["models"]], ["claude-opus-5"])
        self.assertEqual(sum(row["input"] for row in found["agentModels"]), 70)
        self.assertEqual(found["totals"]["input"], 170)

    def test_two_subagents_are_added_together(self):
        (self.slug / f"{self.session}.jsonl").write_text(
            turn(request_id="main-1", input_tokens=100))
        (self.agents_dir / "agent-aaa.jsonl").write_text(
            turn(request_id="side-1", sidechain=True, input_tokens=70))
        (self.agents_dir / "agent-bbb.jsonl").write_text(
            turn(request_id="side-2", sidechain=True, input_tokens=30))
        found = usage.read_usage(self.session, "/home/someone/work")
        self.assertEqual(sum(row["input"] for row in found["agentModels"]), 100)

    def test_a_subagent_does_not_move_the_context_reading(self):
        # Context is what the *session* is carrying. A subagent's window is not
        # the session's, and reading one here would make the header lie.
        (self.slug / f"{self.session}.jsonl").write_text(
            turn(request_id="main-1", input_tokens=100))
        (self.agents_dir / "agent-aaa.jsonl").write_text(
            turn(request_id="side-1", sidechain=True, input_tokens=999_999))
        found = usage.read_usage(self.session, "/home/someone/work")
        self.assertEqual(found["context"], 100)

    def test_reading_twice_does_not_double_the_bill(self):
        # scan_usage keeps its progress in a module-level cache. Merging into
        # that dict rather than a copy of it would grow the total on every poll.
        (self.slug / f"{self.session}.jsonl").write_text(
            turn(request_id="main-1", input_tokens=100))
        (self.agents_dir / "agent-aaa.jsonl").write_text(
            turn(request_id="side-1", sidechain=True, input_tokens=70))
        first = usage.read_usage(self.session, "/home/someone/work")
        second = usage.read_usage(self.session, "/home/someone/work")
        self.assertEqual(first["totals"]["input"], second["totals"]["input"])
        self.assertEqual(second["totals"]["input"], 170)

    def test_a_session_with_no_subagents_is_unchanged(self):
        (self.slug / f"{self.session}.jsonl").write_text(
            turn(request_id="main-1", input_tokens=100))
        found = usage.read_usage(self.session, "/home/someone/work")
        self.assertEqual(found["agentModels"], [])
        self.assertEqual(found["totals"]["input"], 100)
```

`test_usage.py` has no `read_usage` tests today — every existing test calls `scan_usage` on a path directly, so nothing in the file redirects `PROJECT_DIR` yet. Add `from watchtower import transcript as transcript_module  # noqa: E402` to its imports; `tempfile` and `Path` are already there.

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m unittest discover -s tests/python -k SubagentSpend -v`
Expected: FAIL — the agent bucket is empty and `totals["input"]` is 100, not 170.

- [ ] **Step 3: Implement the merge**

In `watchtower/usage.py`, add to the imports:

```python
from watchtower.agents import subagent_dir
```

Then in `read_usage`, replace this line:

```python
        main, agents = rows(held["main"]), rows(held["agents"])
```

with:

```python
        # A subagent's spend is in a file of its own, so the session's scan does
        # not have it. Every entry in those files is marked as a sidechain, so
        # scan_usage sorts them into the agent bucket without being told.
        #
        # Copied, not merged in place: `held` is the dict scan_usage keeps to
        # read each transcript incrementally, and adding to it would count the
        # subagents again on the next poll, and again on the one after.
        spent = {name: dict(counters) for name, counters in held["agents"].items()}
        folder = subagent_dir(session_id, cwd)
        for side in sorted(folder.glob("agent-*.jsonl")) if folder else []:
            extra = scan_usage(side)
            for name, counters in (extra.get("agents") or {}).items():
                target = spent.setdefault(name, blank_counters())
                for field, value in counters.items():
                    target[field] = target.get(field, 0) + value
        main, agents = rows(held["main"]), rows(spent)
```

`context`, `contextModel` and `contextAt` are left as the session's own. `scan_usage` already refuses to take a context reading from a sidechain entry — the `if not side` at `usage.py:210` — so a subagent's window cannot leak into the header.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests/python`
Expected: PASS. The existing usage tests are characterisation tests; if one moves, the merge has changed the arithmetic and must be corrected.

- [ ] **Step 5: Restart and check a real session's bill**

```bash
pkill -f 'python3 .*server.py' || true
python3 server.py &
sleep 3
SID=$(curl -s localhost:8787/api/state | python3 -c "
import json,sys
print(next((s['sessionId'] for s in json.load(sys.stdin)['sessions'] if s.get('agents')), ''))")
curl -s "localhost:8787/api/usage?sessionId=$SID" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('session:', sum(r['input'] for r in d['models']))
print('agents: ', sum(r['input'] for r in d['agentModels']))
print('cost:   ', d['cost'])"
```
Expected: a non-zero agent figure for a session that has run subagents. Before this task it was always zero.

- [ ] **Step 6: Commit**

```bash
git add watchtower/usage.py tests/python/test_usage.py
git commit -m "Count what a session's subagents spent"
```

---

### Task 7: The count on the row

**Files:**
- Modify: `web/src/types.ts` (`Session`, `ToolCall`, and a new `Subagent`)
- Modify: `web/src/main.ts:823-880` (`paintListItem`)
- Modify: `web/styles/` — only if the badge needs a rule of its own. It is a word in an existing `·`-joined line, so it most likely needs none.

**Interfaces:**
- Consumes: `Session.agents` (Task 4), `ToolCall.agent` (Task 3).
- Produces: the TypeScript types Task 8 builds on.

- [ ] **Step 1: Add the types**

In `web/src/types.ts`, add to `Session`:

```ts
  /** How many subagents this session has going, absent when it has none.
      `newest` names one: its type and what it was asked to do. */
  agents?: { running: number; total: number; newest: string };
```

Add to `ToolCall`:

```ts
  /** Only on a Task/Agent call, naming the subagent it started — which is what
      makes the row openable. Absent on every other call. */
  agent?: {
    agentId: string;
    agentType: string;
    state: "running" | "done" | "stopped";
  };
```

And a new interface beside `Transcript`:

```ts
/** GET /api/subagent — one subagent's conversation, in the shape the chat
    already draws, with the meta on top. */
export interface Subagent extends Transcript {
  ok: boolean;
  message?: string;
  agentId: string;
  agentType: string;
  description: string;
  spawnDepth: number;
  state: "running" | "done" | "stopped";
  model?: string;
}
```

- [ ] **Step 2: Put the badge in the supporting line**

In `web/src/main.ts`, inside `paintListItem`, after the `ask` line:

```js
  // A session that has fanned out reads as one session doing one thing without
  // this. Only the running ones are worth a word: six finished agents are six
  // things that already happened.
  const fanned = session.agents?.running
    ? `${session.agents.running} agent${session.agents.running === 1 ? "" : "s"}` : "";
```

Add it to `supporting`, between the state word and the folder:

```js
    + [ask ? ask.label : state.short, fanned, session.folder, nested].filter(Boolean)
      .map(escapeHtml).join(" · ");
```

- [ ] **Step 3: Add it to the signature — the step that is easy to miss**

The `signature` string at `main.ts:859` decides whether a row repaints. A field left out of it never updates on screen, however correct the server is. Add `fanned`:

```js
  const signature = [stateKeyOf(session.status), state.short, session.name, session.folder, host.label,
                     isSelected, session.pinned, subject, nested, ask?.kind, ask?.label, fanned].join(" ");
```

- [ ] **Step 4: Build and look at it**

```bash
node tools/build.mjs
```
Expected: no errors. Then open the panel and find a session running subagents; its row says `2 agents` beside the state word. To make one on demand: in any Claude Code session in this repo, ask for two parallel `Explore` agents, and watch the row.

- [ ] **Step 5: Verify the repaint, which is the part that breaks**

Watch one row while its agents finish. The count must fall to nothing on its own within a few seconds. If it sticks, `fanned` is not in the signature.

- [ ] **Step 6: Commit**

```bash
git add web/src/types.ts web/src/main.ts
git commit -m "Say on the row how many agents a session has going"
```

---

### Task 8: Open the subagent from the tool row

**Files:**
- Create: `web/src/views/subagent.ts`
- Modify: `web/src/state.ts` (add `agentShown` beside `changeShown`)
- Modify: `web/src/views/chat.ts` (render the block, wire `data-act="subagent"`)
- Modify: `web/styles/chat.css` — the new `.agent*` rules. Note the `.change*` rules they mirror live in `web/styles/git.css`, because a change is a diff; a subagent is not, so its rules go with the conversation.

Read `web/src/views/change.ts` first, whole. It is the model for this file: a folded block in the conversation, an opened panel that takes the pane, a `Map` holding what has been fetched, and a way back. Follow its structure rather than inventing a second one.

**Interfaces:**
- Consumes: `Subagent` and `ToolCall.agent` (Task 7), `GET /api/subagent` (Task 5).
- Produces: `agentBlock(tool)`, `agentPanel(session)`, `openAgent(session, agentId)`, `agentBusy` — the same four things `change.ts` exports, under agent names.

- [ ] **Step 1: Add the pane state**

In `web/src/state.ts`, beside `changeShown`:

```ts
  /** The subagent whose conversation is standing in front of the chat, by
      agentId. Null when the conversation itself is showing. */
  agentShown: null,
```

- [ ] **Step 2: Write the folded block**

Create `web/src/views/subagent.ts`:

```ts
/* One subagent's conversation: the work a Task call stands for.

   A Task call is the only tool whose work happened somewhere else. In the
   conversation it reads as a prompt and then nothing — the row says an agent was
   sent off and the next thing you see is Claude carrying on, with everything the
   agent actually did missing from between. This is that middle.

   Built the way a change is (see views/change.ts): folded, it is a line on the
   tool row saying who was sent and whether they are back. Opened, it takes the
   pane, because a conversation squeezed into the width of a tool row is not a
   conversation you can read. */

import { chat } from "../state.js";
import { escapeHtml } from "../ui/format.js";
import { ICON } from "../ui/icons.js";
import { showSnackbar } from "../ui/snackbar.js";

const agentFull = new Map();      // agentId -> the whole conversation, once fetched
export const agentBusy = new Set();

const WORD = {
  running: "working",
  done: "reported back",
  stopped: "stopped without reporting",
};

/* Folded, on the tool row. The type and the state, which is what you want while
   you are reading past it: who was sent, and whether they are back yet. */
export function agentBlock(tool) {
  const agent = tool.agent;
  if (!agent) return "";
  const hint = agentBusy.has(agent.agentId) ? "reading what it did…"
    : "read what it did";
  return `<div class="agent" data-agent="${escapeHtml(agent.agentId)}"
      data-state="${escapeHtml(agent.state)}">
      <button class="agent__bar md-state" type="button" data-act="subagent"
        data-id="${escapeHtml(agent.agentId)}">
        <span class="agent__type md-label-small md-mono">${escapeHtml(agent.agentType)}</span>
        <span class="agent__state md-label-small">${escapeHtml(WORD[agent.state] || agent.state)}</span>
        <span class="agent__hint md-label-small">${escapeHtml(hint)}</span>
      </button>
    </div>`;
}

/* Opened, it takes the pane, for the reason a change does: the way back goes
   where the way back goes, and the conversation stays exactly where you left it.

   The messages are rendered by whatever renders the conversation — a subagent's
   conversation is a conversation, and it arrives in the same shape. */
export function agentPanel(session) {
  const full = agentFull.get(chat.agentShown);
  if (!full) return `<div class="agent-panel agent-panel--waiting md-body-medium">
      reading what it did…</div>`;
  const named = [full.agentType, full.model].filter(Boolean).join(" · ");
  return `<div class="agent-panel">
      <div class="agent-panel__head">
        <button class="agent-panel__back md-state" type="button" data-act="agent-back">
          ${ICON.back}<span class="md-label-large">Back to the conversation</span>
        </button>
        <div class="agent-panel__who md-title-small">${escapeHtml(named)}</div>
        <div class="agent-panel__what md-body-small">${escapeHtml(full.description)}</div>
        <div class="agent-panel__state md-label-small"
          data-state="${escapeHtml(full.state)}">${escapeHtml(WORD[full.state] || full.state)}</div>
      </div>
      <div class="agent-panel__body" data-messages="${full.messages.length}"></div>
    </div>`;
}

/* Fetched once when it has finished, and again on every refresh while it has
   not: a running agent's conversation is still being written. */
export async function openAgent(session, agentId) {
  const held = agentFull.get(agentId);
  if (held && held.state !== "running") {
    chat.agentShown = agentId;
    return;
  }
  agentBusy.add(agentId);
  try {
    const response = await fetch(
      `/api/subagent?sessionId=${encodeURIComponent(session.sessionId)}` +
      `&agentId=${encodeURIComponent(agentId)}`,
      { cache: "no-store" });
    const found = await response.json();
    if (!found.ok) {
      showSnackbar(found.message || "That subagent is no longer there");
      return;
    }
    agentFull.set(agentId, found);
    chat.agentShown = agentId;
  } catch {
    showSnackbar("Could not read what that agent did");
  } finally {
    agentBusy.delete(agentId);
  }
}

export function closeAgent() {
  chat.agentShown = null;
}
```

`agentPanel` leaves `.agent-panel__body` empty on purpose: rendering the messages is Step 3, using whichever function `chat.ts` already calls to draw `chat.transcript.messages`. Find it, and call it with `full.messages`. Do not write a second message renderer — if the existing one is not exported, export it.

- [ ] **Step 3: Wire it into the chat**

In `web/src/views/chat.ts`, three edits, each mirroring what is already there for a change:

1. Where `changeBlock(tool)` is appended to a tool row, append `agentBlock(tool)` beside it.
2. Where `chat.changeShown` decides that the change panel stands in front of the conversation, add the same test for `chat.agentShown`, and render the messages into `.agent-panel__body` with the conversation's own renderer.
3. Where `data-act="change"` is handled in the pane's click listener, add `data-act="subagent"` calling `openAgent(session, id)` and `data-act="agent-back"` calling `closeAgent()`.

Import `agentBlock`, `agentPanel`, `openAgent`, `closeAgent` from `./subagent.js`.

Where the detail refresh re-fetches an open change, do the same for an open agent whose `state` is `running`, so its conversation grows while you watch it.

- [ ] **Step 4: Style it**

Read `.change`, `.change__bar`, `.change__file`, `.change__stat` and `.change__hint` in `web/styles/git.css` — that is the shape to match. Write `.agent`, `.agent__bar`, `.agent__type`, `.agent__state`, `.agent__hint` and the `.agent-panel*` rules into `web/styles/chat.css`, reusing the same custom properties rather than new values. `[data-state="running"]` gets the colour the panel already uses for a working session; `[data-state="stopped"]` the one it uses for a stopped one. Take both from the existing `STATE` colours rather than picking new ones.

- [ ] **Step 5: Build and drive it**

```bash
node tools/build.mjs
pkill -f 'python3 .*server.py' || true
python3 server.py &
```
Then: open a session that has run subagents, scroll to a `Task`/`Agent` tool row, and tap it. Expected — the pane shows that agent's conversation with its type, model and description in the header; Back returns you to the conversation at the scroll position you left.

- [ ] **Step 6: Check the three states read right**

- A finished agent says `reported back` and its conversation is complete.
- A running agent says `working`, and its conversation grows while it is open.
- Kill a session mid-agent (`pkill -f` the Claude process), wait past `AGENT_IDLE_SECONDS`, and the row says `stopped without reporting` rather than `working`.

- [ ] **Step 7: Commit**

```bash
git add web/src/views/subagent.ts web/src/state.ts web/src/views/chat.ts web/styles/chat.css
git commit -m "Read what a subagent did, from the row that started it"
```

---

### Task 9: A fixture that shows it, and the changelog

**Files:**
- Modify: `tests/fixtures.py`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Give a fixture session some subagents**

In `tests/fixtures.py`, find where a session's transcript is written and add, for one session, a `<session-id>/subagents/` directory holding two agents:

```python
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
```

Call it for one busy fixture session, and — so the drill-in is reachable — append an `Agent` tool call to that session's transcript carrying `toolu_a1111111111111111` as its `tool_use` id, so the row it opens from exists.

- [ ] **Step 2: Run the fixtures and look**

```bash
python3 tests/fixtures.py
```
Follow the two commands it prints. Expected: one row says `1 agent`; opening that session shows an `Agent` tool row that can be tapped; the finished agent reads `reported back`.

- [ ] **Step 3: Write the changelog entry**

Read the top of `CHANGELOG.md` and match its format exactly — heading level, whether it is dated, and how entries are worded. Add an unreleased entry saying what a person gets:

> Sessions say how many subagents they are running, and a Task row in the conversation opens what that agent did. A session's spend now includes its subagents, which it did not before.

- [ ] **Step 4: Run everything once more**

```bash
python3 -m unittest discover -s tests/python
node tools/build.mjs
```
Expected: all tests pass, build clean.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures.py CHANGELOG.md
git commit -m "Show a session with subagents in the fixtures"
```

---

## Verification

Before calling this done, all of it, in order:

- [ ] `python3 -m unittest discover -s tests/python` — passes, and the run's test count is higher than it was at the start of Task 1.
- [ ] `node tools/build.mjs` — clean.
- [ ] `python3 tests/fixtures.py` — a row shows an agent count, and the drill-in opens.
- [ ] On a real session running two `Explore` agents: the row counts them, the count falls to nothing when they finish without a page reload, and each agent's conversation opens from its tool row.
- [ ] `curl -s "localhost:8787/api/usage?sessionId=$SID"` — `agentModels` is non-empty for a session that has run subagents.
- [ ] `curl -s "localhost:8787/api/subagent?sessionId=$SID&agentId=../../etc/passwd"` — 404, no traceback in the server log.
- [ ] `git log --oneline develop..HEAD` — nine or so commits, each a sentence in the imperative, none prefixed `feat:`.

## Out of scope

Do not add these, however tempting:

- A dedicated agents pane listing agents across sessions.
- Subagent file changes in the parent's change view. `tests/change-check.py:95` locks that out deliberately; the test stays as it is.
- Drawing nesting. `spawnDepth` is carried and not used; an agent that spawned its own agents reads as one agent.
- A `skip` argument on `list_subagents` to avoid re-reading finished agents. Noted in Task 4, Step 4. Measure first.
