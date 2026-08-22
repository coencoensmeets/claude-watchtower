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

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const page = readFileSync(join(here, "..", "static", "index.html"), "utf8");

function lift(name) {
  const start = page.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`${name} is not in the page any more`);
  let depth = 0;
  for (let at = page.indexOf("{", start); at < page.length; at++) {
    if (page[at] === "{") depth++;
    else if (page[at] === "}" && --depth === 0) return page.slice(start, at + 1);
  }
  throw new Error(`${name} does not close`);
}

const { changeBlock, changePanel, diffBody, diffRows, sideBySide, full, busy, show } = new Function(`
  const escapeHtml = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const ICON = { back: "<svg/>" };
  const changeFull = new Map();
  const changeBusy = new Set();
  let changeShown = null;
  let transcript = null;
  ${lift("diffBody")}
  ${lift("diffRows")}
  ${lift("sideBySide")}
  ${lift("changeBlock")}
  ${lift("changePanel")}
  return { changeBlock, changePanel, diffBody, diffRows, sideBySide,
           full: changeFull, busy: changeBusy,
           show: (id, t) => { changeShown = id; transcript = t; } };`)();

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

/* And one check that is about the stylesheet rather than the markup, because the
   bug it guards is invisible to everything above: a folded preview sitting in
   the middle of a conversation must not be a scroll container. `.scm-diff` is
   one — it has its own overflow and `overscroll-behavior: contain`, which is
   right for the diff a Git row opens — and a box that cannot scroll but has been
   told not to pass scrolling on is a dead patch of the chat. The wheel lands on
   it and nothing moves, which is exactly what happened. */
function ruleBody(selector) {
  const at = page.indexOf(`  ${selector} {`);
  if (at < 0) throw new Error(`${selector} is not in the stylesheet any more`);
  return page.slice(at, page.indexOf("}", at));
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
