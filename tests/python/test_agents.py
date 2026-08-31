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


class SubagentLayout(SubagentFixture, unittest.TestCase):

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


if __name__ == "__main__":
    unittest.main()
