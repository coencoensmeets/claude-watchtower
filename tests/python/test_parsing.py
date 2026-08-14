"""Characterisation tests for the panel's pure readers.

These pin down what the parsers do *today*, before the refactor moves them into
a package. They are deliberately about observed behaviour rather than intent: if
a later phase changes an answer here, that is the signal to stop and look, not
to edit the expectation.

    python3 -m unittest discover -s tests/python

Standard library only, in keeping with the rest of the project.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import server  # noqa: E402
from watchtower import config, transcript  # noqa: E402


def porcelain(*records: str) -> str:
    """Join records the way `status --porcelain=v2 -z` does — NUL after each."""
    return "".join(record + "\0" for record in records)


class StatusFreshness(unittest.TestCase):
    """status_age / effective_status / inferred_status."""

    def test_age_prefers_the_status_stamp_in_milliseconds(self):
        self.assertAlmostEqual(server.status_age({"statusUpdatedAt": 1_000_000}, 1005.0), 5.0)

    def test_age_falls_back_to_updated_then_to_file_mtime(self):
        self.assertAlmostEqual(server.status_age({"updatedAt": 2_000_000}, 2003.0), 3.0)
        self.assertAlmostEqual(server.status_age({"fileMtime": 500.0}, 507.5), 7.5)

    def test_age_is_unknown_when_nothing_carries_a_time(self):
        self.assertIsNone(server.status_age({}, 100.0))

    def test_a_live_session_keeps_its_active_status_however_old(self):
        old = {"status": "busy", "fileMtime": 0.0}
        self.assertEqual(server.effective_status(old, 10_000.0, live=True), "busy")

    def test_a_stale_active_status_with_nothing_backing_it_drops_to_idle(self):
        old = {"status": "busy", "fileMtime": 0.0}
        self.assertEqual(server.effective_status(old, config.STATUS_TTL + 1, live=False), "idle")

    def test_a_fresh_active_status_survives_without_liveness(self):
        recent = {"status": "shell", "fileMtime": 0.0}
        self.assertEqual(server.effective_status(recent, config.STATUS_TTL - 1, live=False), "shell")

    def test_waiting_never_expires_because_it_is_blocked_on_you(self):
        blocked = {"status": "waiting", "fileMtime": 0.0}
        self.assertEqual(server.effective_status(blocked, 10_000.0, live=False), "waiting")

    def test_a_session_that_writes_no_status_is_read_from_its_liveness(self):
        self.assertEqual(server.effective_status({}, 0.0, live=True), "busy")
        self.assertEqual(server.effective_status({}, 0.0, live=False), "idle")


class ParseStatus(unittest.TestCase):
    """parse_status over `status --porcelain=v2 -z`."""

    def test_an_ordinary_change_splits_staged_from_unstaged(self):
        text = porcelain("1 M. N... 100644 100644 100644 aaa bbb server.py")
        entry, = server.parse_status(text)
        self.assertEqual(entry["path"], "server.py")
        self.assertEqual(entry["staged"], "M")
        # A dot means "nothing on this side" and is reported as no letter at all.
        self.assertIsNone(entry["unstaged"])
        self.assertFalse(entry["untracked"])
        self.assertFalse(entry["conflicted"])

    def test_a_change_on_both_sides_keeps_both_letters(self):
        text = porcelain("1 MM N... 100644 100644 100644 aaa bbb both.py")
        entry, = server.parse_status(text)
        self.assertEqual((entry["staged"], entry["unstaged"]), ("M", "M"))

    def test_a_rename_carries_its_source_path_from_the_next_field(self):
        text = porcelain("2 R. N... 100644 100644 100644 aaa bbb R100 new.py", "old.py")
        entry, = server.parse_status(text)
        self.assertEqual(entry["path"], "new.py")
        self.assertEqual(entry["origPath"], "old.py")

    def test_an_unmerged_path_is_marked_conflicted_with_no_letters(self):
        text = porcelain("u UU N... 100644 100644 100644 100644 aaa bbb ccc clash.py")
        entry, = server.parse_status(text)
        self.assertEqual(entry["path"], "clash.py")
        self.assertTrue(entry["conflicted"])
        self.assertIsNone(entry["staged"])

    def test_an_untracked_path_keeps_everything_after_the_marker(self):
        entry, = server.parse_status(porcelain("? notes/new file.md"))
        self.assertEqual(entry["path"], "notes/new file.md")
        self.assertTrue(entry["untracked"])

    def test_entries_come_back_sorted_by_path(self):
        text = porcelain(
            "? zeta.md",
            "1 M. N... 100644 100644 100644 aaa bbb alpha.py",
            "? middle.txt",
        )
        self.assertEqual([e["path"] for e in server.parse_status(text)],
                         ["alpha.py", "middle.txt", "zeta.md"])

    def test_a_truncated_record_is_skipped_rather_than_guessed_at(self):
        self.assertEqual(server.parse_status(porcelain("1 M. N... 100644")), [])

    def test_empty_input_gives_no_entries(self):
        self.assertEqual(server.parse_status(""), [])


class ParseLog(unittest.TestCase):
    """parse_log over the unit-separated LOG_FORMAT."""

    @staticmethod
    def record(sha="a" * 40, short="aaaaaaa", parents="", author="Ada",
               when="1700000000", refs="", subject="do the thing") -> str:
        return "\x1f".join([sha, short, parents, author, when, refs, subject]) + "\x1e"

    def test_a_commit_is_read_field_by_field(self):
        commit, = server.parse_log(self.record())
        self.assertEqual(commit["short"], "aaaaaaa")
        self.assertEqual(commit["author"], "Ada")
        self.assertEqual(commit["at"], 1700000000)
        self.assertEqual(commit["subject"], "do the thing")
        self.assertEqual(commit["parents"], [])

    def test_a_merge_carries_both_parents(self):
        commit, = server.parse_log(self.record(parents="b" * 40 + " " + "c" * 40))
        self.assertEqual(len(commit["parents"]), 2)

    def test_refs_are_split_and_trimmed(self):
        commit, = server.parse_log(self.record(refs="HEAD -> main, origin/main, tag: v1"))
        self.assertEqual(commit["refs"], ["HEAD -> main", "origin/main", "tag: v1"])

    def test_a_subject_containing_separators_of_its_own_still_parses(self):
        # The whole point of the unit-separated format: a subject is free text.
        commit, = server.parse_log(self.record(subject="fix: a, b | c -> d"))
        self.assertEqual(commit["subject"], "fix: a, b | c -> d")

    def test_an_unreadable_timestamp_becomes_zero_rather_than_failing(self):
        commit, = server.parse_log(self.record(when="not-a-time"))
        self.assertEqual(commit["at"], 0)

    def test_short_records_are_skipped(self):
        self.assertEqual(server.parse_log("a\x1fb\x1e"), [])


class GitSaid(unittest.TestCase):
    """git_said — the tail of git's output, for a snackbar."""

    def test_the_last_lines_are_what_is_kept(self):
        self.assertEqual(server.git_said("one\ntwo\nthree", lines=2), "two · three")

    def test_carriage_return_progress_rewrites_are_split_apart(self):
        said = server.git_said("Counting: 1%\rCounting: 100%\rdone", lines=1)
        self.assertEqual(said, "done")

    def test_remote_lines_are_dropped_unless_the_command_failed(self):
        text = "remote: rejected by a hook\nTo git@example:repo"
        self.assertNotIn("rejected", server.git_said(text))
        self.assertIn("rejected", server.git_said(text, remote=True))

    def test_dropping_every_line_falls_back_to_keeping_them(self):
        # Filtering to nothing would leave the panel with no reason to show.
        self.assertIn("remote:", server.git_said("remote: only this"))

    def test_the_result_is_capped(self):
        self.assertLessEqual(len(server.git_said("x" * 5000, lines=1)), 500)


class CleanMessage(unittest.TestCase):
    """clean_message — taking the model at its word, not its formatting."""

    def test_a_bare_message_is_left_alone(self):
        self.assertEqual(server.clean_message("fix: tighten the git lock"),
                         "fix: tighten the git lock")

    def test_a_fenced_block_loses_its_fences(self):
        self.assertEqual(server.clean_message("```\nfix: a thing\n```"), "fix: a thing")

    def test_a_tagged_fence_loses_its_fences_too(self):
        self.assertEqual(server.clean_message("```text\nfix: a thing\n```"), "fix: a thing")

    def test_a_body_survives_the_fence_strip(self):
        cleaned = server.clean_message("```\nsubject line\n\nthe body\n```")
        self.assertEqual(cleaned, "subject line\n\nthe body")

    def test_surrounding_blank_lines_go(self):
        self.assertEqual(server.clean_message("\n\n  subject  \n\n"), "subject")


class ParsePlan(unittest.TestCase):
    """parse_plan over what `/usage` prints."""

    REPORT = (
        "Claude Max 20x\n"
        "Current session: 34% used · resets Aug 12, 5:49pm (Europe/Amsterdam)\n"
        "Current week (all models): 0% used\n"
        "Last 24h · 4141 requests · 46 sessions\n"
        "  Opus 5 · 2000 requests\n"
        "  Sonnet 5 · 2141 requests\n"
    )

    def test_the_first_ordinary_line_becomes_the_headline(self):
        self.assertEqual(server.parse_plan(self.REPORT)["headline"], "Claude Max 20x")

    def test_a_limit_line_is_read_into_figures(self):
        first = server.parse_plan(self.REPORT)["limits"][0]
        self.assertEqual(first["name"], "Current session")
        self.assertEqual(first["percent"], 34)
        self.assertEqual(first["resets"], "Aug 12, 5:49pm (Europe/Amsterdam)")

    def test_a_limit_with_nothing_to_reset_from_still_parses(self):
        second = server.parse_plan(self.REPORT)["limits"][1]
        self.assertEqual((second["name"], second["percent"], second["resets"]),
                         ("Current week (all models)", 0, ""))

    def test_indented_lines_belong_to_the_block_above_them(self):
        block, = server.parse_plan(self.REPORT)["blocks"]
        self.assertTrue(block["title"].startswith("Last 24h"))
        self.assertEqual(len(block["lines"]), 2)

    def test_a_percentage_over_a_hundred_is_clamped(self):
        limit, = server.parse_plan("Session: 140% used")["limits"]
        self.assertEqual(limit["percent"], 100)

    def test_output_that_parses_to_nothing_still_hands_back_its_text(self):
        read = server.parse_plan("something unexpected entirely")
        self.assertEqual(read["limits"], [])
        self.assertEqual(read["text"], "something unexpected entirely")


class QuestionAsked(unittest.TestCase):
    """question_asked — an AskUserQuestion call trimmed to what a card shows."""

    @staticmethod
    def block(**question) -> dict:
        return {"id": "toolu_1", "input": {"questions": [question]}}

    def test_a_question_and_its_options_come_through(self):
        read = transcript.question_asked(self.block(
            question="Which way?", header="Approach", multiSelect=False,
            options=[{"label": "Left", "description": "towards the sea"},
                     {"label": "Right", "description": ""}],
        ))
        self.assertEqual(read["toolUseId"], "toolu_1")
        asked, = read["questions"]
        self.assertEqual(asked["question"], "Which way?")
        self.assertEqual(asked["header"], "Approach")
        self.assertFalse(asked["multiSelect"])
        self.assertEqual([o["label"] for o in asked["options"]], ["Left", "Right"])

    def test_an_option_with_no_label_is_dropped(self):
        read = transcript.question_asked(self.block(
            question="Pick", options=[{"label": ""}, {"label": "Real"}]))
        self.assertEqual([o["label"] for o in read["questions"][0]["options"]], ["Real"])

    def test_options_are_capped(self):
        many = [{"label": f"Option {n}"} for n in range(transcript.MAX_QUESTION_OPTIONS + 5)]
        read = transcript.question_asked(self.block(question="Pick", options=many))
        self.assertEqual(len(read["questions"][0]["options"]), transcript.MAX_QUESTION_OPTIONS)

    def test_a_call_that_is_not_a_question_reads_as_none(self):
        self.assertIsNone(transcript.question_asked({"input": {"questions": []}}))
        self.assertIsNone(transcript.question_asked({"input": "not a dict"}))
        self.assertIsNone(transcript.question_asked({}))


class ToolDetail(unittest.TestCase):
    """tool_detail — the one line that says what a tool call was about."""

    def test_the_first_matching_key_wins_in_listed_order(self):
        detail = transcript.tool_detail({"command": "ls -la", "description": "List files"})
        self.assertEqual(detail, "List files")

    def test_whitespace_is_collapsed(self):
        self.assertEqual(transcript.tool_detail({"command": "git   status\n  --short"}),
                         "git status --short")

    def test_a_question_falls_back_to_the_question_itself(self):
        detail = transcript.tool_detail({"questions": [{"question": "Which way?"}]})
        self.assertEqual(detail, "Which way?")

    def test_nothing_recognisable_gives_an_empty_string(self):
        self.assertEqual(transcript.tool_detail({"unknown": "field"}), "")
        self.assertEqual(transcript.tool_detail("not a dict"), "")

    def test_the_detail_is_capped(self):
        self.assertEqual(len(transcript.tool_detail({"command": "x" * 500})), 200)


class UnwrapSent(unittest.TestCase):
    """unwrap_sent — peeling the envelopes off a message sent over a socket."""

    def test_a_peer_envelope_gives_up_its_body_and_sender(self):
        read = transcript.unwrap_sent(
            '<cross-session-message from-name="Ada">\nhello there\n</cross-session-message>')
        self.assertEqual(read, {"text": "hello there", "from": "Ada"})

    def test_an_envelope_without_a_name_reports_no_sender(self):
        read = transcript.unwrap_sent(
            "<cross-session-message>\nhello\n</cross-session-message>")
        self.assertEqual(read, {"text": "hello", "from": None})

    def test_a_delivered_message_is_unwrapped_from_its_preamble(self):
        read = transcript.unwrap_sent(
            "Another Claude session sent a message:\nplease rebase\n\n"
            "This came from another Claude session — not typed by your user")
        self.assertEqual(read, {"text": "please rebase", "from": None})

    def test_both_wrappings_at_once_peel_in_order(self):
        read = transcript.unwrap_sent(
            "Another Claude session sent a message:\n"
            '<cross-session-message from-name="Ada">\nnested\n</cross-session-message>\n\n'
            "This came from another Claude session — not typed by your user")
        self.assertEqual(read, {"text": "nested", "from": "Ada"})

    def test_an_ordinary_turn_is_not_a_delivery(self):
        self.assertIsNone(transcript.unwrap_sent("just something the user typed"))


class SummariseBlock(unittest.TestCase):
    """summarise_block — one transcript block as a single line."""

    def test_text_is_collapsed_to_one_line(self):
        self.assertEqual(transcript.summarise_block({"type": "text", "text": "a\n\n  b  "}), "a b")

    def test_empty_text_summarises_to_nothing(self):
        self.assertIsNone(transcript.summarise_block({"type": "text", "text": "   "}))

    def test_a_tool_call_is_named_with_its_detail(self):
        summary = transcript.summarise_block(
            {"type": "tool_use", "name": "Bash", "input": {"command": "git status"}})
        self.assertEqual(summary, "Bash: git status")

    def test_a_tool_call_with_no_detail_is_just_its_name(self):
        self.assertEqual(transcript.summarise_block({"type": "tool_use", "name": "Read"}), "Read")

    def test_thinking_says_so(self):
        self.assertEqual(transcript.summarise_block({"type": "thinking"}), "thinking")

    def test_an_unknown_block_summarises_to_nothing(self):
        self.assertIsNone(transcript.summarise_block({"type": "image"}))


class SmallHelpers(unittest.TestCase):

    def test_count_pluralises_everything_but_one(self):
        self.assertEqual(server.count(1, "file"), "1 file")
        self.assertEqual(server.count(0, "file"), "0 files")
        self.assertEqual(server.count(3, "file"), "3 files")

    def test_loopback_covers_localhost_and_both_families(self):
        for host in ("localhost", "", "127.0.0.1", "127.1.2.3", "::1"):
            self.assertTrue(server.is_loopback(host), host)

    def test_anything_reachable_from_elsewhere_is_not_loopback(self):
        for host in ("0.0.0.0", "192.168.1.10", "example.com", "::"):
            self.assertFalse(server.is_loopback(host), host)


if __name__ == "__main__":
    unittest.main()
