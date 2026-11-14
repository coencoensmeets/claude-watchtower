/* The settings page: the palette, the contrast, and what is worth a notification. */

import { Hct, TonalPalette, argbFromHex, hexFromArgb } from "/vendor/material-color-utilities.js";
import { applyScheme, openSettings, persist } from "../main.js";
import { app } from "../state.js";
import { paintUpdateSetting } from "../views/update.js";
import { copyText } from "./clipboard.js";
import { detailPane } from "./dom.js";
import { escapeHtml } from "./format.js";
import { ICON } from "./icons.js";
import { showSnackbar } from "./snackbar.js";
import { CONTRAST_LEVELS, DEFAULT_SEED, NOTIFY_KINDS, SEED_PRESETS } from "./theme.js";

/* ------------------------------------------------------------- settings UI */
/* Each of these draws into the settings page in the detail pane, and does
   nothing when that page is not the one on screen — they are called from the
   theme switch in the app bar and from Reset as well as from the page itself. */
function renderSwatches() {
  const swatchRow = detailPane.querySelector("#swatches");
  if (!swatchRow) return;
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
  const picker = custom.querySelector<HTMLInputElement>("input");
  picker.addEventListener("input", () => setSeed(picker.value));
  swatchRow.appendChild(custom);
  const hct = Hct.fromInt(argbFromHex(app.settings.seed));
  detailPane.querySelector("#seedReadout").innerHTML = `Base <span class="code">${escapeHtml(app.settings.seed.toUpperCase())}</span> · hue ${Math.round(hct.hue)}° · chroma ${Math.round(hct.chroma)}`;
}
/* The light/dark switch, which sits with the rest of the appearance settings
   rather than in the app bar. It was the only setting in the bar, and a switch
   up there read as a thing to flip often — it is not; it is picked once and
   left, like the base colour it belongs beside. */
export function syncTheme() {
  const toggle = detailPane.querySelector<HTMLInputElement>("#themeToggle");
  if (!toggle) return;
  toggle.checked = app.settings.dark;
  detailPane.querySelector("#themeLabel").textContent = app.settings.dark ? "Dark" : "Light";
}

function renderContrast() {
  const contrastGroup = detailPane.querySelector("#contrastGroup");
  if (!contrastGroup) return;
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
/* The notification switches, and the one thing worth saying under them: the
   browser has its own permission, and a switch turned on behind a refused one
   would sit there promising something that cannot happen. */
function renderNotify() {
  const box = detailPane.querySelector("#notifySwitches");
  if (!box) return;
  box.innerHTML = NOTIFY_KINDS.map((kind) => `<label class="switch">
      <input type="checkbox" data-notify="${kind.key}" ${app.settings.notify[kind.key] ? "checked" : ""}>
      <span class="switch__track"><span class="switch__thumb">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
      </span></span>
      <span class="switch__label md-body-medium">${escapeHtml(kind.label)}<span
        class="switch__says md-label-small"><b>${escapeHtml(kind.says[0])}</b>${
        escapeHtml(kind.says[1])}</span></span>
    </label>`).join("");
  for (const input of box.querySelectorAll<HTMLInputElement>("[data-notify]")) {
    input.addEventListener("change", () => {
      app.settings.notify[input.dataset.notify] = input.checked;
      persist();
    });
  }
  paintNotifyPermission();
}

/* The switches above decide what the panel would like to tell you. Whether it
   is allowed to tell you anything is the browser's to say, and it is a separate
   question with a separate answer — so it gets its own notice rather than a
   line of small print, and where there is something to press, the notice is
   the thing that presses it. */
export function paintNotifyPermission() {
  const box = detailPane.querySelector<HTMLElement>("#notifyHint");
  if (!box) return;
  const state = "Notification" in window ? Notification.permission : "unsupported";
  box.hidden = state === "granted";
  box.dataset.state = state;
  if (state === "granted") return;
  if (state === "default") {
    box.innerHTML = `<p class="md-body-medium">Your browser has not let the panel show
        notifications yet. None of the switches above can reach you until it does.</p>
      <button class="button button--filled md-state" id="askNotify">Allow notifications</button>`;
    box.querySelector("#askNotify").addEventListener("click", async () => {
      await Notification.requestPermission();
      paintNotifyPermission();
    });
    return;
  }
  box.innerHTML = state === "unsupported"
    ? `<p class="md-body-medium">This browser cannot show desktop notifications, so none of
        the switches above can reach you. The tab title and the favicon still count the
        sessions waiting.</p>`
    : `<p class="md-body-medium">Your browser is blocking notifications from the panel, so
        none of the switches above can reach you. Turn them back on in the browser's own
        settings for this site — the padlock or the icon at the left of the address bar.</p>`;
}

/* The settings page, drawn where a conversation would be rather than over the
   top of one.

   A dialog was the wrong shape for it: nothing here is a decision to come back
   from — every switch takes effect as it is pressed and there is nothing to
   confirm — so the scrim was buying a modality the page did not want, and it
   put a scrolling box inside a scrolling box on a short window. As a pane it is
   simply another thing the panel can be showing, reached from the gear and left
   by picking any session. */
function settingsPage() {
  return `
    <header class="detail-header detail-header--plain">
      <div class="detail-header__top">
        <div class="detail-header__text">
          <div class="detail-header__title"><h2 class="md-headline-small">Settings</h2></div>
          <div class="detail-header__meta"><span class="md-body-small">
            How the panel looks, and what it tells you about</span></div>
        </div>
        <div class="detail-header__actions">
          <button class="button button--outlined md-state" id="closeSettings">Done</button>
        </div>
      </div>
    </header>
    <!-- Tabs rather than one long page: five unrelated groups of settings
         stacked up made the phone code a long scroll away, and the page
         only grows. Not data-tab — that name belongs to a session's tabs,
         and the delegated lookups for those would find these too. -->
    <div class="tabs" role="tablist" aria-label="Settings">
      ${SETTINGS_TABS.map((tab) => `<button class="tab md-state md-label-large" role="tab" type="button"
          id="settingsTab-${tab.key}" data-settings-tab="${tab.key}" aria-controls="settingsPanel-${tab.key}"
          aria-selected="${tab.key === settingsTab}" tabindex="${tab.key === settingsTab ? 0 : -1}">${ICON[tab.icon]}${tab.label}</button>`).join("")}
    </div>
    <div class="tab-panel" role="tabpanel" id="settingsPanel-appearance" aria-labelledby="settingsTab-appearance"
      ${settingsTab === "appearance" ? "" : "hidden"}>
      <!-- Theme and Contrast are one control each, so they sit beside each
           other on a pane wide enough for both. The swatches take the row under
           them, because they wrap and a third of the pane makes them taller
           than the two settings above put together. -->
      <div class="settings-row">
        <section class="section">
          <h3 class="section__title md-title-small">Theme</h3>
          <label class="switch">
            <input type="checkbox" id="themeToggle" aria-label="Dark theme">
            <span class="switch__track"><span class="switch__thumb">
              <svg viewBox="0 0 24 24" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
            </span></span>
            <span class="switch__label md-body-medium" id="themeLabel">Light</span>
          </label>
        </section>
        <section class="section">
          <h3 class="section__title md-title-small">Contrast</h3>
          <div class="segmented" id="contrastGroup" role="group" aria-label="Contrast level"></div>
        </section>
        <section class="section settings-row__wide">
          <h3 class="section__title md-title-small">Base colour</h3>
          <p class="md-body-medium">Every colour is derived from one base.</p>
          <div class="swatches" id="swatches"></div>
          <p class="seed-readout md-body-small" id="seedReadout"></p>
        </section>
      </div>
      <section class="section">
        <button class="button button--text md-state" id="resetTheme">Reset colours</button>
      </section>
    </div>
    <div class="tab-panel" role="tabpanel" id="settingsPanel-notify" aria-labelledby="settingsTab-notify"
      ${settingsTab === "notify" ? "" : "hidden"}>
      <!-- Which kinds of notification are wanted at all, for every session at
           once. The switches in a session's own Details tab choose which
           sessions may use the kinds turned on here. -->
      <section class="section">
        <h3 class="section__title md-title-small">Notifications</h3>
        <p class="md-body-medium">Raise a desktop notification when a session…</p>
        <div class="notify-grid" id="notifySwitches"></div>
        <div class="notify-permission" id="notifyHint" hidden></div>
      </section>
    </div>
    <div class="tab-panel" role="tabpanel" id="settingsPanel-sessions" aria-labelledby="settingsTab-sessions"
      ${settingsTab === "sessions" ? "" : "hidden"}>
      <!-- What a session's header offers. Only one thing so far, and it is
           here rather than on the session because it is the same answer for
           every session. -->
      <section class="section">
        <h3 class="section__title md-title-small">Session actions</h3>
        <label class="switch">
          <input type="checkbox" id="editorToggle">
          <span class="switch__track"><span class="switch__thumb">
            <svg viewBox="0 0 24 24" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
          </span></span>
          <span class="switch__label md-body-medium">Open in VS Code<span
            class="switch__says md-label-small">A button on every session with a
            folder, opening it in your editor</span></span>
        </label>
        <!-- Whether *Open in terminal* can work at all, filled by paintTerminal
             below and hidden on a machine where it can. -->
        <div class="settings-notice" id="terminalHint" hidden></div>
      </section>
    </div>
    <div class="tab-panel" role="tabpanel" id="settingsPanel-phone" aria-labelledby="settingsTab-phone"
      ${settingsTab === "phone" ? "" : "hidden"}>
      <!-- The way in from a phone. Hidden until the server says it is serving
           the network at all, and filled by paintReach below: the address it
           shows is read at that moment, because a laptop changes networks and a
           code pointing at this morning's address is worse than none. -->
      <section class="section" id="reachSection" hidden></section>
    </div>
    <div class="tab-panel" role="tabpanel" id="settingsPanel-panel" aria-labelledby="settingsTab-panel"
      ${settingsTab === "panel" ? "" : "hidden"}>
      <!-- Which release the panel is on, and a button to go and ask. Empty
           here on purpose: the app bar's chip only appears when there is
           something to press, so this is the only place that says "up to date"
           or names the reason a checkout is being left alone — and it is
           filled by views/update.ts, which is what knows. -->
      <section class="section" id="updateSection" hidden></section>
    </div>`;
}

/* The tabs, in the order they are worth reaching for. The one last open is
   remembered for the session of the browser tab, so coming back to Settings
   lands where you left it — the phone code, usually — rather than on colours. */
const SETTINGS_TABS = [
  { key: "appearance", label: "Appearance", icon: "palette" },
  { key: "notify", label: "Notifications", icon: "bell" },
  { key: "sessions", label: "Sessions", icon: "terminal" },
  { key: "phone", label: "Phone", icon: "phone" },
  { key: "panel", label: "Panel", icon: "info" },
];
let settingsTab = (() => {
  try {
    const kept = sessionStorage.getItem("watchtower.settingsTab");
    return SETTINGS_TABS.some((tab) => tab.key === kept) ? kept : "appearance";
  } catch (error) {
    return "appearance";
  }
})();

function showSettingsTab(key: string, focus = false) {
  if (!SETTINGS_TABS.some((tab) => tab.key === key)) return;
  settingsTab = key;
  try { sessionStorage.setItem("watchtower.settingsTab", key); } catch (error) { /* private mode */ }
  for (const button of detailPane.querySelectorAll<HTMLElement>("[data-settings-tab]")) {
    const on = button.dataset.settingsTab === key;
    button.setAttribute("aria-selected", String(on));
    button.tabIndex = on ? 0 : -1;
    if (on && focus) button.focus();
  }
  for (const tab of SETTINGS_TABS) {
    const panel = detailPane.querySelector<HTMLElement>(`#settingsPanel-${tab.key}`);
    if (panel) panel.hidden = tab.key !== key;
  }
}

/* Click to pick, and the arrow keys, Home and End along the strip — the
   keyboard pattern a tablist is expected to have. */
function wireSettingsTabs() {
  const strip = detailPane.querySelector<HTMLElement>(".tabs[aria-label='Settings']");
  if (!strip) return;
  strip.addEventListener("click", (event) => {
    const button = (event.target as Element).closest<HTMLElement>("[data-settings-tab]");
    if (button) showSettingsTab(button.dataset.settingsTab);
  });
  strip.addEventListener("keydown", (event) => {
    const keys = SETTINGS_TABS.map((tab) => tab.key);
    const at = keys.indexOf(settingsTab);
    const next = event.key === "ArrowRight" ? keys[(at + 1) % keys.length]
      : event.key === "ArrowLeft" ? keys[(at - 1 + keys.length) % keys.length]
      : event.key === "Home" ? keys[0]
      : event.key === "End" ? keys[keys.length - 1] : "";
    if (!next) return;
    event.preventDefault();
    showSettingsTab(next, true);
  });
}

/* Painted once and then left alone: every control on it keeps its own state,
   and a poll rebuilding the pane underneath would take the focus off whatever
   was just pressed. */
export function paintSettings() {
  if (detailPane.dataset.signature === "settings:page") return;
  detailPane.dataset.signature = "settings:page";
  detailPane.innerHTML = settingsPage();
  wireSettingsTabs();
  syncTheme();
  renderSwatches();
  renderContrast();
  renderNotify();
  paintUpdateSetting();
  paintReach();
  paintTerminal();
  const themeToggle = detailPane.querySelector<HTMLInputElement>("#themeToggle");
  themeToggle.addEventListener("change", () => {
    app.settings.dark = themeToggle.checked;
    persist();
    applyScheme();
  });
  const editorToggle = detailPane.querySelector<HTMLInputElement>("#editorToggle");
  editorToggle.checked = app.settings.showEditor;
  editorToggle.addEventListener("change", () => {
    app.settings.showEditor = editorToggle.checked;
    persist();
  });
  detailPane.querySelector("#closeSettings").addEventListener("click", () => openSettings(false));
  detailPane.querySelector("#resetTheme").addEventListener("click", () => {
    // Colours only. The button sits under the notification switches, and wiping
    // those as well would be a surprise the label does not warn about.
    app.settings = { ...app.settings, seed: DEFAULT_SEED,
      dark: matchMedia("(prefers-color-scheme: dark)").matches, contrast: "standard" };
    persist(); applyScheme(); renderSwatches(); renderContrast(); showSnackbar("Colours reset");
  });
}

/* ==========================================================================
   Open in terminal — whether this machine can, and what to type if it cannot.

   The button on a session already says why it is disabled, but only in a
   tooltip and only once a row is open, and neither of the things it needs is
   about the row: a terminal to open, and `claude` on the PATH the panel would
   open it with. So the answer is drawn here, once, beside the other setting
   that decides what a session's header offers.

   The line to copy is a package manager's, guessed by the server from the
   manager it can find — see control.install_line. It is offered the way the
   phone address is: the command itself in the box with the copy on the end of
   it, because a button saying *Copy the install command* hides the one thing
   worth reading.
   ========================================================================== */
async function paintTerminal() {
  const box = detailPane.querySelector<HTMLElement>("#terminalHint");
  if (!box) return;
  let terminal;
  try {
    const response = await fetch("/api/terminal", { cache: "no-store" });
    terminal = await response.json();
  } catch (error) {
    return;                       // the poll will say the panel is unreachable
  }
  // The pane may have moved on while that was in flight, and a machine that can
  // do it has nothing to say about it.
  if (!box.isConnected || terminal?.can) return;
  box.hidden = false;
  box.dataset.state = "missing";
  // Three ways to be unable, and they want different sentences: an override
  // pointing at nothing is a typo to fix rather than a package to install, and a
  // missing `claude` is almost always a PATH the panel was started with rather
  // than an uninstalled CLI — it is running the sessions this panel is showing.
  const said = terminal.override && !terminal.terminal
    ? `<p class="md-body-medium"><b>Open in terminal</b> cannot work here.
        <code class="md-mono">CLAUDE_WATCHTOWER_TERMINAL</code> names
        <code class="md-mono">${escapeHtml(terminal.named)}</code>, which is not on the panel's
        PATH. Point it at a terminal that is, or unset it to let the panel look for one.</p>`
    : !terminal.terminal
    ? `<p class="md-body-medium"><b>Open in terminal</b> cannot work here: this machine has none
        of the terminals the panel knows how to open — Ghostty, WezTerm, kitty, Alacritty,
        Konsole, GNOME Terminal, Xfce Terminal, <code class="md-mono">x-terminal-emulator</code>
        or xterm. Install one, or set <code class="md-mono">CLAUDE_WATCHTOWER_TERMINAL</code> to
        the one you have and the flag that takes a command.</p>`
    : `<p class="md-body-medium"><b>Open in terminal</b> cannot work here: the panel cannot find
        <code class="md-mono">claude</code> on its own PATH. It is on yours — it is running the
        sessions on this list — so this is usually a panel started by systemd or a launcher,
        which does not read your shell's profile. Give the unit the PATH, or an absolute
        <code class="md-mono">ExecStart</code> environment that has it.</p>`;
  box.innerHTML = said + (terminal.install
    ? `<div class="reach__line">
        <code class="md-mono reach__url">${escapeHtml(terminal.install)}</code>
        <button class="icon-button md-state reach__copy" id="copyInstall" type="button"
          title="Copy the command" aria-label="Copy the command">${ICON.copy}</button>
      </div>`
    : "");
  const copy = box.querySelector<HTMLElement>("#copyInstall");
  copy?.addEventListener("click", () => {
    if (!copyText(terminal.install, "Command copied")) return;
    copy.innerHTML = ICON.check;
    setTimeout(() => { if (copy.isConnected) copy.innerHTML = ICON.copy; }, 1400);
  });
}

/* ==========================================================================
   On your phone — the address, and the code that saves typing it.

   The panel serves the network by default and answers nobody off this machine
   without the key it printed, so the address a phone needs has a key on the end
   of it. Typing that once is tolerable; the point of the code is that you do not
   have to. It is drawn by the server — see watchtower/qr.py — because the panel
   ships no packages and an encoder is the same hundred lines of arithmetic in
   either language, with the difference that in Python it can be checked against
   a reference encoder in a test.

   Asked for on every visit to the page rather than kept: it is three lines of
   JSON, and the answer changes when the machine changes networks.
   ========================================================================== */
async function paintReach() {
  const section = detailPane.querySelector<HTMLElement>("#reachSection");
  if (!section) return;
  let reach;
  try {
    const response = await fetch("/api/reach", { cache: "no-store" });
    reach = await response.json();
  } catch (error) {
    return;                       // the poll will say the panel is unreachable
  }
  // The pane may have moved on while that was in flight.
  if (!section.isConnected) return;
  if (!reach?.lan) {
    // Serving this machine only, which is what --local does. Say so, rather
    // than leaving a reader to wonder where the phone section went.
    section.hidden = false;
    section.innerHTML = `<h3 class="section__title md-title-small">On your phone</h3>
      <p class="md-body-medium">This panel is serving this machine only, so there is
        nothing for a phone to open. Start it without <code class="md-mono">--local</code>
        to serve the network.</p>`;
    return;
  }
  section.hidden = false;
  // The address with the copy on the end of it, rather than a button underneath
  // saying what it would copy. The thing being copied is right there, so the
  // control belongs on it: one row, the address taking the room it needs and
  // the icon holding the right-hand end.
  const where = reach.address
    ? `<div class="reach__line">
        <code class="md-mono reach__url">${escapeHtml(reach.url)}</code>
        <button class="icon-button md-state reach__copy" id="copyReach" type="button"
          title="Copy the address" aria-label="Copy the address">${ICON.copy}</button>
      </div>`
    : `<span class="md-body-medium">this machine's own address, port
        <code class="md-mono">${reach.port}</code></span>`;
  section.innerHTML = `
    <h3 class="section__title md-title-small">On your phone</h3>
    <div class="reach">
      ${reach.address ? `<img class="reach__code" src="/api/qr" alt="The address of this panel, as a code to scan"
        width="248" height="248">` : ""}
      <div class="reach__said">
        <p class="md-body-medium">Point a camera at this, or type it in:</p>
        ${where}
        ${reach.key
          ? `<p class="md-body-small reach__note">The key on the end is what lets a phone in — the
             panel answers nothing from off this machine without it. It is the same key every
             time, and each phone only needs it once.</p>`
          : `<p class="md-body-small reach__note">This panel is running without a key, so anyone
             who can reach that address can read every conversation on it.</p>`}
      </div>
    </div>
    <div class="settings-notice" id="wslHint" hidden></div>
    <div class="tailnet" id="tailnetBox" hidden></div>`;
  paintWslHint(section.querySelector<HTMLElement>("#wslHint"), reach.wsl);
  paintTailnet(section.querySelector<HTMLElement>("#tailnetBox"), reach.tailnet);
  // Nothing to copy when there is no address to have copied — the sentence in
  // its place is about this machine's own network name, which the panel does
  // not know and cannot put on the clipboard.
  const copy = section.querySelector<HTMLElement>("#copyReach");
  copy?.addEventListener("click", () => {
    if (!copyText(reach.url, "Address copied")) return;
    // The same tick the copy button on a code block gives: the snackbar is at
    // the other end of the pane, and the answer belongs where the press was.
    copy.innerHTML = ICON.check;
    setTimeout(() => { if (copy.isConnected) copy.innerHTML = ICON.copy; }, 1400);
  });
}

/* Under WSL the code can be right and the phone still told the site cannot be
   reached: Windows drops the connection before Linux sees it. The server asks
   Windows (watchtower/wslnet.py); this says what it found, and in the mirrored
   case carries the button that asks Windows — behind a UAC prompt on the
   desktop — to let the port in. */
function paintWslHint(box: HTMLElement | null, wsl) {
  if (!box) return;
  const blocked = wsl?.firewall === "blocked";
  const nat = wsl?.mode === "nat";
  box.hidden = !(blocked || nat);
  box.dataset.state = "missing";
  if (box.hidden) return;
  if (nat) {
    box.innerHTML = `<p class="md-body-medium">A phone cannot open this address: WSL is in NAT
        mode, so the panel sits behind an address only this computer can reach. Add
        <code class="md-mono">networkingMode=mirrored</code> under <code class="md-mono">[wsl2]</code>
        in <code class="md-mono">%UserProfile%\\.wslconfig</code>, run
        <code class="md-mono">wsl --shutdown</code>, and start the panel again.</p>`;
    return;
  }
  box.innerHTML = `<p class="md-body-medium">A phone cannot open this address yet: Windows'
      firewall for WSL blocks incoming connections to port
      <code class="md-mono">${escapeHtml(String(wsl.port))}</code>.
      Allowing it asks Windows for administrator rights — answer the prompt on this computer.</p>
    <button class="button button--filled md-state" id="allowWsl" type="button">Allow on Windows</button>
    <p class="md-body-small">Or run this in an administrator PowerShell:</p>
    <div class="reach__line">
      <code class="md-mono reach__url">${escapeHtml(wsl.command)}</code>
      <button class="icon-button md-state reach__copy" id="copyWsl" type="button"
        title="Copy the command" aria-label="Copy the command">${ICON.copy}</button>
    </div>`;
  const copy = box.querySelector<HTMLElement>("#copyWsl");
  copy?.addEventListener("click", () => {
    if (!copyText(wsl.command, "Command copied")) return;
    copy.innerHTML = ICON.check;
    setTimeout(() => { if (copy.isConnected) copy.innerHTML = ICON.copy; }, 1400);
  });
  const allow = box.querySelector<HTMLButtonElement>("#allowWsl");
  allow?.addEventListener("click", async () => {
    allow.disabled = true;
    allow.textContent = "Waiting for Windows…";
    let answer;
    try {
      const response = await fetch("/api/reach/firewall", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      answer = await response.json();
    } catch (error) {
      answer = { ok: false, message: "The panel did not answer" };
    }
    showSnackbar(answer?.message || (answer?.ok ? "Allowed" : "Could not allow it"));
    if (!box.isConnected) return;
    if (answer?.wsl) { paintWslHint(box, answer.wsl); return; }
    allow.disabled = false;
    allow.textContent = "Allow on Windows";
  });
}

/* A line of text with a copy button on its end — the shape every command and
   address on this page takes. */
function copyLine(id: string, text: string, label: string) {
  return `<div class="reach__line">
      <code class="md-mono reach__url">${escapeHtml(text)}</code>
      <button class="icon-button md-state reach__copy" id="${id}" type="button"
        title="${label}" aria-label="${label}">${ICON.copy}</button>
    </div>`;
}

function wireCopy(box: HTMLElement, id: string, text: string, said: string) {
  const copy = box.querySelector<HTMLElement>(`#${id}`);
  copy?.addEventListener("click", () => {
    if (!copyText(text, said)) return;
    copy.innerHTML = ICON.check;
    setTimeout(() => { if (copy.isConnected) copy.innerHTML = ICON.copy; }, 1400);
  });
}

/* ==========================================================================
   From anywhere — the same panel over Tailscale.

   Off the wifi there is no address for a phone to open, and the panel is not
   built to be on the internet. Tailscale puts the laptop and the phone on a
   private network of their own, wherever each of them is; the server finds it
   (watchtower/tailnet.py), switches it, and hands back the address on it. The
   phone still needs the key there: it arrives from its own tailnet address, not
   from this machine.

   Installing Tailscale and letting this user switch it both need sudo, so for
   those the box shows the command rather than a button. Only this computer can
   press the switch: from the phone, turning it off would cut the phone off.
   ========================================================================== */
let tailnetPoll = 0;

function paintTailnet(box: HTMLElement | null, net, note = "") {
  if (!box) return;
  window.clearTimeout(tailnetPoll);
  box.hidden = !net;
  if (!net) return;
  const title = `<h4 class="md-title-small tailnet__title">From anywhere</h4>`;
  const phoneApp = `<p class="md-body-small reach__note">On the phone: the Tailscale app, signed in
      to the same account.</p>`;
  if (!net.installed) {
    box.innerHTML = `${title}
      <p class="md-body-medium">Away from this wifi a phone has nothing to open. Tailscale joins
        this computer and your phone into a private network of their own, wherever each one is —
        nothing is opened to the internet. Install it on this computer:</p>
      ${copyLine("copyTsInstall", net.install, "Copy the command")}
      ${phoneApp}
      <button class="button button--text md-state" id="tsRecheck" type="button">Check again</button>`;
    wireCopy(box, "copyTsInstall", net.install, "Command copied");
    box.querySelector("#tsRecheck")?.addEventListener("click", () => refreshTailnet(box));
    return;
  }
  if (net.state === "NoDaemon") {
    const start = "sudo systemctl enable --now tailscaled";
    box.innerHTML = `${title}
      <p class="md-body-medium">Tailscale is installed, but its service is not running. Start it:</p>
      ${copyLine("copyTsStart", start, "Copy the command")}
      <button class="button button--text md-state" id="tsRecheck" type="button">Check again</button>`;
    wireCopy(box, "copyTsStart", start, "Command copied");
    box.querySelector("#tsRecheck")?.addEventListener("click", () => refreshTailnet(box));
    return;
  }
  const on = !!net.running;
  const permission = /permission/i.test(note);
  // https when the certificate is in: the microphone, notifications and the
  // clipboard need a secure page, and the phone only gets one this way.
  const https = net.https || {};
  const address = https.ok ? https.url : net.url;
  box.innerHTML = `${title}
    <label class="switch">
      <input type="checkbox" id="tailnetToggle" ${on ? "checked" : ""} ${net.canSwitch ? "" : "disabled"}>
      <span class="switch__track"><span class="switch__thumb">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
      </span></span>
      <span class="switch__label md-body-medium">Reach from anywhere<span
        class="switch__says md-label-small">${net.canSwitch
          ? "Puts this computer on your Tailscale network. Off takes it off for every device, not just the phone"
          : "Only this computer can switch this"}</span></span>
    </label>
    ${on ? `<div class="reach">
        <img class="reach__code" src="/api/qr?via=tailnet&t=${Date.now()}"
          alt="This panel's address on your tailnet, as a code to scan" width="248" height="248">
        <div class="reach__said">
          <p class="md-body-medium">From anywhere, with Tailscale on the phone:</p>
          ${copyLine("copyTailnet", address, "Copy the address")}
          ${https.ok
            ? `<p class="md-body-small reach__note">Over https, so the phone gets the microphone and
                notifications too. Tailscale's MagicDNS has to be on in the phone's app for the name
                to resolve.</p>`
            : net.name ? `<p class="md-body-small reach__note">Or by name:
              <code class="md-mono">${escapeHtml(net.name)}</code> — the same port and key.</p>` : ""}
          ${phoneApp}
        </div>
      </div>
      ${https.ok ? "" : `<div class="settings-notice" data-state="missing">
          <p class="md-body-medium">Plain http, so the phone gets no microphone and no notifications:
            ${escapeHtml(https.message || "the certificate is still being fetched")}.</p>
          ${/admin\/dns/.test(https.message || "") ? `<a class="button button--text md-state"
              href="${escapeHtml(https.admin)}" target="_blank" rel="noopener noreferrer">Open the
              Tailscale admin console</a>` : ""}
          ${net.canSwitch ? `<button class="button button--text md-state" id="tsTls" type="button">Try https again</button>` : ""}
        </div>`}`
    : net.login ? `<p class="md-body-medium">Tailscale is waiting for you to sign in.</p>
        <a class="button button--filled md-state" href="${escapeHtml(net.login)}" target="_blank"
          rel="noopener noreferrer">Sign in to Tailscale</a>`
    : ""}
    ${permission ? `<p class="md-body-small">Tailscale only lets root switch it until you allow
        this user, once:</p>${copyLine("copyTsOperator", net.operator, "Copy the command")}` : ""}`;
  if (on) wireCopy(box, "copyTailnet", address, "Address copied");
  const retry = box.querySelector<HTMLButtonElement>("#tsTls");
  retry?.addEventListener("click", async () => {
    retry.disabled = true;
    retry.textContent = "Asking Tailscale…";
    let answer;
    try {
      const response = await fetch("/api/reach/tls", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      answer = await response.json();
    } catch (error) {
      answer = { ok: false, message: "The panel did not answer" };
    }
    showSnackbar(answer?.message || (answer?.ok ? "Serving https" : "Still no certificate"));
    if (box.isConnected) paintTailnet(box, answer?.tailnet || net);
  });
  if (permission) wireCopy(box, "copyTsOperator", net.operator, "Command copied");
  // Waiting on a sign-in in another tab: look again until it lands.
  if (!on && net.login) tailnetPoll = window.setTimeout(() => refreshTailnet(box), 3000);
  const toggle = box.querySelector<HTMLInputElement>("#tailnetToggle");
  toggle?.addEventListener("change", async () => {
    toggle.disabled = true;
    let answer;
    try {
      const response = await fetch("/api/reach/tailnet", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ on: toggle.checked }),
      });
      answer = await response.json();
    } catch (error) {
      answer = { ok: false, message: "The panel did not answer" };
    }
    showSnackbar(answer?.message || (answer?.ok ? "Done" : "Could not switch it"));
    const now = answer?.tailnet;
    if (now?.login && !now.running) window.open(now.login, "_blank", "noopener");
    if (box.isConnected) paintTailnet(box, now || net, answer?.ok ? "" : answer?.message || "");
  });
}

async function refreshTailnet(box: HTMLElement) {
  if (!box.isConnected) return;
  try {
    const response = await fetch("/api/reach", { cache: "no-store" });
    const reach = await response.json();
    if (box.isConnected) paintTailnet(box, reach?.tailnet);
  } catch (error) {
    // The poll will say the panel is unreachable.
  }
}

function setSeed(hex) { app.settings.seed = hex; persist(); applyScheme(); renderSwatches(); }
