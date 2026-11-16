"""The GitLab side: what glab is asked, what its answers mean, and what is refused.

Nothing here touches the network, and nothing here needs a GitLab. A fake `glab`
is put on the PATH — a shell script that prints whatever the case is about — so
every reading below is the real code path, run against a real subprocess, with
only the server at the far end replaced.

That is the part worth testing. The panel's whole GitLab story is "shell out to
a CLI and believe what it says", so the risks are all in the seams: a host whose
token has expired while still saying *Logged in*, a paginated answer spliced back
together wrongly, a remote URL in one of git's two spellings, a work item matched
to the wrong folder on this machine.

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

from watchtower.gitlab import cli, match, read  # noqa: E402
from watchtower.gitlab.actions import clean_labels, create_issue  # noqa: E402
from watchtower.gitlab.start import brief  # noqa: E402


class FakeGlab(unittest.TestCase):
    """A test whose `glab` is a script this class wrote."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.was = dict(os.environ)
        os.environ["PATH"] = str(self.bin)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.was)))
        read.forget()
        self.addCleanup(read.forget)

    def glab(self, body: str) -> None:
        """Install a `glab` whose body is the shell below. $@ is the argv it got.

        The script puts the ordinary PATH back for itself. The panel's PATH is
        this test's own directory and nothing else — which is what makes the
        no-glab-installed case testable on a machine that has one — but a shell
        script with no `cat` on its PATH cannot print anything.
        """
        script = self.bin / "glab"
        script.write_text("#!/bin/sh\nPATH=/usr/bin:/bin:/usr/local/bin\n"
                          + textwrap.dedent(body))
        script.chmod(0o755)


# The shape of the real thing, down to the tick and cross characters and the
# fact that all of it arrives on stderr with an exit status of 0.
#
# git.example.com is the case this file exists for: glab reports `! Invalid
# token provided` under a host whose token is perfectly good. It prints that
# whenever the extra call it makes to describe the token fails, which a token
# scoped for the API but not for its own metadata does — and the host then
# answers /user and lists issues without complaint.
AUTH_OUTPUT = """\
gitlab.com
  x gitlab.com: api call failed: GET https://gitlab.com/api/v4/user: 401
  x No token provided
git.example.com
  ✓ Logged in to git.example.com as coen (/home/coen/.config/glab-cli/config.yml)
  ✓ Token: **************************
  ! Invalid token provided
git.good.com
  ✓ Logged in to git.good.com as coen (/home/coen/.config/glab-cli/config.yml)
  ✓ Token: **************************
"""


# The probe answers, as shell. A host is usable or not according to what /user
# says, so these are the whole of the difference between the cases below.
SPEAKS = """            echo '{"id": 90, "username": "coen"}'"""

REFUSES = """            echo '{"message": "401 Unauthorized"}' >&2
            exit 1"""

UNREACHABLE = """            echo 'dial tcp: lookup git.example.com: no such host' >&2
            exit 1"""


def auth_script(probe: str) -> str:
    """A glab that reports AUTH_OUTPUT, then answers the probe that follows.

    AUTH_OUTPUT starts hard against the left margin: glab's own report marks a
    host by its line having no indent, which is the thing being parsed, so
    indenting it here would test something else entirely.
    """
    return f"""
            if [ "$1" = "auth" ]; then
              cat >&2 <<'EOF'
{AUTH_OUTPUT}EOF
              exit 0
            fi
            echo "$@" >> "$GLAB_ARGV"
{probe}
    """


class Auth(FakeGlab):
    def setUp(self) -> None:
        super().setUp()
        self.argv = self.dir / "argv"
        os.environ["GLAB_ARGV"] = str(self.argv)

    def asked(self) -> str:
        return self.argv.read_text() if self.argv.exists() else ""

    def test_a_host_glab_calls_invalid_is_used_anyway_when_it_answers(self) -> None:
        """The regression this probe exists for.

        Taking `! Invalid token provided` as the verdict locked the panel out of
        a GitLab that works: no work items, and a settings page confidently
        telling you to log in again to a host you are already logged in to. The
        token, not glab's opinion of it, is what decides — so the panel asks the
        host directly and believes the answer.
        """
        self.glab(auth_script(SPEAKS))
        answer = read.hosts(force=True)
        self.assertEqual(sorted(answer["usable"]), ["git.example.com", "git.good.com"])
        good = {h["host"]: h for h in answer["hosts"]}
        self.assertEqual(good["git.example.com"]["user"], "coen")
        self.assertEqual(good["git.example.com"]["why"], "")

    def test_a_host_with_no_token_is_not_asked_at_all(self) -> None:
        """Nothing to try, so nothing is spent trying it."""
        self.glab(auth_script(SPEAKS))
        answer = read.hosts(force=True)
        gitlab_com = next(h for h in answer["hosts"] if h["host"] == "gitlab.com")
        self.assertFalse(gitlab_com["ok"])
        self.assertEqual(gitlab_com["why"], "no token")
        self.assertNotIn("gitlab.com", self.asked())

    def test_a_token_the_host_really_turns_down_is_reported(self) -> None:
        """The other half: believing the probe means believing it when it fails."""
        self.glab(auth_script(REFUSES))
        answer = read.hosts(force=True)
        self.assertEqual(answer["usable"], [])
        turned = {h["host"]: h["why"] for h in answer["hosts"]}
        self.assertEqual(turned["git.example.com"], "the token was turned down")

    def test_a_host_that_cannot_be_reached_is_not_a_bad_token(self) -> None:
        """A VPN that is not up should not read as credentials to go and fix."""
        self.glab(auth_script(UNREACHABLE))
        answer = read.hosts(force=True)
        why = {h["host"]: h["why"] for h in answer["hosts"]}
        self.assertEqual(why["git.example.com"], "it could not be reached")

    def test_each_kind_of_trouble_is_a_sentence(self) -> None:
        """The list and the sentence are two different strings, on purpose.

        A host's `why` is the phrase that follows its own name in a list — "…
        — the token was turned down" — and there was one template dropping that
        phrase into a sentence, which read "glab has the token was turned down
        for git.example.com". Anything user-facing built by concatenating these
        has to be checked as the sentence it turns into.
        """
        for entry, expect in [
            ({"host": "gitlab.com", "code": "no-token"},
             "glab has no token for gitlab.com"),
            ({"host": "git.example.com", "code": "turned-down"},
             "git.example.com turned glab's token down — it needs logging in again"),
            ({"host": "git.example.com", "code": "offline"},
             "git.example.com could not be reached"),
            ({"host": "git.example.com", "code": "not-logged-in"},
             "glab is not logged in to git.example.com"),
            # Nothing known about it still has to be a sentence, not a brace.
            ({"host": "git.example.com", "code": ""},
             "glab cannot talk to git.example.com"),
        ]:
            with self.subTest(code=entry["code"]):
                self.assertEqual(read.trouble_line(entry), expect)

    def test_no_glab_at_all_is_its_own_answer(self) -> None:
        answer = read.hosts(force=True)       # nothing on the PATH we made
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason"], cli.NO_CLI)
        self.assertIn("glab", answer["message"])


class WhyItFailed(unittest.TestCase):
    """Each failure has to arrive as a word, because each one has its own fix."""

    def test_the_named_reasons(self) -> None:
        for said, expect in [
            ("GET /api/v4/user: 401 Unauthorized", cli.UNAUTHORIZED),
            ("no token provided", cli.NOT_LOGGED_IN),
            ('dial tcp: lookup git.example.com: no such host', cli.OFFLINE),
            ("context deadline exceeded", cli.OFFLINE),
            ("something else entirely", cli.FAILED),
        ]:
            with self.subTest(said=said):
                self.assertEqual(cli.why_failed(said), expect)


class Endpoints(unittest.TestCase):
    """Every endpoint is written by the panel, and checked again anyway."""

    def test_what_may_be_asked_for(self) -> None:
        for good in ("issues?scope=assigned_to_me&state=opened",
                     "projects/group%2Fsub%2Fproj/issues/4/notes?sort=asc",
                     "user"):
            self.assertIsNotNone(cli.SAFE_ENDPOINT.match(good), good)
        # A leading dash is how a path becomes a flag; the rest are nonsense that
        # should never have been built, and are refused where the process starts.
        for bad in ("--version", "-X", "", "issues; rm -rf /", "issues\nuser", "pro jects"):
            self.assertIsNone(cli.SAFE_ENDPOINT.match(bad), bad)

    def test_a_refused_endpoint_never_reaches_a_process(self) -> None:
        ok, data, reason = cli.api("--version")
        self.assertFalse(ok)
        self.assertEqual(reason, cli.FAILED)
        self.assertIsNone(data)


class Pages(unittest.TestCase):
    """--paginate writes several JSON documents, and they have to be one list."""

    def test_pages_are_joined(self) -> None:
        self.assertEqual(cli.read_pages('[{"iid": 1}]\n[{"iid": 2}]'),
                         [{"iid": 1}, {"iid": 2}])

    def test_a_description_containing_a_bracket_pair_survives(self) -> None:
        """The reason this is a parser and not a string splice.

        `[see][ref]` is ordinary markdown, and an item whose description holds it
        would be silently mangled by anything stitching the pages together as
        text.
        """
        text = '[{"description": "a][b"}]\n[{"description": "]["}]'
        self.assertEqual(cli.read_pages(text),
                         [{"description": "a][b"}, {"description": "]["}])

    def test_rubbish_is_not_half_read(self) -> None:
        self.assertIsNone(cli.read_pages('[{"iid": 1}] and then nonsense'))


class Remotes(unittest.TestCase):
    """A checkout's remote, in both of git's spellings, and nested groups."""

    def test_the_urls_git_writes(self) -> None:
        for url, expect in [
            ("git@git.example.com:group/proj.git", ("git.example.com", "group/proj")),
            ("https://git.example.com/group/sub/deep/proj.git",
             ("git.example.com", "group/sub/deep/proj")),
            ("ssh://git@git.example.com:2222/group/proj.git",
             ("git.example.com", "group/proj")),
            ("https://GIT.Example.com/Group/Proj", ("git.example.com", "Group/Proj")),
            # Not a remote we can do anything with.
            ("/srv/git/bare.git", ("", "")),
            ("", ("", "")),
        ]:
            with self.subTest(url=url):
                self.assertEqual(read.split_remote(url), expect)

    def test_the_path_keeps_its_case(self) -> None:
        """Hosts are case-insensitive and project paths are not.

        GitLab will 404 a project asked for in the wrong case, so only the host
        is lowered — which is also what makes the index key stable.
        """
        self.assertEqual(read.split_remote("git@H.io:Group/MyProj.git")[1], "Group/MyProj")


class Checkouts(unittest.TestCase):
    """Reading remotes out of .git/config, and matching a project to a folder."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.was = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.was)))

    def checkout(self, name: str, *remotes: tuple[str, str]) -> Path:
        root = self.dir / name
        (root / ".git").mkdir(parents=True)
        body = "[core]\n\trepositoryformatversion = 0\n" + "".join(
            f'[remote "{who}"]\n\turl = {url}\n\tfetch = +refs/heads/*\n'
            for who, url in remotes)
        (root / ".git" / "config").write_text(body)
        return root

    def test_origin_is_preferred_over_the_others(self) -> None:
        """A fork's work items are on the project you push to."""
        root = self.checkout("fork",
                             ("upstream", "git@git.example.com:them/proj.git"),
                             ("origin", "git@git.example.com:me/proj.git"))
        self.assertEqual(read.project_for(str(root))["path"], "me/proj")

    def test_a_checkout_with_no_remote_is_not_a_project(self) -> None:
        self.assertEqual(read.project_for(str(self.checkout("alone"))), {})

    def test_a_work_item_finds_its_folder(self) -> None:
        root = self.checkout("proj", ("origin", "git@git.example.com:group/proj.git"))
        os.environ["CLAUDE_WATCHTOWER_PROJECT_DIRS"] = str(self.dir)
        match.PROJECT_DIRS = str(self.dir)       # read at import, so moved by hand
        self.addCleanup(lambda: setattr(match, "PROJECT_DIRS", ""))
        self.assertEqual(match.folder_for("git.example.com", "group/proj"), str(root))

    def test_a_project_nobody_has_checked_out_matches_nothing(self) -> None:
        """The case the page has to handle: a work item with nowhere to go.

        It must come back empty rather than fall back to some other folder — a
        session started in the wrong repository is worse than no session.
        """
        self.checkout("proj", ("origin", "git@git.example.com:group/proj.git"))
        match.PROJECT_DIRS = str(self.dir)
        self.addCleanup(lambda: setattr(match, "PROJECT_DIRS", ""))
        self.assertEqual(match.folder_for("git.example.com", "group/other"), "")


class Filing(FakeGlab):
    """Creating a work item: what is sent, and what is refused before anything is."""

    def test_a_work_item_needs_a_title(self) -> None:
        ok, message, status, _ = create_issue({"host": "h", "path": "p"}, {"title": "   "})
        self.assertFalse(ok)
        self.assertEqual(status, 400)
        # And nothing was run: there is no glab on this PATH to run.

    def test_the_title_goes_through_raw_field(self) -> None:
        """--raw-field, never --field.

        --field converts "true", numbers and a leading @ into other things
        entirely — a work item titled `true` would be sent as a boolean, and one
        whose description begins with @ would be read as a filename to upload.
        """
        self.glab('''
            echo "$@" > "$GLAB_ARGV"
            echo '{"iid": 7, "web_url": "https://x/y/-/issues/7", "references": {"short": "#7"}}'
        ''')
        os.environ["GLAB_ARGV"] = str(self.dir / "argv")
        ok, message, status, item = create_issue(
            {"host": "git.example.com", "path": "group/proj"},
            {"title": "true", "description": "@notafile"})
        self.assertTrue(ok, message)
        self.assertEqual(item["iid"], 7)
        argv = (self.dir / "argv").read_text()
        self.assertIn("--raw-field title=true", argv)
        self.assertIn("--raw-field description=@notafile", argv)
        self.assertNotIn("--field ", argv)
        self.assertIn("--method POST", argv)
        # The project is one encoded segment, which is how GitLab takes a path.
        self.assertIn("projects/group%2Fproj/issues", argv)

    def test_a_refusal_from_the_server_is_not_reported_as_success(self) -> None:
        self.glab('''
            echo '{"message": "401 Unauthorized"}' >&2
            exit 1
        ''')
        ok, message, status, _ = create_issue(
            {"host": "git.example.com", "path": "group/proj"}, {"title": "Something"})
        self.assertFalse(ok)
        self.assertEqual(status, 502)
        self.assertIn("turned the token down", message)


class Labels(unittest.TestCase):
    def test_what_a_label_may_be(self) -> None:
        self.assertEqual(clean_labels("bug, needs review ,, bug"), ["bug", "needs review"])
        self.assertEqual(clean_labels(["  a  b  "]), ["a b"])
        self.assertEqual(clean_labels(None), [])
        self.assertEqual(len(clean_labels([str(n) for n in range(50)])), 12)


class Brief(unittest.TestCase):
    """The first thing a session started on a work item reads."""

    def test_it_carries_the_item_and_the_link(self) -> None:
        text = brief({
            "reference": "group/proj#4", "title": "Fix the thing",
            "webUrl": "https://git.example.com/group/proj/-/issues/4",
            "labels": ["bug"], "milestone": "1.2", "author": "Ann",
            "description": "It breaks on Tuesdays.",
            "notes": [{"author": "Bo", "body": "Only on Tuesdays."}],
        })
        for wanted in ("group/proj#4", "Fix the thing", "https://git.example.com",
                       "bug", "1.2", "Ann", "It breaks on Tuesdays.",
                       "Comment from Bo", "Only on Tuesdays."):
            self.assertIn(wanted, text)

    def test_it_does_not_tell_the_session_to_start_changing_things(self) -> None:
        """A brief that ends in "implement this" gets an implementation of the
        first paragraph before anybody has read the code."""
        text = brief({"title": "Fix it", "description": "Please."})
        self.assertIn("before changing anything", text)

    def test_an_item_with_no_description_still_reads_as_one(self) -> None:
        text = brief({"title": "Fix it", "reference": "g/p#1"})
        self.assertIn("no description", text)

    def test_an_enormous_item_is_cut(self) -> None:
        text = brief({"title": "x", "description": "y" * 50000})
        self.assertLessEqual(len(text), 12000)


if __name__ == "__main__":
    unittest.main()
