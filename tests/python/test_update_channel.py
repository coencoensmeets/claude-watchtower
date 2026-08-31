"""Which line the panel follows, and what it does with each.

The update machinery has two channels: the releases, and the tip of the
development branch. They share every rule that matters — nothing moves over
uncommitted work, nothing is taken from the browser's word for it, the checkout
lands detached — so what is pinned down here is where they differ, and that the
default is the safe one.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from watchtower import update  # noqa: E402


class Channel(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "channel.json"
        self._was = update.CHANNEL_FILE
        update.CHANNEL_FILE = self.file
        self.addCleanup(setattr, update, "CHANNEL_FILE", self._was)
        self.addCleanup(self.tmp.cleanup)
        update.UPDATE_HELD.clear()

    def test_the_releases_are_the_default(self) -> None:
        self.assertEqual(update.read_channel(), "release")

    def test_a_choice_is_remembered(self) -> None:
        ok, said = update.write_channel("development")
        self.assertTrue(ok, said)
        self.assertEqual(update.read_channel(), "development")

    def test_and_can_be_changed_back(self) -> None:
        update.write_channel("development")
        update.write_channel("release")
        self.assertEqual(update.read_channel(), "release")

    def test_a_channel_that_does_not_exist_is_refused(self) -> None:
        ok, said = update.write_channel("nightly")
        self.assertFalse(ok)
        self.assertIn("nightly", said)
        self.assertEqual(update.read_channel(), "release")

    def test_a_file_nobody_can_read_is_the_safe_channel(self) -> None:
        self.file.write_text("{ this is not json")
        self.assertEqual(update.read_channel(), "release")

    def test_so_is_a_file_naming_something_that_is_not_a_channel(self) -> None:
        self.file.write_text(json.dumps({"channel": "whatever"}))
        self.assertEqual(update.read_channel(), "release")

    def test_switching_throws_the_held_reading_away(self) -> None:
        # A reading is about a channel as much as about a moment. Kept across a
        # switch it would answer "you are up to date" about releases to somebody
        # who has just asked for the development branch.
        update.UPDATE_HELD.update({"ok": True, "latest": "v9.9.9", "at": 1.0})
        update.write_channel("development")
        self.assertEqual(update.UPDATE_HELD, {})


class DevelopmentRules(unittest.TestCase):
    """What stops the development channel moving a checkout."""

    CLEAN = {"dirty": False, "branch": "", "detached": True, "sha": "abc"}

    def test_a_clean_detached_checkout_may_move(self) -> None:
        self.assertEqual(update.why_not_development(self.CLEAN, 0), "")

    def test_uncommitted_work_stops_it(self) -> None:
        said = update.why_not_development({**self.CLEAN, "dirty": True}, 0)
        self.assertIn("uncommitted", said)

    def test_a_branch_of_somebody_s_own_is_left_alone(self) -> None:
        said = update.why_not_development({**self.CLEAN, "branch": "my-experiment"}, 0)
        self.assertIn("my-experiment", said)

    def test_but_the_development_branch_itself_is_not_somebody_else_s(self) -> None:
        self.assertEqual(update.why_not_development({**self.CLEAN, "branch": update.DEV_BRANCH}, 0), "")

    def test_nor_is_the_default_branch(self) -> None:
        self.assertEqual(update.why_not_development({**self.CLEAN, "branch": "main"}, 0), "")

    def test_a_checkout_ahead_of_the_branch_has_nowhere_to_go(self) -> None:
        said = update.why_not_development(self.CLEAN, 2)
        self.assertIn("2 commits", said)
        self.assertIn(update.DEV_BRANCH, said)


if __name__ == "__main__":
    unittest.main()
