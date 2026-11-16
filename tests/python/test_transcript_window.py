"""Which part of a long transcript a page shows.

Two ways a long conversation used to go missing at the top: a page cut at the
bare count opened halfway through a turn, and a read that stopped at the byte
cap reported that there was nothing older, which took away the button asking
for more.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower.transcript import parse_transcript  # noqa: E402


def user(text):
    return {"type": "user", "timestamp": "2026-11-14T10:00:00Z", "message": {"role": "user", "content": text}}


def step(n, pad=0):
    return {"type": "assistant", "timestamp": "2026-11-14T10:00:01Z",
            "message": {"role": "assistant", "content": [
                {"type": "text", "text": f"step {n}" + " " * 0},
                {"type": "tool_use", "id": f"t{n}", "name": "Read", "input": {"file_path": f"/x/{n}" + "y" * pad}}]}}


class Window(unittest.TestCase):
    def write(self, lines):
        path = Path(tempfile.mkdtemp()) / "t.jsonl"
        path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
        return path

    def test_a_page_begins_at_the_question_that_started_its_turn(self):
        # The title, which real transcripts carry: without it the walk keeps
        # reading to find one and the page boundary is never tested.
        lines = ([user("first question")] + [step(i) for i in range(30)] + [user("second question")]
                 + [step(i) for i in range(30, 50)] + [{"type": "ai-title", "aiTitle": "Two questions"}])
        page = parse_transcript(self.write(lines), limit=15)
        self.assertEqual(page["messages"][0]["role"], "user")
        self.assertEqual(page["messages"][0]["text"], "second question")
        self.assertTrue(page["truncated"])

    def test_a_read_stopped_by_the_byte_cap_still_says_there_is_more(self):
        # A question, then about 10 MB of tool steps: far more than the 3 MB the
        # smallest page reads back.
        lines = [user("the very first thing")] + [step(i, pad=100_000) for i in range(100)]
        page = parse_transcript(self.write(lines), limit=60)
        self.assertNotEqual(page["messages"][0].get("text"), "the very first thing")
        self.assertTrue(page["truncated"], "older messages exist, so the panel must offer to show them")


if __name__ == "__main__":
    unittest.main()
