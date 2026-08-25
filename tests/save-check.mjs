// Checks for what a message comes down as: the markdown file behind
// "Download as Markdown" on the conversation's context menu.
//
// Same trick as chat-check.mjs and for the same reason — the panel's modules
// touch `document` as they load, so the functions that build the file are lifted
// out of the sources and run on their own. No browser and no packages.
//
//   node tests/save-check.mjs
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

const { messageMarkdown, fileNameFor, messageForRow, chat } = new Function(`
  const chat = { transcript: null };
  ${lift("clockOf")}
  ${lift("messageKey")}
  ${lift("whoSaid")}
  ${lift("stampOf")}
  ${lift("toolsMarkdown")}
  ${lift("messageMarkdown")}
  ${lift("fileNameFor")}
  ${lift("messageForRow")}
  return { messageMarkdown, fileNameFor, messageForRow, chat };`)();

let failures = 0;
function check(what, ok, note = "") {
  console.log(`${ok ? "  ok  " : "FAIL  "}${what}${note ? `  — ${note}` : ""}`);
  if (!ok) failures++;
}

const session = { name: "Watch tower", cwd: "/home/someone/project" };
// Built from a local wall-clock time, because that is what the panel shows and
// what the file says — a fixed UTC string would make this test depend on which
// timezone it is run in.
const at = new Date(2026, 7, 25, 14, 32).toISOString();

/* ------------------------------------------------------------------- speech */
const said = messageMarkdown({ role: "assistant", at, text: "Here is **the plan**.\n\n- one\n- two\n" }, session);
check("the heading says who and when", said.startsWith("# claude — 14:32 · 2026-08-25"), said.split("\n")[0]);
check("and the conversation it came out of is named under it",
  said.includes("*from Watch tower · `/home/someone/project`*"));
check("the markdown is the markdown that was written, not the HTML it was drawn as",
  said.includes("Here is **the plan**.") && said.includes("- one\n- two"));
check("it ends with exactly one newline", said.endsWith("two\n") && !said.endsWith("\n\n"));

const mine = messageMarkdown({ role: "user", at, text: "Try it again." }, session);
check("your own turn is attributed to you", mine.startsWith("# you —"));
const peer = messageMarkdown({ role: "user", at, from: "Mujoco", text: "Done." }, session);
check("and one that arrived from another session carries that session's name",
  peer.startsWith("# Mujoco —"));

/* -------------------------------------------------------------- what it ran */
const change = {
  id: "toolu_01", path: "/home/someone/project/server.py",
  added: 14, removed: 3, lines: 40,
  preview: ["@@ -10,4 +10,15 @@", " context", "-gone", "+arrived"],
};
const ran = messageMarkdown({
  role: "assistant", at, text: "",
  tools: [{ name: "Bash", detail: "ls -l" }, { name: "Edit", detail: "server.py", change }],
}, session);
check("a tool-only turn still saves as itself", ran.includes("## What it ran"));
check("each call is a line", ran.includes("- **Bash** — `ls -l`"));
check("a change goes back as a diff, fenced", ran.includes("```diff") && ran.includes("  +arrived"));
check("with the file and the counts above it", ran.includes("`/home/someone/project/server.py` +14 −3"));
check("and says what the preview left out rather than implying it is whole",
  ran.includes("… 36 more lines not shown here"));
const oneMore = messageMarkdown({ role: "assistant", at, text: "", tools: [{ name: "Edit", detail: "x",
  change: { ...change, lines: 5 } }] }, session);
check("one line short is one line, not \"1 lines\"", oneMore.includes("… 1 more line not shown"));

/* ------------------------------------------------------------------ the name */
check("the file is named for the session, the speaker and the minute",
  fileNameFor({ role: "assistant", at }, session) === "watch-tower-claude-2026-08-25-1432.md",
  fileNameFor({ role: "assistant", at }, session));
check("a session named in punctuation still yields a filename",
  fileNameFor({ role: "user", at }, { name: "!!! ??? !!!" }) === "you-2026-08-25-1432.md",
  fileNameFor({ role: "user", at }, { name: "!!! ??? !!!" }));
check("and a message with no usable time is still saveable",
  fileNameFor({ role: "user", at: null }, null) === "you-message.md",
  fileNameFor({ role: "user", at: null }, null));
check("a session with no folder recorded says only where it came from",
  messageMarkdown({ role: "user", at, text: "hi" }, { name: "Loose" }).includes("*from Loose*"));

/* ----------------------------------------------------- which row is which */
/* The transcript is rebuilt from data on every poll, so the row can only say
   which message it stands for — never hold it. */
const messages = [
  { role: "user", at, text: "first" },
  { role: "assistant", at, text: "second" },
];
chat.transcript = { messages };
const keyOf = (message) => new Function(`${lift("clockOf")}${lift("messageKey")}
  return messageKey(${JSON.stringify(message)});`)();
check("a row finds its own message by key",
  messageForRow({ dataset: { key: keyOf(messages[1]) } })?.text === "second");
check("a row whose message is no longer in the page finds nothing",
  messageForRow({ dataset: { key: "assistant|09:00|gone" } }) === null);
check("and a row with no key at all is not a message", messageForRow({ dataset: {} }) === null);

console.log();
console.log(failures ? `${failures} failed` : "all ok");
process.exit(failures ? 1 : 0);
