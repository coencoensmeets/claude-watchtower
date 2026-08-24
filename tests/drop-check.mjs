// A file dropped on the composer, in a real browser.
//
// The rest of the drop is checked without one — tests/composer-check.mjs takes
// the URI apart and tests/paste-check.py drives the write — but neither of them
// can see the thing that actually goes wrong here: what a browser puts on a
// drag. A file manager offers a `file://` URI and the path is typed in; Chrome's
// downloads offer the bytes and no path at all, and the panel has to save a copy
// and name that instead. Both are drops of a file, and the difference is only
// visible to a browser holding a real DataTransfer.
//
//   1. start a panel (a fixture directory is fine):
//        CLAUDE_WATCHTOWER_SESSION_DIR=/path/to/fixtures python3 server.py --port 8788
//   2. start a throwaway browser with CDP open:
//        google-chrome --headless=new --remote-debugging-port=9333 \
//          --user-data-dir=$(mktemp -d) about:blank
//   3. node tests/drop-check.mjs
//
// Override PANEL_URL / CDP_URL to point elsewhere. A failure prints the case and
// exits 1.

import { existsSync, readFileSync, rmSync } from "node:fs";

const PANEL = process.env.PANEL_URL || "http://127.0.0.1:8788";
const CDP = process.env.CDP_URL || "http://127.0.0.1:9333";
let failures = 0;

const targets = await (await fetch(`${CDP}/json/list`)).json();
const page = targets.find((t) => t.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
const consoleErrors = [];

ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") consoleErrors.push(msg.params.entry.text);
  if (msg.method === "Runtime.exceptionThrown") consoleErrors.push(String(msg.params.exceptionDetails.text));
});
await new Promise((r) => ws.addEventListener("open", r));

const send = (method, params = {}) =>
  new Promise((resolve) => { const n = ++id; pending.set(n, resolve); ws.send(JSON.stringify({ id: n, method, params })); });
const evaluate = async (expression) => {
  const res = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (res.result?.exceptionDetails) throw new Error(String(res.result.exceptionDetails.text));
  return res.result.result.value;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const check = (name, pass, detail = "") => {
  if (!pass) failures += 1;
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
};

await send("Page.enable"); await send("Runtime.enable"); await send("Log.enable");
await send("Page.navigate", { url: `${PANEL}/` });
await sleep(2500);
await evaluate("localStorage.clear()");
await send("Page.navigate", { url: `${PANEL}/` });
await sleep(4000);

/* A session with a box to type in. Not any session: one standing at a question
   has its box taken by the question, so this walks the list until a row draws a
   `#sayField`. The groups are opened first, since a collapsed group has no rows
   to click. */
const opened = await evaluate(`(async () => {
  for (const head of document.querySelectorAll(".group__header[aria-expanded='false']")) head.click();
  await new Promise((r) => setTimeout(r, 600));
  for (const row of document.querySelectorAll(".session-item")) {
    row.click();
    await new Promise((r) => setTimeout(r, 900));
    if (document.querySelector("#sayField")) {
      return document.querySelector(".detail-pane")?.dataset.sessionId || "yes";
    }
  }
  return "";
})()`);
check("a session with a message box is open", !!opened, opened || "no session drew a box");

const cwd = await evaluate(`(async () => {
  const state = await (await fetch("/api/state")).json();
  const one = (state.sessions || []).find((s) => s.sessionId === ${JSON.stringify(opened)});
  return (one || {}).cwd || "";
})()`);

/* Injected once: the drop the browser would make. `DataTransfer` is
   constructible, which is the whole reason this check can exist — a drag out of
   a file manager is a `text/uri-list`, and a drag out of Chrome's downloads is a
   `File` with no path anywhere on it. */
await evaluate(String.raw`
  window.__dropOn = (field, build) => {
    const dt = new DataTransfer();
    build(dt);
    for (const type of ["dragenter", "dragover"]) {
      field.dispatchEvent(new DragEvent(type, { dataTransfer: dt, bubbles: true, cancelable: true }));
    }
    const held = field.classList.contains("is-dropping");
    field.dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
    return held;
  };
  true`);

/* ------------------------------------------------ a drag out of a file manager */

const dragged = await evaluate(String.raw`(() => {
  const field = document.querySelector("#sayField");
  field.value = "look at";
  field.setSelectionRange(7, 7);
  const held = window.__dropOn(field, (dt) =>
    dt.setData("text/uri-list", "file:///tmp/wt%20drop/notes.md"));
  return JSON.stringify({ held, value: field.value, ring: field.classList.contains("is-dropping") });
})()`);
const fromManager = JSON.parse(dragged);
check("the box says a file may be let go of over it", fromManager.held);
check("a dragged file types its path, quoted for the space in it",
  fromManager.value === 'look at "/tmp/wt drop/notes.md" ', JSON.stringify(fromManager.value));
check("and the ring goes when the drag lands", !fromManager.ring);

/* Text dragged out of the conversation is the browser's own business: the drop
   must not be taken over, or a quote dragged into the box would arrive as a
   snackbar. */
const draggedText = await evaluate(String.raw`(() => {
  const field = document.querySelector("#sayField");
  const dt = new DataTransfer();
  dt.setData("text/plain", "just some words");
  const over = new DragEvent("dragover", { dataTransfer: dt, bubbles: true, cancelable: true });
  field.dispatchEvent(over);
  return JSON.stringify({ taken: over.defaultPrevented, ring: field.classList.contains("is-dropping") });
})()`);
check("a drag of plain text is left to the browser",
  !JSON.parse(draggedText).taken && !JSON.parse(draggedText).ring);

/* -------------------------------------------- a drag out of Chrome's downloads */

// The case that started this: the bytes are on the drag and there is no path
// anywhere on it, so the panel has to save a copy and name that.
const NAME = `wt-drop-check-${process.pid}.txt`;
const BODY = "dropped out of a download\n";
await evaluate(String.raw`(() => {
  const field = document.querySelector("#sayField");
  field.value = "";
  window.__dropOn(field, (dt) =>
    dt.items.add(new File([${JSON.stringify(BODY)}], ${JSON.stringify(NAME)}, { type: "text/plain" })));
  return true;
})()`);

// The upload starts on the drop rather than on Send, so this is waiting for a
// round trip and a repaint, not for anything on a clock.
let saved = "";
for (let tries = 0; tries < 30 && !saved; tries++) {
  await sleep(400);
  saved = await evaluate(String.raw`(() => {
    const row = document.querySelector(".attached__item");
    if (!row) return "";
    const name = row.querySelector(".attached__name");
    if (/saving/i.test(name.textContent)) return "";
    return (name.title || name.textContent).split("\n")[0];
  })()`);
}
check("a drop with no path is saved and named", !!saved, saved || "nothing appeared in the strip");
check("under the name it was dropped as, in the session's own folder",
  saved.startsWith(`${cwd}/.claude/watchtower-files/`) && saved.endsWith(NAME), saved);
check("and the strip says plainly that it is a copy",
  await evaluate(`/carried no path/.test(document.querySelector(".attached__name").title)`));
check("and the file the panel named is really there, with the bytes off the drag",
  !!saved && existsSync(saved) && readFileSync(saved, "utf8") === BODY);
check("it is drawn with a glyph rather than a thumbnail it has not got",
  await evaluate(`!!document.querySelector(".attached__thumb--file") && !document.querySelector(".attached__item img")`));
check("nothing was typed into the box for it — the path goes on the message",
  await evaluate(`document.querySelector("#sayField").value === ""`));
check("and it can be left out again",
  await evaluate(String.raw`(() => {
    const drop = document.querySelector("[data-act='unattach']");
    if (!drop) return false;
    drop.click();
    return true;
  })()`));
await sleep(600);
check("removing it clears the strip",
  await evaluate(`!document.querySelector(".attached__item")`));

if (saved && existsSync(saved)) rmSync(saved);

check("nothing threw while any of that happened", consoleErrors.length === 0,
  consoleErrors.slice(0, 3).join(" | "));

console.log(failures ? `\n${failures} failed` : "\nall ok");
ws.close();
process.exit(failures ? 1 : 0);
