/* Watchtower turn-by-turn: GENERATED from docs/design/styleguide.html by
   tools/style_kit.py. No dependencies; see SKILL.md, "Live location". */

/* =========================================================================
   NAV START: watchtower-nav, turn-by-turn along a fixed route.
   tools/style_kit.py copies this block into the watchtower-style skill.

   Pure geometry, no map or DOM. Positions are [lat, lng], distances metres.
   const nav = WTNav.create(line, { loop })   line: the route as [[lat, lng], ...]
   nav.project(latlng, prevS)  -> { s, off, snapped, heading }  snap a fix onto the route
   nav.ahead(s, horizon)       -> { turn, d } | null            next turn and how far
   nav.turns                   -> [{ s, angle, kind: { side, word, text }, at, inBearing, outBearing }]
   nav.slice(s1, s2)           -> [[lat, lng], ...]             a piece of the route
   nav.along(a, b), nav.bearingTo(from, to), nav.headingAt(s)
   WTNav.icons, WTNav.iconKey(turn), WTNav.distText(m)          banner helpers
   Without street names it can say which way to turn, not which street.
   A flat-earth projection around the first point is fine for a town.
   ========================================================================= */
window.WTNav = (() => {
  "use strict";

  const WINDOW = 15;   // metres either side of a bend used to measure the turn
  const MIN_TURN = 30; // degrees; smaller bends are just the street curving
  const MERGE = 22;    // metres; bends closer than this count as one turn

  function create(line, { loop = false } = {}) {
    const lat0 = line[0][0], lng0 = line[0][1];
    const kx = 111320 * Math.cos((lat0 * Math.PI) / 180), ky = 110540;
    const xy = ([lat, lng]) => [(lng - lng0) * kx, (lat - lat0) * ky];
    const unxy = ([x, y]) => [lat0 + y / ky, lng0 + x / kx];

    const P = line.map(xy);
    const cum = [0];
    for (let i = 1; i < P.length; i++) cum.push(cum[i - 1] + Math.hypot(P[i][0] - P[i - 1][0], P[i][1] - P[i - 1][1]));
    const total = cum[cum.length - 1];
    const wrap = (s) => (loop ? ((s % total) + total) % total : Math.min(Math.max(s, 0), total));

    // Point (planar) at distance s along the line.
    function at(s) {
      s = wrap(s);
      let lo = 0, hi = cum.length - 1;
      while (hi - lo > 1) { const m = (lo + hi) >> 1; cum[m] <= s ? (lo = m) : (hi = m); }
      const seg = cum[hi] - cum[lo] || 1, t = (s - cum[lo]) / seg;
      return [P[lo][0] + (P[hi][0] - P[lo][0]) * t, P[lo][1] + (P[hi][1] - P[lo][1]) * t];
    }
    const bearing = (a, b) => ((Math.atan2(b[0] - a[0], b[1] - a[1]) * 180) / Math.PI + 360) % 360;
    const diff = (a, b) => ((b - a + 540) % 360) - 180; // signed, -180..180, + = right
    const headingAt = (s) => bearing(at(s - 3), at(s + 3));

    // Turns: compare the heading 15 m before and 15 m after each vertex.
    const cand = [];
    for (let i = 1; i < P.length - 1; i++) {
      const s = cum[i];
      if (!loop && (s < WINDOW || s > total - WINDOW)) continue;
      const angle = diff(bearing(at(s - WINDOW), P[i]), bearing(P[i], at(s + WINDOW)));
      if (Math.abs(angle) >= MIN_TURN) cand.push({ s, angle });
    }
    const turns = [];
    for (const c of cand) {
      const last = turns[turns.length - 1];
      if (last && c.s - last.s < MERGE && Math.sign(c.angle) === Math.sign(last.angle)) {
        // One corner drawn with two vertices: measure it across both.
        const s = (last.s + c.s) / 2;
        last.s = s;
        last.angle = diff(bearing(at(s - WINDOW - 6), at(s - 6)), bearing(at(s + 6), at(s + WINDOW + 6)));
      } else if (last && c.s - last.s < MERGE && Math.abs(c.angle) > Math.abs(last.angle)) {
        Object.assign(last, c);
      } else if (!(last && c.s - last.s < MERGE)) {
        turns.push({ ...c });
      }
    }
    for (const t of turns) {
      t.kind = kind(t.angle);
      t.at = unxy(at(t.s));
      t.inBearing = headingAt(t.s - WINDOW);
      t.outBearing = headingAt(t.s + WINDOW);
    }

    function kind(a) {
      const side = a > 0 ? "right" : "left", m = Math.abs(a);
      if (m >= 155) return { side, word: "uturn", text: "Turn around" };
      if (m >= 115) return { side, word: "sharp", text: `Sharp ${side}` };
      if (m >= 55) return { side, word: "turn", text: `Turn ${side}` };
      return { side, word: "slight", text: `Bear ${side}` };
    }

    // Snapping: project onto every segment and take the nearest. With an
    // earlier reading, prefer a near-equal one that moves you forward a
    // little, so a street the route uses twice keeps its continuity.
    function project(latlng, prevS = null) {
      const p = xy(latlng);
      const hits = [];
      for (let i = 1; i < P.length; i++) {
        const a = P[i - 1], b = P[i];
        const dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy || 1;
        const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2));
        const q = [a[0] + dx * t, a[1] + dy * t];
        hits.push({ d: Math.hypot(p[0] - q[0], p[1] - q[1]), s: cum[i - 1] + (cum[i] - cum[i - 1]) * t, q });
      }
      hits.sort((x, y) => x.d - y.d);
      let best = hits[0];
      if (prevS != null) {
        const fwd = (s) => (loop ? wrap(s - prevS) : s - prevS);
        const near = hits.filter((h) => h.d <= best.d + 12);
        const ok = near.filter((h) => { const f = fwd(h.s); return f > -25 && f < 250; });
        if (ok.length) best = ok.reduce((a, b) => (Math.abs(fwd(a.s)) <= Math.abs(fwd(b.s)) ? a : b));
      }
      return { s: best.s, off: best.d, snapped: unxy(best.q), heading: headingAt(best.s) };
    }

    // Next turn ahead of s (within `horizon` metres), and the distance to it.
    function ahead(s, horizon = 400) {
      for (const t of turns) {
        const d = loop ? wrap(t.s - s) : t.s - s;
        if (d > 2 && d <= horizon) return { turn: t, d };
      }
      if (loop && turns.length) {
        const t = turns.reduce((a, b) => (wrap(a.s - s) < wrap(b.s - s) ? a : b));
        const d = wrap(t.s - s);
        if (d > 2 && d <= horizon) return { turn: t, d };
      }
      return null;
    }

    // Distance along the route from a to b, walking forward.
    const along = (a, b) => (loop ? wrap(b - a) : b - a);

    // A piece of the route as [lat, lng] points, from s1 to s2 (forward).
    function slice(s1, s2) {
      const out = [unxy(at(s1))];
      const len = along(s1, s2);
      for (let i = 0; i < cum.length; i++) {
        const k = along(s1, cum[i]);
        if (k > 0 && k < len) out.push({ k, p: line[i] });
      }
      const pts = out.slice(1).sort((a, b) => a.k - b.k).map((o) => o.p);
      return [out[0], ...pts, unxy(at(s2))];
    }

    return { total, turns, project, ahead, along, slice, bearingTo: (from, to) => bearing(xy(from), xy(to)), headingAt, diff };
  }

  // 24px stroke paths for the banner icon (stroke-width 2.4, round caps).
  const icons = {
    left: "M15 19V11a3 3 0 0 0-3-3H6M9 4 5 8l4 4", right: "M9 19V11a3 3 0 0 1 3-3h6M15 4l4 4-4 4",
    slightleft: "M15 20v-6L8 7M8 13V7h6", slightright: "M9 20v-6l7-7M16 13V7h-6",
    sharpleft: "M15 4v8l-8 6M7 12v6h6", sharpright: "M9 4v8l8 6M17 12v6h-6",
    uturnleft: "M16 20V9a4 4 0 0 0-8 0v6M5 12l3 3 3-3", uturnright: "M8 20V9a4 4 0 0 1 8 0v6M13 12l3 3 3-3",
    straight: "M12 20V5M7 10l5-5 5 5", arrive: "m5 12.5 4.5 4.5L19 7.5",
  };
  const iconKey = (t) => (t.kind.word === "turn" ? t.kind.side : t.kind.word + t.kind.side);
  // "now" under 15 m; 5 m steps under 100 m, 10 m steps above.
  const distText = (m) => (m < 15 ? "now" : `in ${m < 100 ? Math.round(m / 5) * 5 : Math.round(m / 10) * 10} m`);

  return { create, icons, iconKey, distText };
})();
/* NAV END */
