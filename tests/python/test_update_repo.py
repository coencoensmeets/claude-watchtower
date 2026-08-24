"""How the updater reads a real repository, in a repository built for the purpose.

The rest of the update module's tests are over its pure readers. This one is
about the question those cannot answer: whether a release is *in* this history.
Version numbers do not say — a checkout sitting between two releases has no tag
of its own, and a maintenance release on an abandoned line has a version without
being news. So it is asked of git, and git is what this checks it against.

    python3 -m unittest discover -s tests/python

Standard library only. Skipped where git is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower import update  # noqa: E402


def git(root: str, *args: str) -> None:
    subprocess.run(["git", "-C", root, *args], check=True,
                   capture_output=True, text=True,
                   env={"HOME": root, "GIT_CONFIG_GLOBAL": "/dev/null",
                        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t",
                        "PATH": "/usr/bin:/bin"})


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class MissingReleases(unittest.TestCase):
    """update.releases_missing / commits_between over a real history."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="watchtower-update-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.root = self.dir
        git(self.root, "init", "-q", "-b", "main")
        # v1.0.0 — v1.1.0 — (an untagged commit) — v2.0.0
        for step, tag in (("one", "v1.0.0"), ("two", "v1.1.0"), ("three", None), ("four", "v2.0.0")):
            Path(self.root, "file").write_text(step)
            git(self.root, "add", "-A")
            git(self.root, "commit", "-q", "-m", step)
            if tag:
                git(self.root, "tag", tag)
        self.releases = update.read_tags(self.root)

    def at(self, ref: str) -> None:
        git(self.root, "switch", "-q", "--detach", ref)

    def test_the_tags_are_read_newest_version_first(self):
        self.assertEqual([r["tag"] for r in self.releases], ["v2.0.0", "v1.1.0", "v1.0.0"])

    def test_an_old_release_is_behind_every_release_after_it(self):
        self.at("v1.0.0")
        self.assertEqual([r["tag"] for r in update.releases_missing(self.root, self.releases)],
                         ["v2.0.0", "v1.1.0"])

    def test_the_newest_release_is_missing_nothing(self):
        self.at("v2.0.0")
        self.assertEqual(update.releases_missing(self.root, self.releases), [])

    def test_a_commit_between_two_releases_is_behind_only_what_comes_after_it(self):
        # The case version numbers get wrong: no tag of its own, so comparing
        # versions would call all three releases missing rather than one.
        self.at("v1.1.0")
        git(self.root, "switch", "-q", "--detach", "v2.0.0~1")
        self.assertEqual([r["tag"] for r in update.releases_missing(self.root, self.releases)],
                         ["v2.0.0"])

    def test_a_release_on_an_abandoned_line_is_not_news(self):
        # v1.2.0 tagged off v1.0.0 rather than on main. HEAD on v2.0.0 does not
        # contain it, but it is older, so the walk stops before reaching it.
        self.at("v1.0.0")
        git(self.root, "tag", "v1.2.0")
        releases = update.read_tags(self.root)
        self.assertEqual([r["tag"] for r in releases], ["v2.0.0", "v1.2.0", "v1.1.0", "v1.0.0"])
        self.at("v2.0.0")
        self.assertEqual(update.releases_missing(self.root, releases), [])

    def test_head_state_reads_the_branch_and_the_detachment(self):
        self.assertEqual(update.head_state(self.root)["branch"], "main")
        self.assertFalse(update.head_state(self.root)["detached"])
        self.at("v1.0.0")
        state = update.head_state(self.root)
        self.assertTrue(state["detached"])
        self.assertEqual(state["branch"], "")

    def test_head_state_notices_an_edited_file(self):
        self.assertFalse(update.head_state(self.root)["dirty"])
        Path(self.root, "file").write_text("edited")
        self.assertTrue(update.head_state(self.root)["dirty"])

    def test_commits_between_counts_one_way_only(self):
        self.assertEqual(update.commits_between(self.root, "v1.0.0", "v2.0.0"), 3)
        self.assertEqual(update.commits_between(self.root, "v2.0.0", "v1.0.0"), 0)


if __name__ == "__main__":
    unittest.main()
