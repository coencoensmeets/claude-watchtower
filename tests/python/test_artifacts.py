"""Artifacts a session published, filed under its Preview tab.

Claude Code's Artifact tool publishes a page the session wrote to disk as a
private claude.ai page. The call names the file; the result names the address.
These tests write that shape by hand and check what the panel files.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower import artifacts, previews  # noqa: E402

SESSION = "11111111-2222-3333-4444-555555555555"
URL = "https://claude.ai/code/artifact/0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"


def now_iso(offset: float = 0) -> str:
    return datetime.fromtimestamp(time.time() + offset, timezone.utc).isoformat().replace("+00:00", "Z")


def publish(tool_id: str, path: str, **extra) -> dict:
    return {"type": "assistant", "timestamp": now_iso(),
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tool_id, "name": "Artifact",
                 "input": {"file_path": path, "icon": "chart", **extra}}]}}


def answer(tool_id: str, text: str, error: bool = False, stamp: str | None = None) -> dict:
    return {"type": "user", "timestamp": stamp or now_iso(),
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "is_error": error,
                 "content": [{"type": "text", "text": text}]}]}}


class Artifacts(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cwd = root / "project"
        (self.cwd / "out").mkdir(parents=True)
        self.page = self.cwd / "out" / "report.html"
        self.page.write_text("<html><head><title>Quarterly  numbers</title></head></html>")
        self.transcript = root / f"{SESSION}.jsonl"
        self.transcript.write_text("")
        for patcher in (
            mock.patch.object(artifacts, "transcript_paths", lambda *_: [self.transcript]),
            mock.patch.object(artifacts, "SEEN_FILE", root / "seen.json"),
            mock.patch.object(previews, "PREVIEW_FILE", root / "previews.json"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(artifacts.forget, SESSION)

    def write(self, *entries: dict) -> None:
        with self.transcript.open("a") as handle:
            for item in entries:
                handle.write(json.dumps(item, separators=(",", ":")) + "\n")

    def filed(self) -> list[dict]:
        return previews.read_previews().get(SESSION) or []

    def test_a_publish_becomes_a_preview_with_its_address(self) -> None:
        self.write(publish("t1", str(self.page), description="The numbers"),
                   answer("t1", f"Published to {URL}"))
        self.assertEqual(artifacts.file_new(SESSION, str(self.cwd)), 1)
        [row] = self.filed()
        self.assertEqual(row["kind"], "file")
        self.assertEqual(row["path"], str(self.page.resolve()))
        self.assertEqual(row["artifactUrl"], URL)
        self.assertEqual(row["title"], "Quarterly numbers")
        self.assertEqual(row["description"], "The numbers")

    def test_filed_once_even_after_a_restart(self) -> None:
        self.write(publish("t1", str(self.page)), answer("t1", URL))
        artifacts.file_new(SESSION, str(self.cwd))
        artifacts.forget(SESSION)   # as a restarted panel would start
        self.assertEqual(artifacts.file_new(SESSION, str(self.cwd)), 0)
        self.assertEqual(len(self.filed()), 1)

    def test_reads_lists_and_failures_are_not_publishes(self) -> None:
        self.write(publish("t1", str(self.page), action="read", url=URL), answer("t1", URL),
                   publish("t2", str(self.page)), answer("t2", "refused", error=True),
                   publish("t3", str(self.page), asset=True, url=URL), answer("t3", URL))
        self.assertEqual(artifacts.file_new(SESSION, str(self.cwd)), 0)

    def test_an_update_keeps_its_address_when_the_result_does_not_repeat_it(self) -> None:
        self.write(publish("t1", str(self.page), url=URL), answer("t1", "Updated."))
        artifacts.file_new(SESSION, str(self.cwd))
        self.assertEqual(self.filed()[0]["artifactUrl"], URL)

    def test_a_page_the_panel_may_not_serve_is_the_address_alone(self) -> None:
        self.write(publish("t1", "/etc/hostname"), answer("t1", URL))
        artifacts.file_new(SESSION, str(self.cwd))
        [row] = self.filed()
        self.assertEqual(row["kind"], "url")
        self.assertEqual(row["url"], URL)

    def test_old_publishes_are_history(self) -> None:
        self.write(publish("t1", str(self.page)), answer("t1", URL, stamp=now_iso(-5 * 24 * 3600)))
        self.assertEqual(artifacts.file_new(SESSION, str(self.cwd)), 0)


if __name__ == "__main__":
    unittest.main()
