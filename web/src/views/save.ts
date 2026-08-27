/* ==========================================================================
   Taking a message out of the panel — right-click a bubble and it comes down
   as a markdown file.

   The file is built from the transcript rather than from the bubble on screen:
   the page holds rendered HTML, and what you want to keep is the markdown the
   session actually wrote. The row only says *which* message — its data-key,
   the same handle a comment uses — and the text comes back out of the
   transcript the panel already has.

   Which means the file carries exactly what the panel was shown, no more: the
   server clips a very long message on the way here, and a download cannot
   un-clip it. Better an honest copy of what you were reading than a promise of
   a whole one.
   ========================================================================== */

import { chat, selected } from "../state.js";
import { detailPane, hitClosest } from "../ui/dom.js";
import { ICON } from "../ui/icons.js";
import { openMenu, sessionMenu } from "../ui/menu.js";
import { copyText } from "../ui/clipboard.js";
import { showSnackbar } from "../ui/snackbar.js";
import { messageKey } from "./owned.js";

/* Whose words these were, in the plain form the file wants. The bubble says
   "another session" for the reader's benefit; a saved file wants the name.
   Named away from chat.ts's `speakerOf`, which answers a different question:
   there, whose words these are is being written for the session about to read
   them, so "claude" comes out as "you". */
function whoSaid(message) {
  if (message.role !== "user") return "claude";
  return message.from || "you";
}

/* When it was said, written out in full. The bubble shows a clock because the
   day is the one you are sitting in; a file outlives that. */
function stampOf(message) {
  // `new Date(null)` is the epoch rather than an error, so a message with no
  // time recorded has to be caught before the Date is made — or every one of
  // them would be filed under January 1970.
  const date = message.at ? new Date(message.at) : new Date(NaN);
  if (Number.isNaN(date.valueOf())) return { day: "", clock: "", file: "message" };
  const pad = (n) => String(n).padStart(2, "0");
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const clock = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  return { day, clock, file: `${day}-${pad(date.getHours())}${pad(date.getMinutes())}` };
}

/* The tool calls a turn carried, as a list — and a change it made as a diff,
   because a patch quoted as prose is not a patch. Only the preview is here,
   which is what the conversation itself showed. */
function toolsMarkdown(tools) {
  const lines = [];
  for (const tool of tools || []) {
    lines.push(`- **${tool.name}**${tool.detail ? ` — \`${tool.detail}\`` : ""}`);
    const change = tool.change;
    if (!change) continue;
    const rest = Math.max(0, change.lines - change.preview.length);
    lines.push("");
    lines.push(`  \`${change.path}\` +${change.added} −${change.removed}`);
    lines.push("");
    lines.push("  ```diff");
    for (const line of change.preview) lines.push(`  ${line}`);
    if (rest) lines.push(`  … ${rest} more line${rest === 1 ? "" : "s"} not shown here`);
    lines.push("  ```");
    lines.push("");
  }
  return lines;
}

/* One message as a file: a heading saying who and when, a line saying which
   conversation it came out of, then what was said. */
function messageMarkdown(message, session) {
  const who = whoSaid(message);
  const { day, clock } = stampOf(message);
  const when = [clock, day].filter(Boolean).join(" · ");
  const out = [`# ${who}${when ? ` — ${when}` : ""}`, ""];
  if (session) {
    const where = session.cwd ? ` · \`${session.cwd}\`` : "";
    out.push(`*from ${session.name}${where}*`, "");
  }
  if (message.text) out.push(message.text.replace(/\r/g, "").trimEnd(), "");
  const tools = toolsMarkdown(message.tools);
  if (tools.length) out.push("## What it ran", "", ...tools);
  return `${out.join("\n").trimEnd()}\n`;
}

/* A filename somebody can find again in a downloads folder: the session, who
   spoke, and when. Anything a filesystem would rather not see becomes a dash. */
function fileNameFor(message, session) {
  const slug = (text) => String(text || "").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);
  const parts = [slug(session?.name), slug(whoSaid(message)), stampOf(message).file];
  return `${parts.filter(Boolean).join("-") || "message"}.md`;
}

/* The message a row stands for. The transcript is rebuilt from data on every
   poll, so the element itself is never the thing to read — its key is. */
function messageForRow(row) {
  const key = row?.dataset.key;
  if (!key) return null;
  return (chat.transcript?.messages || []).find((message) => messageKey(message) === key) || null;
}

/* Handing a file to the browser. The anchor is made, clicked and dropped in the
   same breath; the object URL is revoked on the next turn of the loop, once the
   download has taken hold of it. */
function saveFile(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function downloadMessage(message, session) {
  const name = fileNameFor(message, session);
  saveFile(name, messageMarkdown(message, session));
  showSnackbar(`Saved ${name}`);
}

function copyMessage(message, session) {
  copyText(messageMarkdown(message, session), "Copied as Markdown");
}

/* Right-clicking a message. On the pane, not on the transcript: the pane
   survives every render, and a listener attached inside one would die with the
   bubbles it was bound to — or worse, pile up one per poll.

   A right-click on a selection is left to the browser: that menu has Copy in it,
   and the passage you have just selected is the thing you meant to take. */
detailPane.addEventListener("contextmenu", (event) => {
  const row = hitClosest(event, ".msg, .activity-row");
  if (!row || !row.closest("#chatScroll")) return;
  const selection = window.getSelection();
  if (selection && !selection.isCollapsed && String(selection).trim()
      && row.contains(selection.anchorNode)) return;
  if (openMessageMenu(row, event.clientX, event.clientY)) event.preventDefault();
});

/* The button on the turn — the route for a touch screen, which has no
   right-click, and for the keyboard, which has no Shift-F10 here.

   The menu opens under the button rather than at the pointer, because on a phone
   the pointer is a fingertip resting on the thing it just pressed. Hung
   leftwards from the button's right edge: the button sits near the right of the
   pane, and a menu opening rightwards from it would be shunted back by
   openMenu's own clamp anyway — this puts it over the conversation deliberately
   rather than by arithmetic. */
detailPane.addEventListener("click", (event) => {
  const button = hitClosest(event, '[data-act="msg-menu"]');
  if (!button) return;
  const row = button.closest(".msg, .activity-row");
  if (!row) return;
  // Nothing else in the pane should read this as a click on the turn.
  event.stopPropagation();
  const rect = button.getBoundingClientRect();
  openMessageMenu(row, Math.max(8, rect.right - MENU_MIN_WIDTH), rect.bottom + 4);
});
/* What the menu is at least as wide as — .menu's own min-width, so a menu hung
   from the right edge of a button is hung from the right edge of the menu. */
const MENU_MIN_WIDTH = 232;

/* The menu itself, wherever it was asked for. `false` when the row it was asked
   for is no longer a message the panel holds — the caller then leaves the event
   alone, so a right-click still gets the browser's own menu. */
function openMessageMenu(row, x, y) {
  const message = messageForRow(row);
  if (!message) return false;
  const session = selected();
  const who = whoSaid(message);
  const { clock } = stampOf(message);
  markRow(row);
  openMenu({
    title: `${who}${clock ? ` · ${clock}` : ""}`,
    label: "Actions for this message",
    items: [
      { key: "save", icon: ICON.download, label: "Download as Markdown",
        hint: fileNameFor(message, session), run: () => downloadMessage(message, session) },
      { key: "copy", icon: ICON.copy, label: "Copy as Markdown",
        run: () => copyMessage(message, session) },
    ],
  }, x, y);
  return true;
}

/* Which turn the open menu belongs to, marked while it stands. openMenu marks
   its own target, but only in the session list — a menu over the conversation is
   outside everything it knows about, so the mark and the clearing of it are kept
   here. The clearing watches the menu's own open flag rather than asking to be
   told: the menu closes from a dozen places — a pick, Escape, a scroll, a poll
   rebuilding the pane — and every one of them would otherwise have to remember
   this one. */
function markRow(row) {
  for (const stale of detailPane.querySelectorAll<HTMLElement>('[data-menu="open"]')) delete stale.dataset.menu;
  row.dataset.menu = "open";
}
new MutationObserver(() => {
  if (sessionMenu.dataset.open === "true") return;
  for (const stale of detailPane.querySelectorAll<HTMLElement>('[data-menu="open"]')) delete stale.dataset.menu;
}).observe(sessionMenu, { attributes: true, attributeFilter: ["data-open"] });
