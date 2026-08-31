/* One subagent's conversation: the work a Task call stands for.

   A Task call is the only tool whose work happened somewhere else. In the
   conversation it reads as a prompt and then nothing — the row says an agent was
   sent off and the next thing you see is Claude carrying on, with everything the
   agent actually did missing from between. This is that middle.

   Built the way a change is (see views/change.ts): folded, it is a line on the
   tool row saying who was sent and whether they are back. Opened, it takes the
   pane, because a conversation squeezed into the width of a tool row is not a
   conversation you can read. */

import { refreshDetail } from "../refresh.js";
import { chat } from "../state.js";
import { detailPane } from "../ui/dom.js";
import { escapeHtml } from "../ui/format.js";
import { ICON } from "../ui/icons.js";
import { showSnackbar } from "../ui/snackbar.js";

const agentFull = new Map();      // agentId -> the whole conversation, once fetched
export const agentBusy = new Set();

const WORD = {
  running: "working",
  done: "reported back",
  stopped: "stopped without reporting",
};

/* What the pane is showing, for whoever renders the messages: the panel's body
   is the conversation renderer's job, and it lives with the conversation. */
export function agentShownFull() {
  return agentFull.get(chat.agentShown) || null;
}

/* Folded, on the tool row. The type and the state, which is what you want while
   you are reading past it: who was sent, and whether they are back yet. */
export function agentBlock(tool) {
  const agent = tool.agent;
  if (!agent) return "";
  const hint = agentBusy.has(agent.agentId) ? "reading what it did…"
    : "read what it did";
  return `<div class="agent" data-agent="${escapeHtml(agent.agentId)}"
      data-state="${escapeHtml(agent.state)}">
      <button class="agent__bar md-state" type="button" data-act="subagent"
        data-id="${escapeHtml(agent.agentId)}">
        <span class="agent__type md-label-small md-mono">${escapeHtml(agent.agentType)}</span>
        <span class="agent__state md-label-small">${escapeHtml(WORD[agent.state] || agent.state)}</span>
        <span class="agent__hint md-label-small">${escapeHtml(hint)}</span>
      </button>
    </div>`;
}

/* Opened, it takes the pane, for the reason a change does: the way back goes
   where the way back goes, and the conversation stays exactly where you left it.

   The messages are drawn by whatever draws the conversation — a subagent's
   conversation is a conversation, and it arrives in the same shape — so they are
   handed in already rendered rather than rendered a second way here. */
export function agentPanel(body) {
  const full = agentShownFull();
  if (!full) {
    return `<div class="agent-panel">
      <div class="agent-panel__head">
        <button class="button button--text md-state agent-panel__back" type="button" data-act="agent-close">
          ${ICON.back}Conversation
        </button>
      </div>
      <p class="chat__note md-body-medium">${agentBusy.has(chat.agentShown)
        ? "Reading what it did…" : "That subagent could not be read."}</p>
    </div>`;
  }
  const named = [full.agentType, full.model].filter(Boolean).join(" · ");
  return `<div class="agent-panel">
      <div class="agent-panel__head">
        <button class="button button--text md-state agent-panel__back" type="button" data-act="agent-close">
          ${ICON.back}Conversation
        </button>
        <span class="agent-panel__who md-title-small">${escapeHtml(named)}</span>
        <span class="agent-panel__state md-label-small"
          data-state="${escapeHtml(full.state)}">${escapeHtml(WORD[full.state] || full.state)}</span>
        <span class="agent-panel__what md-body-small">${escapeHtml(full.description)}</span>
      </div>
      <div class="agent-panel__body">${body}</div>
    </div>`;
}

/* Opening one, and handing the conversation back afterwards. The scroll
   positions are handled the way a change handles them, and for the same reason:
   the pane is one scroller, so taking it over inherits wherever the conversation
   was, and handing it back inherits wherever the agent's was.

   A finished agent is fetched once and kept — it has nothing left to say. A
   running one is re-read every time it is opened, and again on the poll, because
   its conversation is still being written. */
export async function showAgent(id, session) {
  const scroller = detailPane.querySelector("#chatScroll");
  chat.chatReturn = scroller ? scroller.scrollTop : 0;
  chat.agentShown = id;
  const settle = (top = 0) => {
    refreshDetail(true);
    const now = detailPane.querySelector("#chatScroll");
    if (now) now.scrollTop = top;
  };
  const held = agentFull.get(id);
  if (held && held.state !== "running") { settle(); return; }
  agentBusy.add(id);
  settle();
  await readAgent(id, session);
  if (chat.agentShown === id) settle();
}

/* The read itself, shared with the poll: fetch it, keep it, say so if it is
   gone. Nothing here touches the scroll, so the poll can call it while you read
   without moving the page under you. */
async function readAgent(id, session) {
  try {
    const response = await fetch(
      `/api/subagent?sessionId=${encodeURIComponent(session.sessionId)}&agentId=${encodeURIComponent(id)}`,
      { cache: "no-store" });
    const found = await response.json().catch(() => ({}));
    if (found.ok) agentFull.set(id, found);
    else showSnackbar(found.message || "That subagent is no longer there");
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    agentBusy.delete(id);
  }
}

/* On the poll, while one is open and still working: its conversation grows
   under you rather than needing to be closed and opened again. A finished one is
   left alone — it is not going to say anything else. */
export async function refreshAgent(session) {
  const id = chat.agentShown;
  if (id === null || agentBusy.has(id)) return;
  const held = agentFull.get(id);
  if (held && held.state !== "running") return;
  const before = held ? held.messages.length : -1;
  await readAgent(id, session);
  const now = agentFull.get(id);
  if (chat.agentShown === id && now && now.messages.length !== before) refreshDetail(true);
}

export function hideAgent() {
  if (chat.agentShown === null) return false;
  chat.agentShown = null;
  refreshDetail(true);
  const scroller = detailPane.querySelector("#chatScroll");
  if (scroller) scroller.scrollTop = chat.chatReturn;
  return true;
}
