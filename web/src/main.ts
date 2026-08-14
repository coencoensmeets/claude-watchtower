import {
  argbFromHex, hexFromArgb, Hct, SchemeVibrant, TonalPalette,
} from "/vendor/material-color-utilities.js";
import { ICON, HOST_KIND, HOST_BY_PROCESS, hostOf } from "./ui/icons.js";
import { duration, shorten, clip, clockOf, escapeHtml, ago, plural, tokens } from "./ui/format.js";
import { renderMarkdown } from "./ui/markdown.js";
import { app, chat, repo, spend, sidebar, ui, loadKeySet, CHAT_PAGE } from "./state.js";
import { fetchPlan, openPlan, paintPlanDialog, planButton, planScrim } from "./views/plan.js";
import { MODE_LABELS, WINDOW_CONFIDENCE, windowSays, isAmbiguous, isRemembered } from "./sessions/facts.js";
import { aboutPanel, usagePanel } from "./views/usage.js";
import { EXIT_MS, exitTimers, reveal, conceal } from "./ui/overlay.js";
import { askScrim, askText, askConfirm, closeAsk } from "./ui/ask.js";
import { showSnackbar } from "./ui/snackbar.js";
import { DEFAULT_SEED, SEED_PRESETS, CONTRAST_LEVELS, STATE_BASE_HUES, MIN_HUE_GAP, MAX_CUSTOM_CHROMA, SYS_ROLES, kebab, hueDistance, firstFreeHue, customRoles } from "./ui/theme.js";
import { STATE, STATE_ALIAS, stateKeyOf, stateOf, displaySince, STATE_ORDER } from "./sessions/state.js";
import { panes, sessionList, listEmpty, detailPane, chipSet, barSupporting, themeToggle, themeLabel, snackbar, settingsScrim, endScrim, swatchRow, seedReadout, contrastGroup, backButton, pickBar, pickCount, pickGroup, pickClear } from "./ui/dom.js";
import { run } from "./net.js";
import { serveRefresh } from "./refresh.js";
import { sessionMenu, menuIsOpen, openMenu, closeSessionMenu } from "./ui/menu.js";
import { gitPanel, historyPanel, wireGit } from "./views/git.js";
import { commitDrafts, fetchGit, closeDiff } from "./views/git.js";
import { gitStamp } from "./views/git.js";

/* ==========================================================================
   Dynamic colour — one seed generates the entire scheme.
   ========================================================================== */



/* `plenty` is not a session state: it is the green a plan figure takes while
   there is still room, and it comes from here so it is hue-spaced against the
   seed and the other two like they are. Keep it last so adding it did not move
   the hues waiting and idle already had. */












function loadSettings() {
  const params = new URLSearchParams(location.search);
  const seed = params.get("seed") || localStorage.getItem("cbu-seed");
  if (seed && /^#[0-9a-f]{6}$/i.test(seed)) app.settings.seed = seed;
  const theme = params.get("theme") || localStorage.getItem("cbu-theme");
  if (theme === "dark" || theme === "light") app.settings.dark = theme === "dark";
  const contrast = params.get("contrast") || localStorage.getItem("cbu-contrast");
  if (CONTRAST_LEVELS.some((l) => l.key === contrast)) app.settings.contrast = contrast;
}
function persist() {
  localStorage.setItem("cbu-seed", app.settings.seed);
  localStorage.setItem("cbu-theme", app.settings.dark ? "dark" : "light");
  localStorage.setItem("cbu-contrast", app.settings.contrast);
}

function applyScheme() {
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
  themeToggle.checked = app.settings.dark;
  themeLabel.textContent = app.settings.dark ? "Dark" : "Light";
  paintFavicon();
}

/* ------------------------------------------------------------- state map */

/* `shell` is not shown as a state of its own. It is what Claude Code writes on
   the way into a foreground command *and* what it leaves behind when a turn
   ends, so on screen it was a second colour for the same thing: waiting. The raw
   status still drives behaviour (a message can be queued for a shell session);
   only the display folds it in. */




/* When the state you can see began. The server times the raw status, which
   restarts every time a waiting session dips through `shell`; on screen nothing
   happened, so the clock should not jump back to zero. */

for (const entry of Object.values(STATE)) {
  if (entry.sys) {
    entry.colour = "var(--md-sys-color-primary)";
    entry.container = "var(--md-sys-color-primary-container)";
    entry.onContainer = "var(--md-sys-color-on-primary-container)";
  } else if (entry.prefix) {
    entry.colour = `var(${entry.prefix}-color)`;
    entry.container = `var(${entry.prefix}-container)`;
    entry.onContainer = `var(${entry.prefix}-on-container)`;
  } else {
    entry.colour = "var(--md-sys-color-outline)";
    entry.container = "var(--md-sys-color-surface-container-high)";
    entry.onContainer = "var(--md-sys-color-on-surface-variant)";
  }
}



/* ---------------------------------------------------------------- element refs */





















/* How much conversation the chat tab asks for. A page at a time, because the
   whole point of reading backwards is that a long session costs no more than a
   short one — but a truncated transcript can be asked for more, up to what the
   server will read back. Reset per session: the depth you dug to in one
   conversation says nothing about the next. */
const CHAT_LIMIT_MAX = 500;
// Set when "show more" is what caused the re-render, so the pane can hold the
// message you were reading in place instead of jumping.
/* A half-typed message belongs to the session it was written for, not to the
   composer: switching away puts it aside and coming back brings it out again. */
const sayDrafts = new Map();
/* The writing side of the Git tab. The message being typed is kept per session
   rather than in the DOM, because the pane is rebuilt whenever the repository
   moves — which is exactly while a commit message is being written. */

/* Held apart from gitActing because it outlives a repaint: writing a message
   takes long enough for a poll to rebuild the pane underneath it, and the new
   sparkle has to come back still spinning. */
/* A pinned height for the message box in px, or null to size itself to the text.
   Dragging the rule above the box pins it; double-clicking hands it back. */
// The session whose name is being edited right now, if any. A poll must not
// rebuild the pane out from under the field.

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

/* Folders you told the panel not to group, and groups you folded away. Both are
   keyed by the folder path, which outlives the sessions in it. */
let collapsedFolders = loadKeySet("cbu-collapsed-folders");
const saveKeySet = (key, set) => localStorage.setItem(key, JSON.stringify([...set]));

/* The rows ticked for a group action, the row a shift-click measures from, and
   the ids the list is showing in the order it draws them — which is what makes a
   shift-click select the run you can actually see. */
/* The blocks the list is showing, so a menu opened from the keyboard can find
   the group its header belongs to. */
/* The group whose name is being typed, so a poll cannot rebuild the list out
   from under the field. */

const groupOf = (id) => customGroups.find((g) => g.members.includes(id)) || null;
const folderKeyOf = (session) => session.cwd || session.folder || "";

/* The one order rows are kept in when state is not deciding: when the session
   started, then its id so the comparison is never a tie. */
const bySessionIdentity = (a, b) =>
  (a.startedAt || 0) - (b.startedAt || 0) || (a.sessionId < b.sessionId ? -1 : a.sessionId > b.sessionId ? 1 : 0);

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
    else if (folder && perFolder.get(folder) > 1 && !sidebar.looseFolders.has(folder)) key = `folder:${folder}`;
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
  // one the sorted list reached first — but inside it the rows are held in a
  // fixed order of their own, so a member going busy and idle again does not
  // shuffle the group under the pointer.
  for (const block of blocks) {
    if (block.kind === "group") block.sessions.sort(bySessionIdentity);
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
  if (sidebar.picked.has(id)) sidebar.picked.delete(id);
  else sidebar.picked.add(id);
  render();
}

function pickRange(from, to) {
  const a = sidebar.visibleOrder.indexOf(from);
  const b = sidebar.visibleOrder.indexOf(to);
  if (a === -1 || b === -1) return togglePick(to);
  for (const id of sidebar.visibleOrder.slice(Math.min(a, b), Math.max(a, b) + 1)) sidebar.picked.add(id);
  render();
}

function clearPicked(repaint = true) {
  if (!sidebar.picked.size) return;
  sidebar.picked.clear();
  if (repaint) render();
}

function onRowClick(id, event) {
  if (event.ctrlKey || event.metaKey) {
    togglePick(id);
    sidebar.pickAnchor = id;
    return;
  }
  if (event.shiftKey) {
    pickRange(sidebar.pickAnchor ?? id, id);
    sidebar.pickAnchor = id;
    return;
  }
  sidebar.picked.clear();
  sidebar.pickAnchor = id;
  selectSession(id);
}

/* Group the picked rows. They leave whatever group they were in — a session
   belongs to one — and the new group takes the folder's name when they share
   one, because that is what you would have called it. */
function groupPicked() {
  const ids = sidebar.visibleOrder.filter((id) => sidebar.picked.has(id));
  if (ids.length < 2) return;
  const sessions = ids.map(sessionById).filter(Boolean);
  const folders = new Set(sessions.map((s) => s.folder).filter(Boolean));
  const name = folders.size === 1 ? [...folders][0] : `${ids.length} sessions`;
  for (const group of customGroups) group.members = group.members.filter((id) => !sidebar.picked.has(id));
  customGroups = customGroups.filter((g) => g.members.length);
  customGroups.unshift({ id: `g${Date.now().toString(36)}`, name, members: ids, collapsed: false });
  saveGroups();
  sidebar.picked.clear();
  showSnackbar(`Grouped ${ids.length} sessions as “${name}”`);
  render();
}

function ungroup(block) {
  if (block.custom) {
    customGroups = customGroups.filter((g) => g.id !== block.custom.id);
    saveGroups();
    showSnackbar(`“${block.name}” ungrouped`);
  } else {
    // A folder group is not stored, so leaving it apart is what gets stored.
    sidebar.looseFolders.add(block.folder);
    saveKeySet("cbu-loose-folders", sidebar.looseFolders);
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
  sidebar.looseFolders.clear();
  saveKeySet("cbu-loose-folders", sidebar.looseFolders);
  render();
}

function syncPickBar() {
  const count = sidebar.picked.size;
  pickBar.hidden = count === 0;
  pickCount.textContent = count
    ? `${count} picked${count < 2 ? " — ctrl-click another" : ""}`
    : "";
  pickGroup.disabled = count < 2;
}


const selected = () => app.feed.sessions.find((s) => s.sessionId === app.selectedId) || null;

/* ------------------------------------------------------------------ transport */
async function poll() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    app.skew = data.now - Date.now() / 1000;
    app.feed = data;
    app.lastGood = Date.now();
    notifyWaiting(data.sessions);
    paintOpenButton();
    render();
    if (app.selectedId && (chat.transcriptFor !== app.selectedId || app.tab === "chat")) fetchTranscript();
    if (app.selectedId && isGitTab(app.tab)) fetchGit();
    if (app.selectedId && app.tab === "usage") fetchUsage();
  } catch (error) {
    barSupporting.textContent = "lost the server — retrying";
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

/* Reading a repository costs a subprocess or three, unlike the transcript's file
   read, so this runs on its own much slower clock rather than on every poll.

   Slow on purpose. A working tree does not change on its own — a session or you
   changes it — and everything the panel itself does re-reads immediately, as does
   opening the tab or switching session. So this interval only governs how long a
   change made *elsewhere* takes to show up, which is worth far less than running
   git against somebody's repository every couple of seconds all day. */



/* Everything the Git tab draws, in one string. Same reasoning as the
   transcript's: re-rendering on every poll throws the pane away under the
   pointer, so the panel only redraws when the repository actually moved. */
/* Both git tabs are drawn from the same reading, so they fetch and fall back
   together. */
const isGitTab = (name) => name === "git" || name === "history";





/* Tokens and cost. Cheaper to read than the repository — the server scans the
   transcript once and picks up where it stopped — but there is no point reading
   it while another tab is showing, so it runs on the poll only when its own tab
   is up, and once immediately when you open it. */
function usageStamp(u) {
  return [u?.cost ?? -1, u?.totals?.requests ?? -1, u?.context ?? -1,
          (u?.models ?? []).length, (u?.agentModels ?? []).length].join("|");
}

async function fetchUsage(force) {
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

/* ------------------------------------------------- git: opening and acting */



/* Clicking a row shows its diff under it, and clicking the same row again puts
   it away — one open at a time, because the pane is one column wide. */




/* Every writing git action goes through here: one at a time, the answer in a
   snackbar, and a fresh reading afterwards so the list matches the repository
   again without waiting for the next poll. */



/* --------------------------------------------------------------- rendering */
function render() {
  const counts = {};
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
  const visible = app.filter === "all" ? app.feed.sessions : app.feed.sessions.filter((s) => stateKeyOf(s.status) === app.filter);
  if (!visible.some((s) => s.sessionId === app.selectedId)) {
    app.selectedId = visible.length ? visible[0].sessionId : null;
    if (app.selectedId) localStorage.setItem("cbu-selected", app.selectedId);
  }

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
  listEmpty.hidden = visible.length > 0;
  // A menu whose row is leaving the list — ended, or filtered out — has nothing
  // left to act on.
  if (ui.menuFor && !visible.some((s) => s.sessionId === ui.menuFor)) closeSessionMenu({ restoreFocus: false });
  // Rows that have left the list cannot be part of a pick any more.
  const here = new Set(visible.map((s) => s.sessionId));
  for (const id of [...sidebar.picked]) if (!here.has(id)) sidebar.picked.delete(id);
  if (!visible.length) {
    listEmpty.textContent = app.feed.sessions.length
      ? "Nothing in this state. Pick “all” above."
      : "No sessions are running. Start one with claude in any terminal.";
    sessionList.innerHTML = "";
    sessionList.dataset.layout = "";
    sidebar.visibleOrder = [];
    sidebar.lastBlocks = [];
    syncPickBar();
    return;
  }

  const blocks = listBlocks(visible);
  sidebar.lastBlocks = blocks;
  // A group menu whose group has gone — ungrouped, or its rows filtered out —
  // has nothing left to act on either.
  if (ui.menuGroup && !blocks.some((block) => block.key === ui.menuGroup)) {
    closeSessionMenu({ restoreFocus: false });
  }
  // Only the rows you can see, in the order you see them: what a shift-click
  // range and “group the picked rows” both count along.
  sidebar.visibleOrder = blocks.flatMap((block) => block.kind === "group"
    ? (block.collapsed ? [] : block.sessions.map((s) => s.sessionId))
    : [block.session.sessionId]);

  // The skeleton is rebuilt only when the shape changes — which group a row is
  // in, what a group is called, whether it is folded. A status changing repaints
  // the row in place, as it always did.
  const layout = blocks.map((block) => block.kind === "group"
    ? `g:${block.key}:${block.name}:${block.collapsed}:${block.sessions.map((s) => s.sessionId).join(",")}`
    : `s:${block.session.sessionId}`).join("|");
  if (sessionList.dataset.layout !== layout && !sidebar.renamingGroup) {
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
      ? sessionList.querySelector(`li[data-id="${CSS.escape(ui.menuFor)}"]`)
      : ui.menuGroup ? sessionList.querySelector(`li[data-group="${CSS.escape(ui.menuGroup)}"]`) : null;
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
    const item = sessionList.querySelector(`li[data-group="${CSS.escape(block.key)}"]`);
    if (!item) continue;
    const inside = block.sessions.filter((s) => sidebar.picked.has(s.sessionId)).length;
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
  const states = [...new Set(block.sessions.map((s) => stateKeyOf(s.status)))]
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
  const state = stateOf(session.status);
  const host = hostOf(session);
  const isSelected = session.sessionId === app.selectedId;
  const isPicked = sidebar.picked.has(session.sessionId);
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
  // A multiple-choice question on screen is more particular than "waiting", and
  // naming it is what makes several waiting sessions tellable apart from the
  // list — the header Claude gave the question is written to be a label.
  const asked = session.question?.questions?.[0];
  const asking = asked ? `asks “${clip(asked.header || asked.question || "a question", 40)}”` : "";
  const supporting = [state.short, asking, session.folder, nested].filter(Boolean)
    .map(escapeHtml).join(" · ");
  const signature = [stateKeyOf(session.status), session.name, session.folder, host.label,
                     isSelected, session.sticky, subject, nested, asking].join(" ");
  if (item.dataset.signature !== signature) {
    item.dataset.signature = signature;
    item.innerHTML = `
      <button class="session-item md-state" type="button" data-status="${stateKeyOf(session.status)}"
              aria-current="${isSelected}" data-picked="${isPicked}">
        <span class="session-item__avatar">${host.icon}<span class="session-item__lamp"></span></span>
        <span class="session-item__text">
          <span class="session-item__headline md-title-small">${
            session.sticky ? `<span class="session-item__pin" title="Kept in the dashboard">${ICON.pin}</span>` : ""
          }${escapeHtml(session.name)}</span>
          ${subject ? `<span class="session-item__subject md-body-small">${escapeHtml(subject)}</span>` : ""}
          <span class="session-item__supporting md-body-small">${supporting}</span>
        </span>
        <span class="session-item__trailing md-label-small md-mono" data-since="${displaySince(session)}"></span>
      </button>`;
    const button = item.firstElementChild;
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

function selectSession(id) {
  app.selectedId = id;
  localStorage.setItem("cbu-selected", id);
  chat.transcript = null;
  chat.transcriptFor = null;
  chat.chatLimit = CHAT_PAGE;
  chat.chatGrew = false;
  repo.git = null;
  repo.gitFor = null;
  closeDiff();
  panes.dataset.view = "detail";
  render();
  fetchTranscript();
  if (isGitTab(app.tab)) fetchGit(true);
}

function setTab(next) {
  app.tab = next;
  localStorage.setItem("cbu-tab", next);
  renderDetail(true);
  if (next === "chat") fetchTranscript();
  if (isGitTab(next)) fetchGit();
  if (next === "usage") fetchUsage(true);
}

/* ==========================================================================
   Sidebar context menu — the per-session actions, at the pointer.
   Right-clicking does NOT change the selection: on a narrow screen selecting
   swaps the sidebar out for the detail pane, which would take the menu with it.
   The menu names its session instead, so the target is never ambiguous.
   ========================================================================== */

/* Whether a menu is standing at all, which is not the same question as what it
   points at: a menu opened from a toolbar button — the Git tab's overflow, the
   commit split button — belongs to neither a row nor a group, and asking after
   those two left it with nothing to dismiss it. The DOM is the honest answer. */

/* The row to hand focus back to, held as an id rather than the element: the list
   repaints on every status change, which detaches the button we were given. */
const sessionById = (id) => app.feed.sessions.find((s) => s.sessionId === id) || null;

function setMuted(id, muted) {
  if (muted) app.mutedSessions.add(id);
  else app.mutedSessions.delete(id);
  localStorage.setItem("cbu-muted", JSON.stringify([...app.mutedSessions]));
  renderDetail();
}

async function copyText(text, done) {
  try {
    await navigator.clipboard.writeText(text);
    showSnackbar(done);
  } catch (error) {
    // Clipboard permission can be refused even on loopback. Fall back rather
    // than leaving a menu item that silently does nothing.
    const box = document.createElement("textarea");
    box.value = text;
    box.setAttribute("readonly", "");
    box.style.cssText = "position:fixed;top:-1000px;left:0;opacity:0";
    document.body.appendChild(box);
    box.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    box.remove();
    showSnackbar(ok ? done : "Could not reach the clipboard");
  }
}

/* What this session can actually have done to it, in the order you reach for it.
   Anything that cannot apply right now is left out rather than shown dead —
   except focusing, which is disabled with its reason, because its absence would
   otherwise look like a missing feature. Kept short on purpose: opening the
   session, pairing a window and copying its ids all live in the details pane,
   where there is room to explain them. */
function menuItemsFor(session) {
  const win = session.window;
  const muted = app.mutedSessions.has(session.sessionId);
  const items = [];
  // Window actions mean nothing for a session with no process behind it.
  if (session.status === "stopped") {
    // nothing to focus
  } else if (!app.feed.canFocus) {
    items.push({ key: "focus", icon: ICON.focus, label: "Focus window", hint: "needs xdotool", disabled: true });
  } else if (win) {
    items.push({ key: "focus", icon: ICON.focus, label: "Focus window",
      hint: windowSays(win, "short"),
      run: (el) => run("/api/focus", session, el, isAmbiguous(win) ? IDENTIFY_NOTE : undefined) });
  }
  if (session.status === "stopped") {
    items.push({ key: "start", icon: ICON.play, label: "Start it up",
      hint: app.feed.canSend ? "claude --resume" : "needs loopback",
      disabled: !app.feed.canSend,
      run: (el) => run("/api/start", session, el) });
  }
  // A second session on the same work, started where this one is — a fresh
  // conversation rather than a resume, so it never touches this one's transcript.
  if (session.cwd) {
    items.push({ key: "new", icon: ICON.plus, label: "New session here",
      hint: app.feed.canSend ? (session.folder || shorten(session.cwd, 1)) : "needs loopback",
      disabled: !app.feed.canSend,
      run: (el) => run("/api/new", session, el) });
  }
  items.push({ key: "sticky", icon: session.sticky ? ICON.pinOff : ICON.pin,
    label: session.sticky ? "Stop keeping this one" : "Keep in the dashboard",
    hint: session.sticky ? "drops it when it closes" : "stays after it closes",
    run: (el) => run("/api/sticky", session, el, null, { sticky: !session.sticky }) });
  items.push({ key: "mute", icon: muted ? ICON.bell : ICON.bellOff,
    label: muted ? "Unmute notifications" : "Mute notifications",
    run: () => { setMuted(session.sessionId, !muted); showSnackbar(muted ? "Notifications on" : "Notifications muted"); } });
  // Grouping acts on the rows you picked, so it is offered where the picking is.
  const group = groupOf(session.sessionId);
  if (sidebar.picked.size > 1 && sidebar.picked.has(session.sessionId)) {
    items.push({ divider: true });
    items.push({ key: "group", icon: ICON.group, label: `Group these ${sidebar.picked.size}`,
      run: () => groupPicked() });
  }
  if (group) {
    items.push({ divider: true });
    items.push({ key: "leave", icon: ICON.ungroup, label: "Take out of the group",
      hint: group.name, run: () => leaveGroup(session.sessionId) });
  }
  if (session.alive !== false) {
    items.push({ divider: true });
    items.push({ key: "end", icon: ICON.power, label: "End session…", danger: true,
      run: () => openEndDialog(session) });
  }
  return items;
}

/* One menu, opened over a row or over a group header. `forId` / `forGroup` say
   which, so the thing it acts on is marked while it stands open. */


function openSessionMenu(session, x, y) {
  openMenu({
    title: session.name,
    label: `Actions for ${session.name}`,
    items: menuItemsFor(session),
    forId: session.sessionId,
  }, x, y);
}

/* What a group can have done to it: folded, renamed, or taken apart. A folder
   group has no stored name to change — its name is the folder — so taking it
   apart is the only thing it offers besides folding. */
function openGroupMenu(block, x, y) {
  const items = [
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
    if (sidebar.looseFolders.size) {
      items.push({ key: "regroup", icon: ICON.folder, label: "Group every folder again",
        hint: `${sidebar.looseFolders.size} left out`, run: () => regroupFolders() });
    }
  }
  items.push({ divider: true });
  items.push({ key: "pick", icon: ICON.group, label: "Pick every session in it",
    run: () => {
      for (const session of block.sessions) sidebar.picked.add(session.sessionId);
      sidebar.pickAnchor = block.sessions[block.sessions.length - 1]?.sessionId ?? null;
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
  if (!block.custom || sidebar.renamingGroup) return;
  const header = sessionList.querySelector(`li[data-group="${CSS.escape(block.key)}"] > .group__header`);
  if (!header) return;
  sidebar.renamingGroup = block.key;
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
    sidebar.renamingGroup = null;
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
  const enabled = [...sessionMenu.querySelectorAll(".menu__item:not([disabled])")];
  if (!enabled.length) return;
  const at = enabled.indexOf(document.activeElement);
  const go = (i) => { enabled[(i + enabled.length) % enabled.length].focus(); event.preventDefault(); };
  if (event.key === "ArrowDown") go(at + 1);
  else if (event.key === "ArrowUp") go(at - 1);
  else if (event.key === "Home") go(0);
  else if (event.key === "End") go(enabled.length - 1);
  else if (event.key === "Escape") { event.preventDefault(); closeSessionMenu(); }
  else if (event.key === "Tab") closeSessionMenu();
});

sessionList.addEventListener("contextmenu", (event) => {
  const row = event.target.closest("li[data-id]");
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
  const row = event.target.closest("li[data-id]");
  if (!row || !event.target.closest(".session-item")) return;
  event.preventDefault();     // or the button below takes it as a click
  togglePick(row.dataset.id);
  sidebar.pickAnchor = row.dataset.id;
  // The repaint keeps the row, so put the focus back where the reader left it.
  sessionList.querySelector(`li[data-id="${CSS.escape(row.dataset.id)}"] .session-item`)?.focus();
});

// The keyboard route to the same menu: Shift-F10, or the dedicated menu key.
sessionList.addEventListener("keydown", (event) => {
  if (!(event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey))) return;
  const row = event.target.closest("li[data-id]");
  const session = row && sessionById(row.dataset.id);
  if (session) {
    event.preventDefault();
    ui.menuReturn = session.sessionId;
    const rect = row.querySelector(".session-item").getBoundingClientRect();
    openSessionMenu(session, rect.left + 16, rect.bottom - 8);
    return;
  }
  // The same key on a group header opens the group's menu.
  const header = event.target.closest("li.group")?.querySelector(":scope > .group__header");
  const block = header && sidebar.lastBlocks.find((b) => b.kind === "group" && b.key === header.parentElement.dataset.group);
  if (!block) return;
  event.preventDefault();
  const rect = header.getBoundingClientRect();
  openGroupMenu(block, rect.left + 16, rect.bottom - 8);
});

// Anything that moves the menu away from what it points at closes it.
document.addEventListener("pointerdown", (event) => {
  if (menuIsOpen() && !sessionMenu.contains(event.target)) closeSessionMenu({ restoreFocus: false });
}, true);
window.addEventListener("blur", () => closeSessionMenu({ restoreFocus: false }));
window.addEventListener("resize", () => closeSessionMenu({ restoreFocus: false }));
// Capture, because scroll does not bubble and the scrolling box may be the pane
// or the list itself depending on the breakpoint. Scrolling within the menu is
// the reader working through a long one, not moving away from it.
document.addEventListener("scroll", (event) => {
  if (menuIsOpen() && !sessionMenu.contains(event.target)) closeSessionMenu({ restoreFocus: false });
}, true);

function renderDetail(force) {
  const session = selected();
  if (!session) {
    detailPane.dataset.signature = "";
    detailPane.innerHTML = `<div class="detail-empty">
      <div><h2 class="md-headline-small">Nothing selected</h2>
      <p class="md-body-medium">Pick a session on the left to see its conversation and settings.</p></div></div>`;
    return;
  }
  const state = stateOf(session.status);
  const host = hostOf(session);
  // A session outside a repository has no Git tab to show, so a tab left over
  // from the last session you looked at falls back rather than showing nothing.
  if (isGitTab(app.tab) && !session.repoRoot) app.tab = "chat";
  const signature = [
    session.sessionId, session.name, stateKeyOf(session.status), session.branch, session.window?.confidence,
    host.label, app.tab, chat.transcript?.messages?.length ?? -1, chat.transcript?.title ?? "",
    session.repoRoot ?? "", repo.gitFor === session.sessionId ? gitStamp(repo.git) : "",
    spend.usageFor === session.sessionId ? usageStamp(spend.usage) : "",
    app.mutedSessions.has(session.sessionId), app.feed.canFocus, app.feed.canSend, session.canSay, session.sticky,
    session.permissionMode ?? "", session.title ?? "", session.parentName ?? "",
    // A question going up or being answered has to redraw the pane; the tool use
    // it came in on is what tells one question from the next.
    session.question?.toolUseId ?? "",
  ].join(" ");
  if (!force && detailPane.dataset.signature === signature) {
    const clock = detailPane.querySelector("[data-since]");
    if (clock) clock.dataset.since = displaySince(session);
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
  if ((ui.resizingComposer || sidebar.renamingId === session.sessionId || commentIsOpen()) && !force) {
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
  // A half-typed message must survive a re-render — the poll re-renders every time
  // the status changes or a message lands, which is exactly while you are typing.
  // The text itself lives in sayDrafts under the session it was written for; only
  // the caret is carried through here, and only when the pane is not changing
  // session, because a caret from another session's box means nothing in this one.
  const fieldBefore = detailPane.querySelector("#sayField");
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
  const commitBefore = detailPane.querySelector("#commitField");
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
      ${app.tab === "chat" ? `<div class="jump-dock">
          <button class="jump-bottom md-state" id="jumpBottom" title="Jump to latest" aria-label="Jump to latest" data-open="false" tabindex="-1" hidden>${ICON.toBottom}</button>
          <button class="jump-last md-state md-label-large" id="jumpLast" data-open="false" tabindex="-1" hidden>${ICON.up}Last request</button>
        </div>
        <div class="rail" id="commentRail" hidden><div class="rail__inner" id="commentRailInner"></div></div>` : ""}
    </div>
    ${app.tab === "chat" ? questionCard(session) : ""}
    ${app.tab === "chat" ? composer(session) : ""}`;

  const header = detailPane.querySelector(".detail-header");
  header.style.setProperty("--state-colour", state.colour);
  header.style.setProperty("--state-container", state.container);
  header.style.setProperty("--state-on-container", state.onContainer);

  paintTrace(header, session);
  wireTrace(header);
  // The transcript is rebuilt from the transcript data, so anything drawn over
  // it has to be drawn again — the marks over passages you have commented on
  // included.
  if (app.tab === "chat") { markCommented(); renderRail(); }

  for (const button of detailPane.querySelectorAll("[data-tab]")) {
    button.addEventListener("click", () => setTab(button.dataset.tab));
  }
  detailPane.querySelector("[data-act='rename']")?.addEventListener("click", (e) => startRename(session, e.currentTarget));
  detailPane.querySelector("[data-act='start']")?.addEventListener("click", (e) => run("/api/start", session, e.currentTarget));
  // Two of these when a question is up: the header's button and the card's.
  for (const button of detailPane.querySelectorAll("[data-act='focus']")) {
    button.addEventListener("click", (e) => run("/api/focus", session, e.currentTarget));
  }
  detailPane.querySelector("[data-act='pair']")?.addEventListener("click", (e) =>
    run("/api/pair", session, e.currentTarget, "Click the window that belongs to this session"));
  detailPane.querySelector("[data-act='identify']")?.addEventListener("click", (e) =>
    run("/api/identify", session, e.currentTarget, IDENTIFY_NOTE));
  detailPane.querySelector("[data-act='unpair']")?.addEventListener("click", (e) => run("/api/unpair", session, e.currentTarget));
  detailPane.querySelector("#stickyToggle")?.addEventListener("change", (event) =>
    run("/api/sticky", session, event.target, null, { sticky: event.target.checked }));
  detailPane.querySelector("#muteToggle")?.addEventListener("change", (event) => {
    if (event.target.checked) app.mutedSessions.delete(session.sessionId);
    else app.mutedSessions.add(session.sessionId);
    localStorage.setItem("cbu-muted", JSON.stringify([...app.mutedSessions]));
  });
  for (const button of detailPane.querySelectorAll(".fact-copy")) {
    button.addEventListener("click", () => button.dataset.copy === "cwd"
      ? copyText(session.cwd, "Folder path copied")
      : copyText(session.sessionId, "Session id copied"));
  }
  detailPane.querySelector("[data-act='end']")?.addEventListener("click", () => openEndDialog(session));
  detailPane.querySelector("#openAppearance")?.addEventListener("click", () => openSettings(true));

  const field = detailPane.querySelector("#sayField");
  if (field) {
    const kept = sayDrafts.get(session.sessionId) || "";
    if (kept) {
      field.value = kept;
      if (caret && caret.focused) {
        field.focus();
        field.setSelectionRange(caret.start, caret.end);
      }
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
      ui.cmdOff = false;
      syncCmdBar(session);
    });
    field.addEventListener("keydown", (event) => {
      // The /-picker owns its keys while it stands, including Enter — which
      // takes the highlighted name rather than sending a half-typed one.
      if (cmdKey(event, session)) { event.preventDefault(); return; }
      // Enter sends, as it does in the terminal; Shift-Enter for a newline.
      if (event.key === "Enter" && !event.shiftKey && !event.altKey) {
        event.preventDefault();
        sendMessage(session, detailPane.querySelector("[data-act='say']"));
      }
    });
    // A draft that was left mid-name gets its list back with the pane.
    syncCmdBar(session);
  }
  detailPane.querySelector("[data-act='say']")?.addEventListener("click", (e) => sendMessage(session, e.currentTarget));

  // Both git tabs carry the same header, and the header has buttons in it.
  if (isGitTab(app.tab)) wireGit(session);

  detailPane.querySelector("[data-act='more']")?.addEventListener("click", showMoreChat);

  const chatAfter = detailPane.querySelector("#chatScroll");
  if (chatAfter && app.tab === "chat" && chatFromBottom !== null) {
    chatAfter.scrollTop = chatAfter.scrollHeight - chatFromBottom;
    chat.chatGrew = false;
  } else if (chatAfter && app.tab === "chat" && wasAtBottom) {
    chatAfter.scrollTop = chatAfter.scrollHeight;
  }
  if (chatAfter && app.tab === "chat") wireJumpDock(chatAfter);

  // Fade the panel in when it is showing something genuinely different — another
  // tab, another session. Not on every rebuild: the pane is rebuilt whenever a
  // message lands or a state changes, and a working session would have the thing
  // you are reading pulsing at you every few seconds.
  if (cameFrom !== `${session.sessionId}/${app.tab}`) panelChangedAt = Date.now();
  const since = Date.now() - panelChangedAt;
  if (since < PANEL_FADE_MS) {
    const wrap = detailPane.querySelector(".panel-wrap");
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
  const last = detailPane.querySelector("#jumpLast");
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

/* Smooth scrolling is motion like any other, and someone who has asked their
   system for less of it means this too. */
const glide = () => (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth");

/* One line, matching the send button beside it. */
const COMPOSER_MIN = 48;
/* Leave the conversation something to live in, whatever the window does. */
const composerMax = () => Math.max(COMPOSER_MIN, detailPane.clientHeight - 200);

function growField(field) {
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
  localStorage.setItem("cbu-composer-height", ui.composerHeight);
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
  grip.setAttribute("aria-valuenow", Math.round(ui.composerHeight || field.clientHeight));
  grip.setAttribute("aria-valuemin", COMPOSER_MIN);
  grip.setAttribute("aria-valuemax", Math.round(composerMax()));
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
function setSayDraft(sessionId, text) {
  if (text) sayDrafts.set(sessionId, text);
  else sayDrafts.delete(sessionId);
}

async function sendMessage(session, button) {
  const field = detailPane.querySelector("#sayField");
  if (!field || !button) return;
  const text = field.value.trim();
  if (!text) return;
  const asked = slashOf(text);
  // A terminal-only command would go out as a sentence nobody acts on, so it is
  // stopped here rather than sent into the dark.
  if (asked && terminalOnly(asked.name)) {
    showSnackbar(`/${asked.name} only works at this session's own prompt`);
    return;
  }
  // What actually goes over the wire: a request for a skill by name, if that is
  // what was typed, since an injected turn is never expanded the way the
  // terminal expands one. The box keeps what you wrote, and so does the draft.
  const sent = sentAs(text);
  // Clear optimistically so typing the next one is not blocked on the round trip,
  // and put it back if the send fails — a lost message is worse than a stale box.
  field.value = "";
  setSayDraft(session.sessionId, "");
  growField(field);
  syncCmdBar(session);
  const restore = () => {
    setSayDraft(session.sessionId, text);
    // The pane may have moved to another session while the send was in flight —
    // the text goes back into the map either way, but only into a box that is
    // still this session's.
    if (detailPane.dataset.sessionId !== session.sessionId) return;
    const live = detailPane.querySelector("#sayField");
    if (live && !live.value) { live.value = text; growField(live); }
  };
  if (app.inFlight) { restore(); return; }
  // A stopped session cannot hear anything yet, so the message rides along with
  // the start: the server delivers it once the session is listening again.
  const url = session.status === "stopped" ? "/api/start" : "/api/say";
  app.inFlight = url;
  button.disabled = true;
  try {
    const response = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: session.sessionId, text: sent }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) restore();
    showSnackbar(data.message || (response.ok ? "Sent" : "That did not send"));
  } catch (error) {
    restore();
    showSnackbar("Could not reach the server");
  } finally {
    app.inFlight = null;
    button.disabled = false;
    poll();
  }
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
function slashOf(text) {
  const match = /^\/([A-Za-z0-9:._-]*)(\s([\s\S]*))?$/.exec(text);
  if (!match) return null;
  return { name: match[1], args: (match[3] || "").trim(), chosen: match[2] !== undefined };
}

const cmdEntry = (name) => (catalog?.entries || [])
  .find((entry) => entry.name.toLowerCase() === name.toLowerCase()) || null;

const terminalOnly = (name) => (catalog?.terminalOnly || []).includes(name.toLowerCase());

/* Everything the typed name could still become. A plugin's entries are addressed
   `plugin:name`, so what you type is matched against both halves — typing the
   skill's own name finds it without knowing which plugin it came from. */
function cmdMatches(typed) {
  const needle = typed.toLowerCase();
  const rank = (name) => {
    const whole = name.toLowerCase();
    if (whole.startsWith(needle)) return 0;
    if (whole.split(":").pop().startsWith(needle)) return 1;
    return 2;
  };
  return (catalog?.entries || [])
    .filter((entry) => entry.name.toLowerCase().includes(needle))
    .sort((a, b) => rank(a.name) - rank(b.name) || a.name.localeCompare(b.name))
    .slice(0, 40);
}

/* The sentence that goes out. Anything the catalog does not know is left exactly
   as it was written — a typo is better sent as itself than as a guess. */
function sentAs(text) {
  const asked = slashOf(text);
  if (!asked || !asked.name) return text;
  const entry = cmdEntry(asked.name);
  if (!entry) return text;
  const what = entry.kind === "command" ? "command" : "skill";
  return asked.args
    ? `Use the ${entry.name} ${what}: ${asked.args}`
    : `Use the ${entry.name} ${what}.`;
}

/* The line under the list: what will be sent, or why nothing useful would be. */
function cmdNote(asked) {
  if (!asked.name) return "";
  if (terminalOnly(asked.name)) {
    return `<span class="md-mono">/${escapeHtml(asked.name)}</span> only works at this session's
      own prompt — the terminal keeps that one to itself.`;
  }
  const entry = cmdEntry(asked.name);
  if (entry) return `Sends as “${escapeHtml(sentAs(`/${asked.name} ${asked.args}`.trim()))}”`;
  if (!asked.chosen) return "";
  return `Nothing here is called <span class="md-mono">/${escapeHtml(asked.name)}</span> —
    it will be sent as plain text.`;
}

function syncCmdBar(session) {
  const bar = detailPane.querySelector("#cmdBar");
  const field = detailPane.querySelector("#sayField");
  if (!bar || !field) return;
  const asked = slashOf(field.value);
  if (!asked) {
    bar.hidden = true;
    bar.innerHTML = "";
    cmdRows = [];
    cmdIndex = 0;
    ui.cmdOff = false;
    return;
  }
  loadCatalog(session);

  // Once a space has been typed the name is settled, so the list stands down and
  // leaves the line saying what will be sent.
  cmdRows = (asked.chosen || ui.cmdOff) ? [] : cmdMatches(asked.name);
  cmdIndex = Math.min(cmdIndex, Math.max(0, cmdRows.length - 1));
  const note = cmdNote(asked);
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

  for (const button of bar.querySelectorAll(".cmdbar__item")) {
    // Taking one with the pointer must not take the caret out of the box.
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", () => takeCmd(session, cmdRows[Number(button.dataset.index)]));
  }
  bar.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
}

function takeCmd(session, entry) {
  const field = detailPane.querySelector("#sayField");
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
  if (event.key === "Escape" && !detailPane.querySelector("#cmdBar")?.hidden) {
    ui.cmdOff = true;
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

/* ==========================================================================
   Commenting on what was said — select a passage in the transcript and a chip
   rises over it; taking it drops the passage into the composer as a quote with
   your cursor under it.

   It goes through the composer rather than sending on its own, which buys the
   whole feature for very little: the draft already survives the poll's
   re-render, a session that cannot be sent to has already said why in place of
   a composer, and several passages can be gathered into one message instead of
   arriving as a burst the session queues separately.
   ========================================================================== */
const quoteChip = document.getElementById("quoteChip");
quoteChip.innerHTML =
  `<button class="quote-chip__act" type="button" data-sel="copy">${ICON.copy}<span>Copy</span></button>`
  + `<button class="quote-chip__act" type="button" data-sel="comment">${ICON.chat}<span>Comment</span></button>`;
// Captured when the chip is shown, not read when it is clicked: clicking is a
// pointer event on another element, and by then the selection may be gone.
let pendingQuote = null;

function hideQuoteChip() {
  pendingQuote = null;
  conceal(quoteChip);
}

/* What can be commented on: a speech bubble's body, and a tool row — the only
   place a command the session ran is written down. */
const QUOTABLE = ".msg__text, .activity-row__tools";

/* The passages under the selection, whose they were, and where to put the chip —
   or null if this selection is not something to comment on.

   A selection running across several messages is split into one quote each
   rather than refused: the reason it used to be refused was that a single
   attribution cannot cover two speakers, and giving each its own answers that
   properly. */
function quotableSelection() {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
  if (!selection.toString().trim()) return null;
  const range = selection.getRangeAt(0);
  const scroller = document.getElementById("chatScroll");
  if (!scroller || !scroller.contains(range.commonAncestorContainer)) return null;

  const parts = [];
  for (const body of scroller.querySelectorAll(QUOTABLE)) {
    if (!range.intersectsNode(body)) continue;
    // The selection clipped to this one body, so each passage carries only what
    // was selected inside it.
    const clipped = range.cloneRange();
    const whole = document.createRange();
    whole.selectNodeContents(body);
    if (clipped.compareBoundaryPoints(Range.START_TO_START, whole) < 0) clipped.setStart(body, 0);
    if (clipped.compareBoundaryPoints(Range.END_TO_END, whole) > 0) clipped.setEnd(body, body.childNodes.length);
    const text = clipped.toString().replace(/\s+$/, "").replace(/^\n+/, "");
    if (!text.trim()) continue;
    const owner = body.closest(".msg, .activity-row");
    // Which turn this came from, and which occurrence within it — the same
    // sentence can appear in two turns, and twice in one.
    const first = text.split("\n").map((l) => l.trim()).find((l) => l.length >= 12) || text.trim();
    let nth = 0;
    if (owner && first) {
      const before = document.createRange();
      before.setStart(owner, 0);
      before.setEnd(clipped.startContainer, clipped.startOffset);
      nth = before.toString().split(first).length - 1;
    }
    parts.push({
      text,
      who: owner?.dataset.who || "",
      at: owner?.dataset.at || "",
      key: owner?.dataset.key || "",
      nth,
      // A passage taken wholly from inside a code block goes back as code, not
      // as prose with its indentation flattened into a blockquote.
      code: codeContext(clipped, body),
    });
  }
  if (!parts.length) return null;

  const rect = range.getBoundingClientRect();
  if (!rect.width && !rect.height) return null;
  // Scrolled out of the transcript's own box, the passage is no longer on
  // screen — a chip pointing at it would be floating over the header.
  const box = scroller.getBoundingClientRect();
  if (rect.bottom < box.top || rect.top > box.bottom) return null;
  return { parts, rect, box };
}

/* The language to fence a passage with, or null when it is not code. Both ends
   have to be inside the same code element — half a sentence and half a function
   is prose as far as the quote is concerned. */
function codeContext(range, body) {
  const codeOf = (node) => (node?.nodeType === 1 ? node : node?.parentElement)?.closest("pre.md-code, code");
  const start = codeOf(range.startContainer);
  if (!start || !body.contains(start)) return null;
  if (start !== codeOf(range.endContainer)) return null;
  // Inline code is a few words mid-sentence; fencing it would be heavier than
  // the thing it quotes.
  const pre = start.closest("pre.md-code");
  if (!pre) return null;
  return pre.dataset.lang || "";
}

function showQuoteChip(quote) {
  pendingQuote = quote;
  quoteChip.hidden = false;
  // Above the selection by preference, below it when there is no room — and the
  // room is the transcript's own box, not the window, so a passage half off the
  // top of the scroller does not put the chip over the header above it.
  const { rect, box } = quote;
  const width = quoteChip.offsetWidth;
  const height = quoteChip.offsetHeight;
  const gap = 8;
  const left = Math.min(Math.max(gap, rect.left + rect.width / 2 - width / 2), window.innerWidth - width - gap);
  const above = rect.top - height - gap;
  const top = above >= box.top
    ? above
    : Math.min(rect.bottom + gap, box.bottom - height - gap, window.innerHeight - height - gap);
  quoteChip.style.left = `${Math.round(left)}px`;
  quoteChip.style.top = `${Math.round(Math.max(gap, top))}px`;
  reveal(quoteChip);
}

/* The chip is placed against a viewport rectangle, so anything that moves the
   passage has to move the chip with it — and the transcript moves on its own,
   staying pinned to the newest message while a session works. Following the
   selection is the only version of this that survives that; dismissing on
   scroll loses the chip to a poll landing mid-gesture. */
let quoteFrame = 0;
function syncQuoteChip() {
  if (quoteFrame) return;
  quoteFrame = requestAnimationFrame(() => {
    quoteFrame = 0;
    const quote = quotableSelection();
    if (quote) showQuoteChip(quote); else hideQuoteChip();
  });
}

/* Whose words these were, written from the point of view of the session that is
   about to read it — the panel says "claude" and "you" meaning the assistant and
   the person watching, and both of those invert on the way over. */
function speakerOf(who) {
  if (who === "claude") return "you";
  if (who === "you") return "me";
  return who; // another session, which arrives under its own name at both ends
}

/* A whole-answer selection would put hundreds of lines in the composer and bury
   the remark under them. Long passages keep their head and tail with the gap
   counted out loud — a silent trim would misrepresent what was selected, and the
   marker is in the composer where it can be edited or the passage re-taken
   smaller. */
const QUOTE_MAX_LINES = 40;
function trimLines(lines) {
  if (lines.length <= QUOTE_MAX_LINES) return lines;
  const head = lines.slice(0, QUOTE_MAX_LINES - 12);
  const tail = lines.slice(-10);
  const gone = lines.length - head.length - tail.length;
  return [...head, `… ${gone} lines not quoted …`, ...tail];
}

/* `> [you, 14:32]` over the passage, then a blank line for the remark. The
   attribution is what lets the session find the passage in its own transcript,
   and what keeps two quotes in one message apart. */
function quoteBlock({ text, who, at, code }) {
  const speaker = speakerOf(who);
  const head = speaker && at ? `[${speaker}, ${at}]` : speaker ? `[${speaker}]` : "";
  let lines = trimLines(text.replace(/\r/g, "").split("\n"));
  // Code goes back inside a fence, so it reaches the session as code with its
  // indentation intact rather than as prose flattened into a blockquote.
  if (code !== null && code !== undefined) lines = ["```" + code, ...lines, "```"];
  return [...(head ? [head] : []), ...lines].map((line) => `> ${line}`.trimEnd()).join("\n");
}

/* Commenting opens a card in the margin against the passage, the way a document
   does it, rather than dropping the quote into the composer. The remark stays
   attached to what it is about while you write it, and several can be open at
   once — which is the thing the composer could not do: there, a second quote
   pushed the first out of sight above what you were typing.

   What is sent is unchanged. The cards are gathered into the same attributed
   quote-and-remark message, so the session reads exactly what it read before. */
let commentSeq = 0;
function commentOnSelection() {
  const quote = pendingQuote;
  hideQuoteChip();
  if (!quote) return;
  const why = sendBlockedReason(selected());
  if (why) {
    // A comment that cannot be sent is a note to nobody. Say why now rather than
    // after it has been written.
    showSnackbar(why);
    return;
  }
  const id = app.selectedId;
  if (!id) return;
  if (!comments.has(id)) comments.set(id, []);
  const list = comments.get(id);
  const made = quote.parts.map((part) => {
    const entry = { id: `c${++commentSeq}`, ...part, remark: "", editing: true };
    list.push(entry);
    return entry;
  });
  rememberCommented(made);
  window.getSelection()?.removeAllRanges();
  markCommented();
  activeComment = made[made.length - 1].id;
  renderRail();
  focusComment(activeComment);
}

/* One card gets the caret when it opens; the rest are there to be filled in
   after. */
function focusComment(id) {
  const field = detailPane.querySelector(`.ccard[data-id="${CSS.escape(id)}"] .ccard__field`);
  if (field) { field.focus(); field.selectionStart = field.selectionEnd = field.value.length; }
}

/* ------------------------------------------------- what you already said on */
/* On a long answer you lose your place: nothing about a passage you have
   commented on looks any different from one you have not. These keep a note of
   what was quoted and put a mark back over it after every rebuild.

   Kept in memory rather than on the server or in localStorage, because it is a
   note about this sitting rather than a property of the session — and a mark
   that outlived the conversation it referred to would be worse than none. */
const commented = new Map(); // sessionId -> Set of exact passages
/* Which passages each comment put there, so deleting one takes its underline
   with it. A flat set could not: it had no way of knowing whether a passage was
   still spoken for by another comment. Entries outlive the comment when it is
   sent — a sent comment keeps its mark on purpose — and only a delete removes
   one. */
const commentSnippets = new Map(); // commentId -> [passages]
const COMMENT_MARK_MAX = 200;
/* The comments waiting to be sent, per session. Held in memory for the same
   reason the marks are: they are about this sitting, and one outliving the
   conversation it referred to would be worse than none. */
const comments = new Map(); // sessionId -> [{ id, text, who, at, code, remark, editing }]
let activeComment = null;
/* Measured on the detail pane, not the window — the index takes 340-380px of the
   window before the pane sees any of it, so a threshold picked as if it were a
   window width puts every ordinary laptop into the popover fallback and the
   margin nobody ever sees. A 1280px window leaves the pane about 940px, which
   still has room for the rail and a readable transcript beside it. */
const RAIL_MIN_WIDTH = 860;

const commentsFor = (id) => comments.get(id) || [];
/* A card being typed in must not be rebuilt underneath, the same way a
   half-typed name or a drag on the composer grip holds off a repaint. */
const commentIsOpen = () => commentsFor(app.selectedId).some((c) => c.editing);

function rememberCommented(entries) {
  const id = app.selectedId;
  if (!id) return;
  if (!commented.has(id)) commented.set(id, new Set());
  const set = commented.get(id);
  for (const entry of entries) {
    const mine = [];
    for (const line of entry.text.split("\n")) {
      const snippet = line.trim();
      if (snippet.length >= 12 && set.size < COMMENT_MARK_MAX) { set.add(snippet); mine.push(snippet); }
    }
    commentSnippets.set(entry.id, mine);
  }
}

/* Take one comment's underlines back, keeping any passage another comment — sent
   or still open — also laid claim to. */
function forgetCommented(commentId) {
  const mine = commentSnippets.get(commentId);
  commentSnippets.delete(commentId);
  if (!mine || !mine.length) return;
  const set = commented.get(app.selectedId);
  if (!set) return;
  const stillClaimed = new Set();
  for (const list of commentSnippets.values()) for (const snippet of list) stillClaimed.add(snippet);
  for (const snippet of mine) if (!stillClaimed.has(snippet)) set.delete(snippet);
  // The marks are already in the page, so they have to come out by hand before
  // the remaining ones are drawn again.
  unmarkCommented();
  markCommented();
}

/* Unwrap every mark and put the text back as it was. normalize() re-merges the
   text nodes marking split, so the next pass sees whole runs again rather than
   the fragments left behind by the last one. */
function unmarkCommented() {
  const scroller = detailPane.querySelector("#chatScroll");
  if (!scroller) return;
  for (const mark of [...scroller.querySelectorAll("mark.commented")]) {
    const parent = mark.parentNode;
    if (!parent) continue;
    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
    parent.removeChild(mark);
    parent.normalize();
  }
}

/* Put the marks back. This works on the DOM rather than on the HTML being
   built, so nothing from a message can reach the page as markup — the mark is a
   real element wrapped around a real text node, never a string spliced into
   innerHTML.

   It only matches a passage lying wholly inside one text node. A selection
   crossing a bold run or a link is quoted correctly but goes unmarked, which is
   the honest trade for never rewriting a bubble's structure underneath itself. */
function markCommented() {
  const set = commented.get(app.selectedId);
  if (!set || !set.size) return;
  const scroller = detailPane.querySelector("#chatScroll");
  if (!scroller) return;
  const snippets = [...set].sort((a, b) => b.length - a.length);
  for (const body of scroller.querySelectorAll(QUOTABLE)) {
    // The body's text as one string, with the map back to the nodes it came
    // from. A passage crossing a bold run, a link or a code span occupies
    // several text nodes, and this is what lets it be found across them.
    const nodes = [];
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
    let node, flat = "";
    while ((node = walker.nextNode())) {
      if (node.parentElement?.closest("mark.commented")) continue;
      nodes.push({ node: node, from: flat.length });
      flat += node.data;
    }
    for (const snippet of snippets) {
      let at = flat.indexOf(snippet);
      while (at >= 0) {
        // One mark per text node the passage passes through. Wrapping the whole
        // range in one go fails the moment it straddles an element boundary,
        // which is exactly the case that used to go unmarked.
        const pieces = [];
        for (const entry of nodes) {
          const s0 = Math.max(at, entry.from);
          const e0 = Math.min(at + snippet.length, entry.from + entry.node.data.length);
          if (s0 < e0) pieces.push({ n: entry.node, start: s0 - entry.from, end: e0 - entry.from });
        }
        // Back to front, so wrapping one piece cannot shift the offsets of the
        // pieces still to be wrapped.
        for (const piece of pieces.reverse()) {
          const range = document.createRange();
          range.setStart(piece.n, piece.start);
          range.setEnd(piece.n, piece.end);
          const mark = document.createElement("mark");
          mark.className = "commented";
          mark.title = "you commented on this";
          try { range.surroundContents(mark); } catch { /* leave this piece bare */ }
        }
        if (pieces.length) break;   // one occurrence marked is enough
        at = flat.indexOf(snippet, at + 1);
      }
    }
  }
}

/* -------------------------------------------------------------- the margin */
/* Cards are drawn once and then only moved: their tops are recomputed against
   the passages they belong to, and the whole rail is shifted by the scroller's
   offset, so scrolling costs a transform rather than a relayout. */
function renderRail() {
  const rail = detailPane.querySelector("#commentRail");
  const inner = detailPane.querySelector("#commentRailInner");
  const wrap = detailPane.querySelector(".panel-wrap");
  if (!rail || !inner || !wrap) return;
  const list = commentsFor(app.selectedId);
  const scroller = detailPane.querySelector("#chatScroll");
  rail.hidden = !list.length;
  // Cards live in the transcript now, so anything left over from a previous
  // render has to come out before this one goes in.
  for (const stale of detailPane.querySelectorAll("#chatScroll .ccard")) stale.remove();
  if (!list.length) { inner.innerHTML = ""; return; }

  const sendable = list.filter((c) => c.remark.trim()).length;
  inner.innerHTML = list.map((c) => `
    <div class="ccard" data-id="${escapeHtml(c.id)}" data-active="${c.id === activeComment}">
      <p class="ccard__label md-label-medium">${ICON.chat}<span>your comment</span></p>
      <p class="ccard__quote md-body-small">${escapeHtml(c.text.split("\n")[0].slice(0, 160))}</p>
      ${c.editing
        ? `<textarea class="ccard__field md-body-medium" aria-label="Your comment on this passage"
             placeholder="What about it?">${escapeHtml(c.remark)}</textarea>
           <div class="ccard__actions">
             <button class="button button--text md-state md-label-large" data-cc="drop" data-id="${escapeHtml(c.id)}">Delete</button>
             <button class="button button--text md-state md-label-large" data-cc="keep" data-id="${escapeHtml(c.id)}">Done</button>
           </div>`
        : `<p class="ccard__remark md-body-medium">${escapeHtml(c.remark) || "<em>no comment yet</em>"}</p>
           <div class="ccard__actions">
             <button class="button button--text md-state md-label-large" data-cc="drop" data-id="${escapeHtml(c.id)}">Delete</button>
             <button class="button button--text md-state md-label-large" data-cc="edit" data-id="${escapeHtml(c.id)}">Edit</button>
           </div>`}
    </div>`).join("");

  /* Move each card into the conversation, directly after the message it is
     about. The transcript's own layout then places it — there is no column to
     anchor against and nothing to keep in sync while it scrolls. */
  if (scroller) {
    for (const card of [...inner.querySelectorAll(".ccard")]) {
      const entry = list.find((c) => c.id === card.dataset.id);
      const anchor = findAnchor(scroller, entry);
      const node = anchor && (anchor.nodeType === 1 ? anchor : anchor.commonAncestorContainer);
      const owner = node && (node.nodeType === 1 ? node : node.parentElement)?.closest(".msg, .activity-row");
      // No owner means the passage has scrolled out of the page of transcript
      // being shown; the card waits at the end rather than vanishing with it.
      (owner || scroller.lastElementChild || scroller).after(card);
    }
  }

  // The send button lives outside the scrolled inner so it stays put.
  let send = rail.querySelector(".rail__send");
  if (!send) {
    send = document.createElement("button");
    send.className = "button button--filled md-state rail__send";
    rail.appendChild(send);
  }
  send.textContent = sendable
    ? `Send ${sendable} comment${sendable === 1 ? "" : "s"}`
    : "Write a comment to send";
  send.disabled = !sendable;

  // Wherever the cards ended up, not where they were built: they have already
  // been moved into the transcript by this point, so inner no longer holds them.
  for (const card of detailPane.querySelectorAll(".ccard")) {
    const id = card.dataset.id;
    const entry = list.find((c) => c.id === id);
    card.addEventListener("mousedown", () => { activeComment = id; });
    const field = card.querySelector(".ccard__field");
    if (field) {
      field.addEventListener("input", () => { entry.remark = field.value; refreshSendLabel(); });
      field.addEventListener("keydown", (event) => {
        // Enter finishes the card; a newline inside a remark needs the modifier,
        // which is the same bargain the composer makes.
        if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); closeComment(id, true); }
        else if (event.key === "Escape") { event.preventDefault(); closeComment(id, !!entry.remark.trim()); }
      });
    }
  }
  positionRail();
}

let railFrame = 0;
function scheduleRail() {
  if (railFrame) return;
  railFrame = requestAnimationFrame(() => { railFrame = 0; renderRail(); });
}

function refreshSendLabel() {
  const send = detailPane.querySelector(".rail__send");
  if (!send) return;
  const n = commentsFor(app.selectedId).filter((c) => c.remark.trim()).length;
  send.textContent = n ? `Send ${n} comment${n === 1 ? "" : "s"}` : "Write a comment to send";
  send.disabled = !n;
}

function closeComment(id, keep) {
  const list = commentsFor(app.selectedId);
  const entry = list.find((c) => c.id === id);
  if (!entry) return;
  if (!keep && !entry.remark.trim()) {
    comments.set(app.selectedId, list.filter((c) => c.id !== id));
    forgetCommented(id);
  } else {
    entry.editing = false;
  }
  if (activeComment === id) activeComment = null;
  renderRail();
}

/* The card is in the flow now, so nothing needs positioning. What is left is the
   pairing: the transcript says which message the open card belongs to, rather
   than leaving you to match them up by eye. */
function positionRail() {
  const scroller = detailPane.querySelector("#chatScroll");
  if (!scroller) return;
  for (const lit of scroller.querySelectorAll(".msg--linked, .activity-row--linked")) {
    lit.classList.remove("msg--linked", "activity-row--linked");
  }
  const card = scroller.querySelector('.ccard[data-active="true"]');
  if (!card) return;
  const entry = commentsFor(app.selectedId).find((c) => c.id === card.dataset.id);
  const anchor = findAnchor(scroller, entry);
  const node = anchor && (anchor.nodeType === 1 ? anchor : anchor.commonAncestorContainer);
  const owner = node && (node.nodeType === 1 ? node : node.parentElement)?.closest(".msg, .activity-row");
  if (owner) owner.classList.add(owner.classList.contains("msg") ? "msg--linked" : "activity-row--linked");
}

/* Where the passage this comment belongs to is now. The transcript is rebuilt
   from data, so the element it was selected in never survives — this finds it
   again, in three widening steps.

   It deliberately does not rely on the mark. A passage crossing a bold run or a
   link cannot be wrapped in one, and anchoring to marks alone put exactly those
   cards at the top of the rail rather than beside anything. */
function findAnchor(scroller, entry) {
  if (!entry) return null;
  const first = entry.text.split("\n").map((l) => l.trim()).find((l) => l.length >= 12)
    || entry.text.trim();

  /* The turn it was made against, first and by preference. Searching the whole
     transcript by words attached the comment to whichever turn happened to say
     the same thing first, which is usually not the one you were reading. */
  const owner = entry.key
    ? [...scroller.querySelectorAll(".msg, .activity-row")].find((m) => m.dataset.key === entry.key)
    : null;
  const hunt = (root, skip) => {
    if (!first || first.length < 8) return null;
    let seen = 0;
    for (const body of root.querySelectorAll(QUOTABLE)) {
      const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        let at = node.data.indexOf(first);
        while (at >= 0) {
          if (seen++ === skip) {
            const range = document.createRange();
            range.setStart(node, at);
            range.setEnd(node, at + first.length);
            return range;
          }
          at = node.data.indexOf(first, at + 1);
        }
      }
    }
    return null;
  };

  if (owner) {
    // The right occurrence inside the right turn; failing that, its first; and
    // failing that the turn itself, which is still the correct message.
    return hunt(owner, entry.nth || 0) || hunt(owner, 0) || owner;
  }
  // 1. The mark, when there is one: the tightest anchor available.
  if (first) {
    for (const mark of scroller.querySelectorAll("mark.commented")) {
      if (mark.textContent === first) return mark;
    }
  }
  // 2. The text itself, wherever it sits — the turn is no longer in the page of
  //    transcript being shown, so this is a best effort rather than the answer.
  const loose = hunt(scroller, 0);
  if (loose) return loose;
  // 3. The message it came from, by who and when. Coarse, but it still puts the
  //    card beside the right turn rather than at the top of the rail.
  if (entry.at) {
    for (const owner of scroller.querySelectorAll(".msg, .activity-row")) {
      if (owner.dataset.at === entry.at && owner.dataset.who === entry.who) return owner;
    }
  }
  return null;
}

/* Gathering the cards into one message, in the order they appear in the
   conversation rather than the order they were written. */
function commentsAsMessage(list) {
  return list.filter((c) => c.remark.trim())
    .map((c) => `${quoteBlock(c)}\n\n${c.remark.trim()}`)
    .join("\n\n");
}

async function sendComments(session, button) {
  const list = commentsFor(app.selectedId).filter((c) => c.remark.trim());
  if (!list.length || app.inFlight) return;
  const text = commentsAsMessage(commentsFor(app.selectedId));
  const url = session.status === "stopped" ? "/api/start" : "/api/say";
  app.inFlight = url;
  button.disabled = true;
  try {
    const response = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: session.sessionId, text }),
    });
    const data = await response.json().catch(() => ({}));
    if (response.ok && data.ok) {
      // Sent comments leave the rail but keep their marks: the rail is what is
      // outstanding, the marks are where you have been.
      comments.set(app.selectedId, commentsFor(app.selectedId).filter((c) => !c.remark.trim()));
      activeComment = null;
      renderRail();
    }
    showSnackbar(data.message || (response.ok ? "Sent" : "That did not send"));
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    app.inFlight = null;
    button.disabled = false;
    poll();
  }
}

/* Attached once, to the pane, and never inside a render.

   This was bound inside renderDetail while the cards lived in a container that
   was rebuilt with it, so each listener died with the element it was on. On the
   pane, which survives every render, they accumulated instead — one per repaint,
   each holding the session that happened to be selected when it was attached.
   Sending then fired all of them, and comments landed on sessions that were not
   even on screen. The session is read at click time now, not captured. */
detailPane.addEventListener("click", (event) => {
  const act = event.target.closest("[data-cc]");
  if (act) {
    const id = act.dataset.id;
    if (act.dataset.cc === "drop") {
      comments.set(app.selectedId, commentsFor(app.selectedId).filter((c) => c.id !== id));
      if (activeComment === id) activeComment = null;
      forgetCommented(id);
      renderRail();
    } else if (act.dataset.cc === "edit") {
      const entry = commentsFor(app.selectedId).find((c) => c.id === id);
      if (entry) { entry.editing = true; activeComment = id; renderRail(); focusComment(id); }
    } else if (act.dataset.cc === "keep") {
      closeComment(id, true);
    }
    return;
  }
  const sendButton = event.target.closest(".rail__send");
  if (!sendButton) return;
  const live = selected();
  if (live) sendComments(live, sendButton);
});

// Fires for every selection change, including the ones that clear it, so this
// is both the show and the hide.
document.addEventListener("selectionchange", syncQuoteChip);
// Capture, because the transcript scrolls in its own box and scroll does not
// bubble — the same reason the jump-to-last pill listens this way.
window.addEventListener("scroll", syncQuoteChip, true);
window.addEventListener("resize", syncQuoteChip);
// The cards are placed against passages in the same scrolling box, so they move
// for the same reasons the chip does.
// Nothing to reposition on scroll or resize any more: the cards are in the
// transcript and move with it.
// Taking an action must not take the selection first — mousedown outside a
// selection collapses it, and the passage goes with it.
quoteChip.addEventListener("mousedown", (event) => event.preventDefault());
quoteChip.addEventListener("click", (event) => {
  const act = event.target.closest("[data-sel]")?.dataset.sel;
  if (act === "copy") copySelection();
  else if (act === "comment") commentOnSelection();
});

/* Copy is the other half of what you want from a selection, and the panel is the
   one place the transcript is readable without opening the terminal. */
async function copySelection() {
  const quote = pendingQuote;
  const text = quote ? quote.parts.map((p) => p.text).join("\n\n") : "";
  hideQuoteChip();
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    showSnackbar("Copied");
  } catch {
    showSnackbar("Could not reach the clipboard");
  }
}
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !quoteChip.hidden) { hideQuoteChip(); event.stopPropagation(); }
}, true);
/* A keyboard path to the same thing. The chip is a real button and Tab reaches
   it, but reaching for the mouse to take an offer the keyboard just raised is
   the sort of thing that stops people using it. Alt is used rather than Ctrl or
   a bare letter: a bare letter would fire while you were typing, and the common
   Ctrl combinations are already the browser's. */
document.addEventListener("keydown", (event) => {
  if (!event.altKey || event.ctrlKey || event.metaKey) return;
  if (event.key !== "c" && event.key !== "C") return;
  // Keyboard selection does not run through the pointer, so the chip may not be
  // up yet — read the selection directly rather than waiting for a frame.
  const quote = pendingQuote || quotableSelection();
  if (!quote) return;
  event.preventDefault();
  pendingQuote = quote;
  commentOnSelection();
});

/* Renaming. The title in the header is a button; clicking it swaps in a field
   in the same spot. Enter or leaving the field keeps the name, Escape drops it,
   and an empty name puts the session's own name back. The name is kept by the
   server, so it outlives a reload and shows in the list too. */
function startRename(session, button) {
  if (sidebar.renamingId) return;
  sidebar.renamingId = session.sessionId;
  const field = document.createElement("input");
  field.type = "text";
  field.className = "name-field md-headline-small";
  field.value = session.name;
  field.maxLength = 80;
  field.setAttribute("aria-label", "Session name");
  // Wide enough that a whole name is readable while you type it, and it grows
  // with what you type rather than scrolling inside a short box.
  const fit = () => { field.style.width = `${Math.min(72, Math.max(28, field.value.length + 2))}ch`; };
  fit();
  field.addEventListener("input", fit);
  button.replaceWith(field);
  field.focus();
  field.select();

  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    sidebar.renamingId = null;
    const name = field.value.trim();
    if (save && name !== session.name) commitRename(session, name);
    else renderDetail(true);
  };
  field.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); finish(true); }
    else if (event.key === "Escape") { event.preventDefault(); finish(false); }
  });
  field.addEventListener("blur", () => finish(true));
}

async function commitRename(session, name) {
  // Show the new name at once; the next poll confirms it from the server.
  const local = app.feed.sessions.find((s) => s.sessionId === session.sessionId);
  if (local) local.name = name || local.defaultName || local.name;
  render();
  try {
    const response = await fetch("/api/rename", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: session.sessionId, name }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) showSnackbar(data.message || "That rename did not stick");
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    poll();
  }
}

function detailHeader(session, state, host) {
  const stopped = session.status === "stopped";
  // A stopped session has no host to name and no uptime to count — what it has
  // is a folder to come back to and a time it was last seen.
  const meta = stopped ? [`<span class="md-mono md-body-small">${escapeHtml(shorten(session.cwd, 3))}</span>`] : [
    `<span class="host md-label-small">${host.icon}${escapeHtml(host.label)}</span>`,
    `<span class="md-mono md-body-small">${escapeHtml(shorten(session.cwd, 3))}</span>`,
  ];
  if (session.branch) meta.push(`<span class="meta-sep">·</span><span class="md-mono md-body-small">${escapeHtml(session.branch)}</span>`);
  meta.push(stopped
    ? `<span class="meta-sep">·</span><span class="md-body-small">kept here</span>`
    : `<span class="meta-sep">·</span><span class="md-body-small">up ${duration(Date.now() / 1000 + app.skew - session.startedAt)}</span>`);
  // The transcript's own reading first — it is the fresher of the two — and the
  // one that came with the session behind it, so the subject is there on the
  // first paint rather than a moment after it.
  const title = (chat.transcriptFor === session.sessionId && chat.transcript?.title) || session.title || null;
  return `<header class="detail-header">
      <div class="detail-header__top">
        <div class="detail-header__text">
          <div class="detail-header__title">
            <h2 class="md-headline-small">
              <button class="name-button md-state" data-act="rename" title="Click to rename this session"
                      aria-label="Rename ${escapeHtml(session.name)}"><span>${escapeHtml(session.name)}</span>${ICON.pencil}</button>
            </h2>
            <span class="md-label-large">${escapeHtml(state.label)}
              <span class="md-mono md-body-small" data-since="${displaySince(session)}">${duration(Date.now() / 1000 + app.skew - displaySince(session))}</span></span>
          </div>
          ${title ? `<p class="detail-header__subtitle md-body-medium">${escapeHtml(title)}</p>` : ""}
          <div class="detail-header__meta">${meta.join(" ")}</div>
        </div>
        <div class="detail-header__actions">${headerActions(session)}</div>
      </div>
      ${traceFor(session)}
    </header>`;
}

/* Identifying asks the session's own terminal which window it is showing, by
   retitling it for a moment. The title comes straight back. */
const IDENTIFY_NOTE = "Asking the terminal — its title will flicker";

function headerActions(session) {
  // A stopped session has no window to raise; what it has is a way back.
  if (session.status === "stopped") {
    if (!app.feed.canSend) return `<p class="hint md-label-small">starting needs the panel on loopback</p>`;
    return `<button class="button button--filled md-state" data-act="start">${ICON.play} Start it up</button>
            <p class="hint md-label-small">resumes this conversation</p>`;
  }
  if (!app.feed.canFocus) return `<p class="hint md-label-small">focusing needs xdotool</p>`;
  const win = session.window;
  if (!win) {
    return `<button class="button button--tonal md-state" data-act="pair">${ICON.pair} Pair window</button>
            <p class="hint md-label-small">then one click jumps here</p>`;
  }
  const paired = isRemembered(win);
  // An ambiguous match still gets a Focus button: pressing it identifies the
  // window first and then raises it, which is the one click you wanted anyway.
  const title = isAmbiguous(win) ? "" : win.title;
  return `<button class="button button--filled md-state" data-act="focus"${title ? ` title="${escapeHtml(title)}"` : ""}>${ICON.focus} Focus window</button>
          <p class="hint md-label-small">${windowSays(win, "short")} <span class="meta-sep">·</span>
            <button class="link-button" data-act="${paired ? "unpair" : "pair"}">${paired ? "clear" : "pick another"}</button></p>`;
}

/* An empty shell. The bar is filled by paintTrace and then kept up to date in
   place: the trace changes every second, and rebuilding it under the pointer
   would blink the tooltip out on every poll. */
function traceFor(session) {
  if (!(session.trace || []).length) return "";
  return `<div class="trace">
      <div class="trace__bar" role="img" aria-label="State over the last while"></div>
      <div class="trace__axis md-label-small"><span></span><span>now</span></div>
      <div class="trace__tip md-label-small" data-open="0"></div>
    </div>`;
}

/* The spans to draw: everything inside the window, with neighbours that show as
   the same state joined up — otherwise folding `shell` into Waiting would leave
   a seam every time a session ran a command. */
function traceSpans(session) {
  const spans = session.trace || [];
  if (!spans.length) return null;
  const end = app.feed.now;
  const observed = end - spans[0].from;
  const window = Math.min(app.feed.historySeconds, Math.max(observed, 15));
  const start = end - window;
  const merged = [];
  for (const span of spans) {
    if (span.to <= start || span.from >= end) continue;
    const key = stateKeyOf(span.status);
    const last = merged[merged.length - 1];
    if (last && last.key === key) last.to = span.to;
    else merged.push({ key, from: span.from, to: span.to });
  }
  const drawn = [];
  for (const span of merged) {
    const from = Math.max(span.from, start), to = Math.min(span.to, end);
    if (to <= from) continue;
    drawn.push({
      key: span.key, from: span.from, to: span.to,
      width: ((to - from) / window) * 100,
      live: span.to >= end,
      clipped: span.from < start,
    });
  }
  return { drawn, observed: Math.min(window, observed) };
}

function paintTrace(root, session) {
  const bar = root.querySelector(".trace__bar");
  if (!bar) return;
  const model = traceSpans(session);
  if (!model) return;
  const { drawn, observed } = model;
  const span = duration(observed);
  bar.setAttribute("aria-label", `State over the last ${span}`);
  root.querySelector(".trace__axis span").textContent = `last ${span}`;

  // Same run of states as last time? Then only the numbers moved: widen the
  // slices where they are, and leave every node — and the hover on it — alone.
  const shape = drawn.map((s) => `${s.key}@${s.from.toFixed(3)}`).join("|");
  const same = bar.dataset.shape === shape && bar.children.length === drawn.length;
  if (!same) bar.innerHTML = drawn.map(() => `<span class="trace__span"></span>`).join("");
  bar.dataset.shape = shape;

  drawn.forEach((s, i) => {
    const node = bar.children[i];
    const state = STATE[s.key] || STATE.idle;
    node.style.width = `${s.width.toFixed(3)}%`;
    if (!same) {
      node.style.setProperty("--seg", state.colour);
      node.dataset.state = state.label;
      node.dataset.colour = state.colour;
      node.dataset.from = s.from;
      node.dataset.clipped = s.clipped ? "1" : "";
    }
    const to = s.live ? "" : String(s.to);
    if (node.dataset.to !== to) node.dataset.to = to;
  });

  // A tooltip standing open over the live slice is counting up; refresh its text
  // without disturbing the pointer.
  root._traceRefresh?.();
}

/* Wall-clock time of a server epoch, corrected for the client's clock offset. */
function clockAt(epoch) {
  return new Date((epoch - app.skew) * 1000).toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

/* Hovering a slice of the trace names the state and says when it started and
   ended. A slice running up to now has no end yet, so it says "now" instead of
   inventing one; a slice older than the window says its true start with a "from
   before" marker rather than the clipped edge. */
let tracePointer = null; // last pointer position, so a repaint can pick the tip back up
function wireTrace(root) {
  const bar = root.querySelector(".trace__bar");
  const tip = root.querySelector(".trace__tip");
  if (!bar || !tip) return;
  let hot = null;

  const hide = () => {
    tip.dataset.open = "0";
    hot?.classList.remove("trace__span--hot");
    hot = null;
  };

  const showAt = (clientX, clientY) => {
    const seg = document.elementFromPoint(clientX, clientY)?.closest(".trace__span");
    if (!seg || !bar.contains(seg)) return hide();
    if (seg !== hot) {
      hot?.classList.remove("trace__span--hot");
      seg.classList.add("trace__span--hot");
      hot = seg;
    }
    const from = Number(seg.dataset.from);
    const to = seg.dataset.to ? Number(seg.dataset.to) : null;
    const ended = to === null ? "now" : clockAt(to);
    const began = seg.dataset.clipped ? `before ${clockAt(from)}` : clockAt(from);
    const text = `<span class="trace__tip-state md-label-medium">
        <span class="trace__tip-dot" style="--seg:${seg.dataset.colour}"></span>
        ${escapeHtml(seg.dataset.state)} <span class="md-mono">${duration((to ?? app.feed.now) - from)}</span>
      </span>
      <span class="trace__tip-times md-mono">${escapeHtml(began)} → ${escapeHtml(ended)}</span>`;
    if (tip.innerHTML !== text) tip.innerHTML = text;
    // Follow the pointer along the bar, kept clear of both edges.
    const rect = bar.getBoundingClientRect();
    const half = tip.offsetWidth / 2;
    tip.style.left = `${Math.min(Math.max(clientX - rect.left, half), rect.width - half)}px`;
    tip.dataset.open = "1";
  };

  bar.addEventListener("pointermove", (event) => {
    tracePointer = { x: event.clientX, y: event.clientY };
    showAt(event.clientX, event.clientY);
  });
  // A bar torn out from under the pointer also fires leave. That is not the
  // reader moving away, so it must not forget where the pointer is — the fresh
  // bar is about to ask.
  bar.addEventListener("pointerleave", () => {
    if (!bar.isConnected) return;
    tracePointer = null;
    hide();
  });

  // Repaints and full re-renders both land here. If the pointer never left the
  // bar, put the tip straight back — a stationary reader should not have to
  // jiggle the mouse to get it again, and the live slice keeps counting up.
  root._traceRefresh = () => { if (tracePointer) showAt(tracePointer.x, tracePointer.y); };
  root._traceRefresh();
}

/* Why this session cannot be sent to, or null if it can. Every case says what is
   wrong rather than leaving a dead box on screen. */
function sendBlockedReason(session) {
  if (!app.feed.canSend) return "sending is off — the panel is not on loopback";
  // A stopped session gets a composer of its own: writing in it starts it up.
  if (session.status === "stopped") return null;
  if (session.status === "offline") return "that session has closed";
  if (!session.canSay) return "this session is not listening for messages";
  // A blocked session cannot read a queued message: the prompt in front of it is
  // modal. Queuing one anyway would look like it was ignored. What it says next
  // depends on whether the prompt can be answered from here, because "answer it in
  // the terminal" is wrong advice for a question with buttons right above the
  // composer.
  if (session.status === "waiting") {
    // A question is on the card above, so point at it rather than at "the
    // terminal" in the abstract; a permission prompt has no card and no options.
    return session.question
      ? "answer the question above at this session's own prompt — a message sent from here would queue up behind it"
      : "answer the prompt in the terminal — a queued message cannot reach it";
  }
  return null;
}

/* The multiple-choice question a session is standing at, drawn above the
   composer.

   It is a card and not a form, and that is not a shortcut. The only channel into a
   live session is its messaging socket, and the socket takes exactly one kind of
   message: a user turn, which lands in the prompt queue. The queue is behind the
   question — Claude Code is waiting on a keypress at its own prompt — so an answer
   sent from here would sit unread until somebody answered at the terminal anyway,
   and then arrive afterwards as a stray message. Rather than offer a button that
   quietly does that, the card shows what was asked, numbers the options the way the
   prompt does, and offers the window. Reading it is what saves the trip; the
   keypress still happens there. */
function questionCard(session) {
  const asked = session.question;
  if (!asked || !asked.questions?.length) return "";
  const rows = asked.questions.map((q) => {
    const options = (q.options || []).map((option, i) => `<li class="ask__option">
        <span class="ask__index md-label-medium md-mono">${i + 1}</span>
        <span><span class="ask__label md-body-medium">${escapeHtml(option.label)}</span>
        ${option.description ? `<span class="ask__why md-body-small">${escapeHtml(option.description)}</span>` : ""}</span>
      </li>`).join("");
    return `<div class="ask__q">
        ${q.question ? `<p class="ask__text md-body-medium">${escapeHtml(q.question)}</p>` : ""}
        ${options ? `<p class="ask__how md-label-small">${q.multiSelect ? "several answers can be picked" : "one answer"}
          <span class="meta-sep">·</span> ${q.options.length} options</p>
          <ul class="ask__options">${options}</ul>` : ""}
      </div>`;
  }).join("");
  // Raising the window is the whole of what the panel can do here, so it is only
  // offered when there is a window to raise.
  const go = session.window && app.feed.canFocus
    ? `<button class="button button--text md-state ask__go" data-act="focus">${ICON.focus}Answer there</button>`
    : "";
  return `<div class="ask">
      <div class="ask__card">
        <p class="ask__head md-label-large">${ICON.ask}
          <span class="ask__head-label">${escapeHtml(asked.questions[0].header || "Claude asked you something")}</span>${go}</p>
        ${rows}
        <p class="ask__note md-label-small">Pick it at this session's own prompt — a message sent from here would queue up behind the question rather than answer it.</p>
      </div>
    </div>`;
}

function composer(session) {
  const why = sendBlockedReason(session);
  if (why) {
    return `<div class="composer"><p class="composer__why md-label-medium">${escapeHtml(why)}</p></div>`;
  }
  const queued = stateKeyOf(session.status) === "busy";
  const stopped = session.status === "stopped";
  const placeholder = stopped
    ? "Write here and it starts back up…"
    : queued ? "Queue a message for when it finishes…" : "Send a message…";
  return `<div class="composer">
      <div class="cmdbar" id="cmdBar" hidden></div>
      <div class="composer-grip" id="composerGrip" role="separator" aria-orientation="horizontal"
        tabindex="0" aria-label="Resize the message box"
        title="Drag to resize · double-click to fit the text"></div>
      <textarea class="composer__field md-body-large" id="sayField" rows="1"
        aria-label="Message this session"
        placeholder="${placeholder}"></textarea>
      <button class="button button--filled md-state composer__send" data-act="say">${stopped ? "Start &amp; send" : "Send"}</button>
    </div>`;
}

/* A stable handle on one turn, so a comment can say which message it was made
   against rather than which words. Two turns can carry the same sentence — and
   matching a comment back by its words alone attached it to whichever one came
   first in the transcript, which was usually not the one you were reading. */
function messageKey(message) {
  const when = clockOf(message.at);
  const what = message.text
    ? message.text.slice(0, 48)
    : (message.tools || []).map((t) => t.name).join(",");
  return `${message.role || "tool"}|${when}|${what}`;
}

function chatPanel(session) {
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
  const toolLines = (tools) => (tools || []).map((tool) => `<span class="tool-line md-mono md-body-small">
      <span class="tool-line__name">${escapeHtml(tool.name)}</span>
      <span class="tool-line__detail">${escapeHtml(tool.detail || "")}</span></span>`).join("");

  for (const message of chat.transcript.messages) {
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
        </div>`);
      continue;
    }
    if (!message.text) continue;
    // A message that came in over the socket says so: sent from this composer it
    // reads "you, from here", and sent by another session it carries that
    // session's name — because the receiving Claude was told the same thing.
    const who = message.role !== "user" ? "claude"
      : message.from === undefined ? "you"
      : message.from ? `${escapeHtml(message.from)} <span class="meta-sep">·</span> another session`
      : "you <span class=\"meta-sep\">·</span> from here";
    // The same thing in plain text, for the attribution line a quote carries
    // back to the session. "another session" is a distinction for the reader
    // here, not for the Claude being quoted at — what it needs is whose words
    // these were, and it only ever wrote its own.
    const whoPlain = message.role !== "user" ? "claude" : message.from || "you";
    const tools = toolLines(message.tools);
    rows.push(`<div class="msg msg--${message.role === "user" ? "user" : "assistant"}"
        data-who="${escapeHtml(whoPlain)}" data-at="${escapeHtml(clockOf(message.at))}"
        data-key="${escapeHtml(messageKey(message))}">
        <div class="msg__who"><span class="md-label-small">${who}</span><span class="md-label-small md-mono">${escapeHtml(clockOf(message.at))}</span></div>
        <div class="msg__text md-body-medium">${renderMarkdown(message.text)}</div>
        ${tools ? `<div class="msg__tools">${tools}</div>` : ""}
      </div>`);
  }
  return `<div class="chat">${rows.join("")}</div>`;
}


function notifyWaiting(sessions) {
  const seen = new Set();
  for (const session of sessions) {
    seen.add(session.sessionId);
    const before = app.lastStatuses.get(session.sessionId);
    if (before && before !== "waiting" && session.status === "waiting"
        && !app.mutedSessions.has(session.sessionId)
        && "Notification" in window && Notification.permission === "granted") {
      new Notification(`${session.name} needs you`, {
        body: `${shorten(session.cwd)} is waiting for an answer.`, tag: session.sessionId,
      });
    }
    app.lastStatuses.set(session.sessionId, session.status);
  }
  for (const key of [...app.lastStatuses.keys()]) if (!seen.has(key)) app.lastStatuses.delete(key);
}

function paintFavicon(counts) {
  const read = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const dot = counts?.waiting ? read("--md-extended-color-waiting-color")
    : counts?.busy ? read("--md-sys-color-primary") : read("--md-extended-color-idle-color");
  const back = read("--md-sys-color-surface-container-highest");
  if (!dot || !back) return;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
    <rect width="32" height="32" rx="9" fill="${back}"/><circle cx="16" cy="16" r="7" fill="${dot}"/></svg>`;
  let link = document.querySelector("link[rel='icon']");
  if (!link) { link = document.createElement("link"); link.rel = "icon"; document.head.appendChild(link); }
  link.href = "data:image/svg+xml," + encodeURIComponent(svg);
}
function renderSwatches() {
  swatchRow.innerHTML = "";
  for (const preset of SEED_PRESETS) {
    const button = document.createElement("button");
    button.className = "swatch md-state";
    button.type = "button";
    button.title = preset.name;
    button.setAttribute("aria-label", `Base colour ${preset.name}`);
    button.setAttribute("aria-pressed", String(preset.hex.toLowerCase() === app.settings.seed.toLowerCase()));
    button.style.setProperty("--swatch", preset.hex);
    button.style.setProperty("--swatch-on", hexFromArgb(TonalPalette.fromInt(argbFromHex(preset.hex)).tone(100)));
    button.innerHTML = ICON.check;
    button.addEventListener("click", () => setSeed(preset.hex));
    swatchRow.appendChild(button);
  }
  const custom = document.createElement("label");
  custom.className = "swatch swatch--custom md-state";
  custom.title = "Any colour";
  custom.innerHTML = `${ICON.plus}<input type="color" value="${app.settings.seed}" aria-label="Custom base colour">`;
  custom.querySelector("input").addEventListener("input", (event) => setSeed(event.target.value));
  swatchRow.appendChild(custom);
  const hct = Hct.fromInt(argbFromHex(app.settings.seed));
  seedReadout.innerHTML = `Base <span class="code">${escapeHtml(app.settings.seed.toUpperCase())}</span> · hue ${Math.round(hct.hue)}° · chroma ${Math.round(hct.chroma)}`;
}
function renderContrast() {
  contrastGroup.innerHTML = "";
  for (const level of CONTRAST_LEVELS) {
    const button = document.createElement("button");
    button.className = "segmented__item md-state";
    button.type = "button";
    button.setAttribute("aria-pressed", String(level.key === app.settings.contrast));
    button.innerHTML = `${ICON.check}<span>${level.label}</span>`;
    button.addEventListener("click", () => {
      app.settings.contrast = level.key; persist(); applyScheme(); renderContrast();
    });
    contrastGroup.appendChild(button);
  }
}
function setSeed(hex) { app.settings.seed = hex; persist(); applyScheme(); renderSwatches(); }
/* ------------------------------------------------------- ending a session */
let endTarget = null;
function openEndDialog(session) {
  endTarget = session;
  const waiting = session.status === "waiting" || session.status === "busy" || session.status === "shell";
  document.getElementById("endSupporting").innerHTML =
    `<span class="md-mono">${escapeHtml(session.name)}</span> in
     <span class="md-mono">${escapeHtml(shorten(session.cwd, 2))}</span>, pid
     <span class="md-mono">${session.pid}</span>.
     ${waiting ? "It is mid-turn — anything it is doing right now stops. " : ""}The transcript is kept.
     Force quit only if it will not close on its own.`;
  endScrim.dataset.open = "true";
  document.getElementById("endCancel").focus();
}
function closeEndDialog() {
  endScrim.dataset.open = "false";
  endTarget = null;
  detailPane.querySelector("[data-act='end']")?.focus();
}
document.getElementById("endCancel").addEventListener("click", closeEndDialog);
endScrim.addEventListener("click", (event) => { if (event.target === endScrim) closeEndDialog(); });
for (const [id, force] of [["endConfirm", false], ["endForce", true]]) {
  document.getElementById(id).addEventListener("click", async (event) => {
    const session = endTarget;
    if (!session) return;
    await run("/api/end", session, event.currentTarget, null, { force });
    closeEndDialog();
  });
}



/* ------------------------------------------------- opening one, and the notice */

const openButton = document.getElementById("openButton");

/* The folder picker: the browser's own native dialog and nothing else.

   What it hands back is a folder name and the names directly inside it — never
   where it is — so the panel asks the server to place it. Where the fingerprint
   matches once, that is the folder. Where it matches several, this asks. Where it
   matches nothing, a field appears, because a folder outside your home directory
   has no other way to be named. */
const folderScrim = document.getElementById("folderScrim");
const folderField = document.getElementById("folderField");
const folderTyped = document.getElementById("folderTyped");
const folderChosen = document.getElementById("folderChosen");
const folderTarget = document.getElementById("folderBrowse");
let folderPicked = null;

function openFolderPicker(open) {
  folderScrim.dataset.open = String(open);
  if (open) {
    resetFolderPicker();
    folderTarget.focus();
  } else {
    openButton.focus();
  }
}

function resetFolderPicker() {
  folderPicked = null;
  folderChoices.hidden = true;
  folderTyped.hidden = true;
  folderChosen.hidden = true;
  folderTarget.hidden = false;
  folderField.value = "";
  folderBrowseNote.textContent = "";
  document.getElementById("folderOpen").disabled = true;
}

/* The folder this will open, once there is one. Naming it on screen matters more
   here than it did with a walk: nothing else on the dialog says where the pick
   landed, and starting a session in the wrong checkout is the mistake to avoid. */
function setFolderPicked(path) {
  folderPicked = path;
  document.getElementById("folderChosenPath").textContent = path;
  folderChosen.hidden = false;
  // The invitation has been taken, so it gives up its room to the answer.
  folderTarget.hidden = true;
  folderChoices.hidden = true;
  document.getElementById("folderOpen").disabled = false;
  document.getElementById("folderOpen").focus();
}

/* The browser's native folder dialog.

   It is the real OS picker, which is what makes it worth having. What it will not
   give up is where the folder is: `webkitdirectory` reports each file's path
   relative to whatever you chose, so the page learns the folder's name and what is
   directly inside it and nothing more. Both halves go to the server, which looks
   for the one folder on disk that matches — and where more than one does, this
   asks rather than guessing, because the wrong answer starts a session in the
   wrong checkout.

   The walk below is not a fallback for a missing feature; it is what answers when
   the fingerprint is ambiguous, when the folder is empty enough to have no
   fingerprint at all, and when the panel is being read from another machine, where
   no local dialog can help. */
const folderNative = document.getElementById("folderNative");
const folderBrowseNote = document.getElementById("folderBrowseNote");
const folderChoices = document.getElementById("folderChoices");

/* Two native dialogs exist, and which one opens matters.

   `showDirectoryPicker()` is the folder chooser proper: it asks to *view* the
   folder, hands back a handle, and reads only the names at the top level — which
   is all the fingerprint needs. Nothing is uploaded and nothing is enumerated
   below the first rung, so a repository with a hundred thousand files under
   node_modules costs the same as an empty one.

   A `webkitdirectory` input is the fallback for browsers without it (Firefox,
   Safari). It reaches the same place, but the browser frames it as an upload —
   "Upload N files to this site?" — and walks the whole tree to answer. Nothing
   leaves the page either way: only the folder's name and the names directly
   inside it are ever sent. It is second because of how it reads, not what it
   does. */
document.getElementById("folderBrowse").addEventListener("click", async () => {
  resetFolderPicker();
  if (typeof window.showDirectoryPicker !== "function") {
    folderNative.value = "";     // so picking the same folder twice still fires
    folderNative.click();
    return;
  }
  let handle;
  try {
    handle = await window.showDirectoryPicker({ mode: "read" });
  } catch (error) {
    // Cancelling is not a failure and must not leave a message behind.
    if (error?.name !== "AbortError") folderBrowseNote.textContent = "Your file browser would not open";
    return;
  }
  folderBrowseNote.textContent = `Looking for ${handle.name}…`;
  const children = [];
  try {
    for await (const name of handle.keys()) {
      children.push(name);
      if (children.length >= 40) break;
    }
  } catch (error) {
    // A handle without read permission still names itself, and the name alone is
    // often enough — so this goes on rather than stopping.
  }
  placeFolder(handle.name, children);
});

folderNative.addEventListener("change", () => {
  const files = [...folderNative.files];
  if (!files.length) { folderBrowseNote.textContent = "Nothing came back from that"; return; }
  // "Project/src/main.py" — the first segment is the folder you chose, the second
  // is what is directly inside it.
  const parts = files.map((file) => (file.webkitRelativePath || "").split("/")).filter((p) => p.length > 1);
  const name = parts[0]?.[0];
  if (!name) { folderBrowseNote.textContent = "That folder came back without a name"; return; }
  folderBrowseNote.textContent = `Looking for ${name}…`;
  placeFolder(name, [...new Set(parts.map((p) => p[1]).filter(Boolean))].slice(0, 40));
});

/* Turn a folder's name and what is inside it into the one place on disk it can
   be — see locate_folder for why that is what the browser leaves us to work
   with. */
async function placeFolder(name, children) {
  try {
    const response = await fetch("/api/locate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, children }),
    });
    const found = await response.json().catch(() => ({}));
    if (!response.ok || !found.folders?.length) {
      folderBrowseNote.textContent = found.message || "Could not place that folder";
      folderTyped.hidden = false;
      folderField.value = `/${name}`;
      folderField.focus();
      folderField.select();
      return;
    }
    if (found.folders.length === 1) {
      folderBrowseNote.textContent = "";
      setFolderPicked(found.folders[0]);
      return;
    }
    // More than one folder of that name holds those entries. Which one is a
    // question, not a guess.
    folderBrowseNote.textContent = `${found.folders.length} folders match — pick the right one`;
    folderChoices.innerHTML = "";
    for (const candidate of found.folders) {
      const row = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "folder-row md-state";
      button.title = candidate;
      button.innerHTML = `${ICON.folder}<span class="folder-row__name">${escapeHtml(candidate)}</span>`;
      button.addEventListener("click", () => {
        folderBrowseNote.textContent = "";
        setFolderPicked(candidate);
      });
      row.append(button);
      folderChoices.append(row);
    }
    folderChoices.hidden = false;
  } catch (error) {
    folderBrowseNote.textContent = "Could not reach the server";
  }
}

openButton.addEventListener("click", () => openFolderPicker(true));
document.getElementById("folderAgain").addEventListener("click", () => {
  resetFolderPicker();
  folderTarget.click();
});
document.getElementById("folderCancel").addEventListener("click", () => openFolderPicker(false));
folderScrim.addEventListener("click", (event) => { if (event.target === folderScrim) openFolderPicker(false); });
// A typed path is only ever the one the picker could not place, so Enter opens
// rather than browsing — there is nothing left to browse.
folderField.addEventListener("input", () => {
  document.getElementById("folderOpen").disabled = !folderField.value.trim();
});
folderField.addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  const typed = folderField.value.trim();
  if (typed) openSessionIn(typed);
});

document.getElementById("folderOpen").addEventListener("click", () => {
  const folder = folderTyped.hidden ? folderPicked : (folderField.value.trim() || folderPicked);
  if (folder) openSessionIn(folder);
});

/* Start a session in a folder, and say what came back. The server resolves and
   checks the path again — the field is typed into, so it need not be one of the
   rungs the walk actually visited. */
async function openSessionIn(folder) {
  localStorage.setItem("cbu-open-folder", folder);
  openFolderPicker(false);
  openButton.disabled = true;
  showSnackbar("Opening a session there…", 20000);
  try {
    const response = await fetch("/api/new", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cwd: folder }),
    });
    const said = await response.json().catch(() => ({}));
    showSnackbar(said.message || (response.ok ? "Opening it…" : "That folder did not work"));
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    openButton.disabled = false;
    poll();
  }
}

function paintOpenButton() {
  openButton.hidden = !app.feed.canSend;
}

planButton.addEventListener("click", () => openPlan(true));
document.getElementById("closePlan").addEventListener("click", () => openPlan(false));
document.getElementById("planRefresh").addEventListener("click", () => { fetchPlan(true); paintPlanDialog(); });
planScrim.addEventListener("click", (event) => { if (event.target === planScrim) openPlan(false); });

function openSettings(open) {
  settingsScrim.dataset.open = String(open);
  if (open) { renderSwatches(); renderContrast(); document.getElementById("closeSettings").focus(); }
  else { document.getElementById("settingsButton").focus(); }
}
document.getElementById("settingsButton").addEventListener("click", () => openSettings(true));
document.getElementById("closeSettings").addEventListener("click", () => openSettings(false));
settingsScrim.addEventListener("click", (event) => { if (event.target === settingsScrim) openSettings(false); });
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (askScrim.dataset.open === "true") closeAsk(false);
  else if (folderScrim.dataset.open === "true") openFolderPicker(false);
  else if (endScrim.dataset.open === "true") closeEndDialog();
  else if (planScrim.dataset.open === "true") openPlan(false);
  else if (settingsScrim.dataset.open === "true") openSettings(false);
  // Nothing modal is open, so Escape drops whatever rows are picked.
  else clearPicked();
});
document.getElementById("resetTheme").addEventListener("click", () => {
  app.settings = { seed: DEFAULT_SEED, dark: matchMedia("(prefers-color-scheme: dark)").matches, contrast: "standard" };
  persist(); applyScheme(); renderSwatches(); renderContrast(); showSnackbar("Appearance reset");
});
themeToggle.addEventListener("change", () => { app.settings.dark = themeToggle.checked; persist(); applyScheme(); });
backButton.addEventListener("click", () => { panes.dataset.view = "list"; });
pickGroup.addEventListener("click", () => groupPicked());
pickClear.addEventListener("click", () => clearPicked());

/* ----------------------------------------------------------- interactions */
document.addEventListener("pointerdown", (event) => {
  const target = event.target.closest(".md-state");
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
// Hand the render loop to refresh.js before anything can ask for a redraw:
// modules reach the loop through it rather than importing main back.
serveRefresh({ render, renderDetail, poll });
loadSettings();
applyScheme();
if (app.selectedId) panes.dataset.view = "detail";
poll();
setInterval(poll, 1000);
fetchPlan(true);
setInterval(() => fetchPlan(false), 30_000);
setInterval(() => {
  const now = Date.now() / 1000 + app.skew;
  for (const node of document.querySelectorAll("[data-since]")) {
    node.textContent = duration(now - Number(node.dataset.since));
  }
}, 1000);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  poll();
  // The repository was left alone while the tab was away, so catch it up now
  // rather than at the end of an interval that started before you looked.
  if (app.selectedId && isGitTab(app.tab)) fetchGit(true);
});
