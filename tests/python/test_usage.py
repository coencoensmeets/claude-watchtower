"""Characterisation tests for the usage reader and the cost it implies.

This is the half of the panel that reports money, so the arithmetic is pinned
down exactly rather than approximately. As with test_parsing, these describe
what the code does today — a change here during the refactor is a finding, not
an expectation to update.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower import usage  # noqa: E402
from watchtower import transcript as transcript_module  # noqa: E402


def turn(request_id="req-1", model="claude-opus-5", sidechain=False,
         timestamp="2026-08-14T10:00:00Z", **figures) -> str:
    """One assistant line of a transcript, in the shape scan_usage reads."""
    return json.dumps({
        "type": "assistant",
        "requestId": request_id,
        "isSidechain": sidechain,
        "timestamp": timestamp,
        "message": {"model": model, "usage": {"input_tokens": 0, "output_tokens": 0, **figures}},
    })


class PriceOf(unittest.TestCase):

    def test_a_known_model_gets_its_rate(self):
        self.assertEqual(usage.price_of("claude-opus-5", fast=False), (5.0, 25.0))
        self.assertEqual(usage.price_of("claude-sonnet-5", fast=False), (3.0, 15.0))

    def test_a_dated_model_id_matches_on_its_prefix(self):
        self.assertEqual(usage.price_of("claude-haiku-4-5-20251001", fast=False), (1.0, 5.0))

    def test_the_longest_matching_prefix_wins(self):
        # Both "claude-3-5-haiku" and nothing else could match here; the sort by
        # length is what keeps a short prefix from shadowing a longer one.
        self.assertEqual(usage.price_of("claude-3-5-haiku-20241022", fast=False), (0.8, 4.0))

    def test_fast_mode_is_the_same_model_at_a_premium(self):
        self.assertEqual(usage.price_of("claude-opus-5", fast=True), (10.0, 50.0))
        self.assertEqual(usage.price_of("claude-opus-5", fast=False), (5.0, 25.0))

    def test_fast_falls_back_to_the_ordinary_rate_when_it_has_no_premium(self):
        self.assertEqual(usage.price_of("claude-sonnet-5", fast=True), (3.0, 15.0))

    def test_an_unknown_model_has_no_price(self):
        self.assertIsNone(usage.price_of("some-other-model", fast=False))


class ContextWindow(unittest.TestCase):

    def test_haiku_and_the_claude_3_family_get_the_small_window(self):
        self.assertEqual(usage.context_window("claude-haiku-4-5"), usage.SMALL_WINDOW)
        self.assertEqual(usage.context_window("claude-3-5-haiku"), usage.SMALL_WINDOW)

    def test_everything_current_gets_the_big_one(self):
        self.assertEqual(usage.context_window("claude-opus-5"), usage.BIG_WINDOW)


class AddUsage(unittest.TestCase):

    def setUp(self):
        self.bucket = usage.blank_counters()

    def test_a_blank_bucket_starts_at_nothing(self):
        self.assertEqual(set(self.bucket.values()), {0})

    def test_the_plain_figures_are_folded_in(self):
        usage.add_usage(self.bucket, {"input_tokens": 100, "output_tokens": 20})
        usage.add_usage(self.bucket, {"input_tokens": 5, "output_tokens": 1})
        self.assertEqual(self.bucket["requests"], 2)
        self.assertEqual(self.bucket["input"], 105)
        self.assertEqual(self.bucket["output"], 21)

    def test_thinking_tokens_come_from_the_details(self):
        usage.add_usage(self.bucket, {"output_tokens_details": {"thinking_tokens": 42}})
        self.assertEqual(self.bucket["thinking"], 42)

    def test_an_unsplit_cache_write_is_all_five_minute(self):
        usage.add_usage(self.bucket, {"cache_creation_input_tokens": 900})
        self.assertEqual((self.bucket["cacheWrite5m"], self.bucket["cacheWrite1h"]), (900, 0))

    def test_a_split_cache_write_is_read_bucket_by_bucket(self):
        usage.add_usage(self.bucket, {
            "cache_creation_input_tokens": 1000,
            "cache_creation": {"ephemeral_1h_input_tokens": 400,
                               "ephemeral_5m_input_tokens": 600},
        })
        self.assertEqual((self.bucket["cacheWrite5m"], self.bucket["cacheWrite1h"]), (600, 400))

    def test_the_total_is_trusted_over_a_split_that_does_not_add_up(self):
        # An unfamiliar bucket would otherwise go uncounted rather than merely
        # unclassified, so the remainder lands in the five-minute figure.
        usage.add_usage(self.bucket, {
            "cache_creation_input_tokens": 1000,
            "cache_creation": {"ephemeral_1h_input_tokens": 100,
                               "ephemeral_5m_input_tokens": 200},
        })
        self.assertEqual((self.bucket["cacheWrite5m"], self.bucket["cacheWrite1h"]), (900, 100))

    def test_web_searches_are_counted(self):
        usage.add_usage(self.bucket, {"server_tool_use": {"web_search_requests": 3}})
        self.assertEqual(self.bucket["webSearch"], 3)


class CostOf(unittest.TestCase):

    def counters(self, **figures) -> dict:
        return {**usage.blank_counters(), **figures}

    def test_a_million_tokens_in_and_out_costs_the_listed_rate(self):
        cost = usage.cost_of("claude-opus-5", self.counters(input=1_000_000, output=1_000_000))
        self.assertAlmostEqual(cost, 5.0 + 25.0)

    def test_cache_reads_and_writes_are_multiples_of_the_input_rate(self):
        cost = usage.cost_of("claude-opus-5", self.counters(
            cacheRead=1_000_000, cacheWrite5m=1_000_000, cacheWrite1h=1_000_000))
        expected = 5.0 * usage.CACHE_READ + 5.0 * usage.CACHE_WRITE_5M + 5.0 * usage.CACHE_WRITE_1H
        self.assertAlmostEqual(cost, expected)

    def test_fast_mode_costs_the_premium_rate(self):
        counters = self.counters(input=1_000_000)
        self.assertAlmostEqual(usage.cost_of("claude-opus-5", counters, fast=True), 10.0)
        self.assertAlmostEqual(usage.cost_of("claude-opus-5", counters, fast=False), 5.0)

    def test_web_searches_are_billed_per_thousand(self):
        cost = usage.cost_of("claude-opus-5", self.counters(webSearch=1000))
        self.assertAlmostEqual(cost, usage.WEB_SEARCH_PER_1K)

    def test_an_unknown_model_still_bills_its_searches(self):
        cost = usage.cost_of("mystery-model", self.counters(input=999, webSearch=1000))
        self.assertAlmostEqual(cost, usage.WEB_SEARCH_PER_1K)

    def test_an_unknown_model_with_nothing_billable_has_no_cost_at_all(self):
        self.assertIsNone(usage.cost_of("mystery-model", self.counters(input=999)))


class ScanUsage(unittest.TestCase):
    """scan_usage over a transcript file, including the resumed-scan path."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "transcript.jsonl"
        # The scan remembers where it stopped, keyed by path, in a module global.
        self.addCleanup(usage.USAGE_SCANS.pop, str(self.path), None)

    def write(self, *lines: str, append=False) -> None:
        with self.path.open("a" if append else "w") as handle:
            for line in lines:
                handle.write(line + "\n")

    def test_a_missing_file_reads_as_nothing(self):
        self.assertEqual(usage.scan_usage(Path(self.dir.name) / "absent.jsonl"), {})

    def test_one_turn_is_totalled_under_its_model(self):
        self.write(turn(input_tokens=100, output_tokens=20))
        held = usage.scan_usage(self.path)
        self.assertEqual(held["main"]["claude-opus-5"]["requests"], 1)
        self.assertEqual(held["main"]["claude-opus-5"]["input"], 100)

    def test_the_blocks_of_one_turn_are_counted_once(self):
        # Every content block of a turn is written down separately, all carrying
        # the same requestId. Counting each would bill the turn many times.
        self.write(turn(request_id="req-1", input_tokens=100),
                   turn(request_id="req-1", input_tokens=100),
                   turn(request_id="req-2", input_tokens=50))
        held = usage.scan_usage(self.path)
        self.assertEqual(held["main"]["claude-opus-5"]["requests"], 2)
        self.assertEqual(held["main"]["claude-opus-5"]["input"], 150)

    def test_a_synthetic_turn_the_api_never_billed_is_skipped(self):
        self.write(turn(model="<synthetic>", input_tokens=1_000_000))
        self.assertEqual(usage.scan_usage(self.path)["main"], {})

    def test_fast_mode_is_totalled_apart_from_the_ordinary_model(self):
        self.write(turn(request_id="a", input_tokens=10),
                   turn(request_id="b", input_tokens=10, speed="fast"))
        main = usage.scan_usage(self.path)["main"]
        self.assertEqual(sorted(main), ["claude-opus-5", "claude-opus-5 (fast)"])

    def test_sub_agent_turns_are_kept_apart_from_the_conversation(self):
        self.write(turn(request_id="a", input_tokens=10),
                   turn(request_id="b", input_tokens=70, sidechain=True))
        held = usage.scan_usage(self.path)
        self.assertEqual(held["main"]["claude-opus-5"]["input"], 10)
        self.assertEqual(held["agents"]["claude-opus-5"]["input"], 70)

    def test_context_is_everything_the_last_main_turn_carried(self):
        self.write(turn(request_id="a", input_tokens=1),
                   turn(request_id="b", input_tokens=100,
                        cache_read_input_tokens=900, cache_creation_input_tokens=50))
        held = usage.scan_usage(self.path)
        self.assertEqual(held["context"], 1050)
        self.assertEqual(held["contextModel"], "claude-opus-5")

    def test_a_sub_agent_turn_does_not_move_the_context_reading(self):
        self.write(turn(request_id="a", input_tokens=100),
                   turn(request_id="b", input_tokens=999, sidechain=True))
        self.assertEqual(usage.scan_usage(self.path)["context"], 100)

    def test_a_second_scan_reads_only_what_was_appended(self):
        self.write(turn(request_id="a", input_tokens=100))
        usage.scan_usage(self.path)
        self.write(turn(request_id="b", input_tokens=50), append=True)
        held = usage.scan_usage(self.path)
        self.assertEqual(held["main"]["claude-opus-5"]["requests"], 2)
        self.assertEqual(held["main"]["claude-opus-5"]["input"], 150)

    def test_a_line_still_being_written_waits_for_its_newline(self):
        self.write(turn(request_id="a", input_tokens=100))
        with self.path.open("a") as handle:
            handle.write(turn(request_id="b", input_tokens=50))  # no newline yet
        held = usage.scan_usage(self.path)
        self.assertEqual(held["main"]["claude-opus-5"]["requests"], 1)

        # And it is picked up, once, as soon as the line is finished.
        with self.path.open("a") as handle:
            handle.write("\n")
        held = usage.scan_usage(self.path)
        self.assertEqual(held["main"]["claude-opus-5"]["requests"], 2)

    def test_a_replaced_shorter_file_is_read_from_the_start_again(self):
        self.write(turn(request_id="a", input_tokens=100), turn(request_id="b", input_tokens=100))
        usage.scan_usage(self.path)
        self.write(turn(request_id="c", input_tokens=7))   # truncating rewrite
        held = usage.scan_usage(self.path)
        self.assertEqual(held["main"]["claude-opus-5"]["requests"], 1)
        self.assertEqual(held["main"]["claude-opus-5"]["input"], 7)

    def test_lines_that_are_not_assistant_turns_are_ignored(self):
        self.write(
            json.dumps({"type": "user", "message": {"content": "hello"}}),
            "not json at all",
            json.dumps({"type": "assistant", "message": {"model": "claude-opus-5"}}),  # no usage
            turn(input_tokens=5),
        )
        held = usage.scan_usage(self.path)
        self.assertEqual(held["main"]["claude-opus-5"]["requests"], 1)

    def test_the_span_of_the_conversation_is_remembered(self):
        self.write(turn(request_id="a", timestamp="2026-08-14T10:00:00Z"),
                   turn(request_id="b", timestamp="2026-08-14T11:30:00Z"))
        held = usage.scan_usage(self.path)
        self.assertEqual(held["firstAt"], "2026-08-14T10:00:00Z")
        self.assertEqual(held["lastAt"], "2026-08-14T11:30:00Z")


if __name__ == "__main__":
    unittest.main()


class SubagentSpend(unittest.TestCase):
    """A fanned-out session's real bill.

    Subagents have their own transcripts, so the session's file says nothing
    about what they cost. read_usage has to go and look.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Rebound on the transcript module, not on config: usage.py reads paths
        # through transcript.transcript_paths, and that function reads the name
        # transcript.py copied out of config at import time.
        self.original = transcript_module.PROJECT_DIR
        transcript_module.PROJECT_DIR = self.root
        self.slug = self.root / "-home-someone-work"
        self.slug.mkdir()
        self.session = "11111111-2222-3333-4444-555555555555"
        self.agents_dir = self.slug / self.session / "subagents"
        self.agents_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        transcript_module.PROJECT_DIR = self.original
        self.tmp.cleanup()

    def write(self, path: Path, *lines: str) -> None:
        """Transcript lines, newline-terminated.

        `turn()` returns a line without one — see how the other classes in this
        file write — and scan_usage remembers its progress against the path in a
        module global, so the entry goes when the file does.
        """
        path.write_text("".join(line + "\n" for line in lines))
        self.addCleanup(usage.USAGE_SCANS.pop, str(path), None)

    def test_a_subagents_spend_lands_in_the_agent_bucket(self):
        self.write(self.slug / f"{self.session}.jsonl",
                   turn(request_id="main-1", input_tokens=100, output_tokens=10))
        self.write(self.agents_dir / "agent-aaa.jsonl",
                   turn(request_id="side-1", sidechain=True, input_tokens=70, output_tokens=7))
        found = usage.read_usage(self.session, "/home/someone/work")
        self.assertEqual([row["model"] for row in found["models"]], ["claude-opus-5"])
        self.assertEqual(sum(row["input"] for row in found["agentModels"]), 70)
        self.assertEqual(found["totals"]["input"], 170)

    def test_two_subagents_are_added_together(self):
        self.write(self.slug / f"{self.session}.jsonl",
                   turn(request_id="main-1", input_tokens=100))
        self.write(self.agents_dir / "agent-aaa.jsonl",
                   turn(request_id="side-1", sidechain=True, input_tokens=70))
        self.write(self.agents_dir / "agent-bbb.jsonl",
                   turn(request_id="side-2", sidechain=True, input_tokens=30))
        found = usage.read_usage(self.session, "/home/someone/work")
        self.assertEqual(sum(row["input"] for row in found["agentModels"]), 100)

    def test_a_subagent_does_not_move_the_context_reading(self):
        # Context is what the *session* is carrying. A subagent's window is not
        # the session's, and reading one here would make the header lie.
        self.write(self.slug / f"{self.session}.jsonl",
                   turn(request_id="main-1", input_tokens=100))
        self.write(self.agents_dir / "agent-aaa.jsonl",
                   turn(request_id="side-1", sidechain=True, input_tokens=999_999))
        found = usage.read_usage(self.session, "/home/someone/work")
        self.assertEqual(found["context"], 100)

    def test_reading_twice_does_not_double_the_bill(self):
        # scan_usage keeps its progress in a module-level cache. Merging into
        # that dict rather than a copy of it would grow the total on every poll.
        self.write(self.slug / f"{self.session}.jsonl",
                   turn(request_id="main-1", input_tokens=100))
        self.write(self.agents_dir / "agent-aaa.jsonl",
                   turn(request_id="side-1", sidechain=True, input_tokens=70))
        first = usage.read_usage(self.session, "/home/someone/work")
        second = usage.read_usage(self.session, "/home/someone/work")
        self.assertEqual(first["totals"]["input"], second["totals"]["input"])
        self.assertEqual(second["totals"]["input"], 170)

    def test_a_session_with_no_subagents_is_unchanged(self):
        self.write(self.slug / f"{self.session}.jsonl",
                   turn(request_id="main-1", input_tokens=100))
        found = usage.read_usage(self.session, "/home/someone/work")
        self.assertEqual(found["agentModels"], [])
        self.assertEqual(found["totals"]["input"], 100)
