// Build web/ into dist/, which is what the panel serves.
//
// There is no bundler and no npm package here, and there does not need to be:
// Node strips TypeScript types itself, and browsers load ES modules natively.
// So the whole build is "strip the types, concatenate the stylesheets, copy the
// assets" — which keeps the project's promise that nothing has to be installed
// before it runs.
//
//   node tools/build.mjs           build once
//
// Normally you never run this by hand: server.py runs it when the sources are
// newer than the output. Deciding *whether* to build is entirely Python's job —
// see watchtower/build.py, which writes the stamp this build's output is judged
// by. Timestamps are the one thing the two runtimes must agree on, and they do
// not round them identically, so only one of them is allowed to record them.

import { stripTypeScriptTypes } from "node:module";
import { cp, mkdir, readdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const WEB = join(ROOT, "web");
const DIST = join(ROOT, "dist");
// Staging: a build is assembled here and moved to dist/ only when it is whole.
const WORK = join(ROOT, ".dist-build");

// The cascade is order-dependent, so the order is written down rather than left
// to whatever readdir happens to return. A stylesheet that is not on this list
// is an error, not something to quietly leave out of the page.
const STYLE_ORDER = [
  "tokens.css",       // every --md-sys-* value, and the typescale utilities
  "base.css",         // the reset, and the shell the panes sit in
  "list.css",         // filter chips, the session list, groups, picking rows
  "detail.css",       // the detail pane and its tab strip
  "question.css",     // the card for a question a session is standing at
  "mode.css",         // the chips that pick what a panel-run turn may do
  "ask.css",          // the sheet a turn run from here raises its prompt in
  "composer.css",
  "chat.css",
  "facts.css",        // the Details tab
  "plan.css",         // how much of the subscription has gone, in the app bar
  "update.css",       // a newer release of the panel, in the app bar beside it
  "usage.css",
  "git.css",          // Git and History
  "controls.css",     // buttons, switches, segmented buttons, the context menu
  "comments.css",     // the rail, its cards, and the quote chip
  "dialog.css",       // scrims, dialogs, the snackbar
  "responsive.css",   // last: it narrows what everything above laid out
];

async function* walk(dir) {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch {
    return;                                     // an absent directory is empty
  }
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(path);
    else yield path;
  }
}

async function styles() {
  const dir = join(WEB, "styles");
  const found = (await readdir(dir)).filter((name) => name.endsWith(".css")).sort();
  const missing = found.filter((name) => !STYLE_ORDER.includes(name));
  if (missing.length) {
    throw new Error(
      `web/styles/${missing.join(", ")} is not in STYLE_ORDER in tools/build.mjs.\n` +
      "Add it in the position the cascade needs — leaving it out would drop it from the page.");
  }
  const absent = STYLE_ORDER.filter((name) => !found.includes(name));
  if (absent.length) throw new Error(`STYLE_ORDER names ${absent.join(", ")}, which does not exist`);

  const parts = [];
  for (const name of STYLE_ORDER) {
    parts.push(`/* --- ${name} --- */\n`, await readFile(join(dir, name), "utf8"), "\n");
  }
  return parts.join("");
}

async function scripts(into) {
  const src = join(WEB, "src");
  let count = 0;
  for await (const path of walk(src)) {
    const rel = relative(src, path);
    if (path.endsWith(".ts")) {
      const code = await readFile(path, "utf8");
      // transform, not strip: strip mode rejects enum, namespace and
      // constructor parameter properties outright.
      const out = stripTypeScriptTypes(code, {
        mode: "transform",
        sourceMap: true,
        sourceUrl: `/src/${rel.replaceAll("\\", "/")}`,
      });
      const target = join(into, rel.replace(/\.ts$/, ".js"));
      await mkdir(dirname(target), { recursive: true });
      await writeFile(target, out);
      count += 1;
    } else {
      const target = join(into, rel);
      await mkdir(dirname(target), { recursive: true });
      await cp(path, target);
    }
  }
  return count;
}

/* Everything is written beside dist/ and moved into place only once it is all
   there. A build that fails halfway would otherwise leave a page with no script
   behind it — worse than the stale-but-working output it replaced, and it would
   happen on exactly the typo that made you rebuild. */
async function build() {
  await rm(WORK, { recursive: true, force: true });
  await mkdir(WORK, { recursive: true });

  await cp(join(WEB, "index.html"), join(WORK, "index.html"));
  await writeFile(join(WORK, "app.css"), await styles());
  const modules = await scripts(WORK);
  await cp(join(WEB, "assets"), WORK, { recursive: true });

  await rm(DIST, { recursive: true, force: true });
  await rename(WORK, DIST);
  return modules;
}

const started = Date.now();
let modules;
try {
  modules = await build();
} finally {
  await rm(WORK, { recursive: true, force: true });
}
console.error(`built ${modules} module${modules === 1 ? "" : "s"} into dist/ in ${Date.now() - started}ms`);
