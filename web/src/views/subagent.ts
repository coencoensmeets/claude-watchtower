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
  if (isShell(chat.agentShown)) return shellPanel();
  const full = agentShownFull();
  const back = `<button class="button button--text md-state agent-panel__back" type="button"
      data-act="agent-close">${ICON.back}Conversation</button>`;
  if (!full) {
    return `<div class="agent-panel" data-state="unread">
      <div class="agent-panel__head">${back}</div>
      <p class="chat__note md-body-medium">${agentBusy.has(chat.agentShown)
        ? "Reading what it did…" : "That subagent could not be read."}</p>
    </div>`;
  }
  // What it was sent to do is the headline. Its type is how it was staffed —
  // true, and worth knowing, but not what tells two Explores apart at a glance.
  const said = [full.agentType, full.model,
                full.spawnDepth > 1 ? "sent by another agent" : "",
                full.messages.length === 1 ? "1 message"
                  : `${full.messages.length} messages`]
    .filter(Boolean).map(escapeHtml).join(" · ");
  return `<div class="agent-panel" data-state="${escapeHtml(full.state)}">
      <div class="agent-panel__head">
        <div class="agent-panel__bar">
          ${back}
          <span class="agent-panel__state md-label-small">
            <span class="agent-panel__lamp" aria-hidden="true"></span>
            ${escapeHtml(WORD[full.state] || full.state)}</span>
        </div>
        <p class="agent-panel__what md-title-small">${escapeHtml(full.description)}</p>
        <p class="agent-panel__meta md-label-small">${said}</p>
      </div>
      <div class="agent-panel__body">${body}${full.state === "running"
        ? `<p class="agent-panel__still md-label-small">
             <span class="agent-panel__dots" aria-hidden="true"><i></i><i></i><i></i></span>
             still working — this conversation is still being written</p>` : ""}</div>
    </div>`;
}

/* The agents a session has out right now, over the box you type in.

   A running subagent is the one thing the session is doing that the
   conversation cannot show you: the Task row that started it is however far
   back you have scrolled, and what the agent is doing is in a file of its own.
   So the ones still out get a strip of their own, at the edge of the pane
   nearest your hands, and each chip opens what it stands for.

   Only the running ones, and only ever a few. A finished agent is reachable
   from the row that started it, and a session that has fanned out twenty would
   otherwise bury the composer under them — see AGENT_DOCK_MAX. */
export function agentDock(session) {
  const live = session.agents?.live || [];
  const shells = session.shells?.live || [];
  if (!live.length && !shells.length) return "";
  const chips = live.map((agent) => `<button class="agent-chip md-state" type="button"
      data-act="subagent" data-id="${escapeHtml(agent.agentId)}"
      title="${escapeHtml(agent.description || agent.agentType)} — read what it is doing">
      <span class="agent-chip__lamp" aria-hidden="true"></span>
      <span class="agent-chip__type md-label-small md-mono">${escapeHtml(agent.agentType)}</span>
      <span class="agent-chip__what md-label-small">${escapeHtml(agent.description)}</span>
    </button>`).join("") + shells.map((shell) => `<button class="agent-chip agent-chip--shell md-state"
      type="button" data-act="subagent" data-id="${escapeHtml(SHELL + shell.taskId)}"
      title="${escapeHtml(shell.command || shell.description)} — read its output">
      <span class="agent-chip__lamp" aria-hidden="true"></span>
      <span class="agent-chip__type md-label-small md-mono">shell</span>
      <span class="agent-chip__what md-label-small">${escapeHtml(shell.description || shell.command)}</span>
    </button>`).join("");
  // The head counts what is out, which is not always what is drawn: past the
  // cap the chips are a sample, and the count is the truth.
  const running = session.agents?.running || live.length;
  const commands = session.shells?.running || shells.length;
  const head = [running ? (running === 1 ? "1 agent out" : `${running} agents out`) : "",
                commands ? `${commands} in background` : ""].filter(Boolean).join(" · ");
  return `<div class="agent-dock" data-count="${live.length + shells.length}">
      <span class="agent-dock__head md-label-small">${head}</span>
      <div class="agent-dock__chips">${chips}</div>
    </div>`;
}

/* A command the session left running in the background, opened from its chip.

   Shells share the agent pane rather than having one of their own: the chip
   opens `shell:<taskId>` through showAgent, so the back button, the tab switch
   and the poll that keeps an open agent current all work for a shell as they
   do for an agent. What differs is only what is drawn — the tail of the file
   the command writes to, instead of a conversation. */
const SHELL = "shell:";
const shellFull = new Map();      // "shell:<taskId>" -> the last read of its output

function isShell(id) {
  return typeof id === "string" && id.startsWith(SHELL);
}

function shellOf(session, id) {
  return (session?.shells?.live || []).find((shell) => SHELL + shell.taskId === id) || null;
}

function shellPanel() {
  const id = chat.agentShown;
  const full = shellFull.get(id);
  const back = `<button class="button button--text md-state agent-panel__back" type="button"
      data-act="agent-close">${ICON.back}Conversation</button>`;
  if (!full) {
    return `<div class="agent-panel" data-state="unread">
      <div class="agent-panel__head">${back}</div>
      <p class="chat__note md-body-medium">${agentBusy.has(id)
        ? "Reading its output…" : "That command's output could not be read."}</p>
    </div>`;
  }
  const shell = full.shell || {};
  const state = full.running ? "running" : "done";
  const said = [shell.timedOut ? "moved to the background when it ran past its timeout" : "",
                full.size ? `${full.size.toLocaleString()} bytes written` : "nothing written yet",
                full.clipped ? "showing the end" : ""]
    .filter(Boolean).map(escapeHtml).join(" · ");
  return `<div class="agent-panel agent-panel--shell" data-state="${state}">
      <div class="agent-panel__head">
        <div class="agent-panel__bar">
          ${back}
          <span class="agent-panel__state md-label-small">
            <span class="agent-panel__lamp" aria-hidden="true"></span>
            ${full.running ? "running in the background" : "no longer running"}</span>
        </div>
        <p class="agent-panel__what md-title-small">${escapeHtml(shell.description || "Background command")}</p>
        <p class="agent-panel__meta md-label-small">${said}</p>
      </div>
      <div class="agent-panel__body">
        ${shell.command ? `<pre class="shell-panel__command md-mono">${escapeHtml(shell.command)}</pre>` : ""}
        <pre class="shell-panel__output md-mono">${full.output
          ? escapeHtml(full.output) : `<span class="shell-panel__empty">No output yet.</span>`}</pre>
        ${full.running ? `<p class="agent-panel__still md-label-small">
             <span class="agent-panel__dots" aria-hidden="true"><i></i><i></i><i></i></span>
             still running — the output is read again as it grows</p>` : ""}
      </div>
    </div>`;
}

/* The read, for a shell: the tail of its output, and whether the session still
   has it running — which the poll already knows, so it is taken from there
   rather than asked for again. What it was is kept from the last time it was
   seen running, since a finished command drops out of the session's list. */
async function readShell(id, session) {
  const held = shellFull.get(id);
  const shell = shellOf(session, id);
  try {
    const response = await fetch(
      `/api/shell-output?sessionId=${encodeURIComponent(session.sessionId)}&taskId=${encodeURIComponent(id.slice(SHELL.length))}`,
      { cache: "no-store" });
    const found = await response.json().catch(() => ({}));
    if (found.ok) shellFull.set(id, { ...found, shell: shell || held?.shell, running: !!shell });
    else showSnackbar(found.message || "That command's output is no longer there");
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    agentBusy.delete(id);
  }
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
  if (isShell(id)) {
    agentBusy.add(id);
    settle();
    await readShell(id, session);
    if (chat.agentShown === id) settle(Number.MAX_SAFE_INTEGER);
    return;
  }
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
  if (isShell(id)) {
    const before = shellFull.get(id);
    if (before && !before.running) return;
    const scroller = detailPane.querySelector("#chatScroll");
    // Following the end of the output, the way a terminal does — unless you
    // have scrolled up to read something, in which case it stays put.
    const pinned = !scroller || scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 40;
    const top = scroller ? scroller.scrollTop : 0;
    await readShell(id, session);
    const now = shellFull.get(id);
    if (chat.agentShown !== id || !now) return;
    if (before && now.size === before.size && now.running === before.running) return;
    refreshDetail(true);
    const after = detailPane.querySelector("#chatScroll");
    if (after) after.scrollTop = pinned ? after.scrollHeight : top;
    return;
  }
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
