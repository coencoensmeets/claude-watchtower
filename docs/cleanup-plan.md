# Cleanup plan

A staged refactor of claude-watchtower from two very large files into a Python
package and a TypeScript/CSS frontend, without giving up the thing that makes
the project pleasant to adopt: `git clone && python3 server.py` still starts a
working panel.

The plan below is the original one. What each phase actually settled — including
the places it was wrong — is recorded under Progress.

## Progress

Phases 0–3, 5 and 6 are done. Phases 4 and 7 are not started.

| Phase | State |
|---|---|
| 0 — safety net | done: 95 unit tests, `tests/fixtures.py`, service file fixed |
| 1 — build pipeline | done: `web/` → `dist/`, Node type stripping, no packages |
| 2 — CSS split | done: 16 stylesheets, cascade order preserved exactly |
| 3 — TypeScript modules | done: main.ts 4,972 → 2,055 lines across 19 modules |
| 4 — types | not started |
| 5 — Python package | done: server.py 4,096 → 89 lines across 16 modules |
| 6 — route table | done: 19 routes in a table, with tests over it |
| 7 — docs split | not started |

What phase 3 settled, which the original plan only guessed at:

- **Shared mutable state was the whole problem.** 40 of 63 top-level `let`
  bindings were written from more than one place, and a module cannot assign to
  an imported binding. They live in `state.ts` as fields on six objects. The
  test for whether a binding belongs there is simply whether a second module
  writes it — most do not, and stay with their own code.
- **The call graph was a web, not a layer.** Every subsystem ended by calling
  `render()`, so extracting any of them created an import cycle. `refresh.ts`
  inverts it: main.ts hands its render loop over at boot and everything else
  asks for a redraw without knowing where the loop lives.
- **Text-level moves need a real scanner.** This file is mostly template
  literals full of HTML, so counting brackets or matching identifiers in the raw
  text goes wrong in ways that still compile.

What phase 5 settled:

- **The Python half was in far better shape than this plan assumed.** No
  lower-case module-level names at all, ninety constants, and only two names
  rebound while the panel runs. Those two — `SAY_ENABLED` and `PLAN_RUNNING` —
  are the only ones that cannot be imported by name, and are read as
  `config.SAY_ENABLED` so the lookup happens when it is asked for.
- **A path anchored to `__file__` is the thing to check when moving a module.**
  `STATIC_DIR` was the repository root while it lived in server.py and became
  `watchtower/dist` the moment it moved into the package. Every static file
  404'd while the API answered perfectly — a blank page with a working back end.
- **ruff checks names within a file, not import targets.** `from
  watchtower.config import TRANSCRIPT_LIMIT_MAX` passed every check and took the
  panel down on startup. `tests/python/test_package.py` closes that gap: it
  imports every module and reads every `from watchtower.x import y` statically.

What is left in `main.ts` is the orchestrator — polling, the index, the detail
pane's tab dispatch, the settings and folder dialogs, boot — plus two sections
whose banner comments no longer match how the code is grouped. Splitting those
further needs the seams redrawn, not more moving.

## Where we were

| | lines | shape |
|---|---|---|
| `server.py` | 4,225 | 115 top-level defs/classes, ~20 banner-comment sections, 86 module-level names |
| `static/index.html` | 6,538 | ~1,480 CSS + ~137 markup + ~4,900 JS (191 functions) in one module scope |
| `tests/ui-check.mjs` | 1 file | needs a hand-started Chrome and fixture directory |
| `README.md` | 538 lines | doing the work of a `docs/` tree |

The Python half is the healthier one — the banner sections are real seams and
most functions are small. The frontend is one 4,900-line module scope with no
imports, so nothing can be reasoned about in isolation and nothing can be
extended from outside. That is the half to fix first.

## The build, and what it costs

TypeScript needs a build. The constraint is that `python3 server.py` must still
be the only command anyone types.

**Node 24 strips TypeScript types in-process, with no npm packages at all:**

```js
require("node:module").stripTypeScriptTypes(src, { mode: "transform" })
```

So the build is: Node reads `web/src/**/*.ts`, strips types, writes `.js` next
to a concatenated stylesheet into `dist/`. No bundler, no `package.json`, no
`node_modules`, no network. Browsers load the output as native ES modules, which
is what `index.html` already uses.

`server.py` runs that build on startup when the sources are newer than the
output, then serves. A `--no-build` flag skips it; `--build` runs it and exits.

**What this costs:** Node becomes a requirement for running from a clone. Today
it is only needed for the UI test. This is the one real trade in the plan.

**Recommendation: accept it, and do not commit `dist/`.** The alternative —
committing build output so Node stays optional — puts generated files in a tree
that several sessions edit concurrently, and every one of them becomes a merge
conflict. Node is a single distro package; a stale committed `dist/` is a
permanent tax. If a released tarball needs to be Node-free later, build `dist/`
at release time and ship it in the tarball only.

### Getting Node, for people who can't install it system-wide

The build needs **node**, not **npm** — type stripping is a core Node API. So a
locked-down machine, or Windows, only needs a Node binary from somewhere.

`watchtower/build.py` resolves the interpreter through a search order rather
than assuming `PATH`:

1. `$WATCHTOWER_NODE`, if set
2. a project virtualenv — `.venv/bin/node`, then the `nodejs_wheel` package's
   bundled binary if importable
3. `node` on `PATH`

That makes a venv install a documented fallback without making it the default:

```
python3 -m venv .venv && .venv/bin/pip install nodejs-wheel-binaries
```

`nodejs-wheel-binaries` is currently Node 24.16.0 and ships wheels for linux
x86_64/aarch64, musl, macOS and win_amd64 — new enough for the stripper. It is
an unofficial repackage, ~63 MB. `nodeenv` is the alternative: 0.1 MB of pure
Python that downloads the *official* tarballs into the venv, at the cost of
needing network when you install rather than when you download the wheel.

**Do not bootstrap this automatically.** A 63 MB download kicked off silently by
`python3 server.py` is a surprise, needs network, and needs consent. When Node
is missing the build should fail with the two commands above printed, and
nothing else.

**Type checking is separate from the build.** Stripping does not check types.
`npx tsc --noEmit` (the one optional npm dependency, dev-time only) is the
checker, run in CI and by hand. The build never depends on it.

Two rules follow from using the stripper rather than a compiler:

- Import specifiers are **not** rewritten. Source must import with the output
  extension — `import { render } from "./views/list.js"` — even in `.ts` files.
  This is the standard NodeNext convention and `tsc --noEmit` accepts it.
- Use `mode: "transform"` with source maps, not `mode: "strip"`. Strip mode
  rejects `enum`, `namespace`, and constructor parameter properties.

## Target layout

```
server.py                     entry point only: args, build, serve
watchtower/                   stdlib-only package
  config.py                   paths, tunables, runtime flags — replaces the loose globals
  proc.py                     /proc reads, pids, ancestry, tty
  sessions.py                 session files, child sessions, status inference
  store.py                    SessionStore, sampling thread, history
  windows.py                  WindowIndex, xdotool
  input.py                    say_to_session, answer_question, tmux
  transcript.py               reverse_lines, read_transcript, block summaries
  usage.py                    scan_usage, cost
  plan.py                     read_plan
  catalog.py                  skills, commands, Claude Code plugins
  git/read.py write.py message.py
  http/handler.py routes.py static.py
  build.py                    invokes Node; no-ops when the output is fresh
web/                          frontend source
  index.html                  shell only
  styles/                     tokens.css base.css list.css detail.css chat.css
                              git.css usage.css dialogs.css
  src/
    main.ts                   boot and wiring
    api.ts state.ts types.ts
    ui/                       icons.ts markdown.ts format.ts dom.ts
    views/                    list.ts detail.ts chat.ts git.ts history.ts usage.ts about.ts
    features/                 comments.ts composer.ts questions.ts settings.ts theme.ts
  assets/                     fonts, svg logos, vendor/material-color-utilities.js
dist/                         build output — gitignored, served by the panel
tests/
  python/                     stdlib unittest
  ui-check.mjs
docs/
```

`server.py` stays at the repo root because `claude-watchtower.service` points at
it. (Separately: that unit file still references the pre-rename
`claude-busy-ui` path and is broken as written — worth fixing in phase 0.)

## Phases

Each phase ends with the panel running and `tests/ui-check.mjs` passing. Any
phase can be the stopping point.

### Phase 0 — safety net

No moves yet. This is what makes the rest survivable.

- Land or park the in-flight branches; the working tree is dirty and at least
  two other sessions are editing both large files. **Serialise this work** — the
  refactor phases conflict with everything.
- Add `tests/python/` with stdlib `unittest` over the pure functions that
  currently have zero coverage: `parse_status`, `parse_log`, `parse_plan`,
  `effective_status`, `status_age`, `answer_keys`, `scan_usage`, `clean_message`.
  These are the characterisation tests every later phase is checked against.
- Record a baseline: `ui-check.mjs` output, and a saved `/api/state` +
  `/api/transcript` response from the fixture directory to diff against later.
- Fix the stale service-file path.

### Phase 1 — build pipeline, no restructuring

- `watchtower/build.py` + the Node build script.
- `web/index.html` = the current markup shell; `web/styles/app.css` = the
  current `<style>` verbatim; `web/src/main.ts` = the current `<script>`
  verbatim, renamed to `.ts` with no types added.
- `server.py` builds on startup when stale, serves `dist/`.
- **Gate:** the UI is byte-for-byte equivalent in behaviour. `ui-check.mjs`
  passes unchanged. This phase moves no logic, so any failure is the pipeline.

### Phase 2 — CSS split

Cut `app.css` along the existing comment sections into `styles/*.css`. The build
concatenates in a fixed order, so cascade order is preserved exactly. Pure
motion, no rule edits.

### Phase 3 — TypeScript module split

One module per commit, smallest first, `ui-check.mjs` after each:

`icons.ts` → `format.ts` → `markdown.ts` → `api.ts` → `state.ts` → `dom.ts` →
views → features → `main.ts` becomes wiring only.

Two functions get broken up as they move, because they are the ones that resist
review: `renderDetail` (280 lines) splits into header / tab bar / panel dispatch
/ event wiring, and `renderMarkdown` (148) into its block and inline passes.

Shared mutable state that today is module-scope (`menuFor`, `renamingId`,
`askPicks`, `sayDrafts`, `mutedSessions`, …) moves into `state.ts` behind
accessors. This is the step that actually buys the isolation, and the one most
likely to surface a hidden ordering dependency — hence one module at a time.

### Phase 4 — types

- `types.ts` describing the server's JSON payloads: `Session`, `TranscriptBlock`,
  `GitState`, `Usage`, `Question`, `Plan`. Hand-written from the server, which
  also documents the wire format for the first time.
- `tsconfig.json` for `tsc --noEmit` only. Start at `strict: false` with
  `noImplicitAny` off, then ratchet one flag at a time so the diff stays small.
- CI runs the type check plus both test suites.

### Phase 5 — Python package split

Independent of phases 1–4; can run in parallel if a different person owns it.

`config.py` **first** — the 86 module-level names are the reason a naive split
turns into circular imports. Separate the true constants from the mutable
runtime state (`SAY_ENABLED`, the tmux/pane caches, `STORE`, `WINDOWS`) and give
the mutable ones a single home. Then move one banner section per commit, in
dependency order: `proc` → `sessions` → `windows` → `git/*` → `transcript` →
`usage` → `plan` → `catalog` → `input` → `store` → `http/*`.

`server.py` ends as roughly forty lines: parse args, build, start the store
thread, serve.

### Phase 6 — route table

`Handler` is 373 lines of `if path == …`. Replace with a `{(method, path):
handler}` table in `routes.py`, keeping the existing behaviour that unknown
paths 404 and that core routes are matched before anything else. This is also
the hook the plugin API needs.

### Phase 7 — docs

Split the 66 KB README into `docs/` (usage, options, API, design, contributing),
leaving a short README that points into it. Plugin authoring gets its own page
when that work lands.

## Order and risk

Phases 0 → 1 → 2 → 3 are the critical path; 5 can run alongside from the start.
6 depends on 5, 7 on nothing.

The largest risk is not any single phase — it is doing this while other sessions
edit `server.py` and `static/index.html`. Every phase is a whole-file move. Pick
one owner, take a lock on both files for the duration, and land each phase as
its own small merge rather than one large branch.

The second risk is silent frontend breakage: the UI check covers tokens,
contrast, panes, the conversation view, dialogs and touch targets, but not every
interaction. Phase 0's saved API responses and a manual pass over the composer,
the question card, the git tab and the comment rail are the backstop, and phase
3's one-module-at-a-time rule keeps each suspect small.
