"""What the panel can see of the commands a session left running in the background.

Claude Code marks a backgrounded command in the transcript — the tool result
carries `toolUseResult.backgroundTaskId`, whether it was started that way or
moved there by its timeout — and writes a `<task-notification>` when it ends.
These tests write that shape by hand and check the reader, and the rule that a
command is running exactly while a process holds its output file.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower import shells  # noqa: E402

SESSION = "11111111-2222-3333-4444-555555555555"


def call(tool_id: str, command: str, description: str = "", background: bool = False) -> dict:
    args = {"command": command, "description": description}
    if background:
        args["run_in_background"] = True
    return {"type": "assistant", "timestamp": "2026-11-15T10:00:00Z",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tool_id, "name": "Bash", "input": args}]}}


def result(tool_id: str, task_id: str | None, timed_out: bool = False) -> dict:
    outcome = {"stdout": "", "stderr": "", "interrupted": False}
    if task_id:
        outcome["backgroundTaskId"] = task_id
    if timed_out:
        outcome["timedOutAfterMs"] = 120000
    return {"type": "user", "timestamp": "2026-11-15T10:00:01Z", "toolUseResult": outcome,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}]}}


def notification(task_id: str, status: str) -> dict:
    text = (f"<task-notification>\n<task-id>{task_id}</task-id>\n"
            f"<status>{status}</status>\n<summary>done</summary>\n</task-notification>")
    return {"type": "user", "message": {"role": "user", "content": text}}


class BackgroundShells(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / f"{SESSION}.jsonl"
        self.path.write_text("")
        patcher = mock.patch.object(shells, "transcript_paths", lambda *_: [self.path])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(shells.forget, SESSION)

    def write(self, *entries: dict) -> None:
        with self.path.open("a") as handle:
            for item in entries:
                handle.write(json.dumps(item, separators=(",", ":")) + "\n")

    def test_names_backgrounded_commands_and_skips_foreground_ones(self) -> None:
        self.write(call("t1", "ls"), result("t1", None),
                   call("t2", "npm run dev", "Start the dev server", background=True),
                   result("t2", "bdev00001"),
                   call("t3", "make all", "Build everything"), result("t3", "bmake0001", timed_out=True))
        tasks = {task["taskId"]: task for task in shells.background_tasks(SESSION, "/x")}
        self.assertEqual(set(tasks), {"bdev00001", "bmake0001"})
        self.assertEqual(tasks["bdev00001"]["description"], "Start the dev server")
        self.assertFalse(tasks["bdev00001"]["timedOut"])
        # Moved to the background by its timeout, not asked to go there.
        self.assertTrue(tasks["bmake0001"]["timedOut"])

    def test_reads_only_what_was_added(self) -> None:
        self.write(call("t1", "sleep 99", background=True), result("t1", "bsleep001"))
        self.assertEqual(len(shells.background_tasks(SESSION, "/x")), 1)
        # A half-written line waits for the rest of it.
        with self.path.open("a") as handle:
            handle.write(json.dumps(notification("bsleep001", "completed"))[:20])
        self.assertIsNone(shells.background_tasks(SESSION, "/x")[0]["ended"])
        with self.path.open("a") as handle:
            handle.write(json.dumps(notification("bsleep001", "completed"))[20:] + "\n")
        self.assertEqual(shells.background_tasks(SESSION, "/x")[0]["ended"], "completed")

    def test_running_is_whoever_holds_the_output(self) -> None:
        self.write(call("t1", "a", background=True), result("t1", "baaaaaaa1"),
                   call("t2", "b", background=True), result("t2", "bbbbbbbb2"))
        held = {SESSION: {"bbbbbbbb2": [42]}, "other": {"baaaaaaa1": [7]}}
        running = shells.running_shells(SESSION, "/x", held)
        self.assertEqual([task["taskId"] for task in running], ["bbbbbbbb2"])
        self.assertEqual(running[0]["pids"], [42])

    def test_without_proc_the_notifications_decide(self) -> None:
        self.write(call("t1", "a", background=True), result("t1", "baaaaaaa1"),
                   call("t2", "b", background=True), result("t2", "bbbbbbbb2"),
                   notification("baaaaaaa1", "killed"))
        running = shells.running_shells(SESSION, "/x", None)
        self.assertEqual([task["taskId"] for task in running], ["bbbbbbbb2"])

    def test_output_ids_are_checked_before_a_path_is_built(self) -> None:
        self.assertIsNone(shells.output_path(SESSION, "../../etc/passwd"))
        self.assertIsNone(shells.output_path("../x", "babc"))
        self.assertFalse(shells.read_output(SESSION, "../secret")["ok"])


class HeldOutputs(unittest.TestCase):
    def test_matches_task_output_paths_only(self) -> None:
        pattern = shells.OUTPUT_PATH
        found = pattern.search(f"/tmp/claude-1000/-home-me-proj/{SESSION}/tasks/bxyz12345.output")
        self.assertEqual(found.groups(), (SESSION, "bxyz12345"))
        self.assertIsNone(pattern.search("/tmp/claude-1000/-home-me-proj/tasks/bxyz.log"))


if __name__ == "__main__":
    unittest.main()
