"""Telling a permission prompt from a finished turn in a session that reports no status.

The VS Code extension never writes a status, so the panel reads the open tool
call off the transcript and the process tree off /proc. These tests build the
transcript by hand and stand in for /proc, so the decision itself is what gets
checked.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower import store  # noqa: E402
from watchtower import transcript as transcript_module  # noqa: E402

SESSION = "11111111-2222-3333-4444-666666666666"
CWD = "/home/someone/project"


def stamp(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")


def said(text: str, at: float) -> dict:
    return {"type": "user", "timestamp": stamp(at), "message": {"role": "user", "content": text}}


def call(name: str, use_id: str, at: float) -> dict:
    return {"type": "assistant", "timestamp": stamp(at), "message": {
        "role": "assistant", "content": [{"type": "tool_use", "id": use_id, "name": name, "input": {}}]}}


def result(use_id: str, at: float) -> dict:
    return {"type": "user", "timestamp": stamp(at), "message": {
        "role": "user", "content": [{"type": "tool_result", "tool_use_id": use_id, "content": "ok"}]}}


def answer(text: str, at: float) -> dict:
    return {"type": "assistant", "timestamp": stamp(at), "message": {
        "role": "assistant", "content": [{"type": "text", "text": text}]}}


class InferredWait(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.original = transcript_module.PROJECT_DIR
        transcript_module.PROJECT_DIR = self.root
        folder = self.root / "-home-someone-project"
        folder.mkdir()
        self.path = folder / f"{SESSION}.jsonl"
        self.now = time.time()
        # Stand in for /proc: the session's descendants and when each started.
        self.children: dict[int, float] = {}
        self.saved = (store.descendants, store.started_at)
        store.descendants = lambda pid: list(self.children)
        store.started_at = lambda pid: self.children.get(pid)
        self.store = store.SessionStore()

    def tearDown(self) -> None:
        transcript_module.PROJECT_DIR = self.original
        store.descendants, store.started_at = self.saved
        self.tmp.cleanup()

    def write(self, *entries: dict) -> None:
        self.path.write_text("".join(json.dumps(e) + "\n" for e in entries))

    def wait(self) -> dict | None:
        data = {"pid": 4242, "sessionId": SESSION, "cwd": CWD}
        return self.store._inferred_wait(data, SESSION, self.now)

    def test_open_call_with_nothing_running_is_a_permission_prompt(self) -> None:
        self.write(said("tidy up", self.now - 30), call("Bash", "t1", self.now - 20))
        self.assertEqual(self.wait(), {"status": "waiting", "waitingFor": "permission prompt", "waitingTool": "Bash"})

    def test_finished_turn_is_not_waiting(self) -> None:
        self.write(said("tidy up", self.now - 30), call("Bash", "t1", self.now - 20),
                   result("t1", self.now - 19), answer("Done.", self.now - 18))
        self.assertIsNone(self.wait())

    def test_a_running_tool_has_started_a_process(self) -> None:
        self.write(said("build it", self.now - 30), call("Bash", "t1", self.now - 20))
        self.children = {77: self.now - 300, 78: self.now - 19.5}
        self.assertEqual(self.wait(), {"status": "busy"})

    def test_processes_older_than_the_call_do_not_count(self) -> None:
        # An MCP server or a language server started with the session.
        self.write(said("tidy up", self.now - 30), call("Edit", "t1", self.now - 20))
        self.children = {77: self.now - 300}
        self.assertEqual(self.wait()["waitingTool"], "Edit")

    def test_a_fresh_call_gets_a_moment_first(self) -> None:
        self.write(said("tidy up", self.now - 3), call("Edit", "t1", self.now - 1))
        self.assertIsNone(self.wait())

    def test_slow_in_process_tools_get_longer(self) -> None:
        self.write(said("look it up", self.now - 30), call("WebFetch", "t1", self.now - 20))
        self.assertIsNone(self.wait())
        self.write(said("look it up", self.now - 90), call("mcp__x__y", "t1", self.now - 60))
        self.store = store.SessionStore()  # the transcript cache keys on mtime
        self.assertEqual(self.wait()["waitingTool"], "mcp__x__y")

    def test_a_subagent_is_working_not_waiting(self) -> None:
        self.write(said("fan out", self.now - 60), call("Agent", "t1", self.now - 50))
        self.assertIsNone(self.wait())

    def test_a_call_before_your_last_message_is_not_on_screen(self) -> None:
        self.write(call("Bash", "t1", self.now - 60), said("never mind", self.now - 30))
        self.assertIsNone(self.wait())

    def test_a_question_is_input_needed(self) -> None:
        asked = call("AskUserQuestion", "t1", self.now - 20)
        asked["message"]["content"][0]["input"] = {"questions": [
            {"question": "Which?", "header": "Pick", "options": [{"label": "A"}, {"label": "B"}]}]}
        self.write(said("ask me", self.now - 30), asked)
        self.assertEqual(self.wait(), {"status": "waiting", "waitingFor": "input needed"})

    def test_the_newest_of_parallel_calls_is_the_one_read(self) -> None:
        self.write(said("both", self.now - 30), call("Read", "t1", self.now - 20),
                   result("t1", self.now - 19), call("Bash", "t2", self.now - 18))
        self.assertEqual(self.wait()["waitingTool"], "Bash")


if __name__ == "__main__":
    unittest.main()
