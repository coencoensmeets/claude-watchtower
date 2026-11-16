"""Send now on a session the panel runs: what goes down the pipe, and when.

The pipe is replaced by a list, so the frames themselves are what is checked.
How Claude Code answers them was measured against 2.1.325 and is described in
owned_send_now.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower import owned  # noqa: E402

SESSION = "11111111-2222-3333-4444-777777777777"


class SendNow(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list[dict] = []
        self.saved = (owned.owned_write, owned.owned_say)
        owned.owned_write = lambda sid, frame: self.sent.append(frame) or True
        self.said: list[str] = []
        owned.owned_say = lambda sid, cwd, text, mode: (self.said.append(text) or True, "queued")
        self.held = {"turnStarted": True}
        owned.OWNED_PROCS[SESSION] = self.held
        owned.OWNED_BUSY[SESSION] = 1.0

    def tearDown(self) -> None:
        owned.owned_write, owned.owned_say = self.saved
        for table in (owned.OWNED_PROCS, owned.OWNED_BUSY, owned.OWNED_QUEUE,
                      owned.OWNED_HELD, owned.OWNED_SENDNOW):
            table.pop(SESSION, None)

    def test_what_waits_goes_first_then_the_request(self) -> None:
        owned.OWNED_QUEUE[SESSION] = ["first", "second"]
        ok, said = owned.owned_send_now(SESSION, "/x", "third", "default")
        self.assertTrue(ok, said)
        texts = [f["message"]["content"][0]["text"] for f in self.sent if f["type"] == "user"]
        self.assertEqual(texts, ["first", "second", "third"])
        request = self.sent[-1]
        self.assertEqual(request["type"], "control_request")
        self.assertEqual(request["request"]["subtype"], "interrupt")
        self.assertIs(request["request"]["send_now"], True)
        # Named for the last message, which is the one Send now was pressed on.
        self.assertEqual(request["request"]["message_uuid"], self.sent[-2]["uuid"])
        self.assertNotIn(SESSION, owned.OWNED_QUEUE)
        self.assertEqual(owned.OWNED_SENDNOW[SESSION]["requestId"], request["request_id"])

    def test_a_held_queue_is_let_go_by_it(self) -> None:
        owned.OWNED_QUEUE[SESSION] = ["kept"]
        owned.OWNED_HELD[SESSION] = 1.0
        ok, _ = owned.owned_send_now(SESSION, "/x", "", "default")
        self.assertTrue(ok)
        self.assertNotIn(SESSION, owned.OWNED_HELD)
        self.assertEqual(self.sent[0]["message"]["content"][0]["text"], "kept")

    def test_before_the_turn_has_started_it_queues(self) -> None:
        # A Send now that lands before Claude Code has started the turn is
        # answered `stopped` and the message never runs, so it is not sent.
        self.held["turnStarted"] = False
        ok, _ = owned.owned_send_now(SESSION, "/x", "later", "default")
        self.assertTrue(ok)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.said, ["later"])

    def test_with_no_turn_running_it_is_an_ordinary_send(self) -> None:
        owned.OWNED_BUSY.pop(SESSION)
        owned.owned_send_now(SESSION, "/x", "hello", "default")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.said, ["hello"])

    def test_nothing_to_send(self) -> None:
        ok, said = owned.owned_send_now(SESSION, "/x", "  ", "default")
        self.assertFalse(ok)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
