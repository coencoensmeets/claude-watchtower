/* The settings page: the palette, the contrast, and what is worth a notification. */

import { Hct, TonalPalette, argbFromHex, hexFromArgb } from "/vendor/material-color-utilities.js";
import { applyScheme, openSettings, persist } from "../main.js";
import { app } from "../state.js";
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
  custom.querySelector("input").addEventListener("input", (event) => setSeed(event.target.value));
  swatchRow.appendChild(custom);
  const hct = Hct.fromInt(argbFromHex(app.settings.seed));
  detailPane.querySelector("#seedReadout").innerHTML = `Base <span class="code">${escapeHtml(app.settings.seed.toUpperCase())}</span> · hue ${Math.round(hct.hue)}° · chroma ${Math.round(hct.chroma)}`;
}
/* The light/dark switch, which sits with the rest of the appearance settings
   rather than in the app bar. It was the only setting in the bar, and a switch
   up there read as a thing to flip often — it is not; it is picked once and
   left, like the base colour it belongs beside. */
export function syncTheme() {
  const toggle = detailPane.querySelector("#themeToggle");
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
  for (const input of box.querySelectorAll("[data-notify]")) {
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
  const box = detailPane.querySelector("#notifyHint");
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
    <div class="tab-panel">
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
      <!-- Which kinds of notification are wanted at all, for every session at
           once. The switches in a session's own Details tab choose which
           sessions may use the kinds turned on here. -->
      <section class="section">
        <h3 class="section__title md-title-small">Notifications</h3>
        <p class="md-body-medium">Raise a desktop notification when a session…</p>
        <div class="notify-grid" id="notifySwitches"></div>
        <div class="notify-permission" id="notifyHint" hidden></div>
      </section>
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
      </section>
      <section class="section">
        <button class="button button--text md-state" id="resetTheme">Reset colours</button>
      </section>
    </div>`;
}

/* Painted once and then left alone: every control on it keeps its own state,
   and a poll rebuilding the pane underneath would take the focus off whatever
   was just pressed. */
export function paintSettings() {
  if (detailPane.dataset.signature === "settings:page") return;
  detailPane.dataset.signature = "settings:page";
  detailPane.innerHTML = settingsPage();
  syncTheme();
  renderSwatches();
  renderContrast();
  renderNotify();
  detailPane.querySelector("#themeToggle").addEventListener("change", (event) => {
    app.settings.dark = event.target.checked;
    persist();
    applyScheme();
  });
  const editorToggle = detailPane.querySelector("#editorToggle");
  editorToggle.checked = app.settings.showEditor;
  editorToggle.addEventListener("change", (event) => {
    app.settings.showEditor = event.target.checked;
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

function setSeed(hex) { app.settings.seed = hex; persist(); applyScheme(); renderSwatches(); }
