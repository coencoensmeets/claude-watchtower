---
name: watchtower-style
description: The user's house style for anything visual. Use whenever you create, design or restyle a website, web page, landing page, HTML report, dashboard, chart, graph, map, form, styleguide, prototype, web app UI, component, email template or any other HTML/CSS output — unless the user names a different style or design system for this piece of work. Gives the Watchtower look (Urbanist type, ink actions, pills and circles, mist/chalk surfaces, lime/ember/lilac accents) with a ready-made stylesheet.
---

# The Watchtower house style

Everything visual you make for this user uses the Watchtower style by default:
the look of their Watchtower panel, written down in `styleguide.html` next to
this file. Only depart from it when the user says so — a named brand, another
design system, "match the existing site", "make it look like X". When the
work is a change to an existing site that already has its own consistent
design, keep that site's design and mention that the house style is available.

This overrides any aesthetic direction you would otherwise choose (including
advice from a general frontend-design skill): the direction is already chosen.

## Start from the stylesheet

`watchtower.css` in this folder is the guide's tokens and components, ready to
use. For a new page, copy it into the project (e.g. `styles/watchtower.css`)
and link it, or inline it in a single-file page, plus the fonts:

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Urbanist:wght@300..800&family=Roboto+Mono:wght@400;500&display=swap" rel="stylesheet">
```

For a framework project (React, Vue, Tailwind…), carry the `--wt-*` tokens
across as CSS variables or theme config and follow the same rules. Read
`styleguide.html` when you need a component the stylesheet does not have —
it shows every part in use.

The kit files (`watchtower.css`, `watchtower-viz.js`,
`watchtower-nav.js`) are generated from the guide (`tools/style_kit.py` in
claude-watchtower), so they match it exactly. **Look up a component's markup
in `styleguide.html`** (search for its class) rather than guessing. The guide
shows every part in use, in light and dark.

What `watchtower.css` gives you:

- **Page**: `.wrap`, `.top` / `.brand` / `.toc`, `section` + `h2` + `.lede`,
  `.board`, `.grid .g2/.g3/.g4`, `.hero` + `.pill-word`, `.hatch`.
- **Actions**: `.btn` (`--rider` with `.ride`, `--soft`, `--line`, `--rose`,
  `--sm`), `.ib` icon buttons (`--ink`, `--lime`, `--line`, `--sm`, `--lg`),
  `.chips/.chip` with `.n`, `.seg`.
- **Forms**: `.field` + `label` + `.field__help` (`.field--error`), `.input`,
  `.select`, `.textarea`, `.input-wrap` (leading icon, trailing `.ib`),
  `.check`, `.radio`, `.switch`, `.range` (set `--v` to the percentage),
  `.stepper`, `.drop` zone.
- **Feedback**: `.alert--info/--success/--warning/--error`, `.toast`,
  `.badge`, `.tag`, `.dot-label` (set `data-s` or `--s`), `.progress`
  (`--v`, `--indeterminate`), `.ring-wrap > .ring`, `.spinner`, `.steps`,
  `.skeleton`, `.empty`, `.tip` tooltips.
- **Navigation**: `.side-nav` (+ `.side-nav__group`), `.crumbs`, `.pager`,
  `.accordion` (details/summary), `.menu` + `kbd`, `.avatars`, `.dialog` +
  `.scrim`.
- **Data**: `.table-wrap > .table` (`.num`, `aria-sort`,
  `aria-selected`), `.kv`, `.timeline`, `.cal`.
- **Cards**: `.card` (`--bite` + `.bite-wrap`), `.stat`, `.kpi`, `.meter`,
  `.segbar`, `.meter-list`, `.scale`.
- **Charts**: `.viz` cards, `.legend`, plus the `--viz-1…8`, `--seq-1…6`,
  `--div-*` and `--st-*` tokens.
- **Maps**: `.map` with `.water/.park/.block/.road/.route/.area` for SVG
  basemaps, the `.mk--pin/--dot/--price/--cluster/--me/--end` markers (place
  them with `--x`/`--y`), `.map-pop` (`--below` near the top edge),
  `.map-ctl--tl/tr/bl/br`, `.map-zoom`, `.map-search`, `.map-legend` and
  `.map-sheet` for phones. For live location: `.me-mk`, `.ib--follow`,
  `.ahead`/`.back` lines, `.turn-head` and the `.nav` banner.

## Charts

Copy `watchtower-viz.js` next to the page as well, and draw with it instead
of hand-rolling SVG:

```html
<figure class="viz" id="spend"></figure>
<script src="watchtower-viz.js"></script>
<script>
  WTViz.line(document.getElementById("spend"), {
    title: "Spend", sub: "Last 12 weeks", x: weeks, unit: "$", area: true,
    series: [{ name: "Spend", values: [22, 25, 31, 35, 41, 52] }] });
</script>
```

It provides `line` (with `area`), `columns` (`stacked`, `highlight`),
`ranked`, `scatter`, `heat` and `spark`, and each one comes with a legend, a
hover tooltip, keyboard focus and a "Show table" toggle. A single number is a
`.kpi` tile, not a chart.

When you use a chart library (Chart.js, ECharts, Recharts…) instead, set its
colours from the `--viz-*` tokens in order, use 2px lines and bars at most
24px thick with 4px rounded tips, draw hairline solid gridlines, keep one
y-axis, and turn off 3D, gradients and shadows. Data colours are not state
colours: use them only inside plots and maps. **No pies or donuts**; use a
`.segbar`, a `.meter-list` or a ranked bar instead.

## Maps

Keep the basemap quiet and put the meaning on top. With Leaflet or MapLibre,
use the keyless Esri grey canvas, or a style built from the `--map-*` tokens.
Don't use CARTO Positron or Dark Matter: they now need an API key, and
without one they still return 200 tiles that just read "API KEY REQUIRED".
A route is a 5px ink line over an 11px chalk casing. Areas take a
22% fill with a solid 2px edge (lilac for a chosen area, ember for an alert
zone), and you cluster with the library. Round the map to 32px and float the
controls inside it. On phones the popup becomes a `.map-sheet`.

The Esri canvas, in Leaflet:
- Base: `https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`
  (`World_Dark_Gray_Base` in dark). It carries the street names from about
  zoom 15.
- Place names: `World_Light_Gray_Reference` / `World_Dark_Gray_Reference`,
  on their own pane (`map.createPane("labels").style.zIndex = 350`, with
  `pointerEvents = "none"`). That puts them above the base and below the
  route.
- Set `maxNativeZoom: 16` on both layers. Credit Esri, HERE, Garmin and
  OpenStreetMap.
- On a theme toggle, swap both URLs with `setUrl`.

**Leaflet markers.** The `.mk` classes place themselves with `--x`/`--y` and
a `translate(-50%,-50%)`. That is for the SVG map, and it fights Leaflet's
own positioning. In Leaflet, use `L.divIcon` with a `className`,
`iconSize: [32, 32]` and `iconAnchor: [16, 16]`, and put the look on an
inner span. The kit's `.mk-leaflet` does this. Never give the icon `.mk`'s
position or transform.

**Leaflet's controls draw over the page.** `.leaflet-top` and
`.leaflet-bottom` sit at z-index 1000, above a `.map-sheet` or anything
else beside the map. Give the map's wrapper `position: relative; z-index: 0`.
On phones, move the attribution to the top left, because the sheet covers
the bottom.

## Live location

For a page that guides someone along a route. The look is in the guide
(Maps, "Live location and turn-by-turn"): the `.me-mk` dot with its heading
wedge, a lilac accuracy circle (16%, no stroke, at most 200 m), the
`.ib--follow` locate button, the ember stretch ahead with a `.turn-head`
arrow, and the `.nav` banner (plain, `--turn` ember, `--arrive` lime).

Copy `watchtower-nav.js` next to the page for the route geometry. It has no
dependencies. `WTNav.create(line, { loop })` gives `project`, `ahead`,
`turns`, `slice`, `along`, `bearingTo` and `headingAt`. `WTNav.icons`,
`iconKey(turn)` and `distText(m)` fill the banner.

Getting the position:
- Use `navigator.geolocation.watchPosition` with `enableHighAccuracy: true`,
  `maximumAge: 3000` and `timeout: 20000`.
- It only works in a secure context: https or localhost. Opened from
  `file://` it fails silently with a timeout. Check `window.isSecureContext`
  and say so plainly in the error.
- If `navigator.permissions.query({ name: "geolocation" })` says
  `"granted"`, show the dot on load without asking.
- Heading: use the GPS heading when `speed > 0.7` m/s. Otherwise use the
  compass. Listen to `deviceorientationabsolute` (heading = 360 − alpha)
  and `deviceorientation` (`webkitCompassHeading` on iPhone). iPhone needs
  `DeviceOrientationEvent.requestPermission()`, and only from a tap.
- `navigator.vibrate` works on Android, not on iPhone.
- Follow the user until they drag the map. Then drop the button's pressed
  state; a tap re-centres and follows again.

Guidance (what `watchtower-nav.js` does, and the wording):
- Snap each fix onto the route by projecting onto every segment. Where the
  route uses a street twice, pass the previous position so it keeps going
  the same way.
- A turn is a bend of 30° or more, measured 15 m before and 15 m after the
  vertex. Bends closer than 22 m are one turn.
- Words: "Bear" under 55°, "Turn" under 115°, "Sharp" under 155°, then
  "Turn around". Say "now" under 15 m. Round to 5 m under 100 m and to
  10 m above.
- Ember banner for a turn within about 45 m. Draw the route from you to
  just past the turn (about 25 m) in ember.
- Off route beyond max(35 m, 1.2 × accuracy): point back to the nearest
  point with a dotted ember line. Arrived within about 25–30 m of a stop.
- Without street names it can only say which way, not which street.

Testing: headless Chromium's built-in geolocation override timed out for
us. Stub `navigator.geolocation` with `page.addInitScript` instead: a fake
`watchPosition` that keeps the callback, which the test then drives. Serve
the page over localhost.

## The rules

**Colour means something.** Neutral surfaces carry the page; colour is only
ever a signal:
- **Ink** `#1E1F24` (chalk `#F2F1F6` in dark) — anything you can press, the
  selected item. Primary buttons are ink pills.
- **Lime** `#CBDA72` — done, success, ready, the one highlighted word. The
  circle riding inside a primary button.
- **Ember** `#F28C4E` — needs attention, urgent, the thing to act on now.
- **Lilac** `#A59FE2` — in progress, active, focus rings.
- **Butter** `#F5CD62` — changed, draft, warning. **Rose** `#E9839B` —
  destructive and errors, nothing else.
- Text on any of these colours is always ink, in both themes.
- Large quiet areas use the tints (`--wt-lime-tint` …), not the full colour.
- **One loud thing per screen**: the most important item gets the big colour
  block; everything around it stays neutral.

**Illustrations.** Decorative drawings, such as artwork pictures on a map,
sit on a night tile (`#1E1F24`, the same in both themes). Draw them in
chalk lines and colour them only with the house accents. Round the tile like
a card, with a radius of about 28%. Show state as a ring around the tile
(ember for next, lime for done, lilac for selected). Never recolour the
drawing itself.

**Surfaces.** Page (mist `#ECEBF1` / night `#141518`), then surface (chalk),
then surface-2. Three levels, no more. No borders between surfaces — the step
in tone is the edge. A 1.5px line only on outlined controls.

**Shape.** Controls are pills and circles; nothing clickable has square
corners. Containers round by size: 10 tags/code, 16 small media, 24 tiles and
callouts, 32 cards, 44 page-level boards. Nested shapes: inner radius = outer
radius − padding, so the gap stays even. The **rider** — a circle inside a
pill — for a control with one secondary thing (arrow in a button, count in a
chip). The **bite** (circle cut from a card's top-right with the action in it)
only on the single most important card.

**Type.** Urbanist for everything; Roboto Mono for code and anything typed
into a terminal. Display 56/1.0 600 −3%, headline 28/1.1 600, title 17 650,
body 15/1.55 400, label 13 600. Huge hero headlines (≈100px, −4.5%) with one
word in a lime pill. Sentence case everywhere. Tabular figures for numbers;
big numerals get a small lighter unit beside them and a plain label below.

**Space.** 4px base: 4, 8, 12, 16, 24, 32, 48, 72. 24px inside cards.

**Texture.** Diagonal hatching (`.hatch`) is the only texture and means
"nothing here": empty slots, the unused part of a meter, inactive items.

**Motion.** Only things that changed move. Colour changes 200ms
`cubic-bezier(.2,0,0,1)`; presses scale to .94–.97 and spring back; arrivals
decelerate. Respect `prefers-reduced-motion`.

**Don't:** gradients, drop shadows on cards (one `--wt-float` shadow only
for things floating over content), borders between surfaces, colour as
decoration, square clickable corners, Inter/Roboto/system-font look.

## Light and dark

Ship both. Default to the system preference and offer a circular `.ib`
toggle. Guard storage — previews in Watchtower run sandboxed and
`localStorage` throws there:

```html
<script>
  const root = document.documentElement;
  const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } },
                  set(k, v) { try { localStorage.setItem(k, v); } catch {} } };
  root.dataset.theme = store.get("theme")
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.getElementById("theme")?.addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    store.set("theme", root.dataset.theme);
  });
</script>
```

## Then show it

When the page is viewable, hand it to the user with the watchtower-preview
skill (`mcp__watchtower__show_preview`).
