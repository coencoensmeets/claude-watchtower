/* The panel state that more than one part of the app reads.

   Every field here was a bare `let` in main.ts. That worked while the panel
   was a single scope and cannot survive being split up: a module may read an
   imported binding but never assign to one. Grouped into objects instead, so
   that a write from any module lands where every reader is already looking.

   State only one part of the panel touches does not belong here — it stays
   with the code that owns it. */

import { DEFAULT_SEED } from "./ui/theme.js";

export const CHAT_PAGE = 80;

/* Session ids and folder keys kept in localStorage. Read back defensively:
   it is storage the user can edit, so anything at all may be in it. */
export const loadKeySet = (key) => {
  try {
    const raw = JSON.parse(localStorage.getItem(key) || "[]");
    return new Set(Array.isArray(raw) ? raw.filter((k) => typeof k === "string") : []);
  } catch (error) {
    return new Set();
  }
};

/* What the panel is showing, and what it last heard from the server. */
export const app = {
  settings: { seed: DEFAULT_SEED, dark: matchMedia("(prefers-color-scheme: dark)").matches, contrast: "standard" },
  feed: { sessions: [], now: Date.now() / 1000, historySeconds: 1800, canFocus: true, canSend: false },
  skew: 0,
  lastGood: 0,
  filter: "all",
  selectedId: localStorage.getItem("cbu-selected") || null,
  tab: localStorage.getItem("cbu-tab") || "chat",
  inFlight: null,
  lastStatuses: new Map(),
  mutedSessions: new Set(JSON.parse(localStorage.getItem("cbu-muted") || "[]")),
};

/* The open conversation. */
export const chat = {
  transcript: null,
  transcriptFor: null,
  transcriptBusy: false,
  chatLimit: CHAT_PAGE,
  chatGrew: false,
};

/* The repository the selected session works in. */
export const repo = {
  git: null,
  gitFor: null,
  gitBusy: false,
  diffOpen: null,  // { path, staged } — the one row showing its diff
  diffText: null,  // null while it is being read, "" when there is none
  diffNote: "",  // why there is none, when there is none
  gitActing: false,  // one git action at a time, whatever the pane offers
  suggesting: false,
  commitCaret: null,
};

/* What has been spent — this session's tokens, and the subscription. */
export const spend = {
  usage: null,
  usageFor: null,
  usageBusy: false,
  plan: null,
};

/* The index: what is picked, folded, and being renamed. */
export const sidebar = {
  picked: new Set(),
  pickAnchor: null,
  visibleOrder: [],
  lastBlocks: [],
  renamingGroup: null,
  renamingId: null,
  looseFolders: loadKeySet("cbu-loose-folders"),
};

/* Transient chrome — the menu that is open, the composer being dragged. */
export const ui = {
  menuFor: null,  // sessionId the open menu belongs to
  menuGroup: null,  // or the key of the group it belongs to, for a header menu
  composerHeight: Number(localStorage.getItem("cbu-composer-height")) || null,
  resizingComposer: false,
  menuReturn: null,
  cmdOff: false,  // Escape puts the list away without touching the text
};

export const selected = () => app.feed.sessions.find((s) => s.sessionId === app.selectedId) || null;

export const sessionById = (id) => app.feed.sessions.find((s) => s.sessionId === id) || null;

export const CHAT_LIMIT_MAX = 500;
