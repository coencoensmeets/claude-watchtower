#!/usr/bin/env python3
"""Rebuild the watchtower-style skill's kit from the styleguide.

docs/design/styleguide.html is the one source of truth. The skill ships two
files cut out of it, so that a page Claude builds starts from the same code
the guide is drawn with:

  skills/watchtower-style/watchtower.css    tokens, page, components, kit
  skills/watchtower-style/watchtower-viz.js the chart helpers (WTViz)
  skills/watchtower-style/watchtower-nav.js turn-by-turn geometry (WTNav)

    python3 tools/style_kit.py           write them
    python3 tools/style_kit.py --check   exit 1 if they are out of date

The cuts are made at comments in the guide, and a missing one is an error
rather than a quietly shorter file.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "design" / "styleguide.html"
SKILL = ROOT / "skills" / "watchtower-style"
CSS_OUT = SKILL / "watchtower.css"
JS_OUT = SKILL / "watchtower-viz.js"
NAV_OUT = SKILL / "watchtower-nav.js"

CSS_HEAD = """/* Watchtower house style: a starter stylesheet for new pages and sites.

   GENERATED from docs/design/styleguide.html by tools/style_kit.py. Do not
   edit by hand; change the guide and run the tool. The full guide sits
   beside this file as styleguide.html.

   Needs, in <head>:
     <link rel="preconnect" href="https://fonts.googleapis.com">
     <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
     <link href="https://fonts.googleapis.com/css2?family=Urbanist:wght@300..800&family=Roboto+Mono:wght@400;500&display=swap" rel="stylesheet">

   Dark theme: data-theme="dark" on <html> (see SKILL.md for the toggle).
*/

"""

JS_HEAD = """/* Watchtower charts: GENERATED from docs/design/styleguide.html by
   tools/style_kit.py. Load after watchtower.css; see SKILL.md. */

"""


def _between(text: str, start: str, end: str, *, keep_end: bool = False) -> str:
    a = text.find(start)
    if a < 0:
        raise SystemExit(f"style_kit: the guide has no {start!r}")
    b = text.find(end, a + len(start))
    if b < 0:
        raise SystemExit(f"style_kit: the guide has no {end!r} after {start!r}")
    return text[a:b + (len(end) if keep_end else 0)]


def build(guide: str) -> tuple[str, str]:
    style = _between(guide, "<style>", "</style>")[len("<style>"):]
    parts = [
        # Tokens, the page, the top bar, sections, boards and the primitives
        # (hatch, icon buttons, buttons, chips, segmented control).
        _between(style, "/* =========================================================================\n   Tokens",
                 "/* Session avatar"),
        # State card with the bite, tags, stat tiles, meters.
        _between(style, "/* State card", "/* Conversation"),
        # The hero headline with one word in a lime pill.
        _between(style, ".hero {", ".hero-stack"),
        # Forms, feedback, navigation, tables, charts, maps.
        _between(style, "/* =========================================================================\n   KIT START",
                 "KIT END\n   ========================================================================= */", keep_end=True),
    ]
    css = CSS_HEAD + "\n".join(p.rstrip() + "\n" for p in parts) + """
@media (max-width: 980px) {
  .hero .wrap { grid-template-columns: 1fr; }
  .g4 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .g3 { grid-template-columns: 1fr; }
  .brand { font-size: 0; gap: 0; }
  .top .wrap { gap: var(--wt-s2); }
}
@media (max-width: 640px) {
  .toc-group .long { display: none; }
  .toc-group .short { display: inline; }
  .toc-group > summary { padding: 0 10px; font-size: 13px; height: 36px; }
  .toc-group > summary svg { display: none; }
  .toc { gap: 2px; padding: 3px; }
  .top .ib { width: 40px; height: 40px; }
  .toc-group:nth-child(-n+2) .toc-menu { right: auto; left: 0; }
  .g2, .g4 { grid-template-columns: 1fr; }
  section > .wrap > h2 { font-size: 34px; }
  .board { padding: var(--wt-s4); border-radius: var(--wt-r-lg); }
  .map-pop { width: 220px; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
"""
    js = JS_HEAD + _between(guide, "/* =========================================================================\n   VIZ START",
                            "/* VIZ END */", keep_end=True) + "\n"
    # Nothing that only makes sense inside the guide may leak into the kit.
    leaked = re.findall(r"\.(?:av|row|convo|bubble|panel|sw|type-row|radii|dd)\b[ .{:]", css)
    if leaked:
        raise SystemExit(f"style_kit: guide-only classes in the kit: {sorted(set(leaked))}")
    return css, js


NAV_HEAD = """/* Watchtower turn-by-turn: GENERATED from docs/design/styleguide.html by
   tools/style_kit.py. No dependencies; see SKILL.md, "Live location". */

"""


def build_nav(guide: str) -> str:
    return NAV_HEAD + _between(guide, "/* =========================================================================\n   NAV START",
                               "/* NAV END */", keep_end=True) + "\n"


def main() -> int:
    guide = GUIDE.read_text()
    css, js = build(guide)
    nav = build_nav(guide)
    if "--check" in sys.argv:
        stale = [p.name for p, want in ((CSS_OUT, css), (JS_OUT, js), (NAV_OUT, nav))
                 if not p.exists() or p.read_text() != want]
        if stale:
            print(f"style_kit: out of date: {', '.join(stale)}. Run python3 tools/style_kit.py")
            return 1
        print("style_kit: up to date")
        return 0
    CSS_OUT.write_text(css)
    JS_OUT.write_text(js)
    NAV_OUT.write_text(nav)
    print(f"style_kit: wrote {CSS_OUT.relative_to(ROOT)} ({len(css):,} bytes), "
          f"{JS_OUT.relative_to(ROOT)} ({len(js):,} bytes), "
          f"{NAV_OUT.relative_to(ROOT)} ({len(nav):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
