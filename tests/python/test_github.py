"""The GitHub side, and the layer that decides between GitHub and GitLab.

The same method as test_gitlab: a fake `gh` on the PATH, a shell script printing
what the case is about, so every reading is the real code path against a real
subprocess with only the server replaced. gh's and glab's config directories
are pointed at this test's own, so the machine running it decides nothing.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from watchtower import forges  # noqa: E402
from watchtower.github import cli, read  # noqa: E402
from watchtower.github.actions import create_issue  # noqa: E402
from watchtower.gitlab import read as gitlab_read  # noqa: E402
from watchtower.gitlab.start import brief  # noqa: E402


class FakeGh(unittest.TestCase):
    """A test whose `gh` is a script this class wrote, and whose config is empty."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        (self.dir / "gh").mkdir()
        (self.dir / "glab").mkdir()
        self.was = dict(os.environ)
        os.environ["PATH"] = str(self.bin)
        os.environ["GH_CONFIG_DIR"] = str(self.dir / "gh")
        os.environ["GLAB_CONFIG_DIR"] = str(self.dir / "glab")
        self.argv = self.dir / "argv"
        os.environ["GH_ARGV"] = str(self.argv)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.was)))
        gitlab_read.forget()
        gitlab_read.ALIASES.clear()
        forges.FORGE_CACHE.clear()
        self.addCleanup(gitlab_read.forget)
        self.addCleanup(gitlab_read.ALIASES.clear)

    def gh(self, body: str) -> None:
        script = self.bin / "gh"
        script.write_text("#!/bin/sh\nPATH=/usr/bin:/bin:/usr/local/bin\n"
                          'echo "$@" >> "$GH_ARGV"\n' + textwrap.dedent(body))
        script.chmod(0o755)

    def asked(self) -> list[str]:
        return self.argv.read_text().splitlines() if self.argv.exists() else []

    def hosts_file(self, text: str) -> None:
        (self.dir / "gh" / "hosts.yml").write_text(textwrap.dedent(text))


ISSUE = {
    "number": 12, "title": "Make it faster", "state": "open",
    "html_url": "https://github.com/me/thing/issues/12",
    "repository_url": "https://api.github.com/repos/me/thing",
    "labels": [{"name": "priority: high", "color": "d93f0b"}, {"name": "bug", "color": "fbca04"}],
    "milestone": {"title": "v2"}, "comments": 3,
    "updated_at": "2026-11-10T10:00:00Z", "created_at": "2026-11-01T10:00:00Z",
    "user": {"login": "ann"}, "assignees": [{"login": "me"}],
    "body": "It is slow.",
}

PULL = {**ISSUE, "number": 13, "title": "A pull request", "pull_request": {"url": "x"}}


class Hosts(FakeGh):
    def test_github_com_is_asked_about_even_with_nothing_configured(self) -> None:
        """A token can come from GH_TOKEN, and "not logged in" with a button is
        the answer somebody who never ran gh auth login should see."""
        self.gh("""
            echo '{"login": "me"}'
        """)
        answer = read.hosts(force=True)
        self.assertEqual(answer["usable"], ["github.com"])
        self.assertEqual(answer["hosts"][0]["user"], "me")

    def test_an_enterprise_host_from_hosts_yml_is_probed_too(self) -> None:
        self.hosts_file("""\
            github.com:
                user: me
                oauth_token: secret
            ghe.example.com:
                user: me
        """)
        self.gh("""
            echo '{"login": "me"}'
        """)
        answer = read.hosts(force=True)
        self.assertEqual(sorted(answer["usable"]), ["ghe.example.com", "github.com"])
        self.assertTrue(any("--hostname ghe.example.com" in line for line in self.asked()))

    def test_the_token_under_a_host_is_never_read_as_a_host(self) -> None:
        self.hosts_file("""\
            github.com:
                oauth_token: gho_abc
                users:
                    me:
        """)
        self.assertEqual(cli.configured_hosts(), ["github.com"])

    def test_not_logged_in_is_a_quiet_answer_not_an_error(self) -> None:
        self.gh("""
            echo 'To get started with GitHub CLI, please run:  gh auth login' >&2
            exit 4
        """)
        entry = read.hosts(force=True)["hosts"][0]
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["code"], "no-token")

    def test_a_refused_token_is_trouble(self) -> None:
        self.gh("""
            echo 'HTTP 401: Bad credentials (https://api.github.com/user)' >&2
            exit 1
        """)
        entry = read.hosts(force=True)["hosts"][0]
        self.assertEqual(entry["code"], "turned-down")

    def test_no_gh_at_all_is_its_own_answer(self) -> None:
        answer = read.hosts(force=True)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason"], cli.NO_CLI)


class Items(FakeGh):
    def test_an_issue_comes_out_in_the_shape_the_tab_draws(self) -> None:
        item = read.as_item(ISSUE, "github.com", me="me")
        self.assertEqual(item["forge"], "github")
        self.assertEqual(item["project"], "me/thing")
        self.assertEqual(item["iid"], 12)
        self.assertEqual(item["reference"], "me/thing#12")
        self.assertEqual(item["state"], "opened")
        self.assertTrue(item["mine"])
        self.assertEqual(item["labels"][0],
                         {"name": "priority: high", "colour": "#d93f0b", "onColour": "#ffffff"})
        # Yellow wants black text on it, which GitHub does not say and has to be worked out.
        self.assertEqual(item["labels"][1]["onColour"], "#000000")
        self.assertEqual(item["author"]["username"], "ann")

    def test_pull_requests_are_not_work_items(self) -> None:
        """GitHub's issue endpoints return pull requests too."""
        self.gh(f"""
            case "$*" in
              *issues*) echo '{json.dumps([ISSUE, PULL])}' ;;
              *) echo '{{"login": "me"}}' ;;
            esac
        """)
        answer = read.assigned_items(force=True)
        self.assertEqual([i["iid"] for i in answer["items"]], [12])

    def test_both_forges_are_merged_and_a_missing_cli_is_not_trouble(self) -> None:
        """Somebody who only uses GitHub has no reason to install glab."""
        self.gh(f"""
            case "$*" in
              *issues*) echo '{json.dumps([ISSUE])}' ;;
              *) echo '{{"login": "me"}}' ;;
            esac
        """)
        answer = forges.assigned_items(force=True)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["trouble"], [])
        self.assertEqual([(i["forge"], i["iid"]) for i in answer["items"]], [("github", 12)])

    def test_neither_cli_is_one_clear_answer(self) -> None:
        answer = forges.assigned_items(force=True)
        self.assertFalse(answer["ok"])
        self.assertIn("gh", answer["message"])
        self.assertIn("glab", answer["message"])

    def test_filing_sends_labels_and_assignee_as_arrays(self) -> None:
        """GitHub takes lists where GitLab takes comma-joined strings."""
        self.gh("""
            case "$*" in
              *POST*) echo '{"number": 14, "html_url": "https://github.com/me/thing/issues/14"}' ;;
              *) echo '{"login": "me"}' ;;
            esac
        """)
        ok, message, status, item = create_issue(
            {"host": "github.com", "path": "me/thing"},
            {"title": "A thing", "labels": "bug, docs", "assignToMe": True})
        self.assertTrue(ok, message)
        self.assertEqual(item["iid"], 14)
        post = next(line for line in self.asked() if "POST" in line)
        for wanted in ("labels[]=bug", "labels[]=docs", "assignees[]=me", "title=A thing"):
            self.assertIn(wanted, post)

    def test_a_workflow_run_is_told_in_the_pipeline_words(self) -> None:
        for run, expect in [
            ({"status": "completed", "conclusion": "success"}, "success"),
            ({"status": "completed", "conclusion": "failure"}, "failed"),
            ({"status": "completed", "conclusion": "cancelled"}, "canceled"),
            ({"status": "in_progress"}, "running"),
            ({"status": "queued"}, "pending"),
        ]:
            with self.subTest(run=run):
                self.assertEqual(read.run_status(run), expect)


class WhichForge(FakeGh):
    def test_github_com_and_configured_hosts_are_github(self) -> None:
        self.hosts_file("""\
            ghe.example.com:
                user: me
        """)
        self.assertEqual(forges.forge_of("github.com"), "github")
        self.assertEqual(forges.forge_of("ghe.example.com"), "github")
        self.assertEqual(forges.forge_of("git.example.com"), "gitlab")

    def test_an_ssh_alias_for_github_is_github(self) -> None:
        """`Host github-work` / `HostName github.com` — two accounts on one GitHub."""
        ssh = self.bin / "ssh"
        ssh.write_text('#!/bin/sh\n[ "$2" = "github-work" ] && echo "hostname github.com" '
                       '|| echo "hostname $2"\n')
        ssh.chmod(0o755)
        self.assertEqual(gitlab_read.split_remote("git@github-work:me/thing.git"),
                         ("github.com", "me/thing"))

    def test_an_ssh_hostname_that_is_not_a_forge_does_not_replace_one(self) -> None:
        """ssh's config can say git.example.com is reached through ssh.example.com,
        and the forge is still git.example.com."""
        (self.dir / "glab" / "config.yml").write_text(
            "hosts:\n    git.example.com:\n        user: me\n")
        ssh = self.bin / "ssh"
        ssh.write_text('#!/bin/sh\necho "hostname ssh.example.com"\n')
        ssh.chmod(0o755)
        self.assertEqual(gitlab_read.split_remote("git@git.example.com:g/p.git"),
                         ("git.example.com", "g/p"))
        self.assertEqual(gitlab_read.split_remote("git@other-alias:g/p.git"),
                         ("other-alias", "g/p"))


class Login(unittest.TestCase):
    def test_only_a_host_name_gets_through(self) -> None:
        for raw, expect in [
            ("git.example.com", "git.example.com"),
            ("https://Git.Example.com/group/project", "git.example.com"),
            ("git.example.com:8443", "git.example.com:8443"),
            ("-oProxyCommand=x", ""),
            ("evil.com;rm -rf ~", ""),
            ("a b.com", ""),
            ("localhost", ""),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(forges.clean_host(raw), expect)

    def test_a_bad_host_is_refused_not_turned_into_github_com(self) -> None:
        ok, message, status = forges.login("github", "-oProxyCommand=x")
        self.assertFalse(ok)
        self.assertEqual(status, 400)

    def test_an_unknown_forge_is_refused(self) -> None:
        ok, _, status = forges.login("svn", "a.example.com")
        self.assertFalse(ok)
        self.assertEqual(status, 400)


class Brief(unittest.TestCase):
    def test_labels_and_author_as_the_readers_return_them(self) -> None:
        """The readers hand over labels and authors as objects; joining them as
        strings raised, so starting a session on any labelled item failed."""
        item = read.as_item(ISSUE, "github.com")
        text = brief({**item, "description": "It is slow.", "notes": []})
        self.assertIn("GitHub issue", text)
        self.assertIn("Labels: priority: high, bug", text)
        self.assertIn("Raised by ann", text)
        self.assertNotIn("{", text)


if __name__ == "__main__":
    unittest.main()
