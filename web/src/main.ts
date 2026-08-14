import {
  argbFromHex, hexFromArgb, Hct, SchemeVibrant, TonalPalette,
} from "/vendor/material-color-utilities.js";

/* ==========================================================================
   Dynamic colour — one seed generates the entire scheme.
   ========================================================================== */
const DEFAULT_SEED = "#E8288F";
const SEED_PRESETS = [
  { hex: "#E8288F", name: "Pink" }, { hex: "#7C4DFF", name: "Violet" },
  { hex: "#00A18F", name: "Teal" }, { hex: "#F4511E", name: "Coral" },
  { hex: "#3D5AFE", name: "Blue" }, { hex: "#7CB342", name: "Green" },
];
const CONTRAST_LEVELS = [
  { key: "standard", label: "Standard", value: 0 },
  { key: "medium", label: "Medium", value: 0.5 },
  { key: "high", label: "High", value: 1 },
];
/* `plenty` is not a session state: it is the green a plan figure takes while
   there is still room, and it comes from here so it is hue-spaced against the
   seed and the other two like they are. Keep it last so adding it did not move
   the hues waiting and idle already had. */
const STATE_BASE_HUES = { waiting: "#FF8A00", idle: "#5B6BC0", plenty: "#12A150" };
const MIN_HUE_GAP = 35;
const MAX_CUSTOM_CHROMA = 48;

const SYS_ROLES = [
  "primary", "onPrimary", "primaryContainer", "onPrimaryContainer",
  "secondary", "onSecondary", "secondaryContainer", "onSecondaryContainer",
  "tertiary", "onTertiary", "tertiaryContainer", "onTertiaryContainer",
  "error", "onError", "errorContainer", "onErrorContainer",
  "surface", "onSurface", "onSurfaceVariant", "surfaceDim", "surfaceBright",
  "surfaceContainerLowest", "surfaceContainerLow", "surfaceContainer",
  "surfaceContainerHigh", "surfaceContainerHighest",
  "outline", "outlineVariant", "inverseSurface", "inverseOnSurface", "inversePrimary",
  "shadow", "scrim",
];
const kebab = (name) => name.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
const hueDistance = (a, b) => { const d = Math.abs(a - b) % 360; return d > 180 ? 360 - d : d; };

function firstFreeHue(baseHue, occupied) {
  for (let delta = 0; delta <= 180; delta += 5) {
    for (const sign of delta === 0 ? [1] : [1, -1]) {
      const candidate = (baseHue + sign * delta + 360) % 360;
      if (occupied.every((hue) => hueDistance(candidate, hue) >= MIN_HUE_GAP)) return candidate;
    }
  }
  return baseHue;
}
function customRoles(palette, dark) {
  return dark
    ? { color: palette.tone(80), onColor: palette.tone(20), container: palette.tone(30), onContainer: palette.tone(90) }
    : { color: palette.tone(40), onColor: palette.tone(100), container: palette.tone(90), onContainer: palette.tone(10) };
}

let settings = { seed: DEFAULT_SEED, dark: matchMedia("(prefers-color-scheme: dark)").matches, contrast: "standard" };

function loadSettings() {
  const params = new URLSearchParams(location.search);
  const seed = params.get("seed") || localStorage.getItem("cbu-seed");
  if (seed && /^#[0-9a-f]{6}$/i.test(seed)) settings.seed = seed;
  const theme = params.get("theme") || localStorage.getItem("cbu-theme");
  if (theme === "dark" || theme === "light") settings.dark = theme === "dark";
  const contrast = params.get("contrast") || localStorage.getItem("cbu-contrast");
  if (CONTRAST_LEVELS.some((l) => l.key === contrast)) settings.contrast = contrast;
}
function persist() {
  localStorage.setItem("cbu-seed", settings.seed);
  localStorage.setItem("cbu-theme", settings.dark ? "dark" : "light");
  localStorage.setItem("cbu-contrast", settings.contrast);
}

function applyScheme() {
  const root = document.documentElement;
  const seedArgb = argbFromHex(settings.seed);
  const level = CONTRAST_LEVELS.find((l) => l.key === settings.contrast) || CONTRAST_LEVELS[0];
  const scheme = new SchemeVibrant(Hct.fromInt(seedArgb), settings.dark, level.value);
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
    for (const [role, argb] of Object.entries(customRoles(palette, settings.dark))) {
      root.style.setProperty(`--md-extended-color-${name}-${kebab(role)}`, hexFromArgb(argb));
    }
  }
  root.style.colorScheme = settings.dark ? "dark" : "light";
  root.dataset.schemeReady = "true";
  themeToggle.checked = settings.dark;
  themeLabel.textContent = settings.dark ? "Dark" : "Light";
  paintFavicon();
}

/* ------------------------------------------------------------- state map */
const STATE = {
  waiting: { label: "Needs an answer", short: "answer", prefix: "--md-extended-color-waiting" },
  busy: { label: "Working", short: "working", prefix: "--md-sys-color-primary", sys: true },
  idle: { label: "Waiting", short: "waiting", prefix: "--md-extended-color-idle" },
  offline: { label: "Closed", short: "closed", prefix: null },
  // A kept session whose process has gone. Not lost, just not running.
  stopped: { label: "Stopped", short: "stopped", prefix: null },
};
/* `shell` is not shown as a state of its own. It is what Claude Code writes on
   the way into a foreground command *and* what it leaves behind when a turn
   ends, so on screen it was a second colour for the same thing: waiting. The raw
   status still drives behaviour (a message can be queued for a shell session);
   only the display folds it in. */
const STATE_ALIAS = { shell: "idle" };
const stateKeyOf = (status) => STATE_ALIAS[status] || status;
const stateOf = (status) => STATE[stateKeyOf(status)] || STATE.idle;

/* When the state you can see began. The server times the raw status, which
   restarts every time a waiting session dips through `shell`; on screen nothing
   happened, so the clock should not jump back to zero. */
function displaySince(session) {
  const spans = session.trace || [];
  const key = stateKeyOf(session.status);
  let since = session.statusSince;
  for (let i = spans.length - 1; i >= 0; i--) {
    if (stateKeyOf(spans[i].status) !== key) break;
    since = spans[i].from;
  }
  return since;
}
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
const STATE_ORDER = ["waiting", "busy", "idle", "offline", "stopped"];

/* ---------------------------------------------------------------- icons */
const ICON = {
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
  focus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6M21 3l-9 9M10 5H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/></svg>',
  pair: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m4 4 7 16 2-6 6-2z"/></svg>',
  power: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v9"/><path d="M18.4 6.6a9 9 0 1 1-12.8 0"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-8 8H8l-5 3 1.5-5A8 8 0 1 1 21 12z"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
  // The branch, for the Git tab and the header badge on both git tabs.
  branch: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6.5" cy="18" r="2.5"/><circle cx="6.5" cy="6" r="2.5"/><circle cx="17.5" cy="10" r="2.5"/><path d="M6.5 8.5v7M17.5 12.5c0 3-2.5 3.5-5.5 3.5"/></svg>',
  // A commit graph, for History — three nodes on a line with one branching off.
  history: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="5" r="2"/><circle cx="7" cy="19" r="2"/><circle cx="17" cy="12" r="2"/><path d="M7 7v10M7 12h5.5a3 3 0 0 0 2.7-1.6"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 4.5v15l13-7.5z"/></svg>',
  pin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17v5"/><path d="M9 3h6l-1 6 3 3v2H7v-2l3-3z"/></svg>',
  pinOff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17v5"/><path d="M9 3h6l-1 6 3 3v2H7v-2l3-3z"/><path d="M3 3l18 18"/></svg>',
  up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M6 11l6-6 6 6"/></svg>',
  // An arrow onto a floor, rather than a plain arrow: it travels to the end of
  // the transcript, not one screen down.
  toBottom: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7 10l5 5 5-5"/><path d="M5 20h14"/></svg>',
  pencil: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4l10-10a2.8 2.8 0 0 0-4-4L4 16z"/><path d="m13.5 6.5 4 4"/></svg>',
  bell: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 9a6 6 0 1 0-12 0c0 5-2 6-2 6h16s-2-1-2-6"/><path d="M10.3 20a2 2 0 0 0 3.4 0"/></svg>',
  bellOff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 9a6 6 0 0 0-9-5.2"/><path d="M6.3 6.3A6 6 0 0 0 6 9c0 5-2 6-2 6h13"/><path d="M10.3 20a2 2 0 0 0 3.4 0"/><path d="m3 3 18 18"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>',
  group: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="6" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/></svg>',
  ungroup: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="6" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/><path d="m2 2 20 20"/></svg>',
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8a2 2 0 0 1 2-2h3.6l2 2H19a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  // Source-control actions, in the shapes the editor uses for them: stage is a
  // plus, unstage a minus, discard the undo arrow, sync the two chasing arrows.
  minus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M5 12h14"/></svg>',
  discard: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8h11a5 5 0 0 1 0 10H8"/><path d="m7 4-4 4 4 4"/></svg>',
  sync: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 12a8 8 0 0 1-13.7 5.6L4 15.4"/><path d="M4 12a8 8 0 0 1 13.7-5.6L20 8.6"/><path d="M20 4v4.6h-4.6M4 20v-4.6h4.6"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7 11l5 5 5-5"/><path d="M4 20h16"/></svg>',
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20V9M7 13l5-5 5 5"/><path d="M4 4h16"/></svg>',
  stash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="5" rx="1.5"/><path d="M5 12h14M7 16h10M9 20h6"/></svg>',
  more: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>',
  // A coin, for what the conversation has cost.
  coin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M15 9.2A2.8 2.8 0 0 0 12.4 7.5h-.8a2.2 2.2 0 0 0 0 4.4h.8a2.2 2.2 0 0 1 0 4.4h-.8A2.8 2.8 0 0 1 9 14.8M12 6v12"/></svg>',
  // A question, for the prompt a session is standing at.
  ask: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9.3 9.4A2.8 2.8 0 0 1 12 7.4c1.5 0 2.8 1 2.8 2.4 0 1.8-2.3 2.1-2.8 3.7"/><path d="M12 17h.01"/></svg>',
  // Two stars, the mark every editor now uses for "a model did this".
  sparkle: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3.5 11.7 8 16 9.8 11.7 11.5 10 16l-1.7-4.5L4 9.8 8.3 8z"/><path d="m17.5 15 .8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/></svg>',
};
const HOST_KIND = {
  editor: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 7-5 5 5 5M15 7l5 5-5 5"/></svg>',
  terminal: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="4" width="19" height="16" rx="3"/><path d="m7 10 2.5 2L7 14M12.5 14.5H17"/></svg>',
  multiplexer: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="3.5" width="19" height="17" rx="3"/><path d="M11 3.5v17M11 12h10.5"/></svg>',
  remote: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3.5 9h17M3.5 15h17M12 3a15 15 0 0 0 0 18M12 3a15 15 0 0 1 0 18"/></svg>',
  unknown: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M3 9h18"/></svg>',
};
const HOST_BY_PROCESS = {
  code: ["editor", "VS Code"], "code-insiders": ["editor", "VS Code Insiders"], codium: ["editor", "VSCodium"],
  "gnome-terminal-": ["terminal", "GNOME Terminal"], konsole: ["terminal", "Konsole"],
  "xfce4-terminal": ["terminal", "Xfce Terminal"], alacritty: ["terminal", "Alacritty"],
  kitty: ["terminal", "kitty"], wezterm: ["terminal", "WezTerm"], "wezterm-gui": ["terminal", "WezTerm"],
  ghostty: ["terminal", "Ghostty"], xterm: ["terminal", "xterm"],
  tmux: ["multiplexer", "tmux"], "tmux: server": ["multiplexer", "tmux"], screen: ["multiplexer", "screen"],
  sshd: ["remote", "SSH"],
};
function hostOf(session) {
  for (const name of session.host || []) {
    const hit = HOST_BY_PROCESS[name];
    if (hit) return { icon: HOST_KIND[hit[0]], label: hit[1] };
  }
  const fallback = (session.host || []).filter((n) => !["systemd", "init", "bash", "zsh", "fish", "sh", "login"].includes(n)).pop();
  return fallback ? { icon: HOST_KIND.terminal, label: fallback } : { icon: HOST_KIND.unknown, label: "unknown host" };
}

/* ---------------------------------------------------------------- element refs */
const panes = document.getElementById("panes");
const sessionList = document.getElementById("sessionList");
const listEmpty = document.getElementById("listEmpty");
const detailPane = document.getElementById("detailPane");
const chipSet = document.getElementById("chipSet");
const barSupporting = document.getElementById("barSupporting");
const themeToggle = document.getElementById("themeToggle");
const themeLabel = document.getElementById("themeLabel");
const snackbar = document.getElementById("snackbar");
const settingsScrim = document.getElementById("settingsScrim");
const endScrim = document.getElementById("endScrim");
const swatchRow = document.getElementById("swatches");
const seedReadout = document.getElementById("seedReadout");
const contrastGroup = document.getElementById("contrastGroup");
const backButton = document.getElementById("backButton");
const pickBar = document.getElementById("pickBar");
const pickCount = document.getElementById("pickCount");
const pickGroup = document.getElementById("pickGroup");
const pickClear = document.getElementById("pickClear");

/* --------------------------------------------------- coming and going ------ */
/* Everything that floats over the panel is written to fade in and out, and until
   now none of it faded out: the close paths set data-open="false" and `hidden`
   in the same breath, `hidden` is display:none, and a box that is display:none
   is not drawn — so the exit each of these was given a transition for never got
   a single frame. Things arrived gently and then blinked out of existence.

   These two keep the attribute and the transition in step. Opening flushes the
   closed style first, the way the menu already did by measuring itself between
   the two lines; closing holds the box on screen for exactly as long as the fade
   it is playing, and only then takes it out of the layout. */
const EXIT_MS = 200; /* --md-sys-motion-duration-short4 */
const exitTimers = new WeakMap();

function reveal(el) {
  if (!el) return;
  // Already open. Worth the check rather than setting it again: these are called
  // from a scroll handler, and the reflow below on every frame of a flick is a
  // measurable cost for no change at all.
  if (!el.hidden && el.dataset.open === "true") return;
  clearTimeout(exitTimers.get(el));
  exitTimers.delete(el);
  el.hidden = false;
  // A box arriving from display:none has no previous style to move from, and
  // would land open on the first frame. Reading a layout value forces the closed
  // state to be computed, which gives the transition its starting point.
  void el.offsetWidth;
  el.dataset.open = "true";
}

function conceal(el) {
  if (!el || el.hidden) return;
  // Already on its way out. Without this a scroll handler calling it every frame
  // would restart the timer every frame, and the box would sit there faded to
  // nothing but still holding its place for as long as you kept scrolling.
  if (el.dataset.open === "false") return;
  el.dataset.open = "false";
  clearTimeout(exitTimers.get(el));
  // Hidden on a timer rather than on transitionend: a box whose parent is torn
  // out mid-fade — the detail pane rebuilds under these on any poll — never
  // fires the event, and would be left behind holding its space forever.
  exitTimers.set(el, setTimeout(() => {
    el.hidden = true;
    exitTimers.delete(el);
  }, EXIT_MS));
}

let feed = { sessions: [], now: Date.now() / 1000, historySeconds: 1800, canFocus: true, canSend: false };
let skew = 0;
let lastGood = 0;
let filter = "all";
let selectedId = localStorage.getItem("cbu-selected") || null;
let tab = localStorage.getItem("cbu-tab") || "chat";
let transcript = null;
let transcriptFor = null;
let transcriptBusy = false;
/* How much conversation the chat tab asks for. A page at a time, because the
   whole point of reading backwards is that a long session costs no more than a
   short one — but a truncated transcript can be asked for more, up to what the
   server will read back. Reset per session: the depth you dug to in one
   conversation says nothing about the next. */
const CHAT_PAGE = 80;
const CHAT_LIMIT_MAX = 500;
let chatLimit = CHAT_PAGE;
// Set when "show more" is what caused the re-render, so the pane can hold the
// message you were reading in place instead of jumping.
let chatGrew = false;
let git = null;
let gitFor = null;
let gitBusy = false;
let usage = null;
let usageFor = null;
let usageBusy = false;
/* A half-typed message belongs to the session it was written for, not to the
   composer: switching away puts it aside and coming back brings it out again. */
const sayDrafts = new Map();
/* The writing side of the Git tab. The message being typed is kept per session
   rather than in the DOM, because the pane is rebuilt whenever the repository
   moves — which is exactly while a commit message is being written. */
const commitDrafts = new Map();
let diffOpen = null;      // { path, staged } — the one row showing its diff
let diffText = null;      // null while it is being read, "" when there is none
let diffNote = "";        // why there is none, when there is none
let gitActing = false;    // one git action at a time, whatever the pane offers
/* Held apart from gitActing because it outlives a repaint: writing a message
   takes long enough for a poll to rebuild the pane underneath it, and the new
   sparkle has to come back still spinning. */
let suggesting = false;
let lastStatuses = new Map();
let inFlight = null;
let mutedSessions = new Set(JSON.parse(localStorage.getItem("cbu-muted") || "[]"));
/* A pinned height for the message box in px, or null to size itself to the text.
   Dragging the rule above the box pins it; double-clicking hands it back. */
let composerHeight = Number(localStorage.getItem("cbu-composer-height")) || null;
let resizingComposer = false;
// The session whose name is being edited right now, if any. A poll must not
// rebuild the pane out from under the field.
let renamingId = null;

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
const loadKeySet = (key) => {
  try {
    const raw = JSON.parse(localStorage.getItem(key) || "[]");
    return new Set(Array.isArray(raw) ? raw.filter((k) => typeof k === "string") : []);
  } catch (error) {
    return new Set();
  }
};
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

function ungroup(block) {
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

/* -------------------------------------------------------------------- format */
function duration(seconds) {
  seconds = Math.max(0, Math.floor(seconds));
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  if (h) return `${h}h${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}
function shorten(path, keep = 2) {
  const home = path.replace(/^\/home\/[^/]+/, "~");
  const parts = home.split("/");
  return parts.length > keep + 2 ? `${parts[0]}/…/${parts.slice(-keep).join("/")}` : home;
}
/* A one-line hint has room for a phrase, not a paragraph. Cut on the character
   rather than the path segment — a commit subject has no segments. */
function clip(text, max) {
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}
function clockOf(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.valueOf()) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const selected = () => feed.sessions.find((s) => s.sessionId === selectedId) || null;

/* ------------------------------------------------------------------ markdown */
/* A transcript is Markdown, and reading it raw — literal ###, **, and fences —
   is hard work, so the bubbles render it. This is a small line-based renderer:
   headings, lists, fenced and inline code, quotes, tables, rules, emphasis and
   links, which is what a Claude answer actually uses.

   Safety: nothing from a message ever reaches the page as markup. Code is held
   aside before anything else runs, every other scrap is escaped before a tag is
   added, and a link is only made when its target is http, https or mailto. */
const MD_HOLD = "\u0000";

function safeUrl(url) {
  const value = String(url).trim();
  return /^(https?:\/\/|mailto:)[^\s"'<>]+$/i.test(value) ? value : null;
}

function renderMarkdown(text) {
  const held = [];
  const keep = (html) => `${MD_HOLD}${held.push(html) - 1}${MD_HOLD}`;
  const source = String(text ?? "").replace(/\r\n?/g, "\n");

  // Fenced code first: nothing inside a fence is Markdown. A fence inside a list
  // is indented, so the indent comes off the code and stays on the line — that is
  // what keeps the block inside its list item.
  // The info string is kept on the element rather than thrown away: quoting a
  // passage out of a code block puts the fence back, and it needs the language.
  const body = source.replace(/^([ \t]*)```([^\n]*)\n?([\s\S]*?)(?:^[ \t]*```[ \t]*$|$(?![\s\S]))/gm, (all, pad, info, code) => {
    const flat = pad ? code.replace(new RegExp(`^${pad}`, "gm"), "") : code;
    const lang = String(info || "").trim().split(/\s+/)[0].replace(/[^\w.+-]/g, "").slice(0, 24);
    return pad + keep(`<pre class="md-code"${lang ? ` data-lang="${escapeHtml(lang)}"` : ""}><code class="md-mono">${escapeHtml(flat.replace(/\n$/, ""))}</code></pre>`);
  });

  const inline = (raw) => {
    // Inline code goes the same way, so emphasis cannot reach inside it.
    let s = String(raw).replace(/`([^`\n]+)`/g, (all, code) => keep(`<code class="md-mono">${escapeHtml(code)}</code>`));
    s = escapeHtml(s);
    // An image becomes a link to it: the panel never loads a remote file.
    s = s.replace(/!\[([^\]\n]*)\]\(([^)\s]+)\)/g, (all, alt, url) => `[${alt || "image"}](${url})`);
    s = s.replace(/\[([^\]\n]*)\]\(([^)\s]+)\)/g, (all, label, url) => {
      const href = safeUrl(url.replace(/&amp;/g, "&"));
      return href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${label || escapeHtml(href)}</a>` : all;
    });
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, (all, before, url) => {
      // A sentence's full stop is not part of its link.
      const trail = url.match(/[.,;:!?]+$/)?.[0] || "";
      const bare = url.slice(0, url.length - trail.length);
      const href = safeUrl(bare.replace(/&amp;/g, "&"));
      return href ? `${before}<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${bare}</a>${trail}` : all;
    });
    s = s.replace(/\*\*\*(\S(?:[^*]*\S)?)\*\*\*/g, "<strong><em>$1</em></strong>");
    s = s.replace(/\*\*(\S(?:[^*]*\S)?)\*\*/g, "<strong>$1</strong>");
    // The marks have to hug the words, or `a * b * c` turns into italics.
    s = s.replace(/(^|[^*\w])\*(\S(?:[^*\n]*\S)?)\*/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^_\w])_(\S(?:[^_\n]*\S)?)_(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    return s.replace(/\n/g, "<br>");
  };

  const out = [];
  const stack = [];   // open lists, outermost first
  let para = [];      // lines of the paragraph being read
  let item = [];      // lines of the list item being read
  let quote = [];     // lines of the block quote being read
  let blank = false;  // was the line before this one empty?

  const flushItem = () => { if (item.length) { out.push(inline(item.join("\n"))); item = []; } };
  const flushPara = () => {
    if (stack.length) return flushItem();
    if (para.length) { out.push(`<p>${inline(para.join("\n"))}</p>`); para = []; }
  };
  const flushQuote = () => {
    if (!quote.length) return;
    out.push(`<blockquote>${inline(quote.join("\n"))}</blockquote>`);
    quote = [];
  };
  const closeLists = (indent) => {
    flushItem();
    while (stack.length && stack[stack.length - 1].indent >= indent) {
      const list = stack.pop();
      out.push(`</li></${list.tag}>`);
    }
  };
  const closeAll = () => { flushQuote(); flushPara(); closeLists(0); };
  const add = (line) => (stack.length ? item : para).push(line);

  const lines = body.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const bare = line.trim();

    if (!bare) { flushQuote(); flushItem(); if (!stack.length) flushPara(); blank = true; continue; }

    // A blank line then something flush with the margin is the end of the list;
    // indent the same thing instead and it stays inside the item. The next
    // bullet, of course, carries the list on.
    const startsItem = /^\s*([-*+]|\d+[.)])\s+/.test(line);
    if (blank && stack.length && !startsItem && !/^\s{2,}/.test(line)) closeLists(0);
    blank = false;

    if (bare.startsWith(">")) { flushPara(); flushItem(); quote.push(bare.replace(/^>\s?/, "")); continue; }
    flushQuote();

    // A held code block stands on its own line; inside a list it belongs to the item.
    const only = bare.match(new RegExp(`^${MD_HOLD}(\\d+)${MD_HOLD}$`));
    if (only) { flushItem(); if (!stack.length) flushPara(); out.push(held[Number(only[1])]); continue; }

    const heading = bare.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      closeAll();
      const level = Math.min(heading[1].length + 2, 6);
      const size = heading[1].length <= 2 ? "md-title-medium" : "md-title-small";
      out.push(`<h${level} class="md-head ${size}">${inline(heading[2])}</h${level}>`);
      continue;
    }

    if (/^([-*_])\1{2,}$/.test(bare)) { closeAll(); out.push("<hr>"); continue; }

    // A table needs its header underline on the next line to count as one.
    if (bare.startsWith("|") && /^\|?[\s:|-]+\|[\s:|-]*$/.test((lines[i + 1] || "").trim()) && (lines[i + 1] || "").includes("-")) {
      closeAll();
      const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = cells(bare);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(cells(lines[i++]));
      i--;
      out.push(`<table class="md-table"><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead>`
        + `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }

    const bullet = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
    if (bullet) {
      const indent = bullet[1].length;
      const tag = /\d/.test(bullet[2]) ? "ol" : "ul";
      flushPara();
      flushItem();
      while (stack.length && stack[stack.length - 1].indent > indent) {
        const list = stack.pop();
        out.push(`</li></${list.tag}>`);
      }
      const top = stack[stack.length - 1];
      if (top && top.indent === indent && top.tag === tag) {
        out.push("</li><li>");
      } else {
        if (top && top.indent === indent) { stack.pop(); out.push(`</li></${top.tag}>`); }
        stack.push({ tag, indent });
        out.push(`<${tag}><li>`);
      }
      // A task list reads better as a box than as literal brackets.
      item = [bullet[3].replace(/^\[([ xX])\]\s+/, (all, mark) => (mark === " " ? "☐ " : "☑ "))];
      continue;
    }

    // Not a list line at all: an unindented one ends the list.
    if (stack.length && !/^\s{2,}/.test(line)) { closeLists(0); flushItem(); }
    add(bare);
  }
  closeAll();

  return out.join("").replace(new RegExp(`${MD_HOLD}(\\d+)${MD_HOLD}`, "g"), (all, n) => held[Number(n)]);
}

/* ------------------------------------------------------------------ transport */
async function poll() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    skew = data.now - Date.now() / 1000;
    feed = data;
    lastGood = Date.now();
    notifyWaiting(data.sessions);
    paintOpenButton();
    render();
    if (selectedId && (transcriptFor !== selectedId || tab === "chat")) fetchTranscript();
    if (selectedId && isGitTab(tab)) fetchGit();
    if (selectedId && tab === "usage") fetchUsage();
  } catch (error) {
    barSupporting.textContent = "lost the server — retrying";
  }
}

async function fetchTranscript() {
  const id = selectedId;
  if (!id || transcriptBusy) return;
  if (tab !== "chat" && transcriptFor === id) return;
  transcriptBusy = true;
  try {
    const asked = chatLimit;
    const response = await fetch(`/api/transcript?sessionId=${encodeURIComponent(id)}&limit=${asked}`, { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    if (selectedId !== id) return;             // selection moved on while fetching
    // Enough of the transcript to notice a new message, or the last one growing.
    // Rebuilding on every poll instead — which "always redraw the chat tab" came
    // down to — throws the pane away a second at a time, under the pointer.
    const stamp = (t) => {
      const last = t?.messages?.[t.messages.length - 1];
      return [t?.title ?? "", t?.messages?.length ?? -1, last?.at ?? "",
              last?.text?.length ?? 0, last?.tools?.length ?? 0].join("|");
    };
    const changed = stamp(data) !== stamp(transcript) || transcriptFor !== id;
    transcript = data;
    transcriptFor = id;
    if (changed) renderDetail(true);
  } catch (error) {
    /* leave the previous transcript on screen */
  } finally {
    transcriptBusy = false;
  }
}

/* Another page of the conversation. The reading itself is the server's — this
   only raises what is asked for, and marks the re-render as a growth so the pane
   holds its place. A fetch already in flight is not waited on: the chat tab
   re-fetches on the next poll a moment later, now at the larger limit. */
function showMoreChat(event) {
  const button = event.currentTarget;
  if (chatLimit >= CHAT_LIMIT_MAX) return;
  chatLimit = Math.min(CHAT_LIMIT_MAX, chatLimit + CHAT_PAGE);
  chatGrew = true;
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
let gitPolledAt = 0;
const GIT_POLL_MS = 20000;

/* Everything the Git tab draws, in one string. Same reasoning as the
   transcript's: re-rendering on every poll throws the pane away under the
   pointer, so the panel only redraws when the repository actually moved. */
/* Both git tabs are drawn from the same reading, so they fetch and fall back
   together. */
const isGitTab = (name) => name === "git" || name === "history";

function gitStamp(g) {
  // upstream belongs here: publishing a branch changes nothing else about the
  // repository, and without it the header would go on saying "no upstream".
  return [g?.ok, g?.isRepo, g?.canWrite, g?.head ?? "", g?.branch ?? "", g?.upstream ?? "",
          g?.detached, g?.ahead ?? 0, g?.behind ?? 0,
          g?.stashes ?? 0, g?.commits?.[0]?.sha ?? "", g?.commits?.length ?? -1,
          // The branch menu is built when it opens, from whatever this holds, so a
          // branch appearing or going has to reach the pane.
          (g?.branches?.local ?? []).map((b) => `${b.name}${b.current ? "*" : ""}`).join(","),
          (g?.branches?.remote ?? []).map((b) => b.name).join(","),
          // Each side keeps its own column, filled with a dot when it is empty.
          // Run them together and staging a modified file reads the same either
          // way — "M" and "M" — so the pane would never notice it had moved.
          (g?.files ?? []).map((f) =>
            `${f.path}:${f.staged ?? "."}${f.unstaged ?? "."}${f.untracked ? "?" : ""}${f.conflicted ? "!" : ""}`
          ).join(",")].join("|");
}

async function fetchGit(force) {
  const id = selectedId;
  if (!id || gitBusy) return;
  // Nobody is reading a hidden tab. A Git tab left open behind another window
  // would otherwise go on running git for the rest of the day; coming back to it
  // reads once, immediately.
  if (!force && document.hidden) return;
  if (!force && gitFor === id && Date.now() - gitPolledAt < GIT_POLL_MS) return;
  gitBusy = true;
  gitPolledAt = Date.now();
  try {
    const response = await fetch(`/api/git?sessionId=${encodeURIComponent(id)}`, { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    if (selectedId !== id) return;             // selection moved on while fetching
    const changed = gitStamp(data) !== gitStamp(git) || gitFor !== id;
    git = data;
    gitFor = id;
    if (changed) {
      renderDetail(true);
      // An open diff describes a file that has just moved, so it is read again
      // rather than left showing what the file used to say.
      if (diffOpen) fetchDiff();
    }
  } catch (error) {
    /* leave the previous reading on screen */
  } finally {
    gitBusy = false;
  }
}

/* Tokens and cost. Cheaper to read than the repository — the server scans the
   transcript once and picks up where it stopped — but there is no point reading
   it while another tab is showing, so it runs on the poll only when its own tab
   is up, and once immediately when you open it. */
function usageStamp(u) {
  return [u?.cost ?? -1, u?.totals?.requests ?? -1, u?.context ?? -1,
          (u?.models ?? []).length, (u?.agentModels ?? []).length].join("|");
}

async function fetchUsage(force) {
  const id = selectedId;
  if (!id || usageBusy) return;
  if (!force && document.hidden) return;
  usageBusy = true;
  try {
    const response = await fetch(`/api/usage?sessionId=${encodeURIComponent(id)}`, { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    if (selectedId !== id) return;             // selection moved on while fetching
    const changed = usageStamp(data) !== usageStamp(usage) || usageFor !== id;
    usage = data;
    usageFor = id;
    if (changed) renderDetail(true);
  } catch (error) {
    /* leave the previous reading on screen */
  } finally {
    usageBusy = false;
  }
}

/* ------------------------------------------------- git: opening and acting */

function closeDiff() {
  diffOpen = null;
  diffText = null;
  diffNote = "";
}

/* Clicking a row shows its diff under it, and clicking the same row again puts
   it away — one open at a time, because the pane is one column wide. */
function toggleDiff(path, staged) {
  if (diffOpen && diffOpen.path === path && diffOpen.staged === staged) {
    closeDiff();
    renderDetail(true);
    return;
  }
  diffOpen = { path, staged };
  diffText = null;
  diffNote = "";
  renderDetail(true);
  fetchDiff();
}

async function fetchDiff() {
  const id = selectedId;
  const want = diffOpen;
  if (!id || !want) return;
  const query = new URLSearchParams({ sessionId: id, path: want.path, staged: want.staged ? "1" : "0" });
  try {
    const response = await fetch(`/api/git/diff?${query}`, { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    // The row may have been closed, or another one opened, while this was in the air.
    if (selectedId !== id || diffOpen !== want) return;
    diffText = data.text || "";
    diffNote = data.message || (data.ok ? "No line changes to show" : "Could not read that diff");
  } catch (error) {
    if (diffOpen !== want) return;
    diffText = "";
    diffNote = "Could not reach the server";
  }
  renderDetail(true);
}

/* Every writing git action goes through here: one at a time, the answer in a
   snackbar, and a fresh reading afterwards so the list matches the repository
   again without waiting for the next poll. */
async function gitDo(action, extra, button) {
  const id = selectedId;
  if (!id || gitActing) return false;
  gitActing = true;
  if (button) button.disabled = true;
  let ok = false;
  try {
    const response = await fetch("/api/git", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: id, action, ...extra }),
    });
    const data = await response.json().catch(() => ({}));
    ok = Boolean(data.ok);
    showSnackbar(data.message || (ok ? "Done" : "That did not work"), ok ? 4000 : 8000);
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    gitActing = false;
    if (button) button.disabled = false;
    // The reading is stale the moment anything above succeeded — and after a
    // failure it is worth confirming that nothing moved.
    fetchGit(true);
  }
  return ok;
}

async function run(url, session, button, waitingMessage, extra) {
  if (inFlight) return;
  inFlight = url;
  button.disabled = true;
  if (waitingMessage) showSnackbar(waitingMessage, 44000);
  try {
    const response = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: session.sessionId, ...extra }),
    });
    const data = await response.json().catch(() => ({}));
    showSnackbar(data.message || (response.ok ? "Done" : "That did not work"));
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    inFlight = null;
    button.disabled = false;
    poll();
  }
}

/* --------------------------------------------------------------- rendering */
function render() {
  const counts = {};
  for (const session of feed.sessions) {
    const key = stateKeyOf(session.status);
    counts[key] = (counts[key] || 0) + 1;
  }
  if (filter !== "all" && !counts[filter]) filter = "all";

  // A kept session with no process is on the list but is not running, so it is
  // counted apart from the live ones rather than inflating them.
  const stopped = counts.stopped || 0;
  const total = feed.sessions.length - stopped;
  const waiting = counts.waiting || 0;
  const parts = [];
  if (total) parts.push(`${total} live`);
  if (waiting) parts.push(`${waiting} needs an answer`);
  if (stopped) parts.push(`${stopped} kept`);
  // Counts the blocked sessions only — the ones that cannot go on without you.
  barSupporting.textContent = parts.length ? parts.join(" · ") : "nothing running";
  document.title = waiting ? `(${waiting}) Claude sessions` : "Claude sessions";

  // Keep a valid selection: fall back to the top of the list.
  const visible = filter === "all" ? feed.sessions : feed.sessions.filter((s) => stateKeyOf(s.status) === filter);
  if (!visible.some((s) => s.sessionId === selectedId)) {
    selectedId = visible.length ? visible[0].sessionId : null;
    if (selectedId) localStorage.setItem("cbu-selected", selectedId);
  }

  renderChips(counts);
  renderList(visible);
  renderDetail(false);
  paintFavicon(counts);
}

function renderChips(counts) {
  const entries = [{ key: "all", label: `all ${feed.sessions.length}` }];
  for (const key of STATE_ORDER) if (counts[key]) entries.push({ key, label: `${counts[key]} ${STATE[key].short}` });
  const signature = entries.map((e) => e.key + e.label).join("|") + filter;
  if (chipSet.dataset.signature === signature) return;
  chipSet.dataset.signature = signature;
  chipSet.innerHTML = "";
  for (const entry of entries) {
    const item = document.createElement("li");
    const chip = document.createElement("button");
    chip.className = "chip md-state md-label-large";
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(filter === entry.key));
    if (entry.key !== "all") chip.style.setProperty("--chip-dot", STATE[entry.key].colour);
    chip.innerHTML = `<span class="chip__check">${ICON.check}</span>${entry.key === "all" ? "" : '<span class="chip__dot"></span>'}<span>${escapeHtml(entry.label)}</span>`;
    chip.addEventListener("click", () => { filter = filter === entry.key ? "all" : entry.key; render(); });
    item.appendChild(chip);
    chipSet.appendChild(item);
  }
}

function renderList(visible) {
  listEmpty.hidden = visible.length > 0;
  // A menu whose row is leaving the list — ended, or filtered out — has nothing
  // left to act on.
  if (menuFor && !visible.some((s) => s.sessionId === menuFor)) closeSessionMenu({ restoreFocus: false });
  // Rows that have left the list cannot be part of a pick any more.
  const here = new Set(visible.map((s) => s.sessionId));
  for (const id of [...picked]) if (!here.has(id)) picked.delete(id);
  if (!visible.length) {
    listEmpty.textContent = feed.sessions.length
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
  if (menuGroup && !blocks.some((block) => block.key === menuGroup)) {
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
    const marked = menuFor
      ? sessionList.querySelector(`li[data-id="${CSS.escape(menuFor)}"]`)
      : menuGroup ? sessionList.querySelector(`li[data-group="${CSS.escape(menuGroup)}"]`) : null;
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
  const isSelected = session.sessionId === selectedId;
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
  selectedId = id;
  localStorage.setItem("cbu-selected", id);
  transcript = null;
  transcriptFor = null;
  chatLimit = CHAT_PAGE;
  chatGrew = false;
  git = null;
  gitFor = null;
  closeDiff();
  panes.dataset.view = "detail";
  render();
  fetchTranscript();
  if (isGitTab(tab)) fetchGit(true);
}

function setTab(next) {
  tab = next;
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
const sessionMenu = document.getElementById("sessionMenu");
let menuFor = null;     // sessionId the open menu belongs to
let menuGroup = null;   // or the key of the group it belongs to, for a header menu
/* Whether a menu is standing at all, which is not the same question as what it
   points at: a menu opened from a toolbar button — the Git tab's overflow, the
   commit split button — belongs to neither a row nor a group, and asking after
   those two left it with nothing to dismiss it. The DOM is the honest answer. */
const menuIsOpen = () => sessionMenu.dataset.open === "true";
/* The row to hand focus back to, held as an id rather than the element: the list
   repaints on every status change, which detaches the button we were given. */
let menuReturn = null;

const sessionById = (id) => feed.sessions.find((s) => s.sessionId === id) || null;

function setMuted(id, muted) {
  if (muted) mutedSessions.add(id);
  else mutedSessions.delete(id);
  localStorage.setItem("cbu-muted", JSON.stringify([...mutedSessions]));
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
  const muted = mutedSessions.has(session.sessionId);
  const items = [];
  // Window actions mean nothing for a session with no process behind it.
  if (session.status === "stopped") {
    // nothing to focus
  } else if (!feed.canFocus) {
    items.push({ key: "focus", icon: ICON.focus, label: "Focus window", hint: "needs xdotool", disabled: true });
  } else if (win) {
    items.push({ key: "focus", icon: ICON.focus, label: "Focus window",
      hint: windowSays(win, "short"),
      run: (el) => run("/api/focus", session, el, isAmbiguous(win) ? IDENTIFY_NOTE : undefined) });
  }
  if (session.status === "stopped") {
    items.push({ key: "start", icon: ICON.play, label: "Start it up",
      hint: feed.canSend ? "claude --resume" : "needs loopback",
      disabled: !feed.canSend,
      run: (el) => run("/api/start", session, el) });
  }
  // A second session on the same work, started where this one is — a fresh
  // conversation rather than a resume, so it never touches this one's transcript.
  if (session.cwd) {
    items.push({ key: "new", icon: ICON.plus, label: "New session here",
      hint: feed.canSend ? (session.folder || shorten(session.cwd, 1)) : "needs loopback",
      disabled: !feed.canSend,
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
  if (session.alive !== false) {
    items.push({ divider: true });
    items.push({ key: "end", icon: ICON.power, label: "End session…", danger: true,
      run: () => openEndDialog(session) });
  }
  return items;
}

/* One menu, opened over a row or over a group header. `forId` / `forGroup` say
   which, so the thing it acts on is marked while it stands open. */
function openMenu({ title, label, items, forId = null, forGroup = null }, x, y) {
  closeSessionMenu();
  sessionMenu.setAttribute("aria-label", label);
  sessionMenu.innerHTML =
    `<p class="menu__title md-label-small" role="none">${escapeHtml(title)}</p>` +
    items.map((item) => item.divider
      ? `<hr class="menu__divider" role="separator">`
      : `<button class="menu__item md-state${item.danger ? " menu__item--danger" : ""}" type="button"
           role="menuitem" data-key="${item.key}"${item.disabled ? " disabled" : ""}>
          <span class="menu__icon">${item.icon}</span>
          <span class="menu__label md-label-large">${escapeHtml(item.label)}</span>
          ${item.hint ? `<span class="menu__hint md-label-small">${escapeHtml(item.hint)}</span>` : ""}
        </button>`).join("");

  for (const button of sessionMenu.querySelectorAll(".menu__item")) {
    const item = items.find((i) => i.key === button.dataset.key);
    if (!item?.run) continue;
    button.addEventListener("click", () => {
      closeSessionMenu({ restoreFocus: false });
      item.run(button);
    });
  }

  menuFor = forId;
  menuGroup = forGroup;
  sessionMenu.hidden = false;
  // offsetWidth, not the bounding rect: the open transition scales the box and
  // would give a measurement smaller than the space it is about to need.
  const pad = 8;
  // Cap before measuring: a menu taller than the window has to scroll, or the
  // clamp below would push its last items off the bottom edge.
  sessionMenu.style.maxHeight = `${window.innerHeight - pad * 2}px`;
  const width = sessionMenu.offsetWidth;
  const height = sessionMenu.offsetHeight;
  sessionMenu.style.left = `${Math.max(pad, Math.min(x, window.innerWidth - width - pad))}px`;
  sessionMenu.style.top = `${Math.max(pad, Math.min(y, window.innerHeight - height - pad))}px`;
  // Opened only now it is in the right place, so a menu reopened before the last
  // one finished leaving does not slide across from where that one stood.
  reveal(sessionMenu);

  const mark = forId
    ? sessionList.querySelector(`li[data-id="${CSS.escape(forId)}"]`)
    : forGroup ? sessionList.querySelector(`li[data-group="${CSS.escape(forGroup)}"]`) : null;
  if (mark) mark.dataset.menu = "open";
  sessionMenu.querySelector(".menu__item:not([disabled])")?.focus();
}

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
        hint: feed.canSend ? shorten(block.folder, 1) : "needs loopback",
        disabled: !feed.canSend,
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

function closeSessionMenu({ restoreFocus = true } = {}) {
  if (!menuIsOpen()) return;
  for (const row of sessionList.querySelectorAll('[data-menu="open"]')) delete row.dataset.menu;
  conceal(sessionMenu);
  menuFor = null;
  menuGroup = null;
  const back = menuReturn;
  menuReturn = null;
  if (restoreFocus && back) {
    sessionList.querySelector(`[data-id="${CSS.escape(back)}"] .session-item`)?.focus();
  }
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
  menuReturn = session.sessionId;
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
  pickAnchor = row.dataset.id;
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
    menuReturn = session.sessionId;
    const rect = row.querySelector(".session-item").getBoundingClientRect();
    openSessionMenu(session, rect.left + 16, rect.bottom - 8);
    return;
  }
  // The same key on a group header opens the group's menu.
  const header = event.target.closest("li.group")?.querySelector(":scope > .group__header");
  const block = header && lastBlocks.find((b) => b.kind === "group" && b.key === header.parentElement.dataset.group);
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
  if (isGitTab(tab) && !session.repoRoot) tab = "chat";
  const signature = [
    session.sessionId, session.name, stateKeyOf(session.status), session.branch, session.window?.confidence,
    host.label, tab, transcript?.messages?.length ?? -1, transcript?.title ?? "",
    session.repoRoot ?? "", gitFor === session.sessionId ? gitStamp(git) : "",
    usageFor === session.sessionId ? usageStamp(usage) : "",
    mutedSessions.has(session.sessionId), feed.canFocus, feed.canSend, session.canSay, session.sticky,
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
  if ((resizingComposer || renamingId === session.sessionId || commentIsOpen()) && !force) {
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
  const chatFromBottom = chatGrew && chatBefore
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
  commitCaret = commitBefore ? {
    start: commitBefore.selectionStart,
    end: commitBefore.selectionEnd,
    focused: document.activeElement === commitBefore,
  } : null;

  // Whatever was being typed is gone with the rebuild below.
  renamingId = null;
  // A menu opened from a button in this pane — the Git tab's overflow, the
  // commit split button — is about to be pointing at an element that no longer
  // exists, so it goes with the rebuild rather than floating over the new one.
  if (menuIsOpen() && !menuFor && !menuGroup) closeSessionMenu({ restoreFocus: false });
  detailPane.dataset.signature = signature;
  // What the pane was showing a moment ago, for the fade below. Read before the
  // rebuild overwrites it.
  // Not data-tab: the pane wraps the tab strip and comes first in document
  // order, so a data-tab here answers every [data-tab="…"] lookup meant for the
  // buttons inside it — including the ones the tests click.
  const cameFrom = `${detailPane.dataset.sessionId || ""}/${detailPane.dataset.showing || ""}`;
  detailPane.dataset.sessionId = session.sessionId;
  detailPane.dataset.showing = tab;
  detailPane.innerHTML = `
    ${detailHeader(session, state, host)}
    <div class="tabs" role="tablist">
      <button class="tab md-state md-label-large" role="tab" data-tab="chat" aria-selected="${tab === "chat"}">${ICON.chat}Conversation</button>
      ${session.repoRoot ? `
        <button class="tab md-state md-label-large" role="tab" data-tab="git" aria-selected="${tab === "git"}">${ICON.branch}Git</button>
        <button class="tab md-state md-label-large" role="tab" data-tab="history" aria-selected="${tab === "history"}">${ICON.history}History</button>` : ""}
      <button class="tab md-state md-label-large" role="tab" data-tab="usage" aria-selected="${tab === "usage"}">${ICON.coin}Usage</button>
      <button class="tab md-state md-label-large" role="tab" data-tab="about" aria-selected="${tab === "about"}">${ICON.info}Details</button>
    </div>
    <div class="panel-wrap">
      <div class="tab-panel" id="chatScroll" role="tabpanel">
        ${tab === "chat" ? chatPanel(session)
          : tab === "git" ? gitPanel(session)
          : tab === "history" ? historyPanel(session)
          : tab === "usage" ? usagePanel(session)
          : aboutPanel(session, host)}
      </div>
      ${tab === "chat" ? `<div class="jump-dock">
          <button class="jump-bottom md-state" id="jumpBottom" title="Jump to latest" aria-label="Jump to latest" data-open="false" tabindex="-1" hidden>${ICON.toBottom}</button>
          <button class="jump-last md-state md-label-large" id="jumpLast" data-open="false" tabindex="-1" hidden>${ICON.up}Last request</button>
        </div>
        <div class="rail" id="commentRail" hidden><div class="rail__inner" id="commentRailInner"></div></div>` : ""}
    </div>
    ${tab === "chat" ? questionCard(session) : ""}
    ${tab === "chat" ? composer(session) : ""}`;

  const header = detailPane.querySelector(".detail-header");
  header.style.setProperty("--state-colour", state.colour);
  header.style.setProperty("--state-container", state.container);
  header.style.setProperty("--state-on-container", state.onContainer);

  paintTrace(header, session);
  wireTrace(header);
  // The transcript is rebuilt from the transcript data, so anything drawn over
  // it has to be drawn again — the marks over passages you have commented on
  // included.
  if (tab === "chat") { markCommented(); renderRail(); }

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
    if (event.target.checked) mutedSessions.delete(session.sessionId);
    else mutedSessions.add(session.sessionId);
    localStorage.setItem("cbu-muted", JSON.stringify([...mutedSessions]));
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
      cmdOff = false;
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
  if (isGitTab(tab)) wireGit(session);

  detailPane.querySelector("[data-act='more']")?.addEventListener("click", showMoreChat);

  const chatAfter = detailPane.querySelector("#chatScroll");
  if (chatAfter && tab === "chat" && chatFromBottom !== null) {
    chatAfter.scrollTop = chatAfter.scrollHeight - chatFromBottom;
    chatGrew = false;
  } else if (chatAfter && tab === "chat" && wasAtBottom) {
    chatAfter.scrollTop = chatAfter.scrollHeight;
  }
  if (chatAfter && tab === "chat") wireJumpDock(chatAfter);

  // Fade the panel in when it is showing something genuinely different — another
  // tab, another session. Not on every rebuild: the pane is rebuilt whenever a
  // message lands or a state changes, and a working session would have the thing
  // you are reading pulsing at you every few seconds.
  if (cameFrom !== `${session.sessionId}/${tab}`) panelChangedAt = Date.now();
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
  if (composerHeight) {
    // A pinned box keeps its height and scrolls inside, so the 40vh ceiling
    // that bounds the auto-grown one has to come off.
    field.style.maxHeight = "none";
    field.style.height = `${Math.min(composerHeight, composerMax())}px`;
    return;
  }
  field.style.maxHeight = "";
  field.style.height = "auto";
  field.style.height = `${field.scrollHeight}px`;
}

function setComposerHeight(px, field) {
  composerHeight = Math.round(Math.min(Math.max(px, COMPOSER_MIN), composerMax()));
  localStorage.setItem("cbu-composer-height", composerHeight);
  growField(field);
  syncGrip(field);
}

function resetComposerHeight(field) {
  composerHeight = null;
  localStorage.removeItem("cbu-composer-height");
  growField(field);
  syncGrip(field);
}

function syncGrip(field) {
  const grip = detailPane.querySelector("#composerGrip");
  if (!grip) return;
  grip.setAttribute("aria-valuenow", Math.round(composerHeight || field.clientHeight));
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
      resizingComposer = false;
      renderDetail(); // let through whatever the drag held off
    };
    grip.setPointerCapture(event.pointerId);
    grip.classList.add("is-dragging");
    resizingComposer = true;
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", done, { once: true });
    grip.addEventListener("pointercancel", done, { once: true });
  });
  grip.addEventListener("dblclick", () => resetComposerHeight(field));
  grip.addEventListener("keydown", (event) => {
    const from = composerHeight || field.clientHeight;
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
  if (inFlight) { restore(); return; }
  // A stopped session cannot hear anything yet, so the message rides along with
  // the start: the server delivers it once the session is listening again.
  const url = session.status === "stopped" ? "/api/start" : "/api/say";
  inFlight = url;
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
    inFlight = null;
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
    cmdOff = false;
    return;
  }
  loadCatalog(session);

  // Once a space has been typed the name is settled, so the list stands down and
  // leaves the line saying what will be sent.
  cmdRows = (asked.chosen || cmdOff) ? [] : cmdMatches(asked.name);
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
  const id = selectedId;
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
const commentIsOpen = () => commentsFor(selectedId).some((c) => c.editing);

function rememberCommented(entries) {
  const id = selectedId;
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
  const set = commented.get(selectedId);
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
  const set = commented.get(selectedId);
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
  const list = commentsFor(selectedId);
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
  const n = commentsFor(selectedId).filter((c) => c.remark.trim()).length;
  send.textContent = n ? `Send ${n} comment${n === 1 ? "" : "s"}` : "Write a comment to send";
  send.disabled = !n;
}

function closeComment(id, keep) {
  const list = commentsFor(selectedId);
  const entry = list.find((c) => c.id === id);
  if (!entry) return;
  if (!keep && !entry.remark.trim()) {
    comments.set(selectedId, list.filter((c) => c.id !== id));
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
  const entry = commentsFor(selectedId).find((c) => c.id === card.dataset.id);
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
  const list = commentsFor(selectedId).filter((c) => c.remark.trim());
  if (!list.length || inFlight) return;
  const text = commentsAsMessage(commentsFor(selectedId));
  const url = session.status === "stopped" ? "/api/start" : "/api/say";
  inFlight = url;
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
      comments.set(selectedId, commentsFor(selectedId).filter((c) => !c.remark.trim()));
      activeComment = null;
      renderRail();
    }
    showSnackbar(data.message || (response.ok ? "Sent" : "That did not send"));
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    inFlight = null;
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
      comments.set(selectedId, commentsFor(selectedId).filter((c) => c.id !== id));
      if (activeComment === id) activeComment = null;
      forgetCommented(id);
      renderRail();
    } else if (act.dataset.cc === "edit") {
      const entry = commentsFor(selectedId).find((c) => c.id === id);
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
  if (renamingId) return;
  renamingId = session.sessionId;
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
    renamingId = null;
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
  const local = feed.sessions.find((s) => s.sessionId === session.sessionId);
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
    : `<span class="meta-sep">·</span><span class="md-body-small">up ${duration(Date.now() / 1000 + skew - session.startedAt)}</span>`);
  // The transcript's own reading first — it is the fresher of the two — and the
  // one that came with the session behind it, so the subject is there on the
  // first paint rather than a moment after it.
  const title = (transcriptFor === session.sessionId && transcript?.title) || session.title || null;
  return `<header class="detail-header">
      <div class="detail-header__top">
        <div class="detail-header__text">
          <div class="detail-header__title">
            <h2 class="md-headline-small">
              <button class="name-button md-state" data-act="rename" title="Click to rename this session"
                      aria-label="Rename ${escapeHtml(session.name)}"><span>${escapeHtml(session.name)}</span>${ICON.pencil}</button>
            </h2>
            <span class="md-label-large">${escapeHtml(state.label)}
              <span class="md-mono md-body-small" data-since="${displaySince(session)}">${duration(Date.now() / 1000 + skew - displaySince(session))}</span></span>
          </div>
          ${title ? `<p class="detail-header__subtitle md-body-medium">${escapeHtml(title)}</p>` : ""}
          <div class="detail-header__meta">${meta.join(" ")}</div>
        </div>
        <div class="detail-header__actions">${headerActions(session)}</div>
      </div>
      ${traceFor(session)}
    </header>`;
}

/* How much rope a session has: the permission mode, read and not set.

   Read, because a session writes the mode into its transcript only when the
   metadata block is re-appended — never when the mode changes. One sitting at
   its prompt keeps saying whatever it last said, which is stale often enough
   that the mode is no longer a pill among the header facts, where it read as
   current. It survives only as a fact in About, next to the caveat.

   Not set, because the panel cannot aim a keypress at a session. There is no
   external setter — Shift+Tab is the only way in — and X11 pairs windows, while
   a terminal window holds tabs and a VS Code window holds terminals. A press
   sent at a window lands in whichever tab has focus, which may be a different
   Claude. Loosening the wrong session's permissions is not a thing to get wrong
   occasionally, so this stays a readout. */
const MODE_LABELS = {
  default: "Manual",
  acceptEdits: "Accept edits",
  plan: "Plan",
  bypassPermissions: "Bypass",
  auto: "Auto",
  dontAsk: "Don't ask",
};
/* How sure the panel is about a window, said the same way everywhere. An
   ambiguous match is not a match: several windows scored alike — the usual
   cause being one terminal process behind all of them — and the honest move is
   to say so rather than raise one of them and hope. */
const WINDOW_CONFIDENCE = {
  paired: { short: "paired by you", long: "you chose this one" },
  identified: { short: "confirmed", long: "its terminal pointed to this one" },
  high: { short: "matched", long: "matched automatically" },
  likely: { short: "best guess", long: "a best guess from the process tree" },
  ambiguous: { short: "can't tell yet", long: "several windows look alike from the outside" },
};

function windowSays(win, key) {
  return (WINDOW_CONFIDENCE[win?.confidence] || WINDOW_CONFIDENCE.likely)[key];
}

function isAmbiguous(win) {
  return win?.confidence === "ambiguous";
}

/* Both a click and a probe write a pairing, and either can be cleared. */
function isRemembered(win) {
  return win?.confidence === "paired" || win?.confidence === "identified";
}

/* Identifying asks the session's own terminal which window it is showing, by
   retitling it for a moment. The title comes straight back. */
const IDENTIFY_NOTE = "Asking the terminal — its title will flicker";

function headerActions(session) {
  // A stopped session has no window to raise; what it has is a way back.
  if (session.status === "stopped") {
    if (!feed.canSend) return `<p class="hint md-label-small">starting needs the panel on loopback</p>`;
    return `<button class="button button--filled md-state" data-act="start">${ICON.play} Start it up</button>
            <p class="hint md-label-small">resumes this conversation</p>`;
  }
  if (!feed.canFocus) return `<p class="hint md-label-small">focusing needs xdotool</p>`;
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
  const end = feed.now;
  const observed = end - spans[0].from;
  const window = Math.min(feed.historySeconds, Math.max(observed, 15));
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
  return new Date((epoch - skew) * 1000).toLocaleTimeString([], {
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
        ${escapeHtml(seg.dataset.state)} <span class="md-mono">${duration((to ?? feed.now) - from)}</span>
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
  if (!feed.canSend) return "sending is off — the panel is not on loopback";
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
  const go = session.window && feed.canFocus
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
  if (transcriptFor !== session.sessionId || !transcript) {
    return `<p class="chat__note md-body-medium">Reading the conversation…</p>`;
  }
  if (!transcript.messages.length) {
    // A nested session has no transcript at all — Claude Code writes none for
    // one — so "yet" would be a promise the panel cannot keep.
    return `<p class="chat__note md-body-medium">${session.kind === "child"
      ? "A session started from inside another one keeps no transcript on disk, "
        + "so there is no conversation to read here. Its state, folder and branch "
        + "are still live, and you can still send it a message."
      : "Nothing in this transcript yet."}</p>`;
  }
  const rows = transcript.truncated
    ? [`<p class="chat__note md-label-small">showing the last ${transcript.messages.length} messages${
        chatLimit < CHAT_LIMIT_MAX
          ? ` <button class="button button--text md-state chat__more" data-act="more">show ${CHAT_PAGE} more</button>`
          : " — as far back as this panel reads"}</p>`]
    : [];
  const toolLines = (tools) => (tools || []).map((tool) => `<span class="tool-line md-mono md-body-small">
      <span class="tool-line__name">${escapeHtml(tool.name)}</span>
      <span class="tool-line__detail">${escapeHtml(tool.detail || "")}</span></span>`).join("");

  for (const message of transcript.messages) {
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

/* ==========================================================================
   Git — what the repository this session works in looks like right now.

   The same shape as the editor's Source Control view, because that is the one
   every reader already knows: the message box and its split button, then the
   groups — merge, staged, changes — each row opening its own diff and carrying
   the actions that apply to it.

   It writes as well as reads. A session may be editing the same worktree while
   you stage or commit here, and git's own index lock is what settles that: a
   write that loses the race says so, and the list is read again after every
   action either way. What the panel will not do is choose for you — a pull that
   cannot fast-forward, a rebase, a merge — because those belong to whoever can
   see the conflict.
   ========================================================================== */

/* Commit ages run to days and weeks, which duration() — built for how long a
   session has been in a state — would render as three-figure hours. */
function ago(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  const w = Math.floor(d / 7);
  return w < 6 ? `${w}w ago` : `${Math.floor(d / 30)}mo ago`;
}

/* Lane assignment: the same walk a commit graph is always drawn from.

   `lanes` holds, per column, the sha that column is currently waiting to reach.
   A commit takes the lane that was waiting for it — or a free one if nothing
   was — and hands that lane to its first parent; a merge's remaining parents
   open lanes of their own. Anything else still waiting for this commit was a
   second child, and its lane closes here.

   Each row records the lane state either side of it, which is what lets the
   rail below draw a line that actually joins up with its neighbours. */
function layoutGraph(commits) {
  const lanes = [];
  const rows = [];
  const take = (sha) => {
    const free = lanes.indexOf(null);
    if (free !== -1) { lanes[free] = sha; return free; }
    lanes.push(sha);
    return lanes.length - 1;
  };
  for (const commit of commits) {
    const before = [...lanes];
    let lane = lanes.indexOf(commit.sha);
    if (lane === -1) lane = take(commit.sha);
    // A second child of this commit sits in another lane; it ends here.
    const merged = [];
    for (let i = 0; i < lanes.length; i++) {
      if (i !== lane && lanes[i] === commit.sha) { merged.push(i); lanes[i] = null; }
    }
    lanes[lane] = commit.parents[0] ?? null;
    const forks = [];
    for (const parent of commit.parents.slice(1)) {
      // A parent already expected elsewhere reuses that lane rather than opening
      // a second column for the same line of history.
      const existing = lanes.indexOf(parent);
      forks.push(existing !== -1 ? existing : take(parent));
    }
    // Trailing empties would leave the rail padded with blank columns.
    while (lanes.length && lanes[lanes.length - 1] === null) lanes.pop();
    rows.push({ commit, lane, merged, forks, before, after: [...lanes] });
  }
  return rows;
}

const LANE_COLOURS = [
  "var(--md-sys-color-primary)", "var(--md-sys-color-tertiary)",
  "var(--md-extended-color-waiting-color, var(--md-sys-color-secondary))",
  "var(--md-extended-color-idle-color, var(--md-sys-color-error))",
  "var(--md-sys-color-secondary)",
];
const laneColour = (index) => LANE_COLOURS[index % LANE_COLOURS.length];

const LANE_W = 14;      // horizontal distance between lanes
const ROW_H = 52;       // must equal .git-commit's height, or the lanes break at each join

/* One row's slice of the graph, drawn as its own SVG: the lanes passing
   straight through, the diagonals joining a fork or a merge to this commit, and
   the dot itself. Drawing per row rather than one tall SVG keeps the rail
   aligned with its text however the subject wraps. */
function commitRail(row, width) {
  const x = (lane) => lane * LANE_W + LANE_W / 2;
  const mid = ROW_H / 2;
  const parts = [];
  const line = (x1, y1, x2, y2, lane) =>
    `<path d="M${x1} ${y1}${x1 === x2 ? `V${y2}` : ` C${x1} ${(y1 + y2) / 2} ${x2} ${(y1 + y2) / 2} ${x2} ${y2}`}"
       fill="none" stroke="${laneColour(lane)}" stroke-width="2" stroke-linecap="round"/>`;

  // Lanes that neither start nor stop here pass straight through behind it.
  const span = Math.max(row.before.length, row.after.length);
  for (let i = 0; i < span; i++) {
    if (i === row.lane || row.merged.includes(i) || row.forks.includes(i)) continue;
    if (row.before[i] && row.after[i]) parts.push(line(x(i), 0, x(i), ROW_H, i));
  }
  // This commit's own lane: up to whatever pointed at it, down to its parent.
  if (row.before[row.lane]) parts.push(line(x(row.lane), 0, x(row.lane), mid, row.lane));
  if (row.after[row.lane]) parts.push(line(x(row.lane), mid, x(row.lane), ROW_H, row.lane));
  // A second child arriving from another lane, and a merge parent leaving for one.
  for (const i of row.merged) parts.push(line(x(i), 0, x(row.lane), mid, i));
  for (const i of row.forks) parts.push(line(x(row.lane), mid, x(i), ROW_H, i));

  return `<svg class="git-commit__rail" width="${width}" height="${ROW_H}" viewBox="0 0 ${width} ${ROW_H}" aria-hidden="true">
    ${parts.join("")}
    <circle cx="${x(row.lane)}" cy="${mid}" r="4" fill="${laneColour(row.lane)}"/>
  </svg>`;
}

/* The two status letters, as git itself writes them: staged on the left,
   unstaged on the right. */
/* One letter for what happened to a file, as the editor labels it: the side of
   the status pair that this group is showing, not both at once. A file changed
   in the index and again in the tree appears in both groups, and each row then
   says what its own group is holding. */
const MARK_LABELS = {
  M: "modified", A: "added", D: "deleted", R: "renamed", C: "copied",
  U: "untracked", T: "type changed",
};

function fileMark(file, staged) {
  // The editor marks a conflict apart from every ordinary letter, and so does
  // this: it is the one row that has to be dealt with before anything else can
  // happen. C stays what git means by it, a copy.
  if (file.conflicted) return { mark: "!", label: "conflicted" };
  if (file.untracked) return { mark: "U", label: "untracked" };
  const letter = (staged ? file.staged : file.unstaged) || "M";
  return { mark: letter, label: MARK_LABELS[letter] || letter };
}

/* A row, and under it the diff if this is the row that is open. `staged` says
   which side of the file this row stands for, so its actions and its diff both
   act on the right one. */
function fileRow(file, staged) {
  const { mark, label } = fileMark(file, staged);
  // git reports a directory it will not look inside — a nested repository, a
  // worktree — as one entry with a trailing slash. Split on the segment before
  // it, and keep the slash on the name so the row still reads as a folder.
  const folder = file.path.endsWith("/");
  const trimmed = folder ? file.path.slice(0, -1) : file.path;
  const cut = trimmed.lastIndexOf("/");
  const dir = cut === -1 ? "" : trimmed.slice(0, cut);
  const base = (cut === -1 ? trimmed : trimmed.slice(cut + 1)) + (folder ? "/" : "");
  const open = diffOpen && diffOpen.path === file.path && diffOpen.staged === staged;
  const can = git?.canWrite;
  const act = (action, icon, title, danger) =>
    `<button class="scm-icon md-state${danger ? " scm-icon--danger" : ""}" type="button"
       data-git="${action}" title="${escapeHtml(title)}" aria-label="${escapeHtml(`${title} — ${file.path}`)}">${icon}</button>`;

  return `<div class="git-file" data-mark="${mark}" data-path="${escapeHtml(file.path)}"
      data-staged="${staged ? "1" : "0"}"${open ? ` data-open="1"` : ""}>
      <button class="git-file__open md-state" type="button" data-git="diff"
        title="${escapeHtml(`${file.path} — ${label}`)}" aria-expanded="${open}">
        <span class="git-file__name md-body-medium">${escapeHtml(base)}</span>
        <span class="git-file__dir md-body-small"><bdi>${escapeHtml(dir)}</bdi></span>
        ${file.origPath ? `<span class="git-file__from md-body-small md-mono">← ${escapeHtml(file.origPath)}</span>` : ""}
      </button>
      ${can ? `<div class="scm-actions">
        ${staged ? act("unstage", ICON.minus, "Unstage changes")
          // Staging a conflicted file is how the resolution is recorded; git will
          // not restore an unmerged path, so discard is not offered on one.
          : file.conflicted ? act("stage", ICON.plus, "Stage — marks this conflict resolved")
          : `${act("discard", ICON.discard, "Discard changes", true)}${act("stage", ICON.plus, "Stage changes")}`}
      </div>` : ""}
      <span class="git-file__xy md-mono md-body-small" title="${escapeHtml(label)}">${escapeHtml(mark)}</span>
    </div>
    ${open ? diffPane() : ""}`;
}

/* The open diff, coloured by what each line does to the file. */
function diffPane() {
  if (diffText === null) return `<p class="git-empty md-body-small">Reading the diff…</p>`;
  if (!diffText) return `<p class="git-empty md-body-small">${escapeHtml(diffNote || "No line changes to show")}</p>`;
  const lines = diffText.split("\n");
  // The last line of a patch is the newline before EOF, not a line of its own.
  if (lines[lines.length - 1] === "") lines.pop();
  const body = lines.map((line) => {
    const kind = line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ")
        || line.startsWith("index ") || line.startsWith("new file") || line.startsWith("deleted file")
        || line.startsWith("rename ") || line.startsWith("similarity ") ? "meta"
      : line.startsWith("@@") ? "hunk"
      : line.startsWith("+") ? "add"
      : line.startsWith("-") ? "del" : "";
    return `<span class="scm-diff__line${kind ? ` scm-diff__line--${kind}` : ""}">${escapeHtml(line) || " "}</span>`;
  }).join("");
  return `<pre class="scm-diff md-mono md-body-small" tabindex="0">${body}</pre>`;
}

/* Changes and history are two tabs rather than one scroll, because the history
   is the longer of the two by far and having it below the file list put the
   thing you check most often above a graph you have to scroll past. Both read
   the same fetch, so switching between them costs nothing. */

/* Neither tab is worth showing without knowing which branch it is describing,
   so the same header opens both. */
function gitHead(git) {
  const here = git.branch || (git.head ? git.head.slice(0, 7) : "no commits");
  // The branch is a button when there is somewhere to go: the editor's status-bar
  // branch works this way, and a repository with one branch and no remote has
  // nothing to offer but the new-branch line, which is still worth offering.
  const badge = git.canWrite
    ? `<button class="git-badge git-badge--button md-state md-label-large" type="button" data-git="branch-menu"
         title="Switch branch, or start a new one" aria-haspopup="menu">
        ${ICON.branch}<span class="md-mono">${escapeHtml(here)}</span>${ICON.chevron}
       </button>`
    : `<span class="git-badge md-label-large">${ICON.branch}<span class="md-mono">${escapeHtml(here)}</span></span>`;
  // The counts say what they would do, and do it: the arrow you are looking at
  // when you think "push that" is the arrow itself.
  // Filled, not quiet: an unpushed commit is something to do, and a transparent
  // arrow beside a row of transparent arrows reads as one more fact about the
  // repository. Push borrows the colour this panel already uses for "this one
  // needs you"; pull is work coming the other way, so it takes the primary tone.
  const drift = (key, arrow, count, verb, title) => {
    if (!count) return "";
    const label = `<span class="md-mono">${arrow}${count}</span>`;
    return git.canWrite
      ? `<button class="git-badge git-badge--button git-badge--drift md-state md-label-medium"
           type="button" data-way="${key}" data-git="${key}" title="${escapeHtml(title)}">
          ${label}<span class="git-badge__verb">${verb}</span>
         </button>`
      : `<span class="git-badge git-badge--drift md-label-medium" data-way="${key}"
           title="${escapeHtml(title)}">${label}<span class="git-badge__verb">${verb}</span></span>`;
  };
  return `
    <div class="git-head">
      ${badge}
      ${git.detached ? `<span class="git-badge git-badge--quiet md-body-small">detached HEAD</span>` : ""}
      ${git.upstream ? `<span class="git-badge git-badge--quiet md-body-small md-mono">${escapeHtml(git.upstream)}</span>` : ""}
      ${drift("push", "↑", git.ahead, "to push",
        `Push ${plural(git.ahead, "commit")} to ${git.upstream || "the remote"}`)}
      ${drift("pull", "↓", git.behind, "to pull",
        `Pull ${plural(git.behind, "commit")} from ${git.upstream || "the remote"}`)}
      ${git.stashes ? `<span class="git-badge git-badge--quiet md-body-small">${git.stashes} stashed</span>` : ""}
      ${gitHeadActions(git)}
    </div>`;
}

/* Plural agreement, for the handful of places that count something. Named away
   from `count` because two functions here already use that for a local. */
function plural(n, noun) {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

/* The header's own buttons: sync, and the overflow that holds everything a
   repository can be told to do that is not about one file. Sync is the editor's
   one button for "catch up, then hand over" — pull what is waiting, push what is
   not there yet — and it says which way the traffic is going. */
function gitHeadActions(git) {
  if (!git.canWrite) {
    return `<span class="git-badge git-badge--quiet md-body-small scm-actions" style="opacity:1;margin-inline-start:auto"
      title="This panel is serving read-only, so it can show the repository but not change it">read-only</span>`;
  }
  const sync = !git.detached && (git.upstream
    ? (git.ahead || git.behind
        ? `Sync — pull ${git.behind || 0}, push ${git.ahead || 0}`
        : "Sync — nothing waiting either way")
    : "Publish this branch — it has no upstream yet");
  return `<div class="scm-actions" style="opacity:1;margin-inline-start:auto">
      ${sync ? `<button class="scm-icon md-state" type="button" data-git="sync" title="${escapeHtml(sync)}" aria-label="${escapeHtml(sync)}">${ICON.sync}</button>` : ""}
      <button class="scm-icon md-state" type="button" data-git="menu" title="More git actions" aria-label="More git actions" aria-haspopup="menu">${ICON.more}</button>
    </div>`;
}

/* Whatever both git tabs should show instead of themselves — still loading, not
   readable — or null when there is real data to draw. */
function gitNotice(session) {
  if (!git || gitFor !== session.sessionId) {
    return `<p class="git-empty md-body-medium">Reading the repository…</p>`;
  }
  if (!git.ok) {
    return `<p class="git-empty md-body-medium">${escapeHtml(git.message || "Could not read this repository")}</p>`;
  }
  return null;
}

/* The groups the editor shows, in its order: what still has to be resolved
   first, then what is going into the next commit, then everything else. A file
   with changes on both sides is in two of them, once per side. */
function gitGroups(files) {
  return {
    merge: files.filter((f) => f.conflicted),
    staged: files.filter((f) => f.staged && !f.conflicted),
    changes: files.filter((f) => (f.unstaged || f.untracked) && !f.conflicted),
  };
}

function gitPanel(session) {
  const notice = gitNotice(session);
  if (notice) return notice;

  const groups = gitGroups(git.files);
  const can = git.canWrite;
  const act = (action, icon, title, danger) =>
    `<button class="scm-icon md-state${danger ? " scm-icon--danger" : ""}" type="button"
       data-git="${action}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">${icon}</button>`;

  // A group's buttons carry no paths of their own: the handler reads the group
  // back out of the same split, so what a click acts on is whatever the list is
  // showing at the moment of the click rather than whatever it showed at paint.
  const group = (key, title, files, staged, actions) => files.length ? `
    <section class="scm-group" data-group="${key}">
      <header class="scm-group__head">
        <h3 class="scm-group__title md-label-medium">${escapeHtml(title)}<span class="scm-count md-label-small">${files.length}</span></h3>
        ${can ? `<div class="scm-actions">${actions}</div>` : ""}
      </header>
      <div class="git-files">${files.map((f) => fileRow(f, staged)).join("")}</div>
    </section>` : "";

  return `
    ${gitHead(git)}
    ${can ? commitBox(session, groups) : ""}
    ${group("merge", "Merge changes", groups.merge, false,
      act("stage-group", ICON.plus, "Stage all — marks these conflicts resolved"))}
    ${group("staged", "Staged changes", groups.staged, true,
      act("unstage-group", ICON.minus, "Unstage all"))}
    ${group("changes", "Changes", groups.changes, false,
      act("discard-group", ICON.discard, "Discard all changes", true) + act("stage-group", ICON.plus, "Stage all changes"))}
    ${git.files.length ? "" : `<p class="git-empty md-body-medium">Nothing changed — the working tree is clean.</p>`}`;
}

/* Why the commit button cannot be pressed, or null when it can. One answer, in
   one place: it is needed once when the pane is painted and again on every
   keystroke, and two copies of it would eventually disagree. */
function commitBlocker(session, groups) {
  if (groups.merge.length) return "Resolve the conflicts first";
  if (!groups.staged.length && !groups.changes.length) return "Nothing to commit";
  if (!(commitDrafts.get(session.sessionId) || "").trim()) return "A commit needs a message";
  return null;
}

/* The message and the button that uses it.

   The button says what it will actually do, which depends on what is staged:
   with nothing staged the editor offers to stage everything and commit that in
   one go, and saying so on the button is better than a dialog after the click. */
function commitBox(session, groups) {
  const message = commitDrafts.get(session.sessionId) || "";
  const blocked = commitBlocker(session, groups);
  const label = groups.staged.length ? "Commit"
    : groups.changes.length ? `Commit all ${groups.changes.length}`
    : "Commit";
  const where = git.branch ? ` on ${git.branch}` : "";
  return `
    <div class="scm-commit">
      <div class="scm-commit__box">
        <textarea class="scm-commit__field md-body-medium" id="commitField" rows="2"
          placeholder="Message — ${escapeHtml(`Ctrl+Enter to commit${where}`)}"
          aria-label="Commit message">${escapeHtml(message)}</textarea>
        <button class="scm-icon md-state scm-commit__ai" type="button" data-git="suggest"
          ${suggesting ? `data-busy="1" disabled title="Writing a message…"`
            : `title="Let Claude write the message from the diff"`}
          aria-label="Let Claude write the commit message">${ICON.sparkle}</button>
      </div>
      <div class="scm-commit__row">
        <button class="button button--filled md-state scm-commit__go" type="button" data-git="commit"
          ${blocked ? `disabled title="${escapeHtml(blocked)}"` : `title="${escapeHtml(`Commit${where}`)}"`}>
          ${ICON.check}${escapeHtml(label)}
        </button>
        <button class="button button--filled md-state scm-commit__more" type="button" data-git="commit-menu"
          title="Other ways to commit" aria-label="Other ways to commit" aria-haspopup="menu">${ICON.chevron}</button>
      </div>
    </div>`;
}

/* The message box grows with what is typed, up to the point where the file list
   below it would be pushed off screen. */
const COMMIT_MAX = 180;
function growCommit(field) {
  field.style.height = "auto";
  field.style.height = `${Math.min(COMMIT_MAX, Math.max(56, field.scrollHeight))}px`;
  field.style.overflowY = field.scrollHeight > COMMIT_MAX ? "auto" : "hidden";
}

/* Whether the commit button can be pressed changes as the message is typed,
   which is far too often to rebuild the pane for. */
function syncCommitButton(session) {
  const button = detailPane.querySelector("[data-git='commit']");
  if (!button || !git) return;
  const blocked = commitBlocker(session, gitGroups(git.files || []));
  button.disabled = Boolean(blocked);
  button.title = blocked || `Commit${git.branch ? ` on ${git.branch}` : ""}`;
}

/* Commit, and then — for the split button's other entries — push or sync what
   was just committed. The message is only forgotten once git has taken it. */
async function doCommit(session, button, { amend = false, then = null } = {}) {
  const message = (commitDrafts.get(session.sessionId) || "").trim();
  const groups = gitGroups(git?.files || []);
  if (groups.merge.length) {
    showSnackbar("Resolve the conflicts first, then commit");
    return;
  }
  if (!message && !amend) {
    showSnackbar("A commit needs a message");
    detailPane.querySelector("#commitField")?.focus();
    return;
  }
  // Nothing staged is the editor's "commit all": the Changes group goes in as it
  // stands, which is what the button already said it would do.
  const stageAll = !groups.staged.length && groups.changes.length > 0;
  const ok = await gitDo("commit", { message, amend, stageAll }, button);
  if (!ok) return;
  commitDrafts.delete(session.sessionId);
  const field = detailPane.querySelector("#commitField");
  if (field) { field.value = ""; growCommit(field); }
  if (then) await gitDo(then, {}, button);
}

/* The sparkle: a headless Claude reads the diff and writes the message.

   It takes ten or twenty seconds, which is long enough that the button has to
   say it is working — and long enough that the answer may land after the pane
   has been rebuilt underneath it, so the message goes into the draft first and
   into the field only if the field is still there. */
async function suggestMessage(session, button) {
  if (gitActing) return;
  const typed = (commitDrafts.get(session.sessionId) || "").trim();
  if (typed) {
    const replace = await askConfirm({
      headline: "Replace the message?",
      body: "What Claude writes goes into the box instead of what you have typed.",
      confirmLabel: "Replace", danger: false,
    });
    if (!replace) return;
  }

  gitActing = true;
  suggesting = true;
  button.disabled = true;
  button.dataset.busy = "1";
  showSnackbar("Reading the diff and writing a message…", 90000);
  try {
    const response = await fetch("/api/git", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: session.sessionId, action: "suggestMessage" }),
    });
    const data = await response.json().catch(() => ({}));
    if (!data.ok || !data.text) {
      showSnackbar(data.message || "Could not write a message", 8000);
      return;
    }
    commitDrafts.set(session.sessionId, data.text);
    const field = detailPane.querySelector("#commitField");
    if (field) {
      field.value = data.text;
      growCommit(field);
      field.focus();
      // The caret at the end, because the next thing anyone does with a written
      // message is edit it.
      field.setSelectionRange(data.text.length, data.text.length);
    }
    syncCommitButton(session);
    showSnackbar("Message written — read it before you commit", 5000);
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    gitActing = false;
    suggesting = false;
    const live = detailPane.querySelector("[data-git='suggest']");
    if (live) {
      live.disabled = false;
      delete live.dataset.busy;
      live.title = "Let Claude write the message from the diff";
    }
  }
}

/* What the arrow beside Commit opens: the same list the editor keeps there. */
function commitMenuItems(session) {
  const upstream = git?.upstream;
  return [
    { key: "commit", icon: ICON.check, label: "Commit",
      run: (el) => doCommit(session, el) },
    { key: "commit-push", icon: ICON.upload, label: "Commit & push",
      hint: upstream || "publishes the branch",
      run: (el) => doCommit(session, el, { then: "push" }) },
    { key: "commit-sync", icon: ICON.sync, label: "Commit & sync",
      hint: "pulls first, then pushes",
      run: (el) => doCommit(session, el, { then: "sync" }) },
    { divider: true },
    { key: "amend", icon: ICON.pencil, label: "Commit (amend)",
      hint: git?.commits?.[0]?.subject ? clip(git.commits[0].subject, 34) : "rewrites the last commit",
      disabled: !git?.commits?.length,
      run: (el) => doCommit(session, el, { amend: true }) },
  ];
}

/* The branch list, in the editor's order: the two ways to start a branch, then
   the branches themselves — local ones by how recently they were committed to,
   because that is the handful you are actually moving between, then the remote
   ones nobody here has a local copy of yet. */
function branchMenuItems(session) {
  const local = git?.branches?.local || [];
  const remote = git?.branches?.remote || [];
  const items = [
    { key: "new", icon: ICON.plus, label: "Create new branch…", hint: "from here",
      run: () => createBranch(session, null) },
    // No hint on this one: with one, the label is what gets ellipsised, and
    // "Create new branch …" reads like the line above it.
    { key: "new-from", icon: ICON.branch, label: "Create new branch from…",
      disabled: !local.length && !remote.length,
      // Anchored to the badge, not to the item that was just clicked: that item
      // belongs to a menu which is already closing, and a hidden element has no
      // position to hang the next menu off.
      run: () => openGitMenu(detailPane.querySelector("[data-git='branch-menu']"),
                             "Start the branch from", startPointItems(session)) },
  ];
  const now = Date.now() / 1000 + skew;
  if (local.length) {
    items.push({ divider: true });
    for (const branch of local.slice(0, 40)) {
      items.push({
        key: `local:${branch.name}`, icon: branch.current ? ICON.check : ICON.branch,
        label: branch.name,
        hint: branch.current ? "you are here" : ago(now - branch.at),
        disabled: branch.current,
        run: (el) => switchTo(session, branch.name, el),
      });
    }
  }
  if (remote.length) {
    items.push({ divider: true });
    for (const branch of remote.slice(0, 40)) {
      items.push({
        key: `remote:${branch.name}`, icon: ICON.download, label: branch.name,
        hint: "check out and track",
        run: (el) => switchTo(session, branch.name, el),
      });
    }
  }
  // A repository with nothing but the branch you are on says so. Two create
  // lines and then silence reads as a list that failed to load — which is also
  // what it looks like when the panel is a server old enough not to send one.
  if (local.length <= 1 && !remote.length) {
    items.push({ divider: true });
    items.push({
      key: "none", icon: ICON.branch, disabled: true,
      label: git?.branches ? "No other branches yet" : "Branch list unavailable",
      hint: git?.branches ? "" : "restart the panel",
    });
  }
  return items;
}

/* The same list again, but picking one names where a new branch starts rather
   than where HEAD goes. */
function startPointItems(session) {
  const local = git?.branches?.local || [];
  const remote = git?.branches?.remote || [];
  return [...local, ...remote].slice(0, 60).map((branch) => ({
    key: `from:${branch.name}`, icon: ICON.branch, label: branch.name,
    hint: branch.current ? "where you are" : "",
    run: () => createBranch(session, branch.name),
  }));
}

async function createBranch(session, from) {
  const name = await askText({
    headline: "Name the branch",
    body: from
      ? `It starts from <span class="md-mono">${escapeHtml(from)}</span>.`
      : "It starts from where you are now.",
    placeholder: "feature/something",
    confirmLabel: "Create branch",
  });
  if (!name) return;
  await gitDo("switch", { branch: name, create: true, ...(from ? { from } : {}) },
    detailPane.querySelector("[data-git='branch-menu']"));
}

/* Switching branches rewrites the files a session is working in, so a session
   that is mid-turn gets a word first — the panel knows that much, and the editor
   never did. git's own refusals (uncommitted work in the way, the branch held by
   another worktree) come back as they are. */
async function switchTo(session, branch, button) {
  if (session.status === "busy") {
    const go = await askConfirm({
      headline: "This session is working right now",
      body: `Switching to <span class="md-mono">${escapeHtml(branch)}</span> changes the files
             <span class="md-mono">${escapeHtml(session.name)}</span> is editing underneath it.`,
      confirmLabel: "Switch anyway",
    });
    if (!go) return;
  }
  await gitDo("switch", { branch }, button);
}

/* And what the header's overflow opens: everything about the repository rather
   than about one file. */
function gitMenuItems(session) {
  const files = git?.files || [];
  return [
    { key: "pull", icon: ICON.download, label: "Pull", hint: git?.behind ? `${git.behind} waiting` : git?.upstream || "no upstream",
      disabled: !git?.upstream,
      run: (el) => gitDo("pull", {}, el) },
    { key: "push", icon: ICON.upload, label: git?.upstream ? "Push" : "Publish branch",
      hint: git?.ahead ? `${git.ahead} to push` : git?.upstream || "sets the upstream",
      disabled: git?.detached,
      run: (el) => gitDo("push", {}, el) },
    { key: "sync", icon: ICON.sync, label: "Sync", hint: "pull, then push", disabled: git?.detached,
      run: (el) => gitDo("sync", {}, el) },
    { key: "fetch", icon: ICON.download, label: "Fetch", hint: "just look",
      run: (el) => gitDo("fetch", {}, el) },
    { divider: true },
    { key: "stage-all", icon: ICON.plus, label: "Stage all changes", disabled: !files.length,
      run: (el) => gitDo("stageAll", {}, el) },
    { key: "unstage-all", icon: ICON.minus, label: "Unstage everything",
      disabled: !files.some((f) => f.staged),
      run: (el) => gitDo("unstageAll", {}, el) },
    { key: "discard-all", icon: ICON.discard, label: "Discard all changes…", danger: true,
      disabled: !files.length,
      run: (el) => discardWithConfirm(files.map((f) => f.path), files, el, { all: true }) },
    { divider: true },
    { key: "stash", icon: ICON.stash, label: "Stash all changes", disabled: !files.length,
      hint: "including new files",
      run: (el) => gitDo("stash", { message: commitDrafts.get(session.sessionId) || "" }, el) },
    { key: "stash-pop", icon: ICON.stash, label: "Restore latest stash",
      disabled: !git?.stashes, hint: git?.stashes ? `${git.stashes} stashed` : "nothing stashed",
      run: (el) => gitDo("stashPop", {}, el) },
  ];
}

/* Discarding is the one thing here that git cannot give back, so it says exactly
   what it is about to lose before it does it — and says "delete" rather than
   "discard" for a file that has never been committed, because that is what
   happens to it. */
async function discardWithConfirm(paths, files, button, { all = false } = {}) {
  const byPath = new Map((files || []).map((f) => [f.path, f]));
  const untracked = paths.filter((p) => byPath.get(p)?.untracked);
  const tracked = paths.filter((p) => !byPath.get(p)?.untracked);
  const one = paths.length === 1;
  const name = (p) => `<span class="md-mono">${escapeHtml(p)}</span>`;
  const parts = [];
  if (tracked.length) {
    parts.push(one ? `Changes in ${name(tracked[0])} go back to the last staged version.`
      : `Changes in ${tracked.length} file${tracked.length === 1 ? "" : "s"} go back to the last staged version.`);
  }
  if (untracked.length) {
    parts.push(one ? `${name(untracked[0])} has never been committed, so discarding it deletes it.`
      : `${untracked.length} file${untracked.length === 1 ? "" : "s"} that were never committed are deleted.`);
  }
  parts.push("This cannot be undone.");
  const ok = await askConfirm({
    headline: one ? "Discard this change?" : `Discard ${paths.length} changes?`,
    body: parts.join(" "),
    confirmLabel: untracked.length && !tracked.length ? "Delete" : "Discard",
  });
  if (!ok) return;
  if (all) return gitDo("discardAll", { includeUntracked: true }, button);
  return gitDo("discard", { paths }, button);
}

/* Where the pointer went, translated into one of the actions above. Delegated
   from the buttons the panel just drew, so a rebuild carries no listeners over. */
function onGitAction(session, button) {
  const action = button.dataset.git;
  const row = button.closest(".git-file");
  const path = row?.dataset.path;
  const staged = row?.dataset.staged === "1";
  const groupKey = button.closest(".scm-group")?.dataset.group;
  const groupFiles = groupKey ? gitGroups(git?.files || [])[groupKey] || [] : [];
  const groupPaths = groupFiles.map((f) => f.path);

  switch (action) {
    case "diff": return toggleDiff(path, staged);
    case "stage": return gitDo("stage", { paths: [path] }, button);
    case "unstage": return gitDo("unstage", { paths: [path] }, button);
    case "discard": return discardWithConfirm([path], git?.files || [], button);
    case "stage-group": return gitDo("stage", { paths: groupPaths }, button);
    case "unstage-group": return gitDo("unstage", { paths: groupPaths }, button);
    case "discard-group": return discardWithConfirm(groupPaths, groupFiles, button);
    case "sync": return gitDo(git?.upstream ? "sync" : "push", {}, button);
    case "push": return gitDo("push", {}, button);
    case "pull": return gitDo("pull", {}, button);
    case "branch-menu": return openGitMenu(button, git?.branch || "Branches", branchMenuItems(session));
    case "suggest": return suggestMessage(session, button);
    case "commit": return doCommit(session, button);
    case "commit-menu": return openGitMenu(button, "Commit", commitMenuItems(session));
    case "menu": return openGitMenu(button, git?.branch || "This repository", gitMenuItems(session));
    default: return undefined;
  }
}

/* Both git menus hang off their own button rather than the pointer, which is
   where a menu opened from a toolbar belongs. */
function openGitMenu(button, title, items) {
  const box = button.getBoundingClientRect();
  openMenu({ title, label: `${title} actions`, items }, box.left, box.bottom + 4);
}

/* Kept between renders so the caret does not jump to the end of a message that
   is still being written when the repository moves underneath it. */
let commitCaret = null;

function wireGit(session) {
  const field = detailPane.querySelector("#commitField");
  if (field) {
    growCommit(field);
    if (commitCaret) {
      if (commitCaret.focused) {
        field.focus();
        field.setSelectionRange(commitCaret.start, commitCaret.end);
      }
      commitCaret = null;
    }
    field.addEventListener("input", () => {
      commitDrafts.set(session.sessionId, field.value);
      growCommit(field);
      syncCommitButton(session);
    });
    field.addEventListener("keydown", (event) => {
      // Ctrl+Enter commits, as it does in the editor. Plain Enter is a newline:
      // a commit message has a body, and this box is where it gets written.
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        doCommit(session, detailPane.querySelector("[data-git='commit']"));
      }
    });
  }
  for (const button of detailPane.querySelectorAll("[data-git]")) {
    button.addEventListener("click", () => onGitAction(session, button));
  }
}

function historyPanel(session) {
  const notice = gitNotice(session);
  if (notice) return notice;

  const rows = layoutGraph(git.commits || []);
  const width = Math.max(1, rows.reduce((w, r) => Math.max(w, r.before.length, r.after.length), 1)) * LANE_W;
  const now = Date.now() / 1000 + skew;

  return `
    ${gitHead(git)}
    ${rows.length ? `<div class="git-graph" style="grid-template-columns: ${width}px 1fr;">
      ${rows.map((row) => `
        <div class="git-commit">
          ${commitRail(row, width)}
          <div class="git-commit__body">
            <div class="git-commit__subject md-body-medium">${escapeHtml(row.commit.subject || "(no message)")}</div>
            <div class="git-commit__meta md-body-small">
              ${row.commit.refs.map((ref) => {
                const head = ref.startsWith("HEAD");
                const name = ref.replace(/^HEAD -> /, "");
                return `<span class="git-ref${head ? " git-ref--head" : ""} md-mono">${escapeHtml(name)}</span>`;
              }).join("")}
              <span class="md-mono">${escapeHtml(row.commit.short)}</span>
              <span class="meta-sep">·</span>${escapeHtml(row.commit.author)}
              <span class="meta-sep">·</span>${escapeHtml(ago(now - row.commit.at))}
            </div>
          </div>
        </div>`).join("")}
    </div>` : `<p class="git-empty md-body-medium">No commits yet.</p>`}`;
}

/* ==========================================================================
   Usage — what this session has asked of the models, and what that is worth
   at list price. Read out of the transcript, which is the only place the
   figures exist: every reply Claude Code writes down carries the usage the
   API reported for it.
   ========================================================================== */

/* Named here only to say them in the footnote; the server does the arithmetic. */
const CACHE_WRITE_5M = 1.25;
const CACHE_WRITE_1H = 2;

/* Money to the cent, except when the whole session has not cost one — a figure
   of "$0.00" beside a page of tokens reads as a bug rather than as cheap. */
function money(n) {
  if (!n) return "$0.00";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/* Token counts run to eight figures within an afternoon, so they are shortened.
   The exact number is never the question; the order of magnitude is. */
function tokens(n) {
  n = n || 0;
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  return n.toLocaleString();
}

function usageTile(label, value, note, lead = false) {
  return `<div class="use-tile${lead ? " use-tile--lead" : ""}">
    <div class="use-tile__label md-label-medium">${escapeHtml(label)}</div>
    <div class="use-tile__value ${lead ? "md-headline-small" : "md-title-medium"} md-mono">${value}</div>
    ${note ? `<div class="use-tile__note md-label-small">${note}</div>` : ""}
  </div>`;
}

function usageTable(rows) {
  return `<div class="use-scroll"><table class="use-table md-body-small">
    <thead><tr>
      <th>Model</th><th>Requests</th><th>Fresh in</th><th>Cache write</th>
      <th>Cache read</th><th>Out</th><th>Cost</th>
    </tr></thead>
    <tbody>${rows.map((row) => `<tr>
      <td class="md-mono">${escapeHtml(row.model)}${row.priced ? "" :
        ` <span class="fact-note">no price known</span>`}</td>
      <td class="md-mono">${row.requests.toLocaleString()}</td>
      <td class="md-mono">${tokens(row.input)}</td>
      <td class="md-mono">${tokens(row.cacheWrite5m + row.cacheWrite1h)}</td>
      <td class="md-mono">${tokens(row.cacheRead)}</td>
      <td class="md-mono">${tokens(row.output)}</td>
      <td class="md-mono">${row.priced ? money(row.cost) : "—"}</td>
    </tr>`).join("")}</tbody>
  </table></div>`;
}

function usagePanel(session) {
  if (!usage || usageFor !== session.sessionId) {
    return `<p class="git-empty md-body-medium">Reading the transcript…</p>`;
  }
  const t = usage.totals;
  const every = [...usage.models, ...usage.agentModels];
  if (!every.length) {
    return `<p class="git-empty md-body-medium">${session.kind === "child"
      ? `A session started from inside another writes no transcript of its own, so there
         is nothing here to total. Its usage lands in the transcript of the session that
         started it.`
      : "No model requests recorded yet — this session has not asked Claude anything."}</p>`;
  }
  const cached = t.cacheRead + t.cacheWrite5m + t.cacheWrite1h;
  // Rounded down: a session that has written any cache at all should not read
  // as though every token came out of one.
  const share = cached ? Math.floor(t.cacheRead / cached * 100) : 0;
  const context = usage.context || 0;
  const limit = usage.contextWindow || 1_000_000;
  const full = Math.min(100, Math.round(context / limit * 100));
  const spanEnd = usage.lastAt ? Date.parse(usage.lastAt) / 1000 : null;
  const spanStart = usage.firstAt ? Date.parse(usage.firstAt) / 1000 : null;

  return `
    <div class="use-tiles">
      ${usageTile("Cost so far", money(usage.cost), "at list price", true)}
      ${usageTile("Requests", t.requests.toLocaleString(),
        usage.agentModels.length
          ? `${usage.agentModels.reduce((n, r) => n + r.requests, 0).toLocaleString()} from sub-agents`
          : "")}
      ${usageTile("Tokens in", tokens(t.input + cached),
        `${share}% read from cache`)}
      ${usageTile("Tokens out", tokens(t.output),
        t.thinking ? `${tokens(t.thinking)} thinking` : "")}
    </div>

    <section class="section">
      <h3 class="section__title md-title-small">Context</h3>
      <p class="md-body-medium">${context
        ? `Its last request carried <span class="md-mono">${tokens(context)}</span> tokens of
           context${usage.contextModel ? ` into <span class="md-mono">${escapeHtml(usage.contextModel)}</span>` : ""},
           which is ${full}% of that model's ${tokens(limit)} window.`
        : "Nothing has gone in yet."}</p>
      ${context ? `<div class="use-bar">
        <div class="use-bar__fill" style="width: ${Math.max(1, full)}%" data-tight="${full >= 75 ? 1 : 0}"></div>
      </div>
      <div class="use-legend md-label-small">
        <span>${tokens(context)} carried</span><span>${tokens(limit)} window</span>
      </div>` : ""}
    </section>

    <section class="section">
      <h3 class="section__title md-title-small">By model</h3>
      ${usageTable(usage.models)}
    </section>

    ${usage.agentModels.length ? `<section class="section">
      <h3 class="section__title md-title-small">Sub-agents</h3>
      <p class="md-body-medium">Work this session handed to agents of its own. It is the
        same bill, kept apart because it is not the conversation you are reading.</p>
      ${usageTable(usage.agentModels)}
    </section>` : ""}

    <section class="section">
      <h3 class="section__title md-title-small">About these figures</h3>
      <p class="md-body-medium">Totalled from every reply in this session's transcript, at
        Anthropic's published per-token prices — a cache write costs ${CACHE_WRITE_5M}× fresh
        input for five minutes or ${CACHE_WRITE_1H}× for an hour, a cache read a tenth of it.
        What you are actually charged may be less: a Claude subscription bills a plan rather
        than tokens, and a negotiated rate is not the list one.${usage.unpriced.length
          ? ` No price is known here for
            ${usage.unpriced.map((m) => `<span class="md-mono">${escapeHtml(m)}</span>`).join(", ")},
            so its tokens are counted but its cost is not.` : ""}</p>
      ${spanStart ? `<p class="md-body-small fact-note">First request
        ${escapeHtml(ago(Date.now() / 1000 + skew - spanStart))}, last
        ${escapeHtml(ago(Date.now() / 1000 + skew - spanEnd))}.</p>` : ""}
    </section>`;
}

function aboutPanel(session, host) {
  const win = session.window;
  const muted = mutedSessions.has(session.sessionId);
  const started = new Date(session.startedAt * 1000);
  const facts = [
    ["Working folder", `<span class="md-mono">${escapeHtml(session.cwd)}</span>
      <button class="icon-button md-state fact-copy" data-copy="cwd" type="button"
        title="Copy folder path" aria-label="Copy folder path">${ICON.copy}</button>`],
    ["Git branch", session.branch ? `<span class="md-mono">${escapeHtml(session.branch)}</span>` : "—"],
    ["Runs in", escapeHtml(host.label)],
    // Only a nested session has one of these, and it is the fact that explains
    // the two below it: no transcript, and an id the panel made up.
    ...(session.kind === "child" ? [["Started from", session.parentName
      ? `${escapeHtml(session.parentName)} <span class="meta-sep">·</span>
         <span class="fact-note">pid ${session.parentPid}</span>`
      : `<span class="fact-note">a session that has since closed</span>`]] : []),
    // The only place the mode is said, and said in full: there is room here for
    // the caveat that it is a reading, and possibly an old one.
    ["Permission mode", session.permissionMode
      ? `${escapeHtml(MODE_LABELS[session.permissionMode] || session.permissionMode)}
         <span class="meta-sep">·</span> <span class="fact-note">${session.status === "stopped"
           ? "the last one it wrote down" : "as of the last time it wrote its mode down"}</span>`
      : "not written down yet"],
    ["Process", session.pid ? `<span class="md-mono">pid ${session.pid}</span>` : "not running"],
    ["Claude Code", escapeHtml(session.version || "—")],
    ["Session id", `<span class="md-mono">${escapeHtml(session.sessionId)}</span>
      <button class="icon-button md-state fact-copy" data-copy="id" type="button"
        title="Copy session id" aria-label="Copy session id">${ICON.copy}</button>${
      session.kind === "child"
        ? ` <span class="meta-sep">·</span> <span class="fact-note">the panel's own:
            a nested session publishes none</span>` : ""}`],
    ["Started", `${started.toLocaleString()} <span class="meta-sep">·</span> up ${duration(Date.now() / 1000 + skew - session.startedAt)}`],
    ["Transcript", transcript?.path ? `<span class="md-mono">${escapeHtml(shorten(transcript.path, 2))}</span>` : "—"],
  ];
  // A stopped session owns no window, so that section would only mislead.
  const windowSection = session.status === "stopped" ? "" : `
    <section class="section">
      <h3 class="section__title md-title-small">Window</h3>
      <div class="row">
        <div class="row__grow">
          <p class="md-body-medium">${isAmbiguous(win)
            ? `${win.candidates?.length || 2} windows look equally likely — ${windowSays(win, "long")}, so the panel will not guess. Identify it and the terminal will say which one it is.`
            : win
              ? `Paired with <span class="md-mono">${escapeHtml(win.title || win.id)}</span> — ${windowSays(win, "long")}.`
              : "No window is paired yet. Pair one and a single click brings it to the front."}</p>
          ${isAmbiguous(win) && win.candidates?.length
            ? `<ul class="hint md-label-small">${win.candidates
                .map((c) => `<li><span class="md-mono">${escapeHtml(c.title || c.id)}</span></li>`).join("")}</ul>`
            : ""}
        </div>
        <div class="detail-window-actions">${session.tty && !isRemembered(win)
            ? `<button class="button button--tonal md-state" data-act="identify">${ICON.pair} Identify window</button>`
            : ""}${win
          ? `<button class="button button--outlined md-state" data-act="${isRemembered(win) ? "unpair" : "pair"}">${isRemembered(win) ? "Clear pairing" : "Pick the window"}</button>`
          : `<button class="button button--tonal md-state" data-act="pair">${ICON.pair} Pair window</button>`}</div>
      </div>
    </section>`;
  return `
    ${windowSection}
    <section class="section">
      <h3 class="section__title md-title-small">Keeping it</h3>
      <label class="switch">
        <input type="checkbox" id="stickyToggle" ${session.sticky ? "checked" : ""}>
        <span class="switch__track"><span class="switch__thumb">
          <svg viewBox="0 0 24 24" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
        </span></span>
        <span class="switch__label md-body-medium">${session.kind === "child"
          ? `Keep this one in the dashboard after it closes, so its folder stays here and a
             session can be started up in it again — a nested session leaves no conversation
             behind to keep`
          : `Keep this one in the dashboard after it closes, so the
             conversation stays here and can be started up again`}</span>
      </label>
    </section>
    <section class="section">
      <h3 class="section__title md-title-small">Notifications</h3>
      <label class="switch">
        <input type="checkbox" id="muteToggle" ${muted ? "" : "checked"}>
        <span class="switch__track"><span class="switch__thumb">
          <svg viewBox="0 0 24 24" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
        </span></span>
        <span class="switch__label md-body-medium">Notify me when this session waits for an answer</span>
      </label>
    </section>
    <section class="section">
      <h3 class="section__title md-title-small">Facts</h3>
      <dl class="facts md-body-small">
        ${facts.map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${value}</dd>`).join("")}
      </dl>
    </section>
    <section class="section">
      <h3 class="section__title md-title-small">End session</h3>
      <div class="row">
        <div class="row__grow">
          <p class="md-body-medium">${session.alive
            ? `Closes this Claude Code the way <span class="md-mono">/exit</span> would, leaving ${escapeHtml(host.label)} open. The transcript stays on disk.`
            : "This session's process has already gone; it will drop off the list shortly."}</p>
        </div>
        <div><button class="button button--danger-outlined md-state" data-act="end"${session.alive ? "" : " disabled"}>${ICON.power} End session</button></div>
      </div>
    </section>
    <section class="section">
      <button class="button button--text md-state" id="openAppearance">Appearance settings</button>
    </section>`;
}

/* ------------------------------------------------------------ notifications */
if ("Notification" in window && Notification.permission === "default") {
  document.addEventListener("click", () => Notification.requestPermission(), { once: true });
}
function notifyWaiting(sessions) {
  const seen = new Set();
  for (const session of sessions) {
    seen.add(session.sessionId);
    const before = lastStatuses.get(session.sessionId);
    if (before && before !== "waiting" && session.status === "waiting"
        && !mutedSessions.has(session.sessionId)
        && "Notification" in window && Notification.permission === "granted") {
      new Notification(`${session.name} needs you`, {
        body: `${shorten(session.cwd)} is waiting for an answer.`, tag: session.sessionId,
      });
    }
    lastStatuses.set(session.sessionId, session.status);
  }
  for (const key of [...lastStatuses.keys()]) if (!seen.has(key)) lastStatuses.delete(key);
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

/* ---------------------------------------------------------------- snackbar */
let snackTimer;
function showSnackbar(message, life = 3400) {
  snackbar.textContent = message;
  snackbar.dataset.open = "true";
  clearTimeout(snackTimer);
  snackTimer = setTimeout(() => { snackbar.dataset.open = "false"; }, life);
}

/* ------------------------------------------------------------- settings UI */
function renderSwatches() {
  swatchRow.innerHTML = "";
  for (const preset of SEED_PRESETS) {
    const button = document.createElement("button");
    button.className = "swatch md-state";
    button.type = "button";
    button.title = preset.name;
    button.setAttribute("aria-label", `Base colour ${preset.name}`);
    button.setAttribute("aria-pressed", String(preset.hex.toLowerCase() === settings.seed.toLowerCase()));
    button.style.setProperty("--swatch", preset.hex);
    button.style.setProperty("--swatch-on", hexFromArgb(TonalPalette.fromInt(argbFromHex(preset.hex)).tone(100)));
    button.innerHTML = ICON.check;
    button.addEventListener("click", () => setSeed(preset.hex));
    swatchRow.appendChild(button);
  }
  const custom = document.createElement("label");
  custom.className = "swatch swatch--custom md-state";
  custom.title = "Any colour";
  custom.innerHTML = `${ICON.plus}<input type="color" value="${settings.seed}" aria-label="Custom base colour">`;
  custom.querySelector("input").addEventListener("input", (event) => setSeed(event.target.value));
  swatchRow.appendChild(custom);
  const hct = Hct.fromInt(argbFromHex(settings.seed));
  seedReadout.innerHTML = `Base <span class="code">${escapeHtml(settings.seed.toUpperCase())}</span> · hue ${Math.round(hct.hue)}° · chroma ${Math.round(hct.chroma)}`;
}
function renderContrast() {
  contrastGroup.innerHTML = "";
  for (const level of CONTRAST_LEVELS) {
    const button = document.createElement("button");
    button.className = "segmented__item md-state";
    button.type = "button";
    button.setAttribute("aria-pressed", String(level.key === settings.contrast));
    button.innerHTML = `${ICON.check}<span>${level.label}</span>`;
    button.addEventListener("click", () => {
      settings.contrast = level.key; persist(); applyScheme(); renderContrast();
    });
    contrastGroup.appendChild(button);
  }
}
function setSeed(hex) { settings.seed = hex; persist(); applyScheme(); renderSwatches(); }
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

/* ------------------------------------------------------- asking first */

/* One dialog, awaited rather than called back into: the caller reads like the
   sentence it is — ask, and if the answer is yes, do the thing. */
const askScrim = document.getElementById("askScrim");
let askResolve = null;

/* The same dialog with a field in it, for the one question that needs a word
   back rather than a yes: what to call a new branch. Resolves to the trimmed
   text, or null if it was dismissed. */
function askText({ headline, body, placeholder = "", value = "", confirmLabel = "Create" }) {
  // The dialog goes up first: opening one closes whatever stood before it, and
  // that closing is what puts the field away — reveal it before, and it is
  // hidden again by the time anyone could type in it.
  const answer = askConfirm({ headline, body, confirmLabel, danger: false });
  const field = document.getElementById("askField");
  field.hidden = false;
  field.placeholder = placeholder;
  field.value = value;
  field.focus();
  field.select();
  return answer.then((ok) => (ok ? field.value.trim() || null : null));
}

function askConfirm({ headline, body, confirmLabel = "Confirm", danger = true }) {
  closeAsk(false);
  document.getElementById("askHeadline").textContent = headline;
  document.getElementById("askSupporting").innerHTML = body;
  const confirm = document.getElementById("askConfirm");
  confirm.textContent = confirmLabel;
  // Red is for the answers that lose something. A question that only asks before
  // overwriting a box you can retype is not one of them.
  confirm.classList.toggle("button--danger", danger);
  confirm.classList.toggle("button--filled", !danger);
  askScrim.dataset.open = "true";
  document.getElementById("askCancel").focus();
  return new Promise((resolve) => { askResolve = resolve; });
}

function closeAsk(answer) {
  askScrim.dataset.open = "false";
  // The field belongs to whichever question asked for it, so it goes away with
  // that question rather than lingering into the next one.
  document.getElementById("askField").hidden = true;
  const resolve = askResolve;
  askResolve = null;
  if (resolve) resolve(answer);
}

// Enter in the field is the same as pressing the confirming button beside it.
document.getElementById("askField").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  closeAsk(true);
});

document.getElementById("askCancel").addEventListener("click", () => closeAsk(false));
document.getElementById("askConfirm").addEventListener("click", () => closeAsk(true));
askScrim.addEventListener("click", (event) => { if (event.target === askScrim) closeAsk(false); });

/* ==========================================================================
   The plan — how much of a subscription has gone, which is the one figure in this
   panel that no file on this machine knows. The server runs Claude Code's own
   `/usage` for it, so the reading is the same one the terminal would give you.
   ========================================================================== */
const planScrim = document.getElementById("planScrim");
const planButton = document.getElementById("planButton");
const planChipText = document.getElementById("planChipText");

/* Five seconds and a process per reading, and a figure that moves in points
   over hours: so it is read on the clock rather than on the poll, and not at
   all while nobody is looking at the page. */
const PLAN_POLL_MS = 300_000;
let plan = null;
let planBusy = false;
let planPolledAt = 0;
let planGone = false;      // the panel is read-only, so there is nothing to ask

async function fetchPlan(force) {
  if (planBusy || planGone) return;
  if (!force && document.hidden) return;
  if (!force && Date.now() - planPolledAt < PLAN_POLL_MS) return;
  planBusy = true;
  planPolledAt = Date.now();
  paintPlanChip();
  try {
    const response = await fetch(`/api/plan${force ? "?force=1" : ""}`, { cache: "no-store" });
    if (response.status === 403) {
      // Reading it means running a command, which a panel serving the network
      // does not do. The chip goes rather than sitting there refusing.
      planGone = true;
      planButton.hidden = true;
      return;
    }
    if (!response.ok) throw new Error(String(response.status));
    plan = await response.json();
    // A reading already in flight elsewhere: come back for its answer shortly
    // rather than waiting on this request.
    if (plan.reading) planPolledAt = Date.now() - PLAN_POLL_MS + 4000;
  } catch (error) {
    /* leave the last reading on screen */
  } finally {
    planBusy = false;
    paintPlanChip();
    if (planScrim.dataset.open === "true") paintPlanDialog();
  }
}

/* The two figures worth an app bar: how much of the current session's allowance
   has gone, and how much of the week's. Whichever is tighter colours the chip. */
function planHeadline() {
  const limits = plan?.limits || [];
  const session = limits.find((l) => /session/i.test(l.name));
  const week = limits.find((l) => /week/i.test(l.name) && !/\(/.test(l.name))
    || limits.find((l) => /week.*all models/i.test(l.name))
    || limits.find((l) => /week/i.test(l.name));
  return [session, week].filter(Boolean);
}

function paintPlanChip() {
  if (planGone) { planButton.hidden = true; return; }
  planButton.hidden = false;
  planButton.dataset.reading = planBusy || plan?.reading ? "1" : "0";
  const shown = planHeadline();
  if (!shown.length) {
    planChipText.textContent = plan?.ok === false ? "plan?" : "plan";
    planButton.dataset.tight = "0";
    planButton.title = plan?.message || "How much of your plan has gone";
    return;
  }
  // Used, not left: it reads the same way the bars below it do, and the colour
  // says how worried to be without doing the subtraction in your head.
  planChipText.innerHTML = shown
    .map((l) => `<span class="plan-pct" data-band="${planBand(l.percent)}">${l.percent}%</span>`)
    .join(`<span class="plan-chip__sep">·</span>`);
  const worst = Math.max(...shown.map((l) => l.percent));
  planButton.dataset.tight = String(planBand(worst));
  planButton.title = `${shown.map((l) => `${l.name}: ${l.percent}% used`).join(", ")} — press for the rest`;
}

/* One scale for every plan figure: room, tight, nearly gone. */
const planBand = (percent) => (percent >= 90 ? 2 : percent >= 75 ? 1 : 0);

function planBar(limit) {
  const tight = planBand(limit.percent);
  return `<div class="plan-limit">
    <div class="plan-limit__head">
      <span class="md-body-medium">${escapeHtml(limit.name)}</span>
      <span class="plan-limit__pct plan-pct md-title-medium md-mono"
            data-band="${tight}">${limit.percent}% used</span>
    </div>
    <div class="use-bar">
      <div class="use-bar__fill" style="width: ${limit.percent ? Math.max(1, limit.percent) : 0}%" data-tight="${tight}"></div>
    </div>
    <div class="use-legend md-label-small">
      <span>${100 - limit.percent}% left</span>
      <span>${limit.resets ? `resets ${escapeHtml(limit.resets)}` : "nothing used yet"}</span>
    </div>
  </div>`;
}

function paintPlanDialog() {
  const head = document.getElementById("planHead");
  const body = document.getElementById("planBody");
  const age = document.getElementById("planAge");
  if (!plan) {
    head.textContent = "Reading your usage — this takes a few seconds.";
    body.innerHTML = "";
    age.textContent = "";
    return;
  }
  head.textContent = plan.headline || plan.message || "";
  const limits = plan.limits || [];
  body.innerHTML = `
    ${limits.length ? limits.map(planBar).join("")
      : plan.text ? `<p class="plan-raw md-body-small">${escapeHtml(plan.text)}</p>`
      : `<p class="md-body-medium">${escapeHtml(plan.message || "Nothing came back.")}</p>`}
    ${(plan.blocks || []).map((block) => `<section class="plan-block">
      <h3 class="plan-block__title md-title-small">${escapeHtml(block.title)}</h3>
      ${block.lines.length ? `<ul class="md-body-small">${block.lines
        .map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>` : ""}
    </section>`).join("")}
    <p class="plan-note md-body-small">Read by running Claude Code's own
      <span class="md-mono">/usage</span>, so it is the same answer the terminal gives — and it
      costs no tokens. The panel never touches your credentials.${limits.length && plan.message
        ? ` The last refresh failed: ${escapeHtml(plan.message)}` : ""}</p>`;
  age.textContent = plan.at
    ? planBusy || plan.reading ? "reading…" : `read ${ago(Date.now() / 1000 - plan.at)}`
    : "";
}

function openPlan(open) {
  planScrim.dataset.open = String(open);
  if (!open) { planButton.focus(); return; }
  paintPlanDialog();
  document.getElementById("closePlan").focus();
  // Opening it is asking for the figure, so a stale one is refreshed there and
  // then rather than at the next tick of the clock.
  if (!plan || Date.now() / 1000 - (plan.at || 0) > PLAN_POLL_MS / 1000) fetchPlan(true);
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
  openButton.hidden = !feed.canSend;
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
  settings = { seed: DEFAULT_SEED, dark: matchMedia("(prefers-color-scheme: dark)").matches, contrast: "standard" };
  persist(); applyScheme(); renderSwatches(); renderContrast(); showSnackbar("Appearance reset");
});
themeToggle.addEventListener("change", () => { settings.dark = themeToggle.checked; persist(); applyScheme(); });
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
loadSettings();
applyScheme();
if (selectedId) panes.dataset.view = "detail";
poll();
setInterval(poll, 1000);
fetchPlan(true);
setInterval(() => fetchPlan(false), 30_000);
setInterval(() => {
  const now = Date.now() / 1000 + skew;
  for (const node of document.querySelectorAll("[data-since]")) {
    node.textContent = duration(now - Number(node.dataset.since));
  }
}, 1000);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  poll();
  // The repository was left alone while the tab was away, so catch it up now
  // rather than at the end of an interval that started before you looked.
  if (selectedId && isGitTab(tab)) fetchGit(true);
});
