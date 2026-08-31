/* One file change, whole: the patch a preview in the conversation stands for. */

import { refreshDetail } from "../refresh.js";
import { CHAT_LIMIT_MAX, CHAT_PAGE, chat } from "../state.js";
import { detailPane } from "../ui/dom.js";
import { clockOf, escapeHtml } from "../ui/format.js";
import { ICON } from "../ui/icons.js";
import { linkPaths, renderMarkdown } from "../ui/markdown.js";
import { showSnackbar } from "../ui/snackbar.js";
import { diffBody, sideBySide } from "./git.js";
import { agentBlock, agentPanel, agentShownFull } from "./subagent.js";
import { messageKey } from "./owned.js";

               // the scroll position to hand back
const changeFull = new Map();     // toolUseId -> the whole patch, once fetched
export const changeBusy = new Set();

/* Folded, in the conversation: the patch as a patch. A few lines in one column,
   which is the right shape for something you are reading *past* rather than
   reading — you are following what the session did, and this says it changed
   this file, roughly here, by about this much. */
function changeBlock(tool) {
  const change = tool.change;
  if (!change) return "";
  const name = String(change.path || "").split("/").pop();
  const rest = Math.max(0, change.lines - change.preview.length);
  const hint = changeBusy.has(change.id) ? "reading the whole change…"
    : rest ? `compare side by side — ${rest} more line${rest === 1 ? "" : "s"}`
    : "compare side by side";
  return `<div class="change" data-change="${escapeHtml(change.id)}">
      <button class="change__bar md-state" type="button" data-act="change"
        data-id="${escapeHtml(change.id)}">
        <span class="change__file md-label-small md-mono">${escapeHtml(name)}</span>
        <span class="change__stat md-label-small md-mono"><span class="change__add">+${change.added}</span>
          <span class="change__del">−${change.removed}</span></span>
        <span class="change__hint md-label-small">${escapeHtml(hint)}</span>
      </button>
      <pre class="scm-diff change__diff change__diff--peek md-mono md-body-small"
        data-act="change" data-id="${escapeHtml(change.id)}">${diffBody(change.preview.join("\n"))}</pre>
    </div>`;
}

/* Opened, it takes the whole pane.

   Two files side by side want the width of both, and the conversation is the one
   thing on screen that can lend it: a comparison squeezed into the column a
   message occupies is two narrow columns, which is worse than the one column it
   replaced. So reading a change is a place the pane goes rather than something
   that unfolds inside it — with the way back where the way back goes, and the
   conversation still exactly where you left it when you take it.

   The session, its state, its composer and every other tab stay put. It is the
   conversation this stands in front of, not the panel. */
function changePanel(session) {
  const full = changeFull.get(chat.changeShown);
  const known = (chat.transcript?.messages || [])
    .flatMap((message) => message.tools || [])
    .find((tool) => tool.change?.id === chat.changeShown)?.change;
  const path = full?.path || known?.path || "";
  const name = String(path).split("/").pop() || "the file";
  const where = String(path).slice(0, -name.length);
  const added = full?.added ?? known?.added ?? 0;
  const removed = full?.removed ?? known?.removed ?? 0;
  return `<div class="change-full">
      <div class="change-full__bar">
        <button class="button button--text md-state change-full__back" type="button" data-act="change-close">
          ${ICON.back}Conversation
        </button>
        <span class="change-full__name md-title-small md-mono">${escapeHtml(name)}</span>
        <span class="change-full__where md-label-small md-mono">${escapeHtml(where)}</span>
        <span class="change-full__stat md-label-medium md-mono"><span class="change__add">+${added}</span>
          <span class="change__del">−${removed}</span></span>
      </div>
      ${full
        ? sideBySide(full.text)
        : `<p class="chat__note md-body-medium">${changeBusy.has(chat.changeShown)
            ? "Reading the whole change…" : "That change could not be read."}</p>`}
      ${full?.clipped
        ? `<p class="change__note md-label-small">as much of it as this panel reads — the rest is longer than a change</p>`
        : ""}
    </div>`;
}

/* Opening one, and handing the conversation back afterwards.

   The scroll positions are the whole of the fiddle, and they are worth it: the
   pane is one scroller, so taking it over inherits wherever the conversation was
   — which for a change you clicked halfway up the history is the middle of the
   diff — and handing it back inherits wherever the diff was. Each is set
   deliberately: the comparison opens at the top, and the conversation comes back
   where you left it.

   The patch is fetched once and kept, so opening the same change again is
   instant and survives every re-render the poll brings. */
export async function showChange(id, session) {
  const scroller = detailPane.querySelector("#chatScroll");
  chat.chatReturn = scroller ? scroller.scrollTop : 0;
  chat.changeShown = id;
  const settle = () => {
    refreshDetail(true);
    const now = detailPane.querySelector("#chatScroll");
    if (now) now.scrollTop = 0;
  };
  if (changeFull.has(id)) { settle(); return; }
  changeBusy.add(id);
  settle();
  try {
    const response = await fetch(
      `/api/change?sessionId=${encodeURIComponent(session.sessionId)}&id=${encodeURIComponent(id)}`,
      { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (data.ok) changeFull.set(id, data);
    else showSnackbar(data.message || "Could not read the whole of that change");
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    changeBusy.delete(id);
    // Only if it is still the thing being read: the pane may have moved on while
    // the request was in flight.
    if (chat.changeShown === id) settle();
  }
}

export function hideChange() {
  if (chat.changeShown === null) return false;
  chat.changeShown = null;
  refreshDetail(true);
  const scroller = detailPane.querySelector("#chatScroll");
  if (scroller) scroller.scrollTop = chat.chatReturn;
  return true;
}

/* Every turn carries the same button, and the menu behind it is the one a
   right-click opens — see views/save.js.

   It exists because right-clicking is not a gesture a phone has, and the two
   substitutes are both worse: a long press on a bubble is how you select text
   on a touch screen, which is what raises the Comment chip, and a hidden
   gesture nobody is told about is not an affordance. So the actions get a
   button. It fades in under the pointer on a mouse, like the row actions in the
   Git tab, and stays put wherever there is no hover to fade it in with.

   Which message it belongs to is not written on it: it is inside the row, and
   the row already carries its own key. */
function moreButton() {
  return `<button class="msg__more md-state" type="button" data-act="msg-menu"
      aria-haspopup="menu" aria-label="Actions for this message"
      title="Actions for this message">${ICON.more}</button>`;
}

export function chatPanel(session) {
  // A change being read whole stands in front of the conversation rather than
  // inside it. Before the "is the transcript here yet" test, because the change
  // was opened from a transcript that was: a poll landing mid-read must not
  // replace the comparison with *Reading the conversation…*
  if (chat.changeShown !== null) return changePanel(session);
  // And so does a subagent's conversation, for the same reason: it is a
  // conversation, and a conversation wants the pane a conversation gets.
  if (chat.agentShown !== null) {
    const full = agentShownFull();
    return agentPanel(full
      ? `<div class="chat">${conversationRows(full.messages, true).join("")}</div>` : "");
  }
  if (chat.transcriptFor !== session.sessionId || !chat.transcript) {
    return `<p class="chat__note md-body-medium">Reading the conversation…</p>`;
  }
  if (!chat.transcript.messages.length) {
    // A nested session has no transcript at all — Claude Code writes none for
    // one — so "yet" would be a promise the panel cannot keep.
    return `<p class="chat__note md-body-medium">${session.kind === "child"
      ? "A session started from inside another one keeps no transcript on disk, "
        + "so there is no conversation to read here. Its state, folder and branch "
        + "are still live, and you can still send it a message."
      : "Nothing in this transcript yet."}</p>`;
  }
  const rows = chat.transcript.truncated
    ? [`<p class="chat__note md-label-small">showing the last ${chat.transcript.messages.length} messages${
        chat.chatLimit < CHAT_LIMIT_MAX
          ? ` <button class="button button--text md-state chat__more" data-act="more">show ${CHAT_PAGE} more</button>`
          : " — as far back as this panel reads"}</p>`]
    : [];
  rows.push(...conversationRows(chat.transcript.messages));
  return `<div class="chat">${rows.join("")}</div>`;
}

const toolLines = (tools) => (tools || []).map((tool) => `<span class="tool-line md-mono md-body-small">
      <span class="tool-line__name">${escapeHtml(tool.name)}</span>
      <span class="tool-line__detail">${linkPaths(escapeHtml(tool.detail || ""))}</span></span>${changeBlock(tool)}${agentBlock(tool)}`).join("");

/* The conversation itself: every message as a bubble, every tool-only turn as a
   row of activity. Pulled out of the panel because a subagent's conversation
   arrives in the same shape and is drawn by the same code — see views/subagent.ts. */
export function conversationRows(messages, sidechain = false) {
  const rows = [];
  for (const message of messages) {
    // Tool-only turns are activity, not speech — keep them out of bubbles so the
    // actual conversation stays readable.
    if (!message.text && (message.tools || []).length) {
      // Quotable like a bubble is: "this command was wrong" is a thing worth
      // saying, and the tool line is the only place it is written down. A tool
      // call is the session's own doing, so it is attributed the same as its
      // speech.
      rows.push(`<div class="activity-row" data-who="claude" data-at="${escapeHtml(clockOf(message.at))}"
          data-key="${escapeHtml(messageKey(message))}">
          <span class="activity-row__time md-label-small md-mono">${escapeHtml(clockOf(message.at))}</span>
          <span class="activity-row__tools">${toolLines(message.tools)}</span>
          ${moreButton()}
        </div>`);
      continue;
    }
    if (!message.text) continue;
    // A message that came in over the socket says so: sent from this composer it
    // reads "you, from here", and sent by another session it carries that
    // session's name — because the receiving Claude was told the same thing.
    // In a subagent's transcript nothing on the user's side is yours: it is the
    // errand the agent was handed and the results it was fed back. Saying "you"
    // over those is the one thing in this pane that would be a lie.
    const who = message.role !== "user" ? (sidechain ? "the agent" : "claude")
      : sidechain ? "sent to it"
      : message.from === undefined ? "you"
      : message.from ? `${escapeHtml(message.from)} <span class="meta-sep">·</span> another session`
      : "you <span class=\"meta-sep\">·</span> from here";
    // The same thing in plain text, for the attribution line a quote carries
    // back to the session. "another session" is a distinction for the reader
    // here, not for the Claude being quoted at — what it needs is whose words
    // these were, and it only ever wrote its own.
    const whoPlain = message.role !== "user" ? "claude"
      : sidechain ? "sent to it" : message.from || "you";
    const tools = toolLines(message.tools);
    rows.push(`<div class="msg msg--${message.role === "user" ? "user" : "assistant"}"
        data-who="${escapeHtml(whoPlain)}" data-at="${escapeHtml(clockOf(message.at))}"
        data-key="${escapeHtml(messageKey(message))}">
        <div class="msg__who"><span class="md-label-small">${who}</span><span class="md-label-small md-mono">${escapeHtml(clockOf(message.at))}</span>${moreButton()}</div>
        <div class="msg__text md-body-medium">${renderMarkdown(message.text)}</div>
        ${tools ? `<div class="msg__tools">${tools}</div>` : ""}
      </div>`);
  }
  return rows;
}
