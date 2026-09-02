// Checks for the change a chat message carries — the folded patch, and the two
// panes it opens into — run without a browser.
//
// Same trick as composer-check.mjs and for the same reason: the panel is one
// HTML file with the script inline, so this lifts the two functions that draw a
// change out of the page, hands them a tool call in each state, and asserts what
// they draw. The browser suite needs a Chrome and Node 24; this needs neither.
//
//   node tests/chat-check.mjs
//
// A failure prints the case and exits 1.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

/* Read as JavaScript, not as TypeScript. The sources carry type annotations
   now, and everything below lifts a function out by matching text and then
   evaluates it — so the annotations have to come off first or the lift is not
   valid JS. Node's own stripper, the same one tools/build.mjs uses, in `strip`
   mode: it blanks the types in place rather than reformatting, so every offset
   this file matches on still lines up with the source it came from. */
const asJs = (text) => stripTypeScriptTypes(text, { mode: "strip" });

/* The panel is a package of modules now, and a function can be lifted out of
   whichever one holds it — so what is read is all of them, joined. Importing
   them instead would need a DOM: half of them touch `document` as they load. */
function sources(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return name.endsWith(".ts") ? [asJs(readFileSync(path, "utf8"))] : [];
  });
}
const page = sources(join(here, "..", "web", "src")).join("\n");

const unexport = (text) => text.replace(/(^|\n)export /g, "$1");

function lift(name) {
  const start = page.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`${name} is not in the page any more`);
  let depth = 0;
  for (let at = page.indexOf("{", start); at < page.length; at++) {
    if (page[at] === "{") depth++;
    else if (page[at] === "}" && --depth === 0) return unexport(page.slice(start, at + 1));
  }
  throw new Error(`${name} does not close`);
}

const { changeBlock, changePanel, diffBody, diffRows, sideBySide, full, busy, show } = new Function(`
  const escapeHtml = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const ICON = { back: "<svg/>" };
  const changeFull = new Map();
  const changeBusy = new Set();
  /* What the modules read out of state.ts, stubbed with the same shape. */
  const chat = { changeShown: null, transcript: null };
  ${lift("diffBody")}
  ${lift("diffRows")}
  ${lift("sideBySide")}
  ${lift("changeBlock")}
  ${lift("changePanel")}
  return { changeBlock, changePanel, diffBody, diffRows, sideBySide,
           full: changeFull, busy: changeBusy,
           show: (id, t) => { chat.changeShown = id; chat.transcript = t; } };`)();

/* The same lift, for the constants a lifted function closes over — the
   patterns it matches with are the half of it worth checking. Cut at a
   semicolon that ends a line, since a character class may hold one of its own. */
function liftConst(name) {
  const start = page.indexOf(`const ${name} =`);
  if (start < 0) throw new Error(`${name} is not in the page any more`);
  const end = page.slice(start).search(/;[ \t]*(\n|$)/);
  if (end < 0) throw new Error(`${name} does not end`);
  return unexport(page.slice(start, start + end + 1));
}

let failures = 0;
function check(what, ok, note = "") {
  console.log(`${ok ? "  ok  " : "FAIL  "}${what}${note ? `  — ${note}` : ""}`);
  if (!ok) failures++;
}

const change = {
  id: "toolu_01",
  path: "/home/someone/project/server.py",
  added: 14, removed: 3, lines: 40,
  preview: ["@@ -10,4 +10,15 @@", " context", "-gone", "+arrived", " context"],
};
const tool = { name: "Edit", detail: "/home/someone/project/server.py", change };

/* A tool call that changed nothing on disk carries nothing. Every Bash line in
   the conversation would otherwise gain an empty box. */
check("a tool that changed no file draws nothing",
  changeBlock({ name: "Bash", detail: "ls" }) === "");

const folded = changeBlock(tool);
check("a change draws its patch", folded.includes("scm-diff__line--add") && folded.includes("arrived"));
check("folded, always: opening it is somewhere the pane goes, not something it unfolds",
  folded.includes("change__diff--peek"));
check("it counts what it did", /\+14/.test(folded) && /−3/.test(folded));
check("named for the file rather than the path it is buried in",
  folded.includes(">server.py<") && !folded.includes("/home/someone"));
check("and says how much more there is, and what the click gets you",
  /compare side by side — 35 more lines/.test(folded),
  (folded.match(/change__hint md-label-small">([^<]*)/) || [])[1] || "");
check("the folded patch is a target of its own, not just the bar",
  (folded.match(/data-act="change"/g) || []).length === 2);
check("coloured by what each line does",
  folded.includes("scm-diff__line--del") && folded.includes("scm-diff__line--hunk"));

/* Opening one hands it the whole pane, so nothing about the folded block
   changes when it is open — it is not on screen. What must be true is that the
   pane it goes to says which file, how much moved, and how to come back. */
const tool2 = { name: "Edit", detail: "x", change: { ...change, id: "toolu_pane" } };
show("toolu_pane", { messages: [{ tools: [tool2] }] });
busy.add("toolu_pane");
const reading = changePanel({ sessionId: "s" });
check("the pane says what it is reading while it reads it",
  /Reading the whole change/.test(reading));
check("and names the file from the preview it was opened from",
  reading.includes(">server.py<"), reading.slice(0, 200));
check("with the way back, before anything else",
  reading.indexOf('data-act="change-close"') < reading.indexOf("server.py"));
check("and the counts it already knew", /\+14/.test(reading) && /−3/.test(reading));
busy.delete("toolu_pane");

/* Open, it is the editor's layout: two files, the differences on the same row.
   The pairing is the part worth checking, because it is the part that is not
   just the patch reprinted — a run of removals and the run of additions that
   follows it are one edit written twice. */
const PATCH = [
  "@@ -40,6 +40,7 @@",
  " first",
  "-was this",
  "-and this too",
  "+is this now",
  " fourth",
].join("\n");
const rows = diffRows(PATCH);
check("the hunk header is a row of its own, across both sides", rows[0].kind === "gap");
check("an unchanged line is the same row on both sides",
  rows[1].kind === "same" && rows[1].before.n === 40 && rows[1].after.n === 40,
  JSON.stringify(rows[1]));
check("a removal and the line that replaced it share a row",
  rows[2].kind === "both" && rows[2].before.text === "was this"
  && rows[2].after.text === "is this now", JSON.stringify(rows[2]));
check("a removal with nothing to answer it leaves the other side empty",
  rows[3].kind === "del" && rows[3].before.text === "and this too" && !rows[3].after,
  JSON.stringify(rows[3]));
check("and the numbering carries on from where the change left it",
  rows[4].before.n === 43 && rows[4].after.n === 42, JSON.stringify(rows[4]));

const pane = sideBySide(PATCH);
check("it draws two panes, named", /diff2__head--before[\s\S]*before[\s\S]*diff2__head--after[\s\S]*after/.test(pane));
check("what went is red down the left", /diff2__row--del/.test(pane));
check("what arrived is green down the right", /diff2__row--add/.test(pane));
check("a side with no line at all is washed rather than coloured",
  /diff2__line--gone/.test(pane));
check("both sides carry their own line numbers",
  (pane.match(/diff2__num/g) || []).length >= 10);
check("and a patch is still text from a file, so it is escaped",
  !sideBySide("@@ -1,1 +1,1 @@\n+<script>bad()</script>").includes("<script>"));

full.set("toolu_pane", { ok: true, text: PATCH, path: "/a/b/server.py",
                         added: 1, removed: 2, clipped: false });
const twoUp = changePanel({ sessionId: "s" });
check("the pane it opens into is the comparison", twoUp.includes("diff2-wrap"));
check("and it keeps the way back once the patch has landed",
  twoUp.includes('data-act="change-close"'));
check("the folded block in the conversation stays one column",
  changeBlock(tool).includes("scm-diff") && !changeBlock(tool).includes("diff2-wrap"));
full.set("toolu_pane", { ok: true, text: PATCH, path: "/a/b/server.py",
                         added: 1, removed: 2, clipped: true });
check("a change too long to read still says so in the pane",
  /longer than a change/.test(changePanel({ sessionId: "s" })));

/* A patch is text from a file, and the file is not the panel's. */
full.clear();
const nasty = changeBlock({ name: "Write", detail: "x", change: {
  ...change, id: "toolu_02", preview: ['+<img src=x onerror="alert(1)">'] } });
check("what a patch contains is escaped, not run",
  nasty.includes("&lt;img") && !nasty.includes("<img"));

/* A change with nothing more to show does not offer to show it. */
check("a change whose preview is the whole of it promises no more lines",
  !/more line/.test(changeBlock({ name: "Edit", detail: "x", change: {
    ...change, id: "toolu_03", lines: change.preview.length } })));

/* ============================ paths in a message ==========================
   A path written into the conversation opens in the editor, so the renderer has
   to find the paths and — much harder — leave everything that is not one alone.
   Every line below is a thing that turned up in a real transcript. */
const { linkPaths, renderMarkdown, editor } = new Function(`
  const escapeHtml = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const app = { settings: { showEditor: true } };
  ${liftConst("MD_HOLD")}
  ${liftConst("PATH_RE")}
  ${liftConst("TRAILING")}
  ${liftConst("SUFFIX")}
  ${liftConst("BARE_RE")}
  ${liftConst("MATH_RE")}
  // Maths is drawn by its own module and checked in math-check.mjs; here it
  // only has to exist, so that a message carrying some still renders.
  const renderMath = (tex) => "<math>" + escapeHtml(tex) + "</math>";
  const ICON = { copy: "<svg/>" };
  ${liftConst("copyButton")}
  ${lift("safeUrl")}
  ${lift("linkPaths")}
  ${lift("mark")}
  ${lift("outsideLinks")}
  ${lift("renderMarkdown")}
  return { linkPaths, renderMarkdown, editor: app.settings };`)();

const linked = (text) => [...renderMarkdown(text).matchAll(/data-path="([^"]+)"(?: data-line="(\d+)")?/g)]
  .map((m) => m[1] + (m[2] ? `:${m[2]}` : ""));

check("a path in prose becomes something to open",
  linked("built into ~/.motorcortex/build/20260826T084947Z/intel-2026.05/ · tool commit cb7f775")
    .join() === "~/.motorcortex/build/20260826T084947Z/intel-2026.05/",
  JSON.stringify(linked("built into ~/.motorcortex/build/20260826T084947Z/intel-2026.05/ · tool commit cb7f775")));
check("so does one in code marks, which is where they usually are",
  linked("see `web/src/main.ts` for it").join() === "web/src/main.ts");
check("a line number comes with it", linked("`web/src/main.ts:1560`").join() === "web/src/main.ts:1560");
check("an absolute path, a relative one and a home one all count",
  linked("/tmp/out.log, ./web/styles/base.css and ~/notes").join() === "/tmp/out.log,./web/styles/base.css,~/notes",
  JSON.stringify(linked("/tmp/out.log, ./web/styles/base.css and ~/notes")));
check("the full stop that ends the sentence is not part of the path",
  linked("it went to /tmp/out.log.").join() === "/tmp/out.log");
check("nor is the bracket it was written inside",
  linked("(see docs/cleanup-plan.md) for why").join() === "docs/cleanup-plan.md");

/* A file name with no folder on it is a path too, but only where writing it was
   deliberate: inside code marks, and only with a suffix the panel knows. */
check("a bare file name in code marks counts", linked("look at `server.py:44`").join() === "server.py:44");
check("but not the same name in prose — half the full stops in a sentence would light up",
  linked("look at server.py for it").length === 0);
check("and not a method call, which looks exactly like a file with a short extension",
  linked("use `Array.from` and `app.settings.showEditor`").length === 0,
  JSON.stringify(linked("use `Array.from` and `app.settings.showEditor`")));
check("a name inside a path is not found twice",
  linked("`web/src/main.ts`").join() === "web/src/main.ts");

/* The other half, and the half that matters: what must never light up. */
for (const [text, why] of [
  ["either and/or both", "and/or"],
  ["the I/O is slow", "I/O"],
  ["at 30 km/h", "a unit"],
  ["open 24/7 here", "a phrase with numbers"],
  ["about 1/2 of them", "a fraction"],
  ["see https://example.com/a/b.md now", "a URL"],
  ["read [the docs](https://x.dev/a/b.md) first", "a URL inside a link"],
  ["a `/* comment */` in C", "a comment marker"],
]) {
  check(`${why} is left alone`, linked(text).length === 0, JSON.stringify(linked(text)));
}

check("a path inside a fenced block is left as text — a whole file of them would be a wall of links",
  linked("```\n/tmp/one.log\n```") .length === 0);

editor.showEditor = false;
check("and nothing is a link at all with the editor switched off in Settings",
  linked("see /tmp/out.log").length === 0);
editor.showEditor = true;

/* ========================== copying a code block =========================
   Every fenced block carries the button that takes it, and the button has to be
   outside the <pre> — inside, it rides the block's own sideways scroll off the
   edge of the bubble. */
const fenced = renderMarkdown("here:\n\n```sh\ndocker compose up\n```\n");
check("a fenced block carries a copy button",
  /<button[^>]*data-copy-code/.test(fenced));
check("in a frame of its own, outside the code that scrolls under it",
  /<div class="md-codeblock"><pre class="md-code"[\s\S]*<\/pre><button[^>]*data-copy-code[\s\S]*<\/button><\/div>/.test(fenced));
check("the block is still a pre.md-code with its language on it — that is what a quote reads",
  /<pre class="md-code" data-lang="sh">/.test(fenced),
  fenced.slice(fenced.indexOf("<div class=\"md-codeblock\""), fenced.indexOf("<code")));
check("and inline code gets no button — it is a few words mid-sentence",
  !/data-copy-code/.test(renderMarkdown("run `ls` here")));
check("what the button copies is the code and nothing else",
  /<code class="md-mono">docker compose up<\/code>/.test(fenced));

/* And one check that is about the stylesheet rather than the markup, because the
   bug it guards is invisible to everything above: a folded preview sitting in
   the middle of a conversation must not be a scroll container. `.scm-diff` is
   one — it has its own overflow and `overscroll-behavior: contain`, which is
   right for the diff a Git row opens — and a box that cannot scroll but has been
   told not to pass scrolling on is a dead patch of the chat. The wheel lands on
   it and nothing moves, which is exactly what happened. */
const styles = readdirSync(join(here, "..", "web", "styles"))
  .map((name) => readFileSync(join(here, "..", "web", "styles", name), "utf8")).join("\n");

function ruleBody(selector) {
  const at = styles.indexOf(`  ${selector} {`);
  if (at < 0) throw new Error(`${selector} is not in the stylesheet any more`);
  return styles.slice(at, styles.indexOf("}", at));
}
const peek = ruleBody(".change__diff--peek");
check("the folded preview clips rather than scrolls", /overflow:\s*clip/.test(peek), peek.replace(/\s+/g, " "));
check("and does not keep the wheel to itself",
  /overscroll-behavior:\s*auto/.test(peek) && !/overscroll-behavior:\s*contain/.test(peek));
const wrap = ruleBody(".diff2-wrap");
check("the comparison leaves the scrolling to the pane it took over",
  !/overflow:\s*(auto|scroll)/.test(wrap) && !/overscroll-behavior/.test(wrap),
  wrap.replace(/\s+/g, " "));

console.log();
console.log(failures ? `${failures} failed` : "all ok");
process.exit(failures ? 1 : 0);
