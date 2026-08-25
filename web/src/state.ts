/* The panel state that more than one part of the app reads.

   Every field on the objects below was a bare `let` while the panel was one
   scope, and a bare `let` cannot survive being split up: a module may read an
   imported binding but never assign to one. Grouped into objects instead, so
   that a write from any module lands where every reader is already looking.

   The test for whether something belongs here is only whether a second module
   writes it. State one part of the panel owns stays with that part. */

import { allNotifying, DEFAULT_SEED } from "./ui/theme.js";
import type { Change, Feed, Git, Plan, Transcript, Usage } from "./types.js";

/* localStorage is not ours alone, and a half-written value or one left by an
   older version of the panel should not take the page down on the way up: every
   one of these is read before the first paint. */
export function readJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch (error) {
    return fallback;
  }
}

/* Session ids and folder keys kept in localStorage. Read back defensively:
   it is storage the user can edit, so anything at all may be in it. */
export function loadKeySet(key) {
  const raw = readJson(key, []);
  return new Set(Array.isArray(raw) ? raw.filter((k) => typeof k === "string") : []);
}

/* How much conversation the chat tab asks for. A page at a time, because the
   whole point of reading backwards is that a long session costs no more than a
   short one — but a truncated transcript can be asked for more, up to what the
   server will read back. Reset per session: the depth you dug to in one
   conversation says nothing about the next. */
export const CHAT_PAGE = 80;
export const CHAT_LIMIT_MAX = 500;

/* A half-typed message belongs to the session it was written for, not to the
   composer: switching away puts it aside and coming back brings it out again. */
export const sayDrafts = new Map();

/* The writing side of the Git tab. The message being typed is kept per session
   rather than in the DOM, because the pane is rebuilt whenever the repository
   moves — which is exactly while a commit message is being written. */
export const commitDrafts = new Map();

/* What each session was last announced as, so the same thing twice running is
   said once. */
export const lastAnnounced = new Map();

export const mutedSessions = new Set(readJson("cbu-muted", []));

/* Sessions that should not say when they have finished a turn. Held as the
   exceptions rather than as the subscribers, so the switch is on by default the
   way the prompt one is — and so a session the panel has never seen before
   behaves like the rest of them. */
export const quietWhenDone = new Set(readJson("cbu-quiet-done", []));

/* What the panel is showing, and what it last heard from the server.

   `showingSettings` is whether the detail pane is on the settings page rather
   than a session. Not a route and not stored: it is where you are looking right
   now, and a reload should put you back on the session you were watching. */
export const app = {
  settings: {
    seed: DEFAULT_SEED,
    dark: matchMedia("(prefers-color-scheme: dark)").matches,
    contrast: "standard",
    notify: allNotifying(),
    // Whether a session's header offers to open its folder in an editor. On by
    // default: it is the state the panel shipped with, and a setting that hides
    // a button should not be the reason a button was never seen.
    showEditor: true,
  },
  feed: { sessions: [], now: Date.now() / 1000, historySeconds: 1800, canFocus: true, canSend: false } as Feed,
  skew: 0,
  lastGood: 0,
  filter: "all",
  selectedId: localStorage.getItem("cbu-selected") || null,
  tab: localStorage.getItem("cbu-tab") || "chat",
  inFlight: null,
  showingSettings: false,
};

/* The open conversation, and the change standing in front of it.

   `chatGrew` is set when "show more" is what caused the re-render, so the pane
   can hold the message you were reading in place instead of jumping. */
export const chat = {
  transcript: null,
  transcriptFor: null,
  transcriptBusy: false,
  chatLimit: CHAT_PAGE,
  chatGrew: false,
  changeShown: null,   // toolUseId, or null for the conversation
  chatReturn: 0,       // the scroll position to hand back
};

/* The repository the selected session works in.

   `suggesting` is held apart from `gitActing` because it outlives a repaint:
   writing a message takes long enough for a poll to rebuild the pane underneath
   it, and the new sparkle has to come back still spinning. */
export const repo = {
  git: null,
  gitFor: null,
  gitBusy: false,
  diffOpen: null,     // { path, staged } — the one row showing its diff
  diffText: null,     // null while it is being read, "" when there is none
  diffNote: "",       // why there is none, when there is none
  gitActing: false,   // one git action at a time, whatever the pane offers
  suggesting: false,
  commitCaret: null,
};

/* What this session has asked of the models. */
export const spend = {
  usage: null,
  usageFor: null,
  usageBusy: false,
};

/* The index. The session whose name is being edited must survive a poll: a
   rebuild would take the field out from under whoever is typing in it. */
export const sidebar = {
  renamingId: null,
};

/* Transient chrome — the menu that is open, the composer being dragged.

   `composerHeight` is a pinned height for the message box in px, or null to
   size itself to the text. Dragging the rule above the box pins it;
   double-clicking hands it back. */
export const ui = {
  menuFor: null,     // sessionId the open menu belongs to
  menuGroup: null,   // or the key of the group it belongs to, for a header menu
  menuReturn: null,
  composerHeight: Number(localStorage.getItem("cbu-composer-height")) || null,
  resizingComposer: false,
  // A file is being held over the message box. Same purpose as the flag above:
  // the pane must not be rebuilt out from under a drag that has not landed yet.
  droppingOnComposer: false,
  // Whether the detail pane's header is folded down to its title. A phone thing:
  // the header is four or five lines of context that the conversation needs the
  // room for, so scrolling into the transcript folds it and scrolling back to
  // the top unfolds it again. Lives here rather than on the element because the
  // pane is rebuilt from scratch on every poll, and the fold must survive that.
  headerFolded: false,
};

export const selected = () => app.feed.sessions.find((s) => s.sessionId === app.selectedId) || null;

export const sessionById = (id) => app.feed.sessions.find((s) => s.sessionId === id) || null;
