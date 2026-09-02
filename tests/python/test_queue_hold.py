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


class Clearing(unittest.TestCase):
    """Starting a conversation again, and following it when it changes its name.

    `/clear` does not empty a conversation in place: Claude Code starts a new one
    and reports a new session id from then on — measured against 2.1.239, which
    answered a `/clear` turn with a fresh `init` frame under a different id and
    no memory of what came before. Everything the panel files under the old id
    has to move with it, which is what these are about.
    """

    OLD = "old-id"
    NEW = "new-id"

    def setUp(self) -> None:
        self.stores = [getattr(owned, name) for name in owned.OWNED_BY_ID] + [owned.OWNED_MOVED]
        for store in self.stores:
            kept = dict(store)
            store.clear()
            self.addCleanup(lambda s=store, k=kept: (s.clear(), s.update(k)))
        # The real wait is twenty seconds for a reader thread that does not exist
        # here. What it waits *for* has a test of its own below.
        was_wait = owned.CLEAR_WAIT
        owned.CLEAR_WAIT = 0.05
        self.addCleanup(setattr, owned, "CLEAR_WAIT", was_wait)
        self.held = {"proc": Alive(), "mode": "plan", "cwd": "/tmp", "id": self.OLD}
        owned.OWNED_PROCS[self.OLD] = self.held
        self.sent: list[dict] = []
        self._write_was = owned.owned_write
        owned.owned_write = lambda sid, frame: (self.sent.append(frame) or True)
        self.addCleanup(setattr, owned, "owned_write", self._write_was)
        # Nothing here may touch the real rows, names or owned file.
        self.rows: dict = {}
        self.names: dict = {}
        self.owned: dict = {}
        for name, fake in (
            ("kept_rows", lambda: self.rows),
            ("keep_row", lambda entry: self.rows.__setitem__(entry["sessionId"], entry)),
            ("forget_row", lambda sid: self.rows.pop(sid, None)),
            ("load_names", lambda: self.names),
            ("save_names", lambda names: self.names.update(names)),
            ("load_owned", lambda: self.owned),
            ("save_owned", lambda o: self.owned.update(o)),
        ):
            was = getattr(owned, name)
            setattr(owned, name, fake)
            self.addCleanup(setattr, owned, name, was)

    def test_a_clear_goes_down_the_pipe_as_the_command(self) -> None:
        ok, said, moved = owned.owned_clear(self.OLD)
        self.assertTrue(ok, said)
        texts = ["".join(p.get("text", "") for p in f["message"]["content"])
                 for f in self.sent if f.get("type") == "user"]
        self.assertEqual(texts, ["/clear"])

    def test_it_is_refused_mid_turn(self) -> None:
        owned.OWNED_BUSY[self.OLD] = 1.0
        ok, said, _ = owned.owned_clear(self.OLD)
        self.assertFalse(ok)
        self.assertIn("mid-turn", said)
        self.assertEqual(self.sent, [])

    def test_and_with_something_typed_ahead_waiting(self) -> None:
        # The queue is for this conversation. Clearing with one behind it would
        # send it into a session that has just forgotten what it was about.
        owned.OWNED_QUEUE[self.OLD] = ["and then the tests"]
        ok, said, _ = owned.owned_clear(self.OLD)
        self.assertFalse(ok)
        self.assertEqual(self.sent, [])

    def test_and_on_a_session_the_panel_does_not_hold(self) -> None:
        ok, said, _ = owned.owned_clear("somebody-elses")
        self.assertFalse(ok)
        self.assertIn("not running", said)

    def test_everything_filed_under_the_old_id_follows(self) -> None:
        owned.OWNED_LAST[self.OLD] = {"ok": True}
        owned.OWNED_COMPACT[self.OLD] = {"running": False}
        owned.OWNED_COMMANDS[self.OLD] = {"available": ["clear"]}
        self.rows[self.OLD] = {"sessionId": self.OLD, "name": "the robot one", "cwd": "/tmp"}
        self.names[self.OLD] = "the robot one"
        self.owned[self.OLD] = {"mode": "plan", "here": True}
        owned.owned_rekey(self.OLD, self.NEW, self.held)
        for store in (owned.OWNED_PROCS, owned.OWNED_LAST, owned.OWNED_COMPACT, owned.OWNED_COMMANDS):
            self.assertIn(self.NEW, store)
            self.assertNotIn(self.OLD, store)
        self.assertEqual(self.held["id"], self.NEW)
        self.assertEqual(self.rows[self.NEW]["name"], "the robot one")
        self.assertEqual(self.rows[self.NEW]["cwd"], "/tmp")
        self.assertNotIn(self.OLD, self.rows)
        self.assertEqual(self.names.get(self.NEW), "the robot one")
        self.assertEqual(self.owned.get(self.NEW), {"mode": "plan", "here": True})

    def test_the_name_you_gave_it_is_not_lost(self) -> None:
        self.rows[self.OLD] = {"sessionId": self.OLD, "name": "watchtower", "cwd": "/tmp"}
        self.names[self.OLD] = "watchtower"
        owned.owned_rekey(self.OLD, self.NEW, self.held)
        self.assertEqual(self.names.get(self.NEW), "watchtower")

    def test_going_nowhere_moves_nothing(self) -> None:
        self.rows[self.OLD] = {"sessionId": self.OLD, "name": "x", "cwd": "/tmp"}
        owned.owned_rekey(self.OLD, self.OLD, self.held)
        owned.owned_rekey(self.OLD, "", self.held)
        self.assertIn(self.OLD, self.rows)
        self.assertIn(self.OLD, owned.OWNED_PROCS)

    def test_the_feed_says_where_a_cleared_session_went(self) -> None:
        # The browser is looking at the old id when it happens, and a row that
        # vanishes reads as a session that ended rather than one that carried on.
        owned.owned_rekey(self.OLD, self.NEW, self.held)
        self.assertEqual(owned.owned_moved().get(self.OLD), self.NEW)

    def test_cleared_twice_still_leads_all_the_way(self) -> None:
        # Not to the id it had in between: a browser that missed a poll is
        # holding the first one, and following it should land on the last.
        owned.owned_rekey(self.OLD, self.NEW, self.held)
        owned.owned_rekey(self.NEW, "third-id", self.held)
        self.assertEqual(owned.owned_moved().get(self.OLD), "third-id")
        self.assertEqual(owned.owned_moved().get(self.NEW), "third-id")

    def test_and_forgets_where_it_went_after_a_while(self) -> None:
        import time as clock
        owned.owned_rekey(self.OLD, self.NEW, self.held)
        with owned._OWNED_LOCK:
            owned.OWNED_MOVED[self.OLD] = (self.NEW, clock.time() - owned.MOVED_SECONDS - 1)
        self.assertEqual(owned.owned_moved(), {})

    def test_whoever_asked_is_told_where_it_went(self) -> None:
        import threading
        moved = threading.Event()
        self.held["moved"] = moved
        owned.owned_rekey(self.OLD, self.NEW, self.held)
        self.assertTrue(moved.is_set())


if __name__ == "__main__":
    unittest.main()
