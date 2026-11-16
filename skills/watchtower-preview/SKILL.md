---
name: watchtower-preview
description: Use whenever you create or change something the user will want to look at in a browser — an HTML page, a styleguide or design system page, a component gallery, a report or dashboard rendered to HTML, a static site, a prototype, or a dev server you started (Vite, Next, etc.). Hands it to the Watchtower panel with mcp__watchtower__show_preview so the user can open it from the panel on this computer or their phone.
---

# Showing what you made in Watchtower

The user watches their sessions in the Watchtower panel, often from a phone.
A page you wrote to disk is invisible there unless you hand it over. So every
time you build something viewable, finish with `mcp__watchtower__show_preview`.

## When

- You wrote or changed an `.html` file meant to be looked at: a styleguide,
  a mockup, a report, a chart page, docs, a landing page.
- You built a static site (`dist/`, `build/`, `out/`, `_site/`).
- You started a dev server and it is listening.
- The user asks to "see", "open", "preview" or "show" something you made.

Not for: source files the user did not ask to view, test fixtures, or
anything that only makes sense as code.

## How

1. Write the page first. Keep its stylesheets, scripts and images next to it
   (or in folders below it) and link them with **relative** paths
   (`styles.css`, `./assets/logo.svg`) — the panel serves the page's own
   folder, and a path starting with `/` points outside it.
2. Put a single page in a folder of its own rather than straight in the home
   folder or `/tmp`; the panel refuses to serve those whole.
3. Call `show_preview` with:
   - `target`: the absolute path to the `.html` file, a folder containing an
     `index.html`, or the dev server's URL (`http://localhost:5173`);
   - `title`: a short human name — "Styleguide", "Q3 report", "Landing page".
4. Tell the user it is in the panel's **Preview** tab for this session, and
   give the `url` the tool returned for this computer.

After changing the page again, call `show_preview` once more with the same
target: it moves to the top and the open preview reloads to the new version.

## Things to know

- Pages run sandboxed: scripts work, but `localStorage`, cookies and
  `fetch` to the panel do not. Wrap `localStorage` use in try/catch so a
  theme toggle does not break the page.
- A dev server must listen on all interfaces (`--host`, `0.0.0.0`) for the
  phone to reach it; on this computer `localhost` is fine.
- A page opened from disk (`file://`) can't `fetch()` local JSON. Ship the
  data as a `.js` file that sets a global (`window.DATA = {…}`) and load it
  with a `<script>` tag.
- Browsers only give geolocation to secure pages. To test a "find me"
  button, serve the page over https or localhost
  (`python3 -m http.server`), not from `file://`.
- If the `mcp__watchtower__*` tools are missing, say the user can turn on
  **Watchtower tools** in the panel under Settings → Skills, and give
  them the file path instead.
