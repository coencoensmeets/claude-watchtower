"""Characterisation tests for the panel's pure readers.

These pin down what the readers do *today*. They are deliberately about
observed behaviour rather than intent: if a change makes one of these fail, that
is the signal to stop and look, not to edit the expectation.

    python3 -m unittest discover -s tests/python

Standard library only, in keeping with the rest of the project.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower import config, input, owned, plan, store, transcript, update  # noqa: E402
from watchtower.git import message, read, write  # noqa: E402


def porcelain(*records: str) -> str:
    """Join records the way `status --porcelain=v2 -z` does — NUL after each."""
    return "".join(record + "\0" for record in records)


def record(tag: str, at: str, sha: str, subject: str, body: str) -> str:
    """One `for-each-ref` record, the way update.read_tags asks for it."""
    return update.FIELD.join([tag, at, sha, subject, body]) + update.RECORD


class StatusFreshness(unittest.TestCase):
    """status_age / effective_status / inferred_status."""

    def test_age_prefers_the_status_stamp_in_milliseconds(self):
        self.assertAlmostEqual(store.status_age({"statusUpdatedAt": 1_000_000}, 1005.0), 5.0)

    def test_age_falls_back_to_updated_then_to_file_mtime(self):
        self.assertAlmostEqual(store.status_age({"updatedAt": 2_000_000}, 2003.0), 3.0)
        self.assertAlmostEqual(store.status_age({"fileMtime": 500.0}, 507.5), 7.5)

    def test_age_is_unknown_when_nothing_carries_a_time(self):
        self.assertIsNone(store.status_age({}, 100.0))

    def test_a_live_session_keeps_its_active_status_however_old(self):
        old = {"status": "busy", "fileMtime": 0.0}
        self.assertEqual(store.effective_status(old, 10_000.0, live=True), "busy")

    def test_a_stale_active_status_with_nothing_backing_it_drops_to_idle(self):
        old = {"status": "busy", "fileMtime": 0.0}
        self.assertEqual(store.effective_status(old, config.STATUS_TTL + 1, live=False), "idle")

    def test_a_fresh_active_status_survives_without_liveness(self):
        recent = {"status": "shell", "fileMtime": 0.0}
        self.assertEqual(store.effective_status(recent, config.STATUS_TTL - 1, live=False), "shell")

    def test_waiting_never_expires_because_it_is_blocked_on_you(self):
        blocked = {"status": "waiting", "fileMtime": 0.0}
        self.assertEqual(store.effective_status(blocked, 10_000.0, live=False), "waiting")

    def test_a_session_that_writes_no_status_is_read_from_its_liveness(self):
        self.assertEqual(store.effective_status({}, 0.0, live=True), "busy")
        self.assertEqual(store.effective_status({}, 0.0, live=False), "idle")


class ParseStatus(unittest.TestCase):
    """parse_status over `status --porcelain=v2 -z`."""

    def test_an_ordinary_change_splits_staged_from_unstaged(self):
        text = porcelain("1 M. N... 100644 100644 100644 aaa bbb server.py")
        entry, = read.parse_status(text)
        self.assertEqual(entry["path"], "server.py")
        self.assertEqual(entry["staged"], "M")
        # A dot means "nothing on this side" and is reported as no letter at all.
        self.assertIsNone(entry["unstaged"])
        self.assertFalse(entry["untracked"])
        self.assertFalse(entry["conflicted"])

    def test_a_change_on_both_sides_keeps_both_letters(self):
        text = porcelain("1 MM N... 100644 100644 100644 aaa bbb both.py")
        entry, = read.parse_status(text)
        self.assertEqual((entry["staged"], entry["unstaged"]), ("M", "M"))

    def test_a_rename_carries_its_source_path_from_the_next_field(self):
        text = porcelain("2 R. N... 100644 100644 100644 aaa bbb R100 new.py", "old.py")
        entry, = read.parse_status(text)
        self.assertEqual(entry["path"], "new.py")
        self.assertEqual(entry["origPath"], "old.py")

    def test_an_unmerged_path_is_marked_conflicted_with_no_letters(self):
        text = porcelain("u UU N... 100644 100644 100644 100644 aaa bbb ccc clash.py")
        entry, = read.parse_status(text)
        self.assertEqual(entry["path"], "clash.py")
        self.assertTrue(entry["conflicted"])
        self.assertIsNone(entry["staged"])

    def test_an_untracked_path_keeps_everything_after_the_marker(self):
        entry, = read.parse_status(porcelain("? notes/new file.md"))
        self.assertEqual(entry["path"], "notes/new file.md")
        self.assertTrue(entry["untracked"])

    def test_entries_come_back_sorted_by_path(self):
        text = porcelain(
            "? zeta.md",
            "1 M. N... 100644 100644 100644 aaa bbb alpha.py",
            "? middle.txt",
        )
        self.assertEqual([e["path"] for e in read.parse_status(text)],
                         ["alpha.py", "middle.txt", "zeta.md"])

    def test_a_truncated_record_is_skipped_rather_than_guessed_at(self):
        self.assertEqual(read.parse_status(porcelain("1 M. N... 100644")), [])

    def test_empty_input_gives_no_entries(self):
        self.assertEqual(read.parse_status(""), [])


class ParseLog(unittest.TestCase):
    """parse_log over the unit-separated LOG_FORMAT."""

    @staticmethod
    def record(sha="a" * 40, short="aaaaaaa", parents="", author="Ada",
               when="1700000000", refs="", subject="do the thing") -> str:
        return "\x1f".join([sha, short, parents, author, when, refs, subject]) + "\x1e"

    def test_a_commit_is_read_field_by_field(self):
        commit, = read.parse_log(self.record())
        self.assertEqual(commit["short"], "aaaaaaa")
        self.assertEqual(commit["author"], "Ada")
        self.assertEqual(commit["at"], 1700000000)
        self.assertEqual(commit["subject"], "do the thing")
        self.assertEqual(commit["parents"], [])

    def test_a_merge_carries_both_parents(self):
        commit, = read.parse_log(self.record(parents="b" * 40 + " " + "c" * 40))
        self.assertEqual(len(commit["parents"]), 2)

    def test_refs_are_split_and_trimmed(self):
        commit, = read.parse_log(self.record(refs="HEAD -> main, origin/main, tag: v1"))
        self.assertEqual(commit["refs"], ["HEAD -> main", "origin/main", "tag: v1"])

    def test_a_subject_containing_separators_of_its_own_still_parses(self):
        # The whole point of the unit-separated format: a subject is free text.
        commit, = read.parse_log(self.record(subject="fix: a, b | c -> d"))
        self.assertEqual(commit["subject"], "fix: a, b | c -> d")

    def test_an_unreadable_timestamp_becomes_zero_rather_than_failing(self):
        commit, = read.parse_log(self.record(when="not-a-time"))
        self.assertEqual(commit["at"], 0)

    def test_short_records_are_skipped(self):
        self.assertEqual(read.parse_log("a\x1fb\x1e"), [])


class GitSaid(unittest.TestCase):
    """git_said — the tail of git's output, for a snackbar."""

    def test_the_last_lines_are_what_is_kept(self):
        self.assertEqual(write.git_said("one\ntwo\nthree", lines=2), "two · three")

    def test_carriage_return_progress_rewrites_are_split_apart(self):
        said = write.git_said("Counting: 1%\rCounting: 100%\rdone", lines=1)
        self.assertEqual(said, "done")

    def test_remote_lines_are_dropped_unless_the_command_failed(self):
        text = "remote: rejected by a hook\nTo git@example:repo"
        self.assertNotIn("rejected", write.git_said(text))
        self.assertIn("rejected", write.git_said(text, remote=True))

    def test_dropping_every_line_falls_back_to_keeping_them(self):
        # Filtering to nothing would leave the panel with no reason to show.
        self.assertIn("remote:", write.git_said("remote: only this"))

    def test_the_result_is_capped(self):
        self.assertLessEqual(len(write.git_said("x" * 5000, lines=1)), 500)


class CleanMessage(unittest.TestCase):
    """clean_message — taking the model at its word, not its formatting."""

    def test_a_bare_message_is_left_alone(self):
        self.assertEqual(message.clean_message("fix: tighten the git lock"),
                         "fix: tighten the git lock")

    def test_a_fenced_block_loses_its_fences(self):
        self.assertEqual(message.clean_message("```\nfix: a thing\n```"), "fix: a thing")

    def test_a_tagged_fence_loses_its_fences_too(self):
        self.assertEqual(message.clean_message("```text\nfix: a thing\n```"), "fix: a thing")

    def test_a_body_survives_the_fence_strip(self):
        cleaned = message.clean_message("```\nsubject line\n\nthe body\n```")
        self.assertEqual(cleaned, "subject line\n\nthe body")

    def test_surrounding_blank_lines_go(self):
        self.assertEqual(message.clean_message("\n\n  subject  \n\n"), "subject")


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
        self.assertEqual(plan.parse_plan(self.REPORT)["headline"], "Claude Max 20x")

    def test_a_limit_line_is_read_into_figures(self):
        first = plan.parse_plan(self.REPORT)["limits"][0]
        self.assertEqual(first["name"], "Current session")
        self.assertEqual(first["percent"], 34)
        self.assertEqual(first["resets"], "Aug 12, 5:49pm (Europe/Amsterdam)")

    def test_a_limit_with_nothing_to_reset_from_still_parses(self):
        second = plan.parse_plan(self.REPORT)["limits"][1]
        self.assertEqual((second["name"], second["percent"], second["resets"]),
                         ("Current week (all models)", 0, ""))

    def test_indented_lines_belong_to_the_block_above_them(self):
        block, = plan.parse_plan(self.REPORT)["blocks"]
        self.assertTrue(block["title"].startswith("Last 24h"))
        self.assertEqual(len(block["lines"]), 2)

    def test_a_percentage_over_a_hundred_is_clamped(self):
        limit, = plan.parse_plan("Session: 140% used")["limits"]
        self.assertEqual(limit["percent"], 100)

    def test_output_that_parses_to_nothing_still_hands_back_its_text(self):
        read = plan.parse_plan("something unexpected entirely")
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
        self.assertEqual(write.count(1, "file"), "1 file")
        self.assertEqual(write.count(0, "file"), "0 files")
        self.assertEqual(write.count(3, "file"), "3 files")

    def test_loopback_covers_localhost_and_both_families(self):
        for host in ("localhost", "", "127.0.0.1", "127.1.2.3", "::1"):
            self.assertTrue(input.is_loopback(host), host)

    def test_anything_reachable_from_elsewhere_is_not_loopback(self):
        for host in ("0.0.0.0", "192.168.1.10", "example.com", "::"):
            self.assertFalse(input.is_loopback(host), host)


class ReleaseTags(unittest.TestCase):
    """update.release_of / update.parse_tags — which tags are releases, in order."""

    def test_a_plain_three_part_version_is_a_release_with_or_without_the_v(self):
        self.assertEqual(update.release_of("v1.4.0"), (1, 4, 0))
        self.assertEqual(update.release_of("1.4.0"), (1, 4, 0))
        self.assertEqual(update.release_of(" v0.0.1 "), (0, 0, 1))

    def test_anything_else_is_not_a_release(self):
        for tag in ("v1.4", "v1.4.0-rc1", "v1.4.0+build", "nightly", "release-1.4.0", ""):
            self.assertIsNone(update.release_of(tag), tag)

    def test_tags_come_back_newest_first_by_version_not_by_string(self):
        text = record("v1.9.0", "10", "aaa", "nine", "") + record("v1.10.0", "20", "bbb", "ten", "")
        self.assertEqual([t["tag"] for t in update.parse_tags(text)], ["v1.10.0", "v1.9.0"])

    def test_a_multiline_release_body_survives_the_next_tag(self):
        text = (record("v2.0.0", "20", "bbb", "the big one", "First line.\nSecond line.")
                + record("v1.0.0", "10", "aaa", "the first", ""))
        tags = update.parse_tags(text)
        self.assertEqual(tags[0]["body"], "First line.\nSecond line.")
        self.assertEqual(tags[1]["tag"], "v1.0.0")

    def test_a_tag_that_is_not_a_release_is_left_out_rather_than_carried(self):
        text = record("v1.0.0", "10", "aaa", "one", "") + record("some-branch-point", "20", "bbb", "x", "")
        self.assertEqual([t["tag"] for t in update.parse_tags(text)], ["v1.0.0"])

    def test_an_unreadable_date_does_not_lose_the_tag(self):
        self.assertEqual(update.parse_tags(record("v1.0.0", "not-a-date", "aaa", "one", "")),
                         [{"tag": "v1.0.0", "version": [1, 0, 0], "at": 0.0, "sha": "aaa",
                           "subject": "one", "body": ""}])


class WhyNot(unittest.TestCase):
    """update.why_not — the reasons a checkout is left where it is."""

    CLEAN = {"dirty": False, "branch": "main", "detached": False}

    def test_a_clean_checkout_on_the_default_branch_can_move(self):
        self.assertEqual(update.why_not(self.CLEAN, "main", 0), "")

    def test_a_detached_checkout_can_move_because_a_release_tag_is_detached(self):
        self.assertEqual(update.why_not({**self.CLEAN, "branch": "", "detached": True}, "main", 0), "")

    def test_uncommitted_work_is_the_first_thing_said(self):
        why = update.why_not({**self.CLEAN, "dirty": True, "branch": "wip"}, "main", 3)
        self.assertIn("uncommitted work", why)

    def test_somebody_elses_branch_is_left_alone(self):
        why = update.why_not({**self.CLEAN, "branch": "feature/x"}, "main", 0)
        self.assertIn("feature/x", why)
        self.assertIn("leaves it alone", why)

    def test_being_ahead_of_the_newest_release_is_not_being_behind_it(self):
        self.assertIn("ahead", update.why_not(self.CLEAN, "main", 1))
        self.assertIn("1 commit ", update.why_not(self.CLEAN, "main", 1))
        self.assertIn("2 commits ", update.why_not(self.CLEAN, "main", 2))


class RunningHere(unittest.TestCase):
    """update.running_here — what a restart would actually cost.

    Only the sessions the panel runs itself. One in a terminal is its own process
    with its own pid and lives straight through a panel restart, so counting it
    would be warning about nothing — and a warning that cries wolf is one people
    learn to press past.
    """

    class Alive:
        """Stands in for a held Popen. `poll()` is the whole of the interface."""
        def __init__(self, gone=False):
            self.gone = gone

        def poll(self):
            return 1 if self.gone else None

    def hold(self, **procs):
        """Put fake held sessions in place for the length of one test."""
        for store in (owned.OWNED_PROCS, owned.OWNED_BUSY, owned.OWNED_QUEUE, owned.OWNED_COMPACT):
            kept = dict(store)
            store.clear()
            self.addCleanup(lambda s=store, k=kept: (s.clear(), s.update(k)))
        owned.OWNED_PROCS.update({sid: {"proc": proc} for sid, proc in procs.items()})

    def test_nothing_held_is_nothing_to_warn_about(self):
        self.hold()
        self.assertEqual(update.running_here(),
                         {"here": 0, "busy": 0, "compacting": 0, "queued": 0, "names": []})

    def test_a_held_session_is_counted(self):
        self.hold(one=self.Alive())
        self.assertEqual(update.running_here()["here"], 1)

    def test_a_process_that_has_already_gone_is_not(self):
        self.hold(one=self.Alive(), two=self.Alive(gone=True))
        self.assertEqual(update.running_here()["here"], 1)

    def test_a_turn_in_flight_is_counted_apart(self):
        self.hold(one=self.Alive(), two=self.Alive())
        owned.OWNED_BUSY["one"] = 1.0
        found = update.running_here()
        self.assertEqual((found["here"], found["busy"]), (2, 1))

    def test_a_turn_in_flight_on_a_session_that_is_gone_is_not_counted(self):
        # The bookkeeping outlives the process by a moment either way round, and
        # a warning about a session that is not there is worse than none.
        self.hold(one=self.Alive(gone=True))
        owned.OWNED_BUSY["one"] = 1.0
        self.assertEqual(update.running_here()["busy"], 0)

    def test_typed_ahead_messages_are_totalled_across_sessions(self):
        self.hold(one=self.Alive(), two=self.Alive())
        owned.OWNED_QUEUE["one"] = ["a", "b"]
        owned.OWNED_QUEUE["two"] = ["c"]
        self.assertEqual(update.running_here()["queued"], 3)

    def test_a_queue_on_a_session_that_is_gone_is_not_totalled(self):
        self.hold(one=self.Alive(gone=True))
        owned.OWNED_QUEUE["one"] = ["a", "b"]
        self.assertEqual(update.running_here()["queued"], 0)

    def test_only_a_compaction_that_is_actually_running_counts(self):
        self.hold(one=self.Alive(), two=self.Alive())
        owned.OWNED_COMPACT["one"] = {"running": True}
        owned.OWNED_COMPACT["two"] = {"running": False}
        self.assertEqual(update.running_here()["compacting"], 1)

    def test_the_names_put_the_mid_turn_ones_first(self):
        # They are the ones worth reading before pressing, so they are the ones
        # that survive the cut when there are more sessions than room for names.
        self.hold(**{f"s{n}": self.Alive() for n in range(6)})
        owned.OWNED_BUSY["s5"] = 1.0
        names = update.running_here()["names"]
        self.assertEqual(len(names), update.RUNNING_NAMES_MAX)

    def test_a_session_with_no_row_still_gets_a_word_for_a_name(self):
        self.hold(**{"no-such-session-at-all": self.Alive()})
        self.assertEqual(update.running_here()["names"], ["a session"])


class UnitFromCgroup(unittest.TestCase):
    """update.unit_from_cgroup — which unit, if any, gets restarted.

    The one function here where a wrong answer is destructive: `user@1000.service`
    is the user's whole session manager, and every process in a desktop sits
    somewhere underneath it.
    """

    def test_a_service_is_named_by_the_leaf_of_its_cgroup(self):
        self.assertEqual(update.unit_from_cgroup(
            "0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
            "claude-watchtower.service\n"), "claude-watchtower.service")

    def test_a_scope_inside_the_session_manager_is_not_a_unit_to_restart(self):
        self.assertEqual(update.unit_from_cgroup(
            "0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
            "app-code-7989.scope\n"), "")

    def test_the_session_manager_itself_is_never_the_answer(self):
        self.assertEqual(update.unit_from_cgroup(
            "0::/user.slice/user-1000.slice/user@1000.service\n"), "")

    def test_a_cgroup_v1_machine_gets_no_answer_rather_than_a_guess(self):
        self.assertEqual(update.unit_from_cgroup(
            "12:pids:/user.slice\n11:cpu:/user.slice/thing.service\n"), "")

    def test_nothing_readable_is_no_unit(self):
        for text in ("", "\n", "0::/", "0::/init.scope"):
            self.assertEqual(update.unit_from_cgroup(text), "", repr(text))


if __name__ == "__main__":
    unittest.main()
