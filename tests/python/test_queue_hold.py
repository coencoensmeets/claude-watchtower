"""What happens to what you typed ahead when you stop the turn in front of it.

Stopping used to empty the queue. It now holds it: the messages stay, and they
stay put until you say otherwise — sending, dropping, or typing another one.
Every claim the strip makes about that is pinned down here, because the thing
being protected is minutes of somebody's writing.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from watchtower import owned  # noqa: E402


class Alive:
    """Stands in for a held Popen, for the parts of it these tests reach."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def poll(self):
        return None


class QueueHold(unittest.TestCase):
    SID = "s1"

    def setUp(self) -> None:
        for store in (owned.OWNED_PROCS, owned.OWNED_BUSY, owned.OWNED_QUEUE,
                      owned.OWNED_HELD, owned.OWNED_STOPPING):
            kept = dict(store)
            store.clear()
            self.addCleanup(lambda s=store, k=kept: (s.clear(), s.update(k)))
        owned.OWNED_PROCS[self.SID] = {"proc": Alive(), "mode": "default"}
        # Everything written to the pipe, instead of a pipe.
        self.sent: list[dict] = []
        self._write_was = owned.owned_write
        owned.owned_write = lambda sid, frame: (self.sent.append(frame) or True)
        self.addCleanup(setattr, owned, "owned_write", self._write_was)

    def working(self, *queued: str) -> None:
        owned.OWNED_BUSY[self.SID] = 1.0
        owned.OWNED_QUEUE[self.SID] = list(queued)

    def texts(self) -> list[str]:
        """The prompts that actually went down the pipe, as plain strings."""
        return ["".join(part.get("text", "") for part in f["message"]["content"])
                for f in self.sent if f.get("type") == "user"]

    # ----------------------------------------------------------- stopping
    def test_stopping_keeps_what_was_typed_ahead(self) -> None:
        self.working("first", "second")
        ok, said = owned.owned_interrupt(self.SID)
        self.assertTrue(ok, said)
        self.assertEqual(owned.OWNED_QUEUE[self.SID], ["first", "second"])
        self.assertIn("held", said)

    def test_stopping_says_nothing_about_a_queue_that_is_empty(self) -> None:
        self.working()
        ok, said = owned.owned_interrupt(self.SID)
        self.assertEqual((ok, said), (True, "Stopping it"))
        self.assertNotIn(self.SID, owned.OWNED_HELD)

    def test_the_held_queue_does_not_go_when_the_stopped_turn_ends(self) -> None:
        # The result frame of the interrupted turn calls this. Nothing may go
        # down the pipe on the strength of it — that is the whole point.
        self.working("first")
        owned.owned_interrupt(self.SID)
        owned.OWNED_BUSY.pop(self.SID)
        owned.owned_flush(self.SID)
        self.assertEqual(self.texts(), [])
        self.assertEqual(owned.OWNED_QUEUE[self.SID], ["first"])

    def test_a_queue_nobody_stopped_still_goes_by_itself(self) -> None:
        self.working("first", "second")
        owned.OWNED_BUSY.pop(self.SID)
        owned.owned_flush(self.SID)
        self.assertEqual(self.texts(), ["first"])

    # ------------------------------------------------------------ sending
    def test_sending_it_lets_the_first_one_go(self) -> None:
        self.working("first", "second")
        owned.owned_interrupt(self.SID)
        owned.OWNED_BUSY.pop(self.SID)
        ok, said = owned.owned_resume(self.SID)
        self.assertTrue(ok, said)
        self.assertEqual(self.texts(), ["first"])
        # And the rest is an ordinary queue again: it drains on its own.
        owned.OWNED_BUSY.pop(self.SID)
        owned.owned_flush(self.SID)
        self.assertEqual(self.texts(), ["first", "second"])

    def test_sending_a_queue_nobody_held_is_refused_rather_than_doubled(self) -> None:
        self.working("first")
        owned.OWNED_BUSY.pop(self.SID)
        ok, said = owned.owned_resume(self.SID)
        self.assertFalse(ok)
        self.assertEqual(self.texts(), [])

    def test_sending_nothing_is_refused(self) -> None:
        ok, said = owned.owned_resume(self.SID)
        self.assertFalse(ok)
        self.assertIn("Nothing", said)

    # ----------------------------------------------------------- dropping
    def test_dropping_them_takes_the_hold_with_them(self) -> None:
        self.working("first", "second")
        owned.owned_interrupt(self.SID)
        ok, said = owned.owned_unqueue(self.SID, None)
        self.assertTrue(ok, said)
        self.assertNotIn(self.SID, owned.OWNED_HELD)

    def test_dropping_the_last_one_by_hand_does_too(self) -> None:
        self.working("only")
        owned.owned_interrupt(self.SID)
        owned.owned_unqueue(self.SID, 0)
        self.assertNotIn(self.SID, owned.OWNED_HELD)

    # -------------------------------------------------------------- state
    def test_the_feed_says_whether_the_queue_is_held(self) -> None:
        self.working("first")
        self.assertFalse(owned.owned_state()[self.SID]["queueHeld"])
        owned.owned_interrupt(self.SID)
        state = owned.owned_state()[self.SID]
        self.assertTrue(state["queueHeld"])
        self.assertEqual(state["queued"], ["first"])


if __name__ == "__main__":
    unittest.main()
