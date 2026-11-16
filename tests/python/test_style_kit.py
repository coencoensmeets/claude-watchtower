"""The watchtower-style skill's kit is cut from the styleguide, never edited.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import style_kit  # noqa: E402


class Kit(unittest.TestCase):
    def test_the_skill_kit_matches_the_guide(self):
        css, js = style_kit.build(style_kit.GUIDE.read_text())
        self.assertEqual(style_kit.CSS_OUT.read_text(), css,
                         "watchtower.css is stale: run python3 tools/style_kit.py")
        self.assertEqual(style_kit.JS_OUT.read_text(), js,
                         "watchtower-viz.js is stale: run python3 tools/style_kit.py")
        self.assertEqual(style_kit.NAV_OUT.read_text(), style_kit.build_nav(style_kit.GUIDE.read_text()),
                         "watchtower-nav.js is stale: run python3 tools/style_kit.py")

    def test_the_kit_carries_every_family(self):
        css, js = style_kit.build(style_kit.GUIDE.read_text())
        for needle in ("--wt-lime", "--viz-8", "--seq-6", "--map-water", ".btn", ".field", ".alert",
                       ".side-nav", ".table", ".viz", ".kpi", ".mk--pin", ".map-pop", '[data-theme="dark"]'):
            self.assertIn(needle, css)
        for fn in ("line", "columns", "ranked", "scatter", "heat", "spark"):
            self.assertIn(f"function {fn}(", js)
        nav = style_kit.build_nav(style_kit.GUIDE.read_text())
        for fn in ("create", "project", "ahead", "slice"):
            self.assertIn(f"function {fn}(", nav)
        for needle in (".me-mk", ".nav--turn", ".turn-head", ".ib--follow"):
            self.assertIn(needle, css)

    def test_a_missing_cut_mark_is_an_error_not_a_shorter_file(self):
        with self.assertRaises(SystemExit):
            style_kit.build(style_kit.GUIDE.read_text().replace("KIT START", "KIT BEGIN"))


if __name__ == "__main__":
    unittest.main()
