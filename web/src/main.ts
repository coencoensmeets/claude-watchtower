/* The panel: what is polled, what is drawn, and what the chrome does.

   The orchestrator. It owns the render loop and the poll, hands both to
   refresh.ts at boot so no other module has to know where they live, and holds
   the parts that are about the app as a whole — the index and its groups, the
   composer, the session menu, the dialogs, and boot. */

import { Hct, SchemeVibrant, TonalPalette, argbFromHex, hexFromArgb } from "/vendor/material-color-utilities.js";
import { run } from "./net.js";
import { isAmbiguous, windowSays } from "./sessions/facts.js";
import { ASK_ICON, ASK_WORD, STATE, STATE_ORDER, displaySince, drawnStateOf, standingAsk, stateKeyOf } from "./sessions/state.js";
import type { StateKey } from "./sessions/state.js";
import { CHAT_LIMIT_MAX, CHAT_PAGE, app, chat, loadKeySet, mutedSessions, quietWhenDone, readJson, repo, sayDrafts, selected, sessionById, sidebar, spend, ui } from "./state.js";
import { askConfirm, askScrim, closeAsk } from "./ui/ask.js";
import { backButton, barNudge, barNudgeIcon, barNudgeLink, barNudgeText, barSupporting, chipSet, control, detailPane, endScrim, hitClosest, hitElement, listEmpty, panes, pickBar, pickClear, pickCount, pickGroup, sessionList, settingsButton } from "./ui/dom.js";
import { duration, escapeHtml, shorten, tokens } from "./ui/format.js";
import { ICON, hostOf } from "./ui/icons.js";
import { attachPicture, dropImage, imagesStamp, picturesOn, sendMessage } from "./ui/images.js";
import { wireDrop } from "./ui/dropped.js";
import { closeSessionMenu, menuIsOpen, openMenu, sessionMenu } from "./ui/menu.js";
import { onLongPress } from "./ui/press.js";
import type { MenuItem } from "./ui/menu.js";
import { announce, paintFavicon } from "./ui/notify.js";
import { conceal, reveal } from "./ui/overlay.js";
import { paintSettings, syncTheme } from "./ui/settings.js";
import { copyText } from "./ui/clipboard.js";
import { showSnackbar } from "./ui/snackbar.js";
import { CONTRAST_LEVELS, MAX_CUSTOM_CHROMA, NOTIFY_KINDS, STATE_BASE_HUES, SYS_ROLES, customRoles, firstFreeHue, kebab } from "./ui/theme.js";
import { changeBusy, chatPanel, hideChange, showChange } from "./views/change.js";
import { IDENTIFY_NOTE, commentIsOpen, markCommented, paintTrace, questionCard, renderRail, startRename, wireTrace } from "./views/chat.js";
import { closeDiff, fetchGit, gitPanel, gitStamp, historyPanel, wireGit } from "./views/git.js";
import { askPicksFor, compactPct, composer, detailHeader, ownedFor, pickMode, runsHere, sendAskAnswer } from "./views/owned.js";
import { fetchPlan, openPlan, planScrim } from "./views/plan.js";
import { fetchUpdate, openUpdate, updateScrim } from "./views/update.js";
import { aboutPanel, usagePanel } from "./views/usage.js";
import "./ui/markdown.js";
/* Loaded for its own sake: it wires the message context menu — download or copy
   a message as markdown — onto the detail pane and exports nothing. */
import "./views/save.js";
import { serveRefresh } from "./refresh.js";

function loadSettings() {
  const params = new URLSearchParams(location.search);
  const seed = params.get("seed") || localStorage.getItem("cbu-seed");
  if (seed && /^#[0-9a-f]{6}$/i.test(seed)) app.settings.seed = seed;
  const theme = params.get("theme") || localStorage.getItem("cbu-theme");
  if (theme === "dark" || theme === "light") app.settings.dark = theme === "dark";
  const contrast = params.get("contrast") || localStorage.getItem("cbu-contrast");
  if (CONTRAST_LEVELS.some((l) => l.key === contrast)) app.settings.contrast = contrast;
  // Read key by key rather than taken whole: a kind added in a later version
  // should arrive switched on rather than undefined for everyone who has
  // already saved this.
  const editor = localStorage.getItem("cbu-editor-button");
  if (editor === "off" || editor === "on") app.settings.showEditor = editor === "on";
  const notify = readJson("cbu-notify", null);
  if (notify && typeof notify === "object") {
    for (const kind of NOTIFY_KINDS) {
      if (typeof notify[kind.key] === "boolean") app.settings.notify[kind.key] = notify[kind.key];
    }
  }
}
export function persist() {
  localStorage.setItem("cbu-seed", app.settings.seed);
  localStorage.setItem("cbu-theme", app.settings.dark ? "dark" : "light");
  localStorage.setItem("cbu-contrast", app.settings.contrast);
  localStorage.setItem("cbu-notify", JSON.stringify(app.settings.notify));
  localStorage.setItem("cbu-editor-button", app.settings.showEditor ? "on" : "off");
}

export function applyScheme() {
  const root = document.documentElement;
  const seedArgb = argbFromHex(app.settings.seed);
  const level = CONTRAST_LEVELS.find((l) => l.key === app.settings.contrast) || CONTRAST_LEVELS[0];
  const scheme = new SchemeVibrant(Hct.fromInt(seedArgb), app.settings.dark, level.value);
  for (const role of SYS_ROLES) {
    if (scheme[role] === undefined) continue;
    root.style.setProperty(`--md-sys-color-${kebab(role)}`, hexFromArgb(scheme[role]));
  }
  const occupied = [Hct.fromInt(scheme.primary).hue];
  for (const [name, baseHex] of Object.entries(STATE_BASE_HUES)) {
    const base = Hct.fromInt(argbFromHex(baseHex));
    const hue = firstFreeHue(base.hue, occupied);
    occupied.push(hue);
    const palette = TonalPalette.fromHueAndChroma(hue, Math.min(base.chroma, MAX_CUSTOM_CHROMA));
    for (const [role, argb] of Object.entries(customRoles(palette, app.settings.dark))) {
      root.style.setProperty(`--md-extended-color-${name}-${kebab(role)}`, hexFromArgb(argb));
    }
  }
  root.style.colorScheme = app.settings.dark ? "dark" : "light";
  root.dataset.schemeReady = "true";
  // The switch lives on the settings page, which is only sometimes on screen —
  // and the scheme can change from under it, so it follows rather than leads.
  syncTheme();
  paintFavicon();
}

/* ==========================================================================
   Grouping the index.

   Two kinds of group, and they mean different things. A *folder* group is not
   stored at all: it is what the list looks like when two or more sessions share
   a working folder, which is the shape most people's sessions already have —
   several Claudes in one repository. A group you make yourself, by picking rows
   and pressing Group, is stored, wins over the folder grouping for the rows in
   it, and keeps its own name.

   All of it is a view of the list, so it lives in this browser rather than on
   the server, the same way muting does.
   ========================================================================== */
function loadGroups() {
  try {
    const raw = JSON.parse(localStorage.getItem("cbu-groups") || "[]");
    if (!Array.isArray(raw)) return [];
    return raw
      .filter((g) => g && typeof g.id === "string" && Array.isArray(g.members))
      .map((g) => ({
        id: g.id,
        name: typeof g.name === "string" && g.name.trim() ? g.name : "Group",
        members: g.members.filter((id) => typeof id === "string"),
        collapsed: !!g.collapsed,
      }))
      .filter((g) => g.members.length);
  } catch (error) {
    return [];
  }
}
let customGroups = loadGroups();
const saveGroups = () => localStorage.setItem("cbu-groups", JSON.stringify(customGroups));
let looseFolders = loadKeySet("cbu-loose-folders");
let collapsedFolders = loadKeySet("cbu-collapsed-folders");
const saveKeySet = (key, set) => localStorage.setItem(key, JSON.stringify([...set]));

/* The rows ticked for a group action, the row a shift-click measures from, and
   the ids the list is showing in the order it draws them — which is what makes a
   shift-click select the run you can actually see. */
let picked = new Set();
let pickAnchor = null;
let visibleOrder = [];
/* The blocks the list is showing, so a menu opened from the keyboard can find
   the group its header belongs to. */
let lastBlocks = [];
/* The group whose name is being typed, so a poll cannot rebuild the list out
   from under the field. */
let renamingGroup = null;

const groupOf = (id) => customGroups.find((g) => g.members.includes(id)) || null;
const folderKeyOf = (session) => session.cwd || session.folder || "";

/* The one order rows are kept in when state is not deciding: when the session
   started, then its id so the comparison is never a tie. */
const bySessionIdentity = (a, b) =>
  (a.startedAt || 0) - (b.startedAt || 0) || (a.sessionId < b.sessionId ? -1 : a.sessionId > b.sessionId ? 1 : 0);

/* ==========================================================================
   The order you put the rows in.

   The server sorts by state — anything waiting on you first, then what is
   working — and that is the right answer until you disagree with it. Dragging a
   row says you do: from the first drag on, the order on screen is yours, and a
   session going busy and idle again no longer moves it.

   Held as a list of ids rather than a number on each row, because the thing
   being remembered is a sequence and a sequence stays consistent when it is
   stored as one. Like the grouping, it is a view of the list rather than
   something the sessions carry, so it lives in this browser.

   A session the arrangement has never seen — one started since you last dragged
   anything — is drawn above it rather than at the bottom: it is the new thing on
   the list, and the point of the state sort was that new work is what you look
   at first. It takes its place in the arrangement the next time you drag.
   ========================================================================== */
let manualOrder = (() => {
  const raw = readJson("cbu-order", []);
  return Array.isArray(raw) ? raw.filter((id) => typeof id === "string") : [];
})();
/* The same list as a lookup, because every comparison in a sort asks it. */
const orderRank = new Map();
const indexOrder = () => {
  orderRank.clear();
  manualOrder.forEach((id, at) => orderRank.set(id, at));
};
indexOrder();
const saveOrder = () => {
  indexOrder();
  localStorage.setItem("cbu-order", JSON.stringify(manualOrder));
};

/* The sessions in the order the list should draw them: the server's, until you
   have arranged them. Stable — two rows the arrangement says nothing about keep
   the order the server sent them in. */
function arrange(sessions) {
  if (!manualOrder.length) return sessions;
  return sessions
    .map((session, at) => ({ session, at }))
    .sort((a, b) => {
      const ra = orderRank.has(a.session.sessionId) ? orderRank.get(a.session.sessionId) : -1;
      const rb = orderRank.has(b.session.sessionId) ? orderRank.get(b.session.sessionId) : -1;
      // -1 for a row the arrangement has not placed, which is what puts it on top.
      return ra === rb ? a.at - b.at : ra - rb;
    })
    .map((entry) => entry.session);
}

/* Inside a group: your order when you have given one, and otherwise the fixed
   order of identity — which is there so a member going busy does not shuffle the
   group under the pointer, and is exactly what an arrangement replaces. */
const byArrangement = (a, b) => {
  const ra = orderRank.get(a.sessionId);
  const rb = orderRank.get(b.sessionId);
  if (ra !== undefined && rb !== undefined) return ra - rb;
  if (ra !== undefined) return 1;
  if (rb !== undefined) return -1;
  return bySessionIdentity(a, b);
};

/* Every session in the order the list would draw it in, groups and all — which
   is what a drag is measured against. Filtered rows included: they are on the
   list you arranged even while you cannot see them. */
const onScreenOrder = () => listBlocks(arrange(app.feed.sessions))
  .flatMap((block) => block.kind === "group"
    ? block.sessions.map((s) => s.sessionId)
    : [block.session.sessionId]);

/* Put a row next to another one. The first drag has nothing to move within, so
   the order on screen becomes the arrangement and the move is applied to that.
   The order on screen is the one the blocks are drawn in and not the flat sort
   behind them — a group gathers rows from all over the list, and seeding from
   the sort would rearrange every group on the first drag. Over every session
   rather than the ones a filter is showing, too, or filtering the list would
   forget where the hidden rows sat. */
function moveRow(id, anchorId, after) {
  if (!anchorId || anchorId === id) return;
  const order = onScreenOrder().filter((sid) => sid !== id);
  const at = order.indexOf(anchorId);
  if (at === -1) return;
  order.splice(after ? at + 1 : at, 0, id);
  manualOrder = order;
  saveOrder();
  render();
}

/* Hand the order back to the panel. */
function clearArrangement() {
  if (!manualOrder.length) return;
  manualOrder = [];
  saveOrder();
  showSnackbar("Sorted by state again");
  render();
}

/* What the index draws, top to bottom: bare rows and groups, each group where
   its first member would have been. */
function listBlocks(visible) {
  const perFolder = new Map();
  for (const session of visible) {
    if (groupOf(session.sessionId)) continue;
    const key = folderKeyOf(session);
    perFolder.set(key, (perFolder.get(key) || 0) + 1);
  }
  const blocks = [];
  const open = new Map();
  for (const session of visible) {
    const custom = groupOf(session.sessionId);
    const folder = folderKeyOf(session);
    let key = null;
    if (custom) key = `custom:${custom.id}`;
    // One session in a folder is a row, not a group of one.
    else if (folder && perFolder.get(folder) > 1 && !looseFolders.has(folder)) key = `folder:${folder}`;
    if (!key) {
      blocks.push({ kind: "session", session });
      continue;
    }
    let block = open.get(key);
    if (!block) {
      block = {
        kind: "group", key, custom,
        folder: custom ? null : folder,
        name: custom ? custom.name : (session.folder || folder),
        collapsed: custom ? custom.collapsed : collapsedFolders.has(folder),
        sessions: [],
      };
      open.set(key, block);
      blocks.push(block);
    }
    block.sessions.push(session);
  }
  // A group sits where its most pressing member would have been — that is the
  // one the sorted list reached first — but inside it the rows are held in an
  // order of their own: yours if you have dragged them, and otherwise a fixed
  // one, so a member going busy and idle again does not shuffle the group under
  // the pointer.
  for (const block of blocks) {
    if (block.kind === "group") block.sessions.sort(byArrangement);
  }
  return blocks;
}

function toggleGroup(block) {
  if (block.custom) {
    block.custom.collapsed = !block.custom.collapsed;
    saveGroups();
  } else {
    if (collapsedFolders.has(block.folder)) collapsedFolders.delete(block.folder);
    else collapsedFolders.add(block.folder);
    saveKeySet("cbu-collapsed-folders", collapsedFolders);
  }
  render();
}

/* Picking rows. Ctrl-click ticks one, shift-click ticks the run between it and
   the last row you touched; a plain click goes back to selecting one session. */
function togglePick(id) {
  if (picked.has(id)) picked.delete(id);
  else picked.add(id);
  render();
}

function pickRange(from, to) {
  const a = visibleOrder.indexOf(from);
  const b = visibleOrder.indexOf(to);
  if (a === -1 || b === -1) return togglePick(to);
  for (const id of visibleOrder.slice(Math.min(a, b), Math.max(a, b) + 1)) picked.add(id);
  render();
}

function clearPicked(repaint = true) {
  if (!picked.size) return;
  picked.clear();
  if (repaint) render();
}

function onRowClick(id, event) {
  if (event.ctrlKey || event.metaKey) {
    togglePick(id);
    pickAnchor = id;
    return;
  }
  if (event.shiftKey) {
    pickRange(pickAnchor ?? id, id);
    pickAnchor = id;
    return;
  }
  picked.clear();
  pickAnchor = id;
  selectSession(id);
}

/* Group the picked rows. They leave whatever group they were in — a session
   belongs to one — and the new group takes the folder's name when they share
   one, because that is what you would have called it. */
function groupPicked() {
  const ids = visibleOrder.filter((id) => picked.has(id));
  if (ids.length < 2) return;
  const sessions = ids.map(sessionById).filter(Boolean);
  const folders = new Set(sessions.map((s) => s.folder).filter(Boolean));
  const name = folders.size === 1 ? [...folders][0] : `${ids.length} sessions`;
  for (const group of customGroups) group.members = group.members.filter((id) => !picked.has(id));
  customGroups = customGroups.filter((g) => g.members.length);
  customGroups.unshift({ id: `g${Date.now().toString(36)}`, name, members: ids, collapsed: false });
  saveGroups();
  picked.clear();
  showSnackbar(`Grouped ${ids.length} sessions as “${name}”`);
  render();
}

export function ungroup(block) {
  if (block.custom) {
    customGroups = customGroups.filter((g) => g.id !== block.custom.id);
    saveGroups();
    showSnackbar(`“${block.name}” ungrouped`);
  } else {
    // A folder group is not stored, so leaving it apart is what gets stored.
    looseFolders.add(block.folder);
    saveKeySet("cbu-loose-folders", looseFolders);
    showSnackbar(`${block.name} is no longer grouped by folder`);
  }
  render();
}

function leaveGroup(id) {
  const group = groupOf(id);
  if (!group) return;
  group.members = group.members.filter((member) => member !== id);
  customGroups = customGroups.filter((g) => g.members.length);
  saveGroups();
  showSnackbar("Taken out of the group");
  render();
}

function regroupFolders() {
  looseFolders.clear();
  saveKeySet("cbu-loose-folders", looseFolders);
  render();
}

function syncPickBar() {
  const count = picked.size;
  pickBar.hidden = count === 0;
  pickCount.textContent = count
    ? `${count} picked${count < 2 ? " — ctrl-click another" : ""}`
    : "";
  pickGroup.disabled = count < 2;
}

/* ------------------------------------------------------------------ transport */
async function poll() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    app.skew = data.now - Date.now() / 1000;
    app.feed = data;
    app.lastGood = Date.now();
    announce(data.sessions);
    render();
    if (app.selectedId && (chat.transcriptFor !== app.selectedId || app.tab === "chat")) fetchTranscript();
    if (app.selectedId && isGitTab(app.tab)) fetchGit();
    if (app.selectedId && app.tab === "usage") fetchUsage();
  } catch (error) {
    barSupporting.textContent = "lost the server — retrying";
    // The counts on screen are stale, so a nudge based on them is a guess.
    barNudge.hidden = true;
  }
}

async function fetchTranscript() {
  const id = app.selectedId;
  if (!id || chat.transcriptBusy) return;
  if (app.tab !== "chat" && chat.transcriptFor === id) return;
  chat.transcriptBusy = true;
  try {
    const asked = chat.chatLimit;
    const response = await fetch(`/api/transcript?sessionId=${encodeURIComponent(id)}&limit=${asked}`, { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    if (app.selectedId !== id) return;             // selection moved on while fetching
    // Enough of the transcript to notice a new message, or the last one growing.
    // Rebuilding on every poll instead — which "always redraw the chat tab" came
    // down to — throws the pane away a second at a time, under the pointer.
    const stamp = (t) => {
      const last = t?.messages?.[t.messages.length - 1];
      return [t?.title ?? "", t?.messages?.length ?? -1, last?.at ?? "",
              last?.text?.length ?? 0, last?.tools?.length ?? 0].join("|");
    };
    const changed = stamp(data) !== stamp(chat.transcript) || chat.transcriptFor !== id;
    chat.transcript = data;
    chat.transcriptFor = id;
    if (changed) renderDetail(true);
  } catch (error) {
    /* leave the previous transcript on screen */
  } finally {
    chat.transcriptBusy = false;
  }
}

/* Another page of the conversation. The reading itself is the server's — this
   only raises what is asked for, and marks the re-render as a growth so the pane
   holds its place. A fetch already in flight is not waited on: the chat tab
   re-fetches on the next poll a moment later, now at the larger limit. */
function showMoreChat(event) {
  const button = event.currentTarget;
  if (chat.chatLimit >= CHAT_LIMIT_MAX) return;
  chat.chatLimit = Math.min(CHAT_LIMIT_MAX, chat.chatLimit + CHAT_PAGE);
  chat.chatGrew = true;
  button.disabled = true;
  button.textContent = "reading…";
  fetchTranscript();
}

/* Everything the Git tab draws, in one string. Same reasoning as the
   transcript's: re-rendering on every poll throws the pane away under the
   pointer, so the panel only redraws when the repository actually moved. */
/* Both git tabs are drawn from the same reading, so they fetch and fall back
   together. */
const isGitTab = (name: string) => name === "git" || name === "history";

/* Tokens and cost. Cheaper to read than the repository — the server scans the
   transcript once and picks up where it stopped — but there is no point reading
   it while another tab is showing, so it runs on the poll only when its own tab
   is up, and once immediately when you open it. */
function usageStamp(u) {
  return [u?.cost ?? -1, u?.totals?.requests ?? -1, u?.context ?? -1,
          (u?.models ?? []).length, (u?.agentModels ?? []).length].join("|");
}

async function fetchUsage(force = false) {
  const id = app.selectedId;
  if (!id || spend.usageBusy) return;
  if (!force && document.hidden) return;
  spend.usageBusy = true;
  try {
    const response = await fetch(`/api/usage?sessionId=${encodeURIComponent(id)}`, { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    if (app.selectedId !== id) return;             // selection moved on while fetching
    const changed = usageStamp(data) !== usageStamp(spend.usage) || spend.usageFor !== id;
    spend.usage = data;
    spend.usageFor = id;
    if (changed) renderDetail(true);
  } catch (error) {
    /* leave the previous reading on screen */
  } finally {
    spend.usageBusy = false;
  }
}

/* ------------------------------------------------------------------ nudges */
/* Every session is working and none of them is asking anything: there is
   nothing here for you to do for a while, so the bar says so. The list is
   deliberately mundane — a nudge away from the screen, not a fortune cookie. */
/* Short enough to sit in the bar whole: the pill is one nowrap line, so a
   sentence with a preamble on it just gets ellipsised. The preamble was the
   part carrying no information anyway — the pill only appears when every
   session is busy. */
/* Each line carries its own mark. One icon for ten different messages read as
   decoration; the drop only means anything on the line about the water. The
   strokes match the rest of the panel's icons: 24-box, round caps, no fill. */
const nudgeIcon = (paths) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
const NUDGES = [
  { text: "Go drink some water", icon: nudgeIcon('<path d="M12 3s5.5 6 5.5 10a5.5 5.5 0 0 1-11 0C6.5 9 12 3 12 3z"/>') },
  // A line stretched from both ends.
  { text: "Stand up and stretch", icon: nudgeIcon('<path d="M12 3.5v17"/><path d="M8.5 7 12 3.5 15.5 7"/><path d="M8.5 17 12 20.5 15.5 17"/>') },
  { text: "Rest your eyes on the far wall", icon: nudgeIcon('<path d="M2.5 12S6 6.5 12 6.5 21.5 12 21.5 12 18 17.5 12 17.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.4"/>') },
  // A tumbler with a waterline, next to the drop that means drinking.
  { text: "Refill your glass", icon: nudgeIcon('<path d="M6.5 4h11l-1.1 15.2a2 2 0 0 1-2 1.8h-4.8a2 2 0 0 1-2-1.8z"/><path d="M7.1 11.5h9.8"/>') },
  // Once round the room: the loop, with the arrowhead saying it is a walk.
  { text: "Take a lap around the room", icon: nudgeIcon('<path d="M20.5 12a8.5 8.5 0 1 1-3.4-6.8"/><path d="M20.5 4v5h-5"/>') },
  { text: "Unclench your jaw", icon: nudgeIcon('<circle cx="12" cy="12" r="9"/><path d="M8.5 14.2s1.2 1.8 3.5 1.8 3.5-1.8 3.5-1.8"/><path d="M9 9.5h.01M15 9.5h.01"/>') },
  { text: "Go get a snack", icon: nudgeIcon('<path d="M12 8.5c-2.9 0-5 2.2-5 5.5s2.4 7 5 7 5-3.7 5-7-2.1-5.5-5-5.5z"/><path d="M12 8.5c0-2 1.5-3.6 3.5-3.8"/>') },
  // Head and shoulders, since the shoulders are the point.
  { text: "Roll your shoulders back", icon: nudgeIcon('<circle cx="12" cy="7" r="3.2"/><path d="M4.5 20c1.3-3.7 4.1-5.8 7.5-5.8s6.2 2.1 7.5 5.8"/>') },
  { text: "Look out of a window", icon: nudgeIcon('<rect x="4" y="4" width="16" height="16" rx="1.5"/><path d="M12 4v16M4 12h16"/>') },
  // Straight up off a floor, rather than the stretch's pull in both directions.
  { text: "Sit up straight", icon: nudgeIcon('<path d="M5 20.5h14"/><path d="M12 17.5V5"/><path d="M8.5 8.5 12 5l3.5 3.5"/>') },
  // The one line that is not a nudge away from the screen, so it is the only one
  // that goes anywhere: a phone on its side playing something, and a click opens
  // the reels. New tab — the panel is a live view, and navigating away from it
  // would drop the stream.
  { text: "Watch some reels", icon: nudgeIcon('<rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M10.8 9.6v4.8l4-2.4z"/>'), href: "https://www.instagram.com/reels/" },
];
// How long one message stays up before the next one takes over.
const NUDGE_ROTATE_MS = 90_000;
let nudgeIndex = Math.floor(Math.random() * NUDGES.length);
let nudgeShownAt = 0;

/* `counts` is the same tally the chips are drawn from. */
function renderNudge(counts: Record<string, number>) {
  const busy = counts.busy || 0;
  // Anything that is neither working nor put away is something you could be
  // attending to, so the nudge would be a lie.
  const yours = (counts.waiting || 0) + (counts.idle || 0) + (counts.here || 0);
  if (!busy || yours) {
    barNudge.hidden = true;
    nudgeShownAt = 0;
    return;
  }
  const now = Date.now();
  // A fresh message each time the bar falls quiet, then a new one every so
  // often while it stays quiet — the same line for an hour stops being read.
  // Stepping through the list in order made the sequence learnable, and a
  // learnt sequence is read as wallpaper; the next one is drawn at random from
  // the others instead, so it is never the line already on screen.
  if (!nudgeShownAt || now - nudgeShownAt >= NUDGE_ROTATE_MS) {
    if (nudgeShownAt) nudgeIndex = (nudgeIndex + 1 + Math.floor(Math.random() * (NUDGES.length - 1))) % NUDGES.length;
    nudgeShownAt = now;
  }
  const { text, icon, href } = NUDGES[nudgeIndex];
  if (barNudgeText.textContent !== text) barNudgeText.textContent = text;
  if (barNudgeIcon.innerHTML !== icon) barNudgeIcon.innerHTML = icon;
  // The href is what makes the line clickable at all — the styling, the focus
  // ring and the pointer all hang off `a[href]` — so it is removed again for the
  // messages that lead nowhere rather than left over from the last one.
  if (href) {
    barNudgeLink.href = href;
    barNudgeLink.target = "_blank";
    barNudgeLink.rel = "noopener noreferrer";
  } else {
    barNudgeLink.removeAttribute("href");
    barNudgeLink.removeAttribute("target");
    barNudgeLink.removeAttribute("rel");
  }
  barNudge.hidden = false;
  barNudge.title = href ? `${text} — opens Instagram` : text;
}

/* --------------------------------------------------------------- rendering */
function render() {
  const counts: Record<string, number> = {};
  for (const session of app.feed.sessions) {
    const key = stateKeyOf(session.status);
    counts[key] = (counts[key] || 0) + 1;
  }
  if (app.filter !== "all" && !counts[app.filter]) app.filter = "all";

  // A kept session with no process is on the list but is not running, so it is
  // counted apart from the live ones rather than inflating them.
  const stopped = counts.stopped || 0;
  const total = app.feed.sessions.length - stopped;
  const waiting = counts.waiting || 0;
  const parts = [];
  if (total) parts.push(`${total} live`);
  if (waiting) parts.push(`${waiting} needs an answer`);
  if (stopped) parts.push(`${stopped} kept`);
  // Counts the blocked sessions only — the ones that cannot go on without you.
  barSupporting.textContent = parts.length ? parts.join(" · ") : "nothing running";
  document.title = waiting ? `(${waiting}) Claude sessions` : "Claude sessions";

  // Keep a valid selection: fall back to the top of the list.
  const ordered = arrange(app.feed.sessions);
  const visible = app.filter === "all" ? ordered : ordered.filter((s) => stateKeyOf(s.status) === app.filter);
  if (!visible.some((s) => s.sessionId === app.selectedId)) {
    app.selectedId = visible.length ? visible[0].sessionId : null;
    if (app.selectedId) localStorage.setItem("cbu-selected", app.selectedId);
  }

  renderNudge(counts);
  renderChips(counts);
  renderList(visible);
  renderDetail(false);
  paintFavicon(counts);
}

function renderChips(counts) {
  const entries = [{ key: "all", label: `all ${app.feed.sessions.length}` }];
  for (const key of STATE_ORDER) if (counts[key]) entries.push({ key, label: `${counts[key]} ${STATE[key].short}` });
  const signature = entries.map((e) => e.key + e.label).join("|") + app.filter;
  if (chipSet.dataset.signature === signature) return;
  chipSet.dataset.signature = signature;
  chipSet.innerHTML = "";
  for (const entry of entries) {
    const item = document.createElement("li");
    const chip = document.createElement("button");
    chip.className = "chip md-state md-label-large";
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(app.filter === entry.key));
    if (entry.key !== "all") chip.style.setProperty("--chip-dot", STATE[entry.key].colour);
    chip.innerHTML = `<span class="chip__check">${ICON.check}</span>${entry.key === "all" ? "" : '<span class="chip__dot"></span>'}<span>${escapeHtml(entry.label)}</span>`;
    chip.addEventListener("click", () => { app.filter = app.filter === entry.key ? "all" : entry.key; render(); });
    item.appendChild(chip);
    chipSet.appendChild(item);
  }
}

function renderList(visible) {
  // A drag holds the list still. Rebuilding a row takes it out from under the
  // pointer, which cancels the drag; the poll that wanted the rebuild is a
  // second old, and dropping redraws everything anyway.
  if (dragging) return;
  listEmpty.hidden = visible.length > 0;
  // A menu whose row is leaving the list — ended, or filtered out — has nothing
  // left to act on.
  if (ui.menuFor && !visible.some((s) => s.sessionId === ui.menuFor)) closeSessionMenu({ restoreFocus: false });
  // Rows that have left the list cannot be part of a pick any more.
  const here = new Set(visible.map((s) => s.sessionId));
  for (const id of [...picked]) if (!here.has(id)) picked.delete(id);
  if (!visible.length) {
    listEmpty.textContent = app.feed.sessions.length
      ? "Nothing in this state. Pick “all” above."
      : "No sessions are running. Start one with claude in any terminal.";
    sessionList.innerHTML = "";
    sessionList.dataset.layout = "";
    visibleOrder = [];
    lastBlocks = [];
    syncPickBar();
    return;
  }

  const blocks = listBlocks(visible);
  lastBlocks = blocks;
  // A group menu whose group has gone — ungrouped, or its rows filtered out —
  // has nothing left to act on either.
  if (ui.menuGroup && !blocks.some((block) => block.key === ui.menuGroup)) {
    closeSessionMenu({ restoreFocus: false });
  }
  // Only the rows you can see, in the order you see them: what a shift-click
  // range and “group the picked rows” both count along.
  visibleOrder = blocks.flatMap((block) => block.kind === "group"
    ? (block.collapsed ? [] : block.sessions.map((s) => s.sessionId))
    : [block.session.sessionId]);

  // The skeleton is rebuilt only when the shape changes — which group a row is
  // in, what a group is called, whether it is folded. A status changing repaints
  // the row in place, as it always did.
  const layout = blocks.map((block) => block.kind === "group"
    ? `g:${block.key}:${block.name}:${block.collapsed}:${block.sessions.map((s) => s.sessionId).join(",")}`
    : `s:${block.session.sessionId}`).join("|");
  if (sessionList.dataset.layout !== layout && !renamingGroup) {
    sessionList.dataset.layout = layout;
    sessionList.textContent = "";
    for (const block of blocks) {
      if (block.kind === "session") {
        const row = document.createElement("li");
        row.dataset.id = block.session.sessionId;
        sessionList.appendChild(row);
        continue;
      }
      const item = document.createElement("li");
      item.className = "group";
      item.dataset.group = block.key;
      item.dataset.collapsed = String(block.collapsed);
      item.appendChild(groupHeader(block));
      const inner = document.createElement("ul");
      inner.className = "group__items";
      for (const session of block.sessions) {
        const row = document.createElement("li");
        row.dataset.id = session.sessionId;
        inner.appendChild(row);
      }
      item.appendChild(inner);
      sessionList.appendChild(item);
    }
    // The rebuild threw away the mark on the row the open menu points at.
    const marked = ui.menuFor
      ? sessionList.querySelector<HTMLElement>(`li[data-id="${CSS.escape(ui.menuFor)}"]`)
      : ui.menuGroup
        ? sessionList.querySelector<HTMLElement>(`li[data-group="${CSS.escape(ui.menuGroup)}"]`)
        : null;
    if (marked) marked.dataset.menu = "open";
  }

  for (const session of visible) {
    const item = sessionList.querySelector(`li[data-id="${CSS.escape(session.sessionId)}"]`);
    if (item) paintListItem(item, session);
  }
  // A folded group can be hiding rows you picked, so its header says so rather
  // than leaving the count in the bar looking wrong.
  for (const block of blocks) {
    if (block.kind !== "group") continue;
    const item = sessionList.querySelector<HTMLElement>(`li[data-group="${CSS.escape(block.key)}"]`);
    if (!item) continue;
    const inside = block.sessions.filter((s) => picked.has(s.sessionId)).length;
    if (inside) item.dataset.picked = String(inside);
    else delete item.dataset.picked;
    const count = item.querySelector(".group__count");
    if (count) count.textContent = inside ? `${inside} of ${block.sessions.length}` : String(block.sessions.length);
  }
  syncPickBar();
}

/* The header of one group: fold it away with a click, act on it with a
   right-click. A folded group still shows the states inside it. */
function groupHeader(block) {
  const button = document.createElement("button");
  button.className = "group__header md-state";
  button.type = "button";
  button.setAttribute("aria-expanded", String(!block.collapsed));
  const states: StateKey[] = [...new Set<StateKey>(block.sessions.map((s) => stateKeyOf(s.status)))]
    .sort((a, b) => STATE_ORDER.indexOf(a) - STATE_ORDER.indexOf(b));
  button.innerHTML = `
    <span class="group__chevron">${ICON.chevron}</span>
    <span class="group__icon" title="${block.custom ? "A group you made" : "Sessions in one folder"}">${
      block.custom ? ICON.group : ICON.folder}</span>
    <span class="group__name md-title-small">${escapeHtml(block.name)}</span>
    <span class="group__lamps">${states
      .map((key) => `<span class="group__lamp" style="--lamp:${STATE[key].colour}" title="${escapeHtml(STATE[key].label)}"></span>`)
      .join("")}</span>
    <span class="group__count md-label-small md-mono">${block.sessions.length}</span>`;
  button.addEventListener("click", () => toggleGroup(block));
  button.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    openGroupMenu(block, event.clientX, event.clientY);
  });
  return button;
}

function paintListItem(item, session) {
  const state = drawnStateOf(session);
  const host = hostOf(session);
  // Nothing on the list is what the pane is showing while the settings page is,
  // so no row claims to be.
  const isSelected = !app.showingSettings && session.sessionId === app.selectedId;
  const isPicked = picked.has(session.sessionId);
  // Picking is deliberately out of the signature: it is an attribute on a row
  // that already exists, and rebuilding the row would take the keyboard focus
  // away from it just as you were picking the next one.
  // What Claude calls the work, which is not what the row is called: a session
  // named "watchtower-55" says where you are, and "Fix browser input not
  // displaying" says what it is doing. Dropped when it only repeats the name.
  const subject = session.title && session.title.toLowerCase() !== session.name.toLowerCase()
    ? session.title : "";
  // A nested session — one started from inside another — has no transcript to
  // take a subject from, and its name is the panel's own guess rather than one
  // Claude chose. Saying whose child it is puts the row in context instead.
  const nested = session.kind === "child"
    ? `in ${session.parentName || `pid ${session.parentPid || "?"}`}` : "";
  // A prompt on screen is more particular than "waiting", and naming it is what
  // makes several waiting sessions tellable apart from the list: the header
  // Claude gave a question is written to be a label, and a permission gate is
  // named for the tool it wants.
  const ask = standingAsk(session);
  // The badge carries the kind, so the words beside it do not have to repeat it
  // — and it takes the place of the state word, which a standing prompt has
  // already said more precisely.
  const supporting = (ask ? `<span class="session-item__ask md-label-small">${
      ICON[ASK_ICON[ask.kind]]
    }${ASK_WORD[ask.kind]}</span>` : "")
    + [ask ? ask.label : state.short, session.folder, nested].filter(Boolean)
      .map(escapeHtml).join(" · ");
  // `state.short` alongside the raw status, because they are not the same
  // question: a compacting session and a working one share a status, and the row
  // says different words for them.
  const signature = [stateKeyOf(session.status), state.short, session.name, session.folder, host.label,
                     isSelected, session.pinned, subject, nested, ask?.kind, ask?.label].join(" ");
  if (item.dataset.signature !== signature) {
    item.dataset.signature = signature;
    item.innerHTML = `
      <button class="session-item md-state" type="button" data-status="${stateKeyOf(session.status)}"
              aria-current="${isSelected}" data-picked="${isPicked}">
        <span class="session-item__avatar">${host.icon}<span class="session-item__lamp"></span></span>
        <span class="session-item__text">
          <span class="session-item__headline md-title-small">${
            session.pinned ? `<span class="session-item__pin" title="Pinned — survives a panel restart">${ICON.pin}</span>` : ""
          }${escapeHtml(session.name)}</span>
          ${subject ? `<span class="session-item__subject md-body-small">${escapeHtml(subject)}</span>` : ""}
          <span class="session-item__supporting md-body-small">${supporting}</span>
        </span>
        <span class="session-item__trailing md-label-small md-mono" data-since="${displaySince(session)}"></span>
        <span class="session-item__grip" aria-hidden="true"
              title="Drag the row to move it, or hold alt and press up or down">${ICON.drag}</span>
      </button>`;
    const button = item.firstElementChild;
    // Carrying the row is the whole button rather than the grip alone: the grip
    // says the row can be moved, and having to hit a 14px target to move it
    // would be a worse answer than the one the cue is advertising. A drag needs
    // the pointer to travel first, so the click this button is mostly for is
    // untouched.
    button.draggable = true;
    button.style.setProperty("--state-colour", state.colour);
    button.style.setProperty("--state-container", state.container);
    button.style.setProperty("--state-on-container", state.onContainer);
    // The id, not the session: the object is replaced on every poll, the row is not.
    const id = session.sessionId;
    button.addEventListener("click", (event) => onRowClick(id, event));
  } else {
    const button = item.firstElementChild;
    button.setAttribute("aria-current", String(isSelected));
    button.dataset.picked = String(isPicked);
    button.dataset.status = stateKeyOf(session.status);
  }
  const trailing = item.querySelector("[data-since]");
  if (trailing) trailing.dataset.since = displaySince(session);
}

export function selectSession(id) {
  // Picking a session is the way out of the settings page: it is what the pane
  // would otherwise be showing.
  if (app.showingSettings) { app.showingSettings = false; settingsButton.setAttribute("aria-pressed", "false"); }
  app.selectedId = id;
  localStorage.setItem("cbu-selected", id);
  chat.transcript = null;
  chat.transcriptFor = null;
  chat.chatLimit = CHAT_PAGE;
  chat.chatGrew = false;
  // A change belongs to the conversation it was made in, so it does not travel
  // to the next session's pane. The patch itself stays cached under its own id,
  // which is unique — coming back and opening it again costs nothing.
  chat.changeShown = null;
  chat.chatReturn = 0;
  repo.git = null;
  repo.gitFor = null;
  closeDiff();
  panes.dataset.view = "detail";
  render();
  fetchTranscript();
  if (isGitTab(app.tab)) fetchGit(true);
}

function setTab(next) {
  // The tab says *Conversation*, so that is what it shows when you press it.
  if (next === "chat") chat.changeShown = null;
  app.tab = next;
  localStorage.setItem("cbu-tab", next);
  renderDetail(true);
  if (next === "chat") fetchTranscript();
  if (isGitTab(next)) fetchGit();
  if (next === "usage") fetchUsage(true);
}

function setMuted(id, muted) {
  if (muted) mutedSessions.add(id);
  else mutedSessions.delete(id);
  localStorage.setItem("cbu-muted", JSON.stringify([...mutedSessions]));
  renderDetail();
}


/* What this session can actually have done to it, in the order you reach for it.
   Anything that cannot apply right now is left out rather than shown dead —
   except focusing, which is disabled with its reason, because its absence would
   otherwise look like a missing feature. Kept short on purpose: opening the
   session, pairing a window and copying its ids all live in the details pane,
   where there is room to explain them. */
function menuItemsFor(session) {
  const win = session.window;
  const muted = mutedSessions.has(session.sessionId);
  const items: MenuItem[] = [];
  // Window actions mean nothing for a session with no window: one with no
  // process at all, or one the panel is running, whose process is a pipe.
  if (runsHere(session)) {
    // nothing to focus
  } else if (!app.feed.canFocus) {
    items.push({ key: "focus", icon: ICON.focus, label: "Focus window", hint: "needs xdotool", disabled: true });
  } else if (win) {
    items.push({ key: "focus", icon: ICON.focus, label: "Focus window",
      hint: windowSays(win),
      run: (el) => run("/api/focus", session, el, isAmbiguous(win) ? IDENTIFY_NOTE : undefined) });
  }
  // Only a row with nothing behind it is offered a start. A session the panel is
  // running is already running; handing it back to a terminal is what End
  // session does, and offering it here only invited undoing the thing you just
  // asked for.
  if (runsHere(session) && !ownedFor(session).running) {
    items.push({ key: "start", icon: ICON.play, label: "Start it up",
      hint: app.feed.canSend ? "the panel runs it" : "needs loopback",
      disabled: !app.feed.canSend,
      run: (el) => run("/api/owned/say", session, el, null, { text: "" }) });
  }
  // A second session on the same work, started where this one is — a fresh
  // conversation rather than a resume, so it never touches this one's transcript.
  // Interactive, like every other way of starting one: the New menu has defaulted
  // to that for a while and this item had been left behind opening a terminal, so
  // which door you came through decided what kind of session you got. A terminal
  // is still a choice, at the bottom of the New menu, where it says so.
  if (session.cwd) {
    items.push({ key: "new", icon: ICON.plus, label: "New session here",
      hint: app.feed.canSend ? (session.folder || shorten(session.cwd, 1)) : "needs loopback",
      disabled: !app.feed.canSend,
      run: (el) => startOwnedSession({ from: session }, el) });
  }
  // Pinning is about restarts, not about closing: a session the panel runs
  // already has a row for as long as the panel is up, and pinning is what makes
  // that row outlive the panel too.
  items.push({ key: "sticky", icon: session.pinned ? ICON.pinOff : ICON.pin,
    label: session.pinned ? "Unpin this one" : "Pin to the dashboard",
    hint: session.pinned ? (runsHere(session) ? "kept only while the panel runs" : "drops it when it closes")
                         : "survives a panel restart",
    run: (el) => run("/api/sticky", session, el, null, { pinned: !session.pinned }) });
  items.push({ key: "mute", icon: muted ? ICON.bell : ICON.bellOff,
    label: muted ? "Unmute notifications" : "Mute notifications",
    run: () => { setMuted(session.sessionId, !muted); showSnackbar(muted ? "Notifications on" : "Notifications muted"); } });
  // Handing the order back, offered only once there is an order of yours to
  // hand back — and on any row, because it is the whole list it undoes.
  if (manualOrder.length) {
    items.push({ key: "sort", icon: ICON.sort, label: "Sort by state again",
      hint: "forgets the order you dragged", run: () => clearArrangement() });
  }
  // Grouping acts on the rows you picked, so it is offered where the picking is.
  const group = groupOf(session.sessionId);
  if (picked.size > 1 && picked.has(session.sessionId)) {
    items.push({ divider: true });
    items.push({ key: "group", icon: ICON.group, label: `Group these ${picked.size}`,
      run: () => groupPicked() });
  }
  if (group) {
    items.push({ divider: true });
    items.push({ key: "leave", icon: ICON.ungroup, label: "Take out of the group",
      hint: group.name, run: () => leaveGroup(session.sessionId) });
  }
  // One way out, not two. Stopping a session and taking its row off the list
  // were separate items, so ending something left it sitting on the dashboard
  // until you asked again; now stopping removes it, and pinning is what says
  // otherwise. A session the panel runs has a process too — a pipe rather than
  // a terminal, which is why it reads as `alive: false` — so ending it means
  // the panel letting go.
  const holding = ownedFor(session).running;
  const last = [];
  if (session.alive !== false || holding) {
    last.push({ key: "end", icon: ICON.power, danger: true,
      label: session.alive === false ? "Stop running it here…" : "End session…",
      hint: session.pinned ? "pinned, so the row stays" : "the row goes; the conversation stays on disk",
      run: () => openEndDialog(session) });
  } else if (session.kept) {
    // Nothing left to stop — a row whose process went on its own. Removing it
    // is the same ending, arrived at from the other side.
    last.push({ key: "forget", icon: ICON.trash, label: "Remove from the dashboard", danger: true,
      hint: "the conversation stays on disk",
      run: () => openForgetDialog(session) });
  }
  // What takes something away sits below a line, at the end.
  if (last.length) items.push({ divider: true }, ...last);
  return items;
}

function openSessionMenu(session, x, y) {
  openMenu({
    title: session.name,
    label: `Actions for ${session.name}`,
    items: menuItemsFor(session),
    forId: session.sessionId,
  }, x, y);
}

/* The sidebar's own New. A new session needs a folder to open in, and the panel
   will only ever use a folder it is already showing — /api/new takes a session
   and reads the folder off that, never a path from the request — so the button
   asks which of the folders on the list, one row per folder rather than per
   session. The first session on the list in a folder stands for it; which one it
   is makes no difference, since only its cwd is used.

   Sessions with no folder — a session file that never recorded one — are left
   out rather than offered as an item that would be refused. */
function newSessionFolders() {
  const byFolder = new Map();
  for (const session of app.feed.sessions) {
    if (!session.cwd || byFolder.has(session.cwd)) continue;
    byFolder.set(session.cwd, session);
  }
  return [...byFolder.values()];
}

function openNewMenu(button) {
  const folders = newSessionFolders();
  // Interactive is what a new session is, unless you say otherwise: the panel
  // runs it, so its mode is yours and its prompts come here from the first word.
  // The terminal route is still there, at the bottom, for when you want a
  // terminal — but it is the exception now rather than the only door.
  const items: MenuItem[] = folders.map((session) => ({
    key: `new:${session.sessionId}`,
    icon: ICON.folder,
    label: session.folder || shorten(session.cwd, 1),
    hint: app.feed.canSend ? shorten(session.cwd, 2) : "needs loopback",
    disabled: !app.feed.canSend,
    run: (el) => startOwnedSession({ from: session }, el),
  }));
  // Anywhere else on the disk. The panel will not be told a path — /api/new
  // refuses one and this endpoint asks for a chooser rather than sending one —
  // so the folder is picked in a window the desktop draws, on the machine the
  // session will run on. It stays last: the folders above are the answer most
  // of the time.
  if (items.length) items.push({ divider: true });
  items.push({
    key: "pick",
    icon: ICON.folder,
    label: "Another folder…",
    hint: !app.feed.canSend ? "needs loopback"
      : app.feed.canPickFolder === false ? "no chooser on this desktop" : "opens a folder chooser",
    disabled: !app.feed.canSend || app.feed.canPickFolder === false,
    run: (el) => startOwnedSession({ pick: true }, el),
  });
  items.push({ divider: true });
  items.push({
    key: "terminal",
    icon: ICON.power,
    label: "In a terminal instead",
    hint: "the panel cannot answer its prompts",
    disabled: !app.feed.canSend,
    run: (el) => openNewMenuTerminal(button),
  });
  const box = button.getBoundingClientRect();
  openMenu({
    title: "New session",
    label: "Where to start a new session",
    items,
  }, box.left, box.bottom + 4);
}

/* The old way, kept but out of the way. A session a terminal runs cannot have
   its mode picked here and cannot have its prompts answered here, which is the
   whole of why it is no longer the first thing offered. */
function openNewMenuTerminal(button) {
  const items: MenuItem[] = newSessionFolders().map((session) => ({
    key: `term:${session.sessionId}`,
    icon: ICON.folder,
    label: session.folder || shorten(session.cwd, 1),
    hint: shorten(session.cwd, 2),
    run: (el) => run("/api/new", session, el),
  }));
  if (items.length) items.push({ divider: true });
  items.push({ key: "pick", icon: ICON.folder, label: "Another folder…",
    hint: app.feed.canPickFolder === false ? "no chooser on this desktop" : "opens a folder chooser",
    disabled: app.feed.canPickFolder === false, run: (el) => pickNewFolder(el) });
  const box = button.getBoundingClientRect();
  openMenu({ title: "New session in a terminal", label: "Folders", items },
    box.left, box.bottom + 4);
}

/* The chooser stands open until somebody answers it, which is longer than any
   other call the panel makes — hence the snackbar that says so, and no timeout
   of our own. Cancelling comes back ok:false with cancelled:true, and is worth
   no more than a quiet line: it is what half the openings of a chooser end in. */
async function pickNewFolder(button) {
  button.disabled = true;
  showSnackbar("Pick a folder in the chooser…", 300000);
  try {
    const response = await fetch("/api/new-folder", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    const data = await response.json().catch(() => ({}));
    showSnackbar(data.message || (response.ok ? "Done" : "That did not work"));
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    button.disabled = false;
    poll();
  }
}

const newButton = document.getElementById("newButton");
newButton.addEventListener("click", () => {
  // A second click on the button closes the menu it opened, rather than the
  // pointerdown handler closing it and this one opening it straight back.
  if (menuIsOpen()) { closeSessionMenu({ restoreFocus: false }); return; }
  openNewMenu(newButton);
});

/* What a group can have done to it: folded, renamed, or taken apart. A folder
   group has no stored name to change — its name is the folder — so taking it
   apart is the only thing it offers besides folding. */
function openGroupMenu(block, x, y) {
  const items: MenuItem[] = [
    { key: "fold", icon: ICON.chevron, label: block.collapsed ? "Expand" : "Fold away",
      hint: `${block.sessions.length} sessions`, run: () => toggleGroup(block) },
    { divider: true },
  ];
  if (block.custom) {
    items.push({ key: "rename", icon: ICON.pencil, label: "Rename group…",
      run: () => startGroupRename(block) });
    items.push({ key: "ungroup", icon: ICON.ungroup, label: "Ungroup",
      hint: "the rows stay", run: () => ungroup(block) });
  } else {
    // Every row under a folder header shares that folder, so any of them names
    // the place a new session should open — a custom group has no one folder.
    const any = block.sessions[0];
    if (any) {
      items.push({ key: "new", icon: ICON.plus, label: "New session here",
        hint: app.feed.canSend ? shorten(block.folder, 1) : "needs loopback",
        disabled: !app.feed.canSend,
        run: (el) => run("/api/new", any, el) });
      items.push({ divider: true });
    }
    items.push({ key: "ungroup", icon: ICON.ungroup, label: "Do not group this folder",
      hint: shorten(block.folder, 2), run: () => ungroup(block) });
    if (looseFolders.size) {
      items.push({ key: "regroup", icon: ICON.folder, label: "Group every folder again",
        hint: `${looseFolders.size} left out`, run: () => regroupFolders() });
    }
  }
  items.push({ divider: true });
  items.push({ key: "pick", icon: ICON.group, label: "Pick every session in it",
    run: () => {
      for (const session of block.sessions) picked.add(session.sessionId);
      pickAnchor = block.sessions[block.sessions.length - 1]?.sessionId ?? null;
      render();
    } });
  openMenu({
    title: block.name,
    label: `Actions for the group ${block.name}`,
    items,
    forGroup: block.key,
  }, x, y);
}

/* Renaming a group works like renaming a session: the name in the header turns
   into a field, Enter or clicking away keeps it, Escape leaves it alone. */
function startGroupRename(block) {
  if (!block.custom || renamingGroup) return;
  const header = sessionList.querySelector(`li[data-group="${CSS.escape(block.key)}"] > .group__header`);
  if (!header) return;
  renamingGroup = block.key;
  // The header is a button, and a field inside a button would fold the group away
  // on the first space, so the whole header steps aside while you type.
  const row = document.createElement("div");
  row.className = "group__header";
  const field = document.createElement("input");
  field.type = "text";
  field.className = "group__field md-title-small";
  field.value = block.custom.name;
  field.maxLength = 60;
  field.setAttribute("aria-label", "Group name");
  row.innerHTML = `<span class="group__chevron">${ICON.chevron}</span>
    <span class="group__icon">${ICON.group}</span>`;
  row.appendChild(field);
  header.replaceWith(row);
  field.focus();
  field.select();

  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    renamingGroup = null;
    const typed = field.value.trim();
    if (save && typed && typed !== block.custom.name) {
      block.custom.name = typed;
      saveGroups();
    }
    // Rebuilt from the model, which puts the real header back either way.
    sessionList.dataset.layout = "";
    render();
  };
  field.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); finish(true); }
    else if (event.key === "Escape") { event.preventDefault(); finish(false); }
  });
  field.addEventListener("blur", () => finish(true));
}

/* Arrow keys walk the enabled items and wrap; Escape gives focus back to the row. */
sessionMenu.addEventListener("keydown", (event) => {
  const enabled = [...sessionMenu.querySelectorAll<HTMLButtonElement>(".menu__item:not([disabled])")];
  if (!enabled.length) return;
  const at = enabled.indexOf(document.activeElement as HTMLButtonElement);
  const go = (i: number) => { enabled[(i + enabled.length) % enabled.length].focus(); event.preventDefault(); };
  if (event.key === "ArrowDown") go(at + 1);
  else if (event.key === "ArrowUp") go(at - 1);
  else if (event.key === "Home") go(0);
  else if (event.key === "End") go(enabled.length - 1);
  else if (event.key === "Escape") { event.preventDefault(); closeSessionMenu(); }
  else if (event.key === "Tab") closeSessionMenu();
});

sessionList.addEventListener("contextmenu", (event) => {
  const row = hitClosest(event, "li[data-id]");
  const session = row && sessionById(row.dataset.id);
  if (!session) return;
  event.preventDefault();
  ui.menuReturn = session.sessionId;
  openSessionMenu(session, event.clientX, event.clientY);
});

/* Space on a focused row picks it, which is the keyboard's ctrl-click: without it
   grouping would be a pointer-only feature. Enter still opens the session. */
sessionList.addEventListener("keydown", (event) => {
  if (event.key !== " ") return;
  const row = hitClosest(event, "li[data-id]");
  if (!row || !hitClosest(event, ".session-item")) return;
  event.preventDefault();     // or the button below takes it as a click
  togglePick(row.dataset.id);
  pickAnchor = row.dataset.id;
  // The repaint keeps the row, so put the focus back where the reader left it.
  sessionList.querySelector<HTMLElement>(`li[data-id="${CSS.escape(row.dataset.id)}"] .session-item`)?.focus();
});

/* ==========================================================================
   Dragging a row into place.

   The browser's own drag rather than a pointer-move harness: it gives the
   carried image, the cursor and the escape key for nothing, and the list is
   already a tree of ordinary elements to hit-test against.

   A row only ever lands in the list it came out of. Dropping one into a group
   would have to mean joining that group, which is a different act with its own
   item in the menu, and a drag is too easy to do by accident to be the way you
   discover it. At the top level a group counts as one block, so a row dragged
   past it goes past the whole thing.
   ========================================================================== */
/* The row being carried, and the edge it is hovering over. Kept out of the
   list's own state because a poll must not disturb either. */
let dragging = null;    // { id, from } — the row's id and the ul it belongs to
let dropMark = null;    // the li the landing line is drawn on

function clearDropMark() {
  if (dropMark) delete dropMark.dataset.drop;
  dropMark = null;
}

function endDrag() {
  const row = dragging && sessionList.querySelector<HTMLElement>(`li[data-id="${CSS.escape(dragging.id)}"]`);
  if (row) delete row.dataset.dragging;
  clearDropMark();
  dragging = null;
  delete sessionList.dataset.dragging;
  // Whatever the polls wanted to change while the list was held still.
  render();
}

/* Which row the pointer is over, and which side of it — within the list the
   carried row came from, so anything else is not a place it can land. */
function dropTargetFor(event) {
  if (!dragging?.from) return null;
  let node = hitClosest(event, "li");
  while (node && node.parentElement !== dragging.from) node = node.parentElement?.closest("li") ?? null;
  if (!node) return null;
  const box = node.getBoundingClientRect();
  return { node, after: event.clientY > box.top + box.height / 2 };
}

/* The session a landing place is measured from. A session row is its own
   answer; a group is the member at the end the row is landing on, which is what
   puts the row outside the group rather than into it. */
function anchorIdOf(node, after) {
  if (node.dataset.id) return node.dataset.id;
  const rows = [...node.querySelectorAll("li[data-id]")];
  return (after ? rows[rows.length - 1] : rows[0])?.dataset.id ?? null;
}

sessionList.addEventListener("dragstart", (event) => {
  const row = hitClosest(event, "li[data-id]");
  if (!row || !hitClosest(event, ".session-item")) return;
  dragging = { id: row.dataset.id, from: row.parentElement };
  row.dataset.dragging = "true";
  sessionList.dataset.dragging = "true";
  // A menu still open would point at a row that is about to be somewhere else.
  if (menuIsOpen()) closeSessionMenu({ restoreFocus: false });
  event.dataTransfer.effectAllowed = "move";
  // Firefox starts no drag at all without data on it, and the id is the one
  // thing worth putting there. Nothing reads it back: the drag never leaves the
  // list, and `dragging` says more than a string can.
  event.dataTransfer.setData("text/plain", row.dataset.id);
});

sessionList.addEventListener("dragover", (event) => {
  if (!dragging) return;
  const target = dropTargetFor(event);
  if (!target) { clearDropMark(); return; }
  // Only a place it can land takes the drop, so the cursor says so too.
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
  if (dropMark !== target.node) clearDropMark();
  dropMark = target.node;
  target.node.dataset.drop = target.after ? "after" : "before";
});

sessionList.addEventListener("drop", (event) => {
  if (!dragging) return;
  event.preventDefault();
  const target = dropTargetFor(event);
  const id = dragging.id;
  const anchorId = target && anchorIdOf(target.node, target.after);
  const after = !!target?.after;
  // Before the move, because moving redraws the list this was holding still.
  endDrag();
  if (anchorId) moveRow(id, anchorId, after);
});

// Dropped outside the list, or given up on with Escape. Either way the list has
// been standing still and has a poll's worth of news to catch up on.
sessionList.addEventListener("dragend", () => { if (dragging) endDrag(); });

/* The keyboard route to the same move: alt with an arrow, on the row itself.
   One step is one block, so a row steps over a whole group rather than into it —
   the same rule the drag follows. */
sessionList.addEventListener("keydown", (event) => {
  if (!event.altKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return;
  const row = hitClosest(event, "li[data-id]");
  if (!row || !hitClosest(event, ".session-item")) return;
  event.preventDefault();
  const down = event.key === "ArrowDown";
  const siblings = [...row.parentElement.children];
  const next = siblings[siblings.indexOf(row) + (down ? 1 : -1)];
  const id = row.dataset.id;
  if (next) moveRow(id, anchorIdOf(next, down), down);
  // The move rebuilt the list around the row; the reader is still on it.
  sessionList.querySelector<HTMLElement>(`li[data-id="${CSS.escape(id)}"] .session-item`)?.focus();
});

// The keyboard route to the same menu: Shift-F10, or the dedicated menu key.
sessionList.addEventListener("keydown", (event) => {
  if (!(event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey))) return;
  const row = hitClosest(event, "li[data-id]");
  const session = row && sessionById(row.dataset.id);
  if (session) {
    event.preventDefault();
    ui.menuReturn = session.sessionId;
    const rect = row.querySelector(".session-item").getBoundingClientRect();
    openSessionMenu(session, rect.left + 16, rect.bottom - 8);
    return;
  }
  // The same key on a group header opens the group's menu.
  const header = hitClosest(event, "li.group")?.querySelector(":scope > .group__header");
  const block = header && lastBlocks.find((b) => b.kind === "group" && b.key === header.parentElement.dataset.group);
  if (!block) return;
  event.preventDefault();
  const rect = header.getBoundingClientRect();
  openGroupMenu(block, rect.left + 16, rect.bottom - 8);
});

/* And the touch route to both of them: press and hold a row, the way every
   phone opens the actions on a list item. A phone has no right-click, and the
   `contextmenu` event a long press produces is Android's alone — so the gesture
   is recognised here rather than waited for. See ui/press.js.

   The list is where this belongs and the conversation is where it does not: a
   long press on a bubble is how a phone starts a text selection, which is what
   raises the Comment chip. Rows have no text to select — the stylesheet says so
   — so there is nothing for the press to take away. */
onLongPress(sessionList, ({ x, y, target }) => {
  const row = target.closest?.("li[data-id]");
  const session = row && sessionById(row.dataset.id);
  if (session) {
    ui.menuReturn = session.sessionId;
    openSessionMenu(session, x, y);
    return;
  }
  const header = target.closest?.("li.group > .group__header");
  const block = header && lastBlocks.find((b) => b.kind === "group" && b.key === header.parentElement.dataset.group);
  if (block) openGroupMenu(block, x, y);
});

// Anything that moves the menu away from what it points at closes it.
document.addEventListener("pointerdown", (event) => {
  // Except the button the menu hangs off: closing here would let its own click
  // reopen the menu it was meant to shut.
  if (hitClosest(event, "#newButton")) return;
  if (menuIsOpen() && !sessionMenu.contains(hitElement(event))) closeSessionMenu({ restoreFocus: false });
}, true);
window.addEventListener("blur", () => closeSessionMenu({ restoreFocus: false }));
window.addEventListener("resize", () => closeSessionMenu({ restoreFocus: false }));
// Capture, because scroll does not bubble and the scrolling box may be the pane
// or the list itself depending on the breakpoint. Scrolling within the menu is
// the reader working through a long one, not moving away from it.
document.addEventListener("scroll", (event) => {
  if (menuIsOpen() && !sessionMenu.contains(hitElement(event))) closeSessionMenu({ restoreFocus: false });
}, true);

function renderDetail(force = false) {
  if (app.showingSettings) { paintSettings(); return; }
  const session = selected();
  if (!session) {
    detailPane.dataset.signature = "";
    detailPane.innerHTML = `<div class="detail-empty">
      <div><h2 class="md-headline-small">Nothing selected</h2>
      <p class="md-body-medium">Pick a session on the left to see its conversation and settings.</p></div></div>`;
    return;
  }
  const state = drawnStateOf(session);
  const host = hostOf(session);
  // A session outside a repository has no Git tab to show, so a tab left over
  // from the last session you looked at falls back rather than showing nothing.
  if (isGitTab(app.tab) && !session.repoRoot) app.tab = "chat";
  const signature = [
    session.sessionId, session.name, stateKeyOf(session.status), session.branch, session.window?.confidence,
    host.label, app.tab, chat.transcript?.messages?.length ?? -1, chat.transcript?.title ?? "",
    session.repoRoot ?? "", repo.gitFor === session.sessionId ? gitStamp(repo.git) : "",
    spend.usageFor === session.sessionId ? usageStamp(spend.usage) : "",
    mutedSessions.has(session.sessionId), app.feed.canFocus, app.feed.canSend, session.canSay,
    session.pinned, session.kept,
    session.permissionMode ?? "", session.title ?? "", session.parentName ?? "",
    // A question going up or being answered has to redraw the pane; the tool use
    // it came in on is what tells one question from the next.
    session.question?.toolUseId ?? "",
    // What is pasted into the box but not sent yet. The strip is drawn from a
    // map rather than from the server, so nothing else here would move when a
    // picture lands or finishes saving.
    imagesStamp(session.sessionId),
    // And everything about a session the panel runs. Without these the pane
    // never repainted for any of it: a mode picked on a running session changed
    // on the server and nowhere on screen, which read as the chips not working.
    (() => { const o = ownedFor(session);
      return [o.mode ?? "", o.here ?? "", o.running ?? "", o.busy ?? "",
              o.ask?.requestId ?? "", o.stopping ?? "", (o.queued || []).length,
              // A compaction starting, ending, and what it managed. Not the
              // running percentage — that walks forward on the clock without a
              // repaint; see the ticker at the foot of the file.
              [o.compact?.at, o.compact?.running, o.compact?.ok,
               o.compact?.after].join(","),
              // Which change is being read whole, if any: the pane draws
              // something else entirely while one is.
              chat.changeShown ?? "", changeBusy.has(chat.changeShown) ? "reading" : "",
              // Not the count alone: dropping the first of two and typing a
              // third leaves the count where it was and the list different.
              (o.queued || []).join("\u0000").slice(0, 200)].join("/"); })(),
  ].join(" ");
  if (!force && detailPane.dataset.signature === signature) {
    const clock = detailPane.querySelector<HTMLElement>("[data-since]");
    if (clock) clock.dataset.since = String(displaySince(session));
    // The trace moves on every poll even when nothing else does. Repaint it in
    // place rather than rebuilding the pane around it.
    const standing = detailPane.querySelector(".detail-header");
    if (standing) paintTrace(standing, session);
    return;
  }

  // A drag holds the pointer capture on the grip, so the pane must not be
  // rebuilt under it. Keep the trace moving in place and let the rebuild land
  // when the pointer comes up — the signature stays stale until then.
  // The same goes for a name being typed: rebuilding would throw the field and
  // the half-typed name away.
  // A file held over the message box is the same case: rebuilding the pane under
  // the drag would take away the box it is about to land in.
  if ((ui.resizingComposer || ui.droppingOnComposer
       || sidebar.renamingId === session.sessionId || commentIsOpen()) && !force) {
    const standing = detailPane.querySelector(".detail-header");
    if (standing) paintTrace(standing, session);
    return;
  }

  // Preserve chat scroll position across re-renders.
  const chatBefore = detailPane.querySelector("#chatScroll");
  const wasAtBottom = !chatBefore || chatBefore.scrollHeight - chatBefore.scrollTop - chatBefore.clientHeight < 80;
  // Older messages are added above what you were reading, so holding scrollTop
  // would slide the whole conversation out from under you. Hold the distance to
  // the bottom instead, which is the part that did not move.
  const chatFromBottom = chat.chatGrew && chatBefore
    ? chatBefore.scrollHeight - chatBefore.scrollTop : null;
  // And where it was, for every other rebuild. Without this the pane was rebuilt
  // to scrollTop 0 whenever you were reading anything but the newest message —
  // so opening a change, or a poll landing while you read back through the
  // conversation, threw you to the very top of it. The two cases above are the
  // ones that want something other than "where you were"; everything else wants
  // exactly that.
  const chatWas = chatBefore ? chatBefore.scrollTop : 0;
  // A half-typed message must survive a re-render — the poll re-renders every time
  // the status changes or a message lands, which is exactly while you are typing.
  // The text itself lives in sayDrafts under the session it was written for; only
  // the caret is carried through here, and only when the pane is not changing
  // session, because a caret from another session's box means nothing in this one.
  const fieldBefore = detailPane.querySelector<HTMLTextAreaElement>("#sayField");
  const sameSession = detailPane.dataset.sessionId === session.sessionId;
  if (fieldBefore && detailPane.dataset.sessionId) {
    setSayDraft(detailPane.dataset.sessionId, fieldBefore.value);
  }
  const caret = fieldBefore && sameSession ? {
    start: fieldBefore.selectionStart,
    end: fieldBefore.selectionEnd,
    focused: document.activeElement === fieldBefore,
  } : null;
  // Same for a commit message: its text lives in commitDrafts, but where the
  // caret was does not, and a poll landing mid-word would otherwise move it.
  const commitBefore = detailPane.querySelector<HTMLTextAreaElement>("#commitField");
  repo.commitCaret = commitBefore ? {
    start: commitBefore.selectionStart,
    end: commitBefore.selectionEnd,
    focused: document.activeElement === commitBefore,
  } : null;

  // Whatever was being typed is gone with the rebuild below.
  sidebar.renamingId = null;
  // A menu opened from a button in this pane — the Git tab's overflow, the
  // commit split button — is about to be pointing at an element that no longer
  // exists, so it goes with the rebuild rather than floating over the new one.
  if (menuIsOpen() && !ui.menuFor && !ui.menuGroup) closeSessionMenu({ restoreFocus: false });
  detailPane.dataset.signature = signature;
  // What the pane was showing a moment ago, for the fade below. Read before the
  // rebuild overwrites it.
  // Not data-tab: the pane wraps the tab strip and comes first in document
  // order, so a data-tab here answers every [data-tab="…"] lookup meant for the
  // buttons inside it — including the ones the tests click.
  const cameFrom = `${detailPane.dataset.sessionId || ""}/${detailPane.dataset.showing || ""}`;
  detailPane.dataset.sessionId = session.sessionId;
  detailPane.dataset.showing = app.tab;
  detailPane.innerHTML = `
    ${detailHeader(session, state, host)}
    <div class="tabs" role="tablist">
      <button class="tab md-state md-label-large" role="tab" data-tab="chat" aria-selected="${app.tab === "chat"}">${ICON.chat}Conversation</button>
      ${session.repoRoot ? `
        <button class="tab md-state md-label-large" role="tab" data-tab="git" aria-selected="${app.tab === "git"}">${ICON.branch}Git</button>
        <button class="tab md-state md-label-large" role="tab" data-tab="history" aria-selected="${app.tab === "history"}">${ICON.history}History</button>` : ""}
      <button class="tab md-state md-label-large" role="tab" data-tab="usage" aria-selected="${app.tab === "usage"}">${ICON.coin}Usage</button>
      <button class="tab md-state md-label-large" role="tab" data-tab="about" aria-selected="${app.tab === "about"}">${ICON.info}Details</button>
    </div>
    <div class="panel-wrap">
      <div class="tab-panel" id="chatScroll" role="tabpanel">
        ${app.tab === "chat" ? chatPanel(session)
          : app.tab === "git" ? gitPanel(session)
          : app.tab === "history" ? historyPanel(session)
          : app.tab === "usage" ? usagePanel(session)
          : aboutPanel(session, host)}
      </div>
      ${app.tab === "chat" && chat.changeShown === null ? `<div class="jump-dock">
          <button class="jump-bottom md-state" id="jumpBottom" title="Jump to latest" aria-label="Jump to latest" data-open="false" tabindex="-1" hidden>${ICON.toBottom}</button>
          <button class="jump-last md-state md-label-large" id="jumpLast" data-open="false" tabindex="-1" hidden>${ICON.up}Last request</button>
        </div>
        <div class="rail" id="commentRail" hidden><div class="rail__inner" id="commentRailInner"></div></div>` : ""}
    </div>
    ${app.tab === "chat" ? questionCard(session) : ""}
    ${app.tab === "chat" ? composer(session) : ""}`;

  const header = detailPane.querySelector<HTMLElement>(".detail-header");
  header.style.setProperty("--state-colour", state.colour);
  header.style.setProperty("--state-container", state.container);
  header.style.setProperty("--state-on-container", state.onContainer);

  paintTrace(header, session);
  wireTrace(header);
  // The transcript is rebuilt from the transcript data, so anything drawn over
  // it has to be drawn again — the marks over passages you have commented on
  // included.
  if (app.tab === "chat") { markCommented(); renderRail(); }

  for (const button of detailPane.querySelectorAll<HTMLElement>("[data-tab]")) {
    button.addEventListener("click", () => setTab(button.dataset.tab));
  }
  detailPane.querySelector("[data-act='rename']")?.addEventListener("click", (e) => startRename(session, e.currentTarget));
  // Two of these when a question is up: the header's button and the card's.
  for (const button of detailPane.querySelectorAll("[data-act='focus']")) {
    button.addEventListener("click", (e) => run("/api/focus", session, control(e)));
  }
  detailPane.querySelector("[data-act='pair']")?.addEventListener("click", (e) =>
    run("/api/pair", session, control(e), "Click the window that belongs to this session"));
  detailPane.querySelector("[data-act='identify']")?.addEventListener("click", (e) =>
    run("/api/identify", session, control(e), IDENTIFY_NOTE));
  detailPane.querySelector("[data-act='unpair']")?.addEventListener("click", (e) => run("/api/unpair", session, control(e)));
  detailPane.querySelector("[data-act='editor']")?.addEventListener("click", (e) => run("/api/editor", session, control(e)));
  detailPane.querySelector("#stickyToggle")?.addEventListener("change", (event) =>
    run("/api/sticky", session, control(event), null,
        { pinned: control<HTMLInputElement>(event).checked }));
  detailPane.querySelector("[data-act='forget']")?.addEventListener("click", () => openForgetDialog(session));
  detailPane.querySelector("#muteToggle")?.addEventListener("change", (event) => {
    if (control<HTMLInputElement>(event).checked) mutedSessions.delete(session.sessionId);
    else mutedSessions.add(session.sessionId);
    localStorage.setItem("cbu-muted", JSON.stringify([...mutedSessions]));
    // The switch below follows it rather than waiting for the next poll to
    // rebuild the pane, which would take the focus off the one just pressed.
    const done = detailPane.querySelector<HTMLInputElement>("#doneToggle");
    if (done) done.disabled = !control<HTMLInputElement>(event).checked;
  });
  detailPane.querySelector("#doneToggle")?.addEventListener("change", (event) => {
    if (control<HTMLInputElement>(event).checked) quietWhenDone.delete(session.sessionId);
    else quietWhenDone.add(session.sessionId);
    localStorage.setItem("cbu-quiet-done", JSON.stringify([...quietWhenDone]));
  });
  for (const button of detailPane.querySelectorAll<HTMLElement>(".fact-copy")) {
    button.addEventListener("click", () => button.dataset.copy === "cwd"
      ? copyText(session.cwd, "Folder path copied")
      : copyText(session.sessionId, "Session id copied"));
  }
  detailPane.querySelector("[data-act='end']")?.addEventListener("click", () => openEndDialog(session));
  detailPane.querySelector("#openAppearance")?.addEventListener("click", () => openSettings(true));

  const field = detailPane.querySelector<HTMLTextAreaElement>("#sayField");
  if (field) {
    const kept = sayDrafts.get(session.sessionId) || "";
    if (kept) field.value = kept;
    // The caret comes back whether or not there was text to come back to. It
    // used to be restored only for a non-empty draft, and pasting a picture into
    // an empty box repaints the pane — so the box you were about to type in lost
    // the focus for the crime of being empty so far.
    if (caret && caret.focused) {
      field.focus();
      const end = field.value.length;
      field.setSelectionRange(Math.min(caret.start, end), Math.min(caret.end, end));
    }
    growField(field);
    syncGrip(field);
    const grip = detailPane.querySelector("#composerGrip");
    if (grip) wireComposerGrip(grip, field);
    field.addEventListener("input", () => {
      setSayDraft(session.sessionId, field.value);
      growField(field);
      // Typing past a name the list was hiding from is a new question, so a
      // dismissed list comes back rather than staying shut for the whole draft.
      cmdOff = false;
      syncCmdBar(session);
    });
    // Ctrl-V of a screenshot. Only a paste carrying a picture is taken over —
    // text falls through to the browser, which is better at pasting text than
    // anything written here would be.
    field.addEventListener("paste", (event) => {
      const pictures = picturesOn(event.clipboardData);
      if (!pictures.length) return;
      event.preventDefault();
      for (const picture of pictures) attachPicture(session, picture);
    });
    // And a file dragged in from a file manager, which types its path. The rest
    // of it is in ui/dropped.ts, including why a drop can name a file where a
    // paste has to save one first.
    wireDrop(field, session);
    field.addEventListener("keydown", (event) => {
      // The /-picker owns its keys while it stands, including Enter — which
      // takes the highlighted name rather than sending a half-typed one.
      if (cmdKey(event, session)) { event.preventDefault(); return; }
      // Enter sends, as it does in the terminal; Shift-Enter for a newline.
      if (event.key === "Enter" && !event.shiftKey && !event.altKey) {
        event.preventDefault();
        // Whichever way this session is sent to. An interactive one shows
        // only [data-act=own], and looking for [data-act=say] alone found
        // nothing — so Enter did nothing at all on the sessions the panel runs.
        const here = detailPane.querySelector("[data-act='own']");
        sendMessage(session, here || detailPane.querySelector("[data-act='say']"), !!here);
      }
    });
    // A draft that was left mid-name gets its list back with the pane.
    syncCmdBar(session);
  }
  detailPane.querySelector("[data-act='say']")?.addEventListener("click", (e) => sendMessage(session, e.currentTarget));
  detailPane.querySelector("[data-act='own']")?.addEventListener("click", (e) => sendMessage(session, e.currentTarget, true));
  detailPane.querySelector("[data-act='adopt']")?.addEventListener("click", () => openAdoptDialog(session));
  detailPane.querySelector("[data-act='stop']")?.addEventListener("click", (e) =>
    run("/api/owned/interrupt", session, control(e)));
  detailPane.querySelector("[data-act='compact']")?.addEventListener("click", (e) =>
    compactSession(session, e.currentTarget));
  // Leaving out a picture that was pasted by mistake. The file it saved stays
  // where it is — the sweep on the next paste is what clears it — and all this
  // drops is the panel's intention to name it.
  for (const drop of detailPane.querySelectorAll<HTMLElement>("[data-act='unattach']")) {
    drop.addEventListener("click", () => dropImage(session.sessionId, drop.dataset.id));
  }
  // Taking back something typed ahead. By index rather than by text, because two
  // identical messages in the queue are two messages, and dropping "the one that
  // says this" would drop the wrong one.
  for (const drop of detailPane.querySelectorAll<HTMLElement>("[data-act='unqueue']")) {
    drop.addEventListener("click", (e) => run("/api/owned/unqueue", session, control(e),
      null, { index: Number(drop.dataset.index) }));
  }
  // Answering the prompt a panel turn is standing on.
  const standing = ownedFor(session).ask;
  if (standing) {
    const picks = askPicksFor(standing);
    for (const pick of detailPane.querySelectorAll<HTMLElement>(".ask--live [data-answer]")) {
      pick.addEventListener("click", () => {
        // `.ask-q`, which is what the markup says. It said `.ask__q` for one
        // release and the whole thing came apart quietly: the row was never
        // found, so the pick was filed under "" and matched nothing, and the
        // repaint at the end of this handler was the card appearing to respawn
        // with nothing chosen.
        const holder = pick.closest<HTMLElement>(".ask-q");
        if (!holder) return;
        const question = holder.dataset.question || "";
        const many = !!standing.input?.questions?.[Number(holder.dataset.q)]?.multiSelect;
        const label = pick.dataset.label;
        const had = picks[question] || [];
        // One answer replaces; several toggle. Same rule the prompt itself uses.
        picks[question] = many
          ? (had.includes(label) ? had.filter((l) => l !== label) : [...had, label])
          : (had.includes(label) ? [] : [label]);
        // Marked where it stands rather than by rebuilding the sheet: rebuilding
        // replayed its entrance animation on every click, which is what made
        // picking an option look like losing the card.
        for (const row of holder.querySelectorAll<HTMLElement>("[data-answer]")) {
          const on = picks[question].includes(row.dataset.label);
          row.setAttribute("aria-pressed", String(on));
          row.setAttribute("aria-checked", String(on));
        }
        const answer = detailPane.querySelector<HTMLButtonElement>("[data-act='ask-allow']");
        if (answer) {
          answer.disabled = !(standing.input?.questions || [])
            .every((q) => (picks[q.question] || []).length);
        }
      });
    }
    detailPane.querySelector("[data-act='ask-allow']")?.addEventListener("click", (e) =>
      sendAskAnswer(session, standing, "allow", e.currentTarget));
    detailPane.querySelector("[data-act='ask-deny']")?.addEventListener("click", (e) =>
      sendAskAnswer(session, standing, "deny", e.currentTarget));
  }
  // Picking a mode runs nothing: it is remembered, and the next turn launched
  // from here is launched with it. Hence no waiting state and no confirmation.
  for (const chip of detailPane.querySelectorAll("[data-mode]")) {
    chip.addEventListener("click", () => pickMode(session, chip));
  }

  // Both git tabs carry the same header, and the header has buttons in it.
  if (isGitTab(app.tab)) wireGit(session);

  detailPane.querySelector("[data-act='more']")?.addEventListener("click", showMoreChat);
  // A change opens on the bar or on the folded patch itself — the whole block is
  // the target, because "click it to see all of it" is what a folded thing says.
  // A selection wins, though: dragging across the visible lines to quote them
  // must not fold the thing you were reading out from under you.
  for (const opener of detailPane.querySelectorAll<HTMLElement>("[data-act='change']")) {
    opener.addEventListener("click", (event) => {
      if (String(window.getSelection?.() ?? "")) return;
      event.preventDefault();
      showChange(opener.dataset.id, session);
    });
  }
  detailPane.querySelector("[data-act='change-close']")?.addEventListener("click", hideChange);

  const chatAfter = detailPane.querySelector("#chatScroll");
  if (chatAfter && app.tab === "chat" && chatFromBottom !== null) {
    chatAfter.scrollTop = chatAfter.scrollHeight - chatFromBottom;
    chat.chatGrew = false;
  } else if (chatAfter && app.tab === "chat" && wasAtBottom) {
    chatAfter.scrollTop = chatAfter.scrollHeight;
  } else if (chatAfter && app.tab === "chat" && sameSession) {
    chatAfter.scrollTop = chatWas;
  }
  // Nothing to jump to the bottom of while a comparison has the pane: the dock
  // is about the conversation, and the conversation is not what is on screen.
  if (chatAfter && app.tab === "chat" && chat.changeShown === null) wireJumpDock(chatAfter);
  wireHeaderFold(chatAfter);

  // Fade the panel in when it is showing something genuinely different — another
  // tab, another session. Not on every rebuild: the pane is rebuilt whenever a
  // message lands or a state changes, and a working session would have the thing
  // you are reading pulsing at you every few seconds.
  if (cameFrom !== `${session.sessionId}/${app.tab}`) panelChangedAt = Date.now();
  const since = Date.now() - panelChangedAt;
  if (since < PANEL_FADE_MS) {
    const wrap = detailPane.querySelector<HTMLElement>(".panel-wrap");
    if (wrap) {
      wrap.classList.add("panel-wrap--entering");
      // A tab that has to fetch its contents renders twice — once empty, once
      // filled — and the second rebuild would otherwise restart the fade from
      // nothing, which is the flicker this was meant to remove. Winding the
      // animation back by the time already served picks it up where the last
      // one left off.
      if (since) wrap.style.animationDelay = `-${since}ms`;
      // Taken off once it has played, so the pane is not left claiming to be
      // arriving long after it arrived.
      wrap.addEventListener("animationend", () => {
        wrap.classList.remove("panel-wrap--entering");
        wrap.style.animationDelay = "";
      }, { once: true });
    }
  }
}

/* When the pane last began showing something new, and how long it takes to
   arrive — the fade lives across renders, so it is timed rather than one-shot. */
let panelChangedAt = 0;
const PANEL_FADE_MS = 300; /* --md-sys-motion-duration-medium2 */

/* The two buttons that float over the transcript:

   "Last request" — a long answer buries the thing you asked for, so this jumps
   back to your last message. It shows only when that message is off screen, and
   the arrow points the way it will travel.

   "Jump to latest" — the other direction. Reading back through a long answer
   leaves you a long way from the newest message, and dragging the whole way is
   the one scroll nobody wants to make by hand.

   Both are driven off the same scroll, so they are wired together: two listeners
   measuring the same box on every frame of a flick is work for nothing. */
/* Far enough from the end to be worth a button. Generous, so nudging around near
   the bottom does not have it flickering in and out at the corner of your eye. */
const AT_BOTTOM = 120;

function wireJumpDock(scroller) {
  const last = detailPane.querySelector<HTMLButtonElement>("#jumpLast");
  const bottom = detailPane.querySelector("#jumpBottom");
  if (!last || !bottom) return;
  const target = () => [...scroller.querySelectorAll(".msg--user")].pop() || null;

  // Out of the tab order while faded out: an invisible stop between the
  // transcript and the composer is a trap for anyone arriving by keyboard.
  const show = (button, on) => {
    (on ? reveal : conceal)(button);
    button.tabIndex = on ? 0 : -1;
  };

  const sync = () => {
    const box = scroller.getBoundingClientRect();
    const mark = target();
    if (!mark) show(last, false);
    else {
      const spot = mark.getBoundingClientRect();
      const above = spot.bottom < box.top + 8;
      const below = spot.top > box.bottom - 8;
      show(last, above || below);
      // Only while it is on screen: turning the arrow over on a pill that is
      // already fading out is motion nobody asked to watch.
      if (above || below) last.dataset.dir = above ? "up" : "down";
    }
    show(bottom, scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight > AT_BOTTOM);
  };

  last.addEventListener("click", () => {
    const mark = target();
    if (!mark) return;
    const top = mark.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
    scroller.scrollTo({ top: scroller.scrollTop + top - 12, behavior: glide() });
  });
  bottom.addEventListener("click", () => {
    scroller.scrollTo({ top: scroller.scrollHeight, behavior: glide() });
  });
  scroller.addEventListener("scroll", sync, { passive: true });
  sync();
}

/* Folding the detail header away on a phone.

   The header is the session's whole context — subject, mode chips, how full the
   conversation is, folder, branch, uptime, the state trace. On a desktop that
   sits beside the conversation for free. On a phone it is most of the screen
   before a single message is drawn, so it folds down to its title as soon as
   you scroll into the transcript, and comes back when you scroll to the top.
   The chevron in the title row does either on demand.

   Only the attribute changes here; what it means is in responsive.css, and it
   means nothing above phone width.

   Both thresholds and the hold below exist for the same reason: folding the
   header makes the panel taller, which moves the scroll under the reader, and
   anything that folds and unfolds on adjacent pixels reads as a shudder. */
const FOLD_AT = 120;        /* scrolled this far in, the header folds */
const UNFOLD_AT = 24;       /* back to about the top, it comes out again */
/* A panel with less than this left to scroll keeps its header: folding one away
   to reveal two more lines, and bouncing the scroll back to the top doing it, is
   a worse trade than the room it wins. */
const FOLD_WORTH_IT = 200;
const onPhone = () => matchMedia("(max-width: 599px)").matches;
/* Someone who unfolds the header by hand while reading halfway down means it:
   without this the very next scroll event folds it straight back, and the
   button looks broken. The hold is let go when the scroll comes back to the
   top, which is the point at which the reader is plainly done with it. */
let foldHeld = false;

function setFold(folded) {
  ui.headerFolded = folded;
  const header = detailPane.querySelector<HTMLElement>(".detail-header");
  if (header) header.dataset.folded = folded ? "1" : "0";
  const button = detailPane.querySelector<HTMLElement>(".fold-button");
  if (!button) return;
  const what = folded ? "Show the session's details" : "Fold the details away";
  button.setAttribute("aria-expanded", String(!folded));
  button.setAttribute("aria-label", what);
  button.title = what;
}

function wireHeaderFold(scroller) {
  detailPane.querySelector<HTMLElement>(".fold-button")?.addEventListener("click", () => {
    foldHeld = true;
    setFold(!ui.headerFolded);
  });
  if (!scroller) return;
  const sync = () => {
    if (!onPhone()) return;
    const room = scroller.scrollHeight - scroller.clientHeight;
    if (scroller.scrollTop < UNFOLD_AT) { foldHeld = false; setFold(false); }
    else if (!foldHeld && scroller.scrollTop > FOLD_AT && room > FOLD_WORTH_IT) setFold(true);
  };
  scroller.addEventListener("scroll", sync, { passive: true });
  sync();
}

/* Smooth scrolling is motion like any other, and someone who has asked their
   system for less of it means this too. */
const glide = () => (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth");

/* One line, matching the send button beside it. */
const COMPOSER_MIN = 48;
/* Leave the conversation something to live in, whatever the window does. */
const composerMax = () => Math.max(COMPOSER_MIN, detailPane.clientHeight - 200);

export function growField(field) {
  if (ui.composerHeight) {
    // A pinned box keeps its height and scrolls inside, so the 40vh ceiling
    // that bounds the auto-grown one has to come off.
    field.style.maxHeight = "none";
    field.style.height = `${Math.min(ui.composerHeight, composerMax())}px`;
    return;
  }
  field.style.maxHeight = "";
  field.style.height = "auto";
  field.style.height = `${field.scrollHeight}px`;
}

function setComposerHeight(px, field) {
  ui.composerHeight = Math.round(Math.min(Math.max(px, COMPOSER_MIN), composerMax()));
  localStorage.setItem("cbu-composer-height", String(ui.composerHeight));
  growField(field);
  syncGrip(field);
}

function resetComposerHeight(field) {
  ui.composerHeight = null;
  localStorage.removeItem("cbu-composer-height");
  growField(field);
  syncGrip(field);
}

function syncGrip(field) {
  const grip = detailPane.querySelector("#composerGrip");
  if (!grip) return;
  grip.setAttribute("aria-valuenow", String(Math.round(ui.composerHeight || field.clientHeight)));
  grip.setAttribute("aria-valuemin", String(COMPOSER_MIN));
  grip.setAttribute("aria-valuemax", String(Math.round(composerMax())));
}

function wireComposerGrip(grip, field) {
  grip.addEventListener("pointerdown", (event) => {
    // Without this the drag selects the transcript text behind the pointer.
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = field.getBoundingClientRect().height;
    // Up is bigger: the box grows towards the conversation it is pushing back.
    const move = (e) => setComposerHeight(startHeight + (startY - e.clientY), field);
    const done = () => {
      grip.removeEventListener("pointermove", move);
      grip.classList.remove("is-dragging");
      ui.resizingComposer = false;
      renderDetail(); // let through whatever the drag held off
    };
    grip.setPointerCapture(event.pointerId);
    grip.classList.add("is-dragging");
    ui.resizingComposer = true;
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", done, { once: true });
    grip.addEventListener("pointercancel", done, { once: true });
  });
  grip.addEventListener("dblclick", () => resetComposerHeight(field));
  grip.addEventListener("keydown", (event) => {
    const from = ui.composerHeight || field.clientHeight;
    const step = event.shiftKey ? 64 : 16;
    if (event.key === "ArrowUp") setComposerHeight(from + step, field);
    else if (event.key === "ArrowDown") setComposerHeight(from - step, field);
    else if (event.key === "Home" || event.key === "Escape") resetComposerHeight(field);
    else return;
    event.preventDefault();
  });
}

/* An empty box is the absence of a draft, not a draft of nothing — otherwise the
   map fills up with blanks for every session whose composer was ever touched. */
export function setSayDraft(sessionId, text) {
  if (text) sayDrafts.set(sessionId, text);
  else sayDrafts.delete(sessionId);
}

/* ==========================================================================
   Asking for a skill by name.

   A leading /name is expanded by the terminal, not by the session, and a message
   arriving over the messaging socket is never expanded — Claude Code queues it
   with slash commands switched off on purpose, since command markdown can carry
   inline shell. So the panel does not pretend to be the terminal. It offers what
   the session could be asked for, and turns what you typed into the sentence
   that asks for it, which is the one thing an injected turn can do.

   The rewrite is shown before it is sent. Nothing is expanded behind your back.
   ========================================================================== */
let catalog = null;       // { entries, terminalOnly } from /api/commands
let catalogFor = null;    // the session it was read for
let catalogWaiting = false;
let cmdRows = [];         // what the picker is showing, in the order it shows it
let cmdIndex = 0;         // which of those the keyboard is on
let cmdOff = false;       // Escape puts the list away without touching the text

/* The walk costs a few dozen file heads, so it is paid for by the first "/" of a
   session rather than by opening the conversation. */
async function loadCatalog(session) {
  if (catalogFor === session.sessionId || catalogWaiting) return;
  catalogWaiting = true;
  try {
    const response = await fetch(`/api/commands?sessionId=${encodeURIComponent(session.sessionId)}`);
    const data = await response.json();
    if (data && data.ok) catalog = data;
  } catch (error) {
    // The picker is a convenience; the box sends with or without it.
  } finally {
    catalogWaiting = false;
    catalogFor = session.sessionId;
    if (detailPane.dataset.sessionId === session.sessionId) syncCmdBar(session);
  }
}

/* What the box holds, read as a request for something by name: the name while it
   is still being typed, and the arguments once a space has been typed after it.
   The slash has to be the first character, as it is in the terminal — a message
   that happens to open with a path is a message. */
export function slashOf(text) {
  const match = /^\/([A-Za-z0-9:._-]*)(\s([\s\S]*))?$/.exec(text);
  if (!match) return null;
  return { name: match[1], args: (match[3] || "").trim(), chosen: match[2] !== undefined };
}

const cmdEntry = (name) => (catalog?.entries || [])
  .find((entry) => entry.name.toLowerCase() === name.toLowerCase()) || null;

/* Whether a slash command would go nowhere, which is a question about the
   transport rather than about the command.

   Over a session's **messaging socket** every one of them goes nowhere: Claude
   Code queues an injected message with slash commands switched off on purpose,
   so `/compact` arrives as four words of prose. That is what the panel's own
   list describes, and for a session in a terminal it is right.

   Over a **held pipe** they are expanded, and the session says so itself. Its
   `init` frame carries `slash_commands` and `terminal_slash_commands`, and down
   that transport the second list is two entries long — `doctor` and `color` —
   while `compact`, `context`, `model` and `clear` are all in the first. So for a
   session the panel runs, the session's own answer wins over the panel's guess.
   Asked, it is right; guessed, it refused `/compact` on the one kind of session
   where compacting works. */
/* What a held pipe keeps for the terminal anyway, until the session says
   otherwise. Two, observed on 2.1.239, and they are the *default* rather than
   the answer: the session's own `terminal_slash_commands` replaces this the
   moment one arrives.

   Falling back to the panel's socket list here instead was a bug, and one that
   bit in the ordinary case rather than a corner. A held process emits nothing
   until it is sent something, so `commands` is empty for every session that has
   been started and not yet spoken to — and typing `/compact` at a session you
   have just brought up is exactly when you would. The refusal came back on a
   session where it works, which is where this started. Whether a command is
   expanded is settled by the transport and is known without asking; only *which
   ones are excepted* needs the session, and that is all the list is used for. */
const PIPE_TERMINAL_ONLY = ["doctor", "color"];

export const terminalOnly = (name, session) => {
  if (session && runsHere(session)) {
    const said = ownedFor(session).commands;
    return (said?.terminalOnly || PIPE_TERMINAL_ONLY).includes(name.toLowerCase());
  }
  return (catalog?.terminalOnly || []).includes(name.toLowerCase());
};

/* And whether it is worth sending at all: a session that has listed what it
   takes will not act on a name that is not on the list. Only asked of a session
   that has answered — before that, anything goes and the catalogue guesses. */
const knownCommand = (name, session) => {
  const said = session && runsHere(session) ? ownedFor(session).commands : null;
  if (!said || !said.available?.length) return true;
  return said.available.some((x) => x.toLowerCase() === name.toLowerCase());
};

/* Everything the typed name could still become. A plugin's entries are addressed
   `plugin:name`, so what you type is matched against both halves — typing the
   skill's own name finds it without knowing which plugin it came from. */
function cmdMatches(typed, session) {
  const needle = typed.toLowerCase();
  const rank = (name) => {
    const whole = name.toLowerCase();
    if (whole.startsWith(needle)) return 0;
    if (whole.split(":").pop().startsWith(needle)) return 1;
    return 2;
  };
  // The catalogue is a walk of the skill and command folders on disk, so it
  // finds what was written down and misses what is built in: `/compact`,
  // `/context`, `/model`, `/clear` are Claude Code's own and have no file to
  // find. A session the panel runs lists them itself, and a command you cannot
  // type the name of is a command you do not have — so its list is folded in,
  // with the catalogue's own entries winning where both know a name, since only
  // those carry a description.
  const said = session && runsHere(session) ? ownedFor(session).commands : null;
  const known = new Set((catalog?.entries || []).map((e) => e.name.toLowerCase()));
  const extra = (said?.available || [])
    .filter((name) => !known.has(name.toLowerCase())
                   && !(said.terminalOnly || []).includes(name.toLowerCase()))
    .map((name) => ({ name, kind: "command", description: "this session's own" }));
  return [...(catalog?.entries || []), ...extra]
    .filter((entry) => entry.name.toLowerCase().includes(needle))
    .sort((a, b) => rank(a.name) - rank(b.name) || a.name.localeCompare(b.name))
    .slice(0, 40);
}

/* The sentence that goes out. Anything the catalog does not know is left exactly
   as it was written — a typo is better sent as itself than as a guess. */
export function sentAs(text, session) {
  const asked = slashOf(text);
  if (!asked || !asked.name) return text;
  // A session the panel runs expands slash commands itself — that is the whole
  // difference between the two transports — so what you typed is what goes, and
  // rewriting it would be the panel talking over you. Proved rather than
  // assumed: a literal `/compact` turn down a held pipe compacted, where "Use
  // the compact command." would only have been read as a request.
  if (session && runsHere(session) && !terminalOnly(asked.name, session)) return text;
  const entry = cmdEntry(asked.name);
  if (!entry) return text;
  const what = entry.kind === "command" ? "command" : "skill";
  return asked.args
    ? `Use the ${entry.name} ${what}: ${asked.args}`
    : `Use the ${entry.name} ${what}.`;
}

/* The line under the list: what will be sent, or why nothing useful would be. */
function cmdNote(asked, session) {
  if (!asked.name) return "";
  const typed = `/${asked.name} ${asked.args}`.trim();
  if (terminalOnly(asked.name, session)) {
    return `<span class="md-mono">/${escapeHtml(asked.name)}</span> only works at this session's
      own prompt — the terminal keeps that one to itself.`;
  }
  // A session of ours takes the command itself, so there is no rewriting to
  // report. What is worth saying is the opposite of the old note: this one goes
  // in as you typed it.
  if (session && runsHere(session) && sentAs(typed, session) === typed) {
    return knownCommand(asked.name, session)
      ? `Goes in as <span class="md-mono">${escapeHtml(typed)}</span> — this session runs it.`
      : `This session does not list a <span class="md-mono">/${escapeHtml(asked.name)}</span> —
         it will be sent as plain text.`;
  }
  const entry = cmdEntry(asked.name);
  if (entry) return `Sends as “${escapeHtml(sentAs(typed, session))}”`;
  if (!asked.chosen) return "";
  return `Nothing here is called <span class="md-mono">/${escapeHtml(asked.name)}</span> —
    it will be sent as plain text.`;
}

export function syncCmdBar(session) {
  const bar = detailPane.querySelector<HTMLElement>("#cmdBar");
  const field = detailPane.querySelector<HTMLTextAreaElement>("#sayField");
  if (!bar || !field) return;
  const asked = slashOf(field.value);
  if (!asked) {
    bar.hidden = true;
    bar.innerHTML = "";
    cmdRows = [];
    cmdIndex = 0;
    cmdOff = false;
    return;
  }
  loadCatalog(session);

  // Once a space has been typed the name is settled, so the list stands down and
  // leaves the line saying what will be sent.
  cmdRows = (asked.chosen || cmdOff) ? [] : cmdMatches(asked.name, session);
  cmdIndex = Math.min(cmdIndex, Math.max(0, cmdRows.length - 1));
  const note = cmdNote(asked, session);
  bar.hidden = !cmdRows.length && !note;
  bar.innerHTML =
    (cmdRows.length
      ? `<ul class="cmdbar__list" role="listbox" aria-label="Skills and commands">${cmdRows
          .map((entry, index) => `<li role="none">
            <button class="cmdbar__item md-body-small" type="button" role="option"
              data-index="${index}" aria-selected="${index === cmdIndex}">
              <span class="cmdbar__name md-label-large">/${escapeHtml(entry.name)}</span>
              <span class="cmdbar__desc">${escapeHtml(entry.description)}</span>
              <span class="cmdbar__source md-label-small">${escapeHtml(entry.source)}</span>
            </button></li>`).join("")}</ul>`
      : "")
    + (note ? `<p class="cmdbar__as md-label-medium">${note}</p>` : "");

  for (const button of bar.querySelectorAll<HTMLElement>(".cmdbar__item")) {
    // Taking one with the pointer must not take the caret out of the box.
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", () => takeCmd(session, cmdRows[Number(button.dataset.index)]));
  }
  bar.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
}

function takeCmd(session, entry) {
  const field = detailPane.querySelector<HTMLTextAreaElement>("#sayField");
  if (!field || !entry) return;
  // A trailing space is the point: it settles the name and moves the bar on to
  // saying what will be sent, with the caret where the arguments go.
  field.value = `/${entry.name} `;
  setSayDraft(session.sessionId, field.value);
  field.focus();
  field.setSelectionRange(field.value.length, field.value.length);
  cmdIndex = 0;
  growField(field);
  syncCmdBar(session);
}

/* The picker gets first refusal on the keys it owns, and only while it is
   standing — Enter is the send key every other moment. */
function cmdKey(event, session) {
  if (event.key === "Escape" && !detailPane.querySelector<HTMLElement>("#cmdBar")?.hidden) {
    cmdOff = true;
    syncCmdBar(session);
    return true;
  }
  if (!cmdRows.length) return false;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    const step = event.key === "ArrowDown" ? 1 : cmdRows.length - 1;
    cmdIndex = (cmdIndex + step) % cmdRows.length;
    syncCmdBar(session);
    return true;
  }
  if (event.key === "Enter" || event.key === "Tab") {
    takeCmd(session, cmdRows[cmdIndex]);
    return true;
  }
  return false;
}
/* ------------------------------------------------------- ending a session */
let endTarget = null;
function openEndDialog(session) {
  endTarget = session;
  const waiting = session.status === "waiting" || session.status === "busy" || session.status === "shell";
  // A session the panel holds has no pid worth naming — the process is ours, and
  // ending it is the panel letting go rather than a signal to a terminal.
  const holding = session.alive === false && ownedFor(session).running;
  // Ending is the only way out, so the dialog has to say what becomes of the row
  // as well as the process: it goes, unless the session was pinned on purpose.
  const fate = session.pinned
    ? `It is pinned, so the row stays: type into it and it starts back up.`
    : `The row comes off the dashboard. The conversation stays on disk, where
       <span class="md-mono">claude --resume</span> in
       <span class="md-mono">${escapeHtml(session.folder || shorten(session.cwd, 1))}</span> still finds it.`;
  document.getElementById("endHeadline").textContent = holding
    ? (session.pinned ? "Stop running it here?" : "Stop it and remove the row?")
    : "End this session?";
  document.getElementById("endSupporting").innerHTML = holding
    ? `The panel lets go of <span class="md-mono">${escapeHtml(session.name)}</span>.
       ${session.status === "busy" ? "Mid-turn — what it is doing stops. " : ""}${fate}`
    : `<span class="md-mono">${escapeHtml(session.name)}</span> in
       <span class="md-mono">${escapeHtml(shorten(session.cwd, 2))}</span>, pid
       <span class="md-mono">${session.pid}</span>.
       ${waiting ? "Mid-turn — what it is doing stops. " : ""}${fate}`;
  // Force is a signal, and there is nothing to signal when the process is a pipe
  // of ours: the panel closes it and waits. So the harder button is not offered.
  document.getElementById("endForce").hidden = holding;
  document.getElementById("endConfirm").textContent = holding
    ? (session.pinned ? "Stop it" : "Stop it and remove") : "End session";
  endScrim.dataset.open = "true";
  document.getElementById("endCancel").focus();
}
function closeEndDialog() {
  endScrim.dataset.open = "false";
  endTarget = null;
  detailPane.querySelector<HTMLElement>("[data-act='end']")?.focus();
}
/* Starting a session the panel runs. There is nothing to ask first: the panel
   names the session itself — `--session-id` takes a uuid of our choosing — so
   the row exists the moment it is started and the first message is the first
   thing you type into it, in the session, like any other. */
async function startOwnedSession(where, item) {
  if (item) item.disabled = true;
  showSnackbar(where.pick ? "Pick a folder in the chooser…" : "Starting it…", 300000);
  try {
    const response = await fetch("/api/owned/new", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(where.pick ? { pick: true } : { sessionId: where.from.sessionId }),
    });
    const data = await response.json().catch(() => ({}));
    showSnackbar(data.message || (response.ok ? "Started" : "That did not start"));
    if (data.ok && data.sessionId) {
      app.selectedId = data.sessionId;
      localStorage.setItem("cbu-selected", data.sessionId);
      setTab("chat");
      await poll();
      detailPane.querySelector<HTMLTextAreaElement>("#sayField")?.focus();
    }
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    if (item) item.disabled = false;
  }
}

/* Making a live session interactive. The wording names what is lost — the
   terminal — and what is not, which is the conversation: the row is kept before
   anything is signalled, so the chat is still there afterwards. */
const adoptScrim = document.getElementById("adoptScrim");
let adoptTarget = null;

function openAdoptDialog(session) {
  adoptTarget = session;
  const name = `<span class="md-mono">${escapeHtml(session.name)}</span>`;
  // Only the conversation is worth a clause, and only when there is one: saying
  // "the conversation is kept" about a session nobody has typed at yet reads as
  // a warning where nothing is at stake.
  const carried = session.spoken ? " The conversation is kept." : "";
  document.getElementById("adoptSupporting").innerHTML =
    `${name} runs in a terminal, so making it interactive <strong>ends that session</strong>.${carried}
     You then type to it here, pick its mode, and answer its prompts. It stays that way:
     the panel does not hand a conversation back to a terminal.`;
  document.getElementById("adoptForce").hidden = true;
  adoptScrim.dataset.open = "true";
  document.getElementById("adoptConfirm").focus();
}

function closeAdoptDialog() {
  adoptScrim.dataset.open = "false";
  adoptTarget = null;
}

document.getElementById("adoptCancel").addEventListener("click", closeAdoptDialog);
adoptScrim.addEventListener("click", (event) => { if (event.target === adoptScrim) closeAdoptDialog(); });
/* Force is not offered up front. A session mid-turn takes a moment to stop, and
   the first thing to try is asking it to — so the harder button only appears
   once asking has demonstrably not worked. */
async function adopt(button, force) {
  const session = adoptTarget;
  if (!session) return;
  button.disabled = true;
  showSnackbar(force ? "Forcing it to stop…" : "Ending the terminal session…", 20000);
  try {
    const response = await fetch("/api/owned/adopt", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: session.sessionId, force }),
    });
    const data = await response.json().catch(() => ({}));
    showSnackbar(data.message || (response.ok ? "Done" : "That did not work"));
    if (data.ok) {
      // Landing on it with the caret in the box is the whole point of the word
      // "interactive": there is nothing further to do but type.
      const id = session.sessionId;
      closeAdoptDialog();
      app.selectedId = id;
      localStorage.setItem("cbu-selected", id);
      await poll();
      detailPane.querySelector<HTMLTextAreaElement>("#sayField")?.focus();
    } else if (data.needsForce) {
      document.getElementById("adoptForce").hidden = false;
    }
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    button.disabled = false;
    poll();
  }
}

/* Compacting: the session summarises what has happened so far and carries on
   from the summary instead of from the whole conversation.

   It asks first. Not because anything is lost from disk — the transcript is
   untouched and the chat above still reads the same afterwards — but because
   what the session *remembers* is replaced by a summary of itself, and there is
   no putting it back. That is a decision, and a decision one mis-click away from
   a button that sits in the header is a decision made by accident. It also takes
   the better part of a minute, which the dialog says rather than leaving you
   wondering whether the click registered.

   The percentage does not move afterwards, and this is not a bug worth chasing:
   the reading comes off the last request the model answered, so it only catches
   up when the session is next used. What the compaction did is reported from its
   own frames instead — see contextBar. */
async function compactSession(session, button) {
  const ctx = session.context;
  const ok = await askConfirm({
    headline: "Compact this conversation?",
    body: `<span class="md-mono">${escapeHtml(session.name)}</span> summarises everything so far
      and carries on from that summary${ctx ? `, in place of the
      ${escapeHtml(tokens(ctx.tokens))} tokens it is carrying now` : ""}. It takes
      up to a minute, and it cannot be undone — though nothing is lost from the
      transcript, so the conversation above still reads the same.`,
    confirmLabel: "Compact it",
    danger: false,
  });
  if (!ok) return;
  await run("/api/owned/compact", session, button, "Compacting…");
}

/* Taking a row off the list. The conversation is not what goes — the transcript
   is Claude Code's and stays where it is, so this is the row and nothing else,
   which is why it asks in one line rather than spelling out a loss. A session
   the panel is running is stopped on the way out: a held process with no row is
   a session nobody can see. */
async function openForgetDialog(session) {
  const running = ownedFor(session).running;
  const ok = await askConfirm({
    headline: running ? "Stop it and remove the row?" : "Remove this row?",
    body: `<span class="md-mono">${escapeHtml(session.name)}</span> comes off the list${running
      ? `, and the turn it is holding open here is stopped`
      : ``}. The conversation itself stays on disk, where
      <span class="md-mono">claude --resume</span> in ${escapeHtml(session.folder || "its folder")} still finds it.`,
    confirmLabel: running ? "Stop it and remove" : "Remove",
  });
  if (!ok) return;
  try {
    const response = await fetch("/api/forget", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: session.sessionId }),
    });
    const data = await response.json().catch(() => ({}));
    showSnackbar(data.message || (response.ok ? "Removed" : "That did not work"));
    if (data.ok && app.selectedId === session.sessionId) {
      app.selectedId = null;
      localStorage.removeItem("cbu-selected");
    }
  } catch (error) {
    showSnackbar("Could not reach the server");
  }
  poll();
}

document.getElementById("adoptConfirm").addEventListener("click", (e) => adopt(e.currentTarget, false));
document.getElementById("adoptForce").addEventListener("click", (e) => adopt(e.currentTarget, true));

document.getElementById("endCancel").addEventListener("click", closeEndDialog);
endScrim.addEventListener("click", (event) => { if (event.target === endScrim) closeEndDialog(); });
for (const [id, force] of [["endConfirm", false], ["endForce", true]] as [string, boolean][]) {
  document.getElementById(id).addEventListener("click", async (event) => {
    const session = endTarget;
    if (!session) return;
    const data = await run("/api/end", session, control(event), null, { force });
    if (data?.removed && app.selectedId === session.sessionId) {
      app.selectedId = null;
      localStorage.removeItem("cbu-selected");
    }
    closeEndDialog();
  });
}

export function openSettings(open) {
  app.showingSettings = open;
  settingsButton.setAttribute("aria-pressed", String(open));
  // On one pane at a time the page is the detail side, so opening it has to
  // bring that side forward the way picking a session does.
  if (open) panes.dataset.view = "detail";
  // The pane it is leaving is a session's, and that session's signature is
  // still on the pane — clear it or the rebuild is skipped as a repeat.
  detailPane.dataset.signature = "";
  render();
  if (open) detailPane.querySelector<HTMLElement>("#closeSettings")?.focus();
  else settingsButton.focus();
}
settingsButton.addEventListener("click", () => openSettings(!app.showingSettings));
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (askScrim.dataset.open === "true") closeAsk(false);
  else if (adoptScrim.dataset.open === "true") closeAdoptDialog();
  else if (endScrim.dataset.open === "true") closeEndDialog();
  else if (planScrim.dataset.open === "true") openPlan(false);
  else if (updateScrim.dataset.open === "true") openUpdate(false);
  else if (app.showingSettings) openSettings(false);
  // A comparison standing in front of the conversation is the nearest thing to
  // modal in the pane, and Escape is how you come back out of it.
  else if (hideChange()) { /* the conversation is back */ }
  // Nothing modal is open, so Escape drops whatever rows are picked.
  else clearPicked();
});
backButton.addEventListener("click", () => { panes.dataset.view = "list"; });
pickGroup.addEventListener("click", () => groupPicked());
pickClear.addEventListener("click", () => clearPicked());

/* ----------------------------------------------------------- interactions */
document.addEventListener("pointerdown", (event) => {
  const target = hitClosest<HTMLButtonElement>(event, ".md-state");
  if (!target || target.disabled) return;
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const box = target.getBoundingClientRect();
  const size = Math.max(box.width, box.height);
  const ripple = document.createElement("span");
  ripple.className = "md-ripple";
  ripple.style.width = ripple.style.height = `${size}px`;
  ripple.style.left = `${event.clientX - box.left - size / 2}px`;
  ripple.style.top = `${event.clientY - box.top - size / 2}px`;
  target.appendChild(ripple);
  ripple.addEventListener("animationend", () => ripple.remove());
});

/* -------------------------------------------------------------------- boot */
serveRefresh({ render, renderDetail, poll });

loadSettings();
applyScheme();
if (app.selectedId) panes.dataset.view = "detail";
poll();
setInterval(poll, 1000);
fetchPlan(true);
setInterval(() => fetchPlan(false), 30_000);
// The panel's own version, on its own much longer clock: the server holds the
// answer for hours, and a release does not land while you are looking at the bar.
fetchUpdate(true);
setInterval(() => fetchUpdate(false), 60_000);
setInterval(() => {
  const now = Date.now() / 1000 + app.skew;
  for (const node of document.querySelectorAll<HTMLElement>("[data-since]")) {
    node.textContent = duration(now - Number(node.dataset.since));
  }
  // The compaction bar moves on the clock, not on the wire — nothing arrives
  // between `compacting` and the result — so it is walked forward here for the
  // same reason the durations are, rather than by repainting the pane at 1Hz
  // for a number the signature cannot see change.
  for (const bar of document.querySelectorAll<HTMLElement>("[data-compact-since]")) {
    const going = compactPct(now - Number(bar.dataset.compactSince));
    const fill = bar.querySelector<HTMLElement>(".ctx__fill");
    if (fill) fill.style.width = `${Math.max(2, going)}%`;
    bar.setAttribute("aria-label", `Compacting, about ${going}% of the way`);
    const said = bar.parentElement?.querySelector(".ctx__said");
    if (said) said.textContent = `compacting… ${going}%`;
  }
}, 1000);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  poll();
  // The repository was left alone while the tab was away, so catch it up now
  // rather than at the end of an interval that started before you looked.
  if (app.selectedId && isGitTab(app.tab)) fetchGit(true);
});
