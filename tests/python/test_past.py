"""The Sessions tab: which transcripts in a folder count as conversations, and how each reads.

The transcripts are written by hand into a stand-in for ~/.claude/projects, so
what is checked is the reading — what is left out, what a row is called, what
counts as a prompt — and not whatever happens to be on this machine.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower import past  # noqa: E402
from watchtower import transcript as transcript_module  # noqa: E402

CWD = "/home/someone/project"
A = "aaaaaaaa-0000-0000-0000-000000000001"
B = "bbbbbbbb-0000-0000-0000-000000000002"
C = "cccccccc-0000-0000-0000-000000000003"


def prompt(text, branch="main", at="2026-11-01T10:00:00.000Z", **extra):
    return {"type": "user", "timestamp": at, "gitBranch": branch, "cwd": CWD,
            "message": {"role": "user", "content": text}, **extra}


def reply(text="ok"):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def tool_result():
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "done"}]}}


class PastSessions(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.original = transcript_module.PROJECT_DIR
        transcript_module.PROJECT_DIR = Path(self.tmp.name)
        self.folder = past.project_dir(CWD)
        self.folder.mkdir(parents=True)
        past._CACHE.clear()

    def tearDown(self) -> None:
        transcript_module.PROJECT_DIR = self.original
        self.tmp.cleanup()

    def write(self, session_id, *entries, mtime=None, compact=True):
        path = self.folder / f"{session_id}.jsonl"
        separators = (",", ":") if compact else None
        path.write_text("".join(json.dumps(e, separators=separators) + "\n" for e in entries))
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_the_folder_is_named_the_way_claude_code_names_it(self):
        self.assertEqual(past.project_dir("/home/a/my.repo").name, "-home-a-my-repo")

    def test_newest_first_and_named_by_the_title_claude_gave_it(self):
        self.write(A, prompt("fix the bug"), reply(), mtime=1000)
        self.write(B, prompt("write the docs"), reply(),
                   {"type": "ai-title", "aiTitle": "Old name"},
                   {"type": "ai-title", "aiTitle": "Docs for the API"}, mtime=2000)
        found, more = past.past_sessions(CWD)
        self.assertEqual([x["sessionId"] for x in found], [B, A])
        self.assertEqual(found[0]["title"], "Docs for the API")
        self.assertIsNone(found[1]["title"])
        self.assertEqual(found[1]["first"], "fix the bug")
        self.assertEqual(more, 0)

    def test_only_what_somebody_typed_is_a_prompt(self):
        self.write(A,
                   prompt("<command-name>/clear</command-name>"),
                   prompt("first real one"), reply(),
                   tool_result(), reply(),
                   prompt("an aside", isMeta=True),
                   prompt("[Request interrupted by user for tool use]"),
                   prompt("> [you, 10:02 AM] the passage\n> more of it\n\nwhat I think of it"),
                   prompt('<cross-session-message from-name="other">\nfrom a peer\n</cross-session-message>'),
                   reply())
        (row,), _ = past.past_sessions(CWD)
        self.assertEqual(row["prompts"], 3)
        self.assertEqual(row["first"], "first real one")
        self.assertEqual(row["last"], "from a peer")

    def test_a_quoted_comment_reads_as_what_was_said_about_it(self):
        self.write(A, prompt("start"), reply(),
                   prompt("> [you, 10:02 AM] the passage\n> more of it\n\nwhat I think of it"), reply())
        (row,), _ = past.past_sessions(CWD)
        self.assertEqual(row["last"], "what I think of it")

    def test_a_transcript_nobody_was_answered_in_is_left_out(self):
        self.write(A, prompt("<command-name>/clear</command-name>"))
        self.write(B, prompt("asked, never answered"))
        self.assertEqual(past.past_sessions(CWD), ([], 0))

    def test_the_panels_own_commit_message_errand_is_left_out(self):
        self.write(A, prompt("Write a git commit message for the change below.\n\nRules: …"), reply("Fix it"))
        self.assertEqual(past.past_sessions(CWD), ([], 0))

    def test_spaced_json_reads_the_same(self):
        self.write(A, prompt("spaced"), reply(), compact=False)
        (row,), _ = past.past_sessions(CWD)
        self.assertEqual((row["first"], row["prompts"]), ("spaced", 1))

    def test_a_subagent_transcript_is_part_of_its_session_not_a_session(self):
        self.write(A, prompt("parent"), reply())
        nested = self.folder / A / "subagents"
        nested.mkdir(parents=True)
        (nested / "agent-1.jsonl").write_text(json.dumps(prompt("child")) + "\n" + json.dumps(reply()) + "\n")
        self.assertEqual([x["sessionId"] for x in past.past_sessions(CWD)[0]], [A])

    def test_past_the_limit_the_rest_are_counted(self):
        for n, sid in enumerate((A, B, C)):
            self.write(sid, prompt(f"p{n}"), reply(), mtime=1000 + n)
        found, more = past.past_sessions(CWD, limit=2)
        self.assertEqual([x["sessionId"] for x in found], [C, B])
        self.assertEqual(more, 1)

    def test_a_changed_transcript_is_read_again(self):
        path = self.write(A, prompt("one"), reply(), mtime=1000)
        self.assertEqual(past.past_sessions(CWD)[0][0]["prompts"], 1)
        with path.open("a") as handle:
            handle.write(json.dumps(prompt("two")) + "\n" + json.dumps(reply()) + "\n")
        os.utime(path, (2000, 2000))
        self.assertEqual(past.past_sessions(CWD)[0][0]["prompts"], 2)

    def test_one_by_id_only_from_this_folder(self):
        self.write(A, prompt("here"), reply())
        self.assertEqual(past.past_session(CWD, A)["first"], "here")
        self.assertIsNone(past.past_session(CWD, B))
        self.assertIsNone(past.past_session(CWD, "../../etc/passwd"))
        self.assertIsNone(past.past_session("/home/someone/other", A))


if __name__ == "__main__":
    unittest.main()
