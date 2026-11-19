import { reloadState } from "../refresh.js";
import { app } from "../state.js";
import { copyText } from "../ui/clipboard.js";
import { escapeHtml } from "../ui/format.js";
import { ICON } from "../ui/icons.js";
import { showSnackbar } from "../ui/snackbar.js";
import { ownedFor } from "./owned.js";
import { aboutCard, switchRow } from "./usage.js";

/* ==========================================================================
   Sandbox — a tab of its own. Running a session in a container is a choice
   with enough to it (what it reaches, which local devices it may talk to, the
   one thing it cannot protect) that a card squeezed into Details turned into
   a wall of small print. Here each part gets a card and full sentences.

   Two halves. *This session*: the switch, what its box reaches, the local
   addresses and secrets it is given. *Every sandbox*: what all boxes share —
   whether new sessions start boxed, the SSH key, the secrets themselves and the
   packages in the image — which only this computer may change.
   The mechanics are in watchtower/sandbox.py and the README.
   ========================================================================== */

const PACKAGE_HINT = { apt: "libusb-1.0-0", pip: "pyserial", npm: "prettier" };

const removable = (entry: string, attr: string, locked: boolean, extra = "", title = "") =>
  `<span class="sbx-lan__item${extra}"${title ? ` title="${escapeHtml(title)}"` : ""}><span class="md-mono">${
    escapeHtml(entry)}</span><button type="button" class="sbx-lan__remove md-state" ${attr}="${escapeHtml(entry)}"
    aria-label="Remove ${escapeHtml(entry)}" title="Remove"${locked ? " disabled" : ""}>×</button></span>`;

export function sandboxPanel(session) {
  const owned = ownedFor(session);
  const report = app.feed.sandbox;
  const boxedOn = !!owned.sandbox;
  // Only a session the panel runs can be in one. A terminal session is somebody
  // else's process, and a switch reading *on* over a session running loose would
  // be worse than no switch at all.
  const inTerminal = session.alive === true;
  // Off is always allowed between turns; on needs a computer that can do it.
  const lockBox = !app.feed.canSend || inTerminal || !!owned.busy || (!boxedOn && !report?.available);
  const status = boxedOn
    ? (report?.building ? "Building the sandbox image. This takes a few minutes."
      : report?.buildError ? `The sandbox image did not build: ${escapeHtml(report.buildError)}`
      : owned.boxed ? "Running in the sandbox now."
      : "On. It starts in the sandbox the next time it runs.")
    : !report?.available ? escapeHtml(report?.why || "Not available on this computer.")
    : inTerminal ? "It is running in a terminal. Hand it to Watchtower first."
    : owned.busy ? "Switch when it has finished this turn."
    : "Off. It runs like any other session.";

  const switchCard = aboutCard(ICON.shield, "Sandbox", `
      ${switchRow("sandboxToggle", "Run it in a container", status, boxedOn, lockBox)}
      <p class="about-card__body">It can change this checkout and nothing else. Your other folders,
        keys and settings are not in the box, and it cannot reach this computer over the network.</p>`,
    owned.boxed ? " about-card--ok" : "");

  return `<div class="about-tab sbx-tab">
    ${switchCard}
    ${boxedOn ? sessionPart(session) : ""}
    ${report?.available ? sharedPart() : ""}
  </div>`;
}

/* What this session's box reaches and is given. */
function sessionPart(session) {
  const owned = ownedFor(session);
  const report = app.feed.sandbox;
  // The runtime is the one the running box was started with, not the next one's.
  const gvisor = owned.boxed ? owned.boxRuntime === "runsc" : !!report?.gvisor;
  const lan: string[] = owned.lan || [];
  const missed: string[] = owned.lanMissed || [];
  const lockLan = !app.feed.canSend || !!owned.busy;
  const given: string[] = owned.secrets || [];
  const allSecrets: string[] = report?.secrets || [];
  const lockSecrets = !app.feed.thisComputer || !!owned.busy;
  const login = {
    token: "A long-lived token you saved.",
    apikey: "Your API key.",
    session: "Your current access token, renewed every few hours. It never gets the refresh token, so it cannot take over your login.",
  }[report?.login || ""] || "—";

  const facts: [string, string][] = [
    ["Files", `This checkout, read-write. Its <span class="md-mono">.git</span> settings and hooks are read-only, so it cannot leave git a command to run on this computer.`],
    ["Network", lan.length
      ? `The internet, and the ${lan.length === 1 ? "local address" : `${lan.length} local addresses`} it was allowed. This computer and the rest of your network are blocked.`
      : "The internet only. This computer and your local network are blocked."],
    ["Kernel", gvisor ? "Its own, from gVisor."
      : "Shared with this computer. Install gVisor and the box gets a kernel of its own."],
    ["Login", escapeHtml(login)],
    ["Push and pull", "From this computer. The box has none of your SSH keys, so it commits and you push."],
  ];
  const reachCard = aboutCard(ICON.info, "What it can reach", `<dl class="sbx-facts">
      ${facts.map(([key, value]) => `<div class="sbx-fact"><dt>${key}</dt><dd>${value}</dd></div>`).join("")}
    </dl>`);

  const lanCard = aboutCard(ICON.globe, "Local network", `
      <p class="about-card__body">Addresses on your network it may reach, such as a device to ssh to.
        This computer stays blocked even if you allow the subnet it is on.</p>
      ${lan.length ? `<div class="sbx-lan__list">${lan.map((entry) => removable(entry, "data-lan-remove", lockLan,
          missed.includes(entry) ? " sbx-lan__item--missed" : "",
          missed.includes(entry) ? "Did not resolve when the box started" : "")).join("")}</div>` : ""}
      <form class="sbx-lan__add" id="lanAdd">
        <input class="md-mono" name="entry" type="text" autocomplete="off" spellcheck="false"
          placeholder="192.168.2.10, 10.0.0.0/24 or device.local:22"${lockLan ? " disabled" : ""}>
        <button class="button button--tonal md-state" type="submit"${lockLan ? " disabled" : ""}>Allow</button>
      </form>`);

  // Which secrets this box gets: one switch per secret that exists.
  const secretsCard = aboutCard(ICON.gate, "Secrets for this session", allSecrets.length ? `
      <p class="about-card__body">Given to the box as environment variables. Claude is told their names
        and asked never to print them.</p>
      <div class="about-switches">${allSecrets.map((name) => switchRow(`secret-${name}`,
        `<span class="md-mono">${escapeHtml(name)}</span>`, "", given.includes(name), lockSecrets)
        .replace('type="checkbox"', `type="checkbox" data-secret="${escapeHtml(name)}"`)).join("")}</div>`
    : `<p class="about-card__body">None yet. Add one under <b>Every sandbox</b> below, such as a device's
        password, and switch it on here.</p>`);

  const warn = `<section class="sbx-warn">
      <span class="sbx-warn__icon">${ICON.flag}</span>
      <p><b>Read the diff before you run its changes here.</b> A script, Makefile, test or hook it changed
        runs with your rights when you run it on this computer. No sandbox can cover that.</p>
    </section>`;

  return `${warn}
    <div class="about-grid sbx-grid">${reachCard}${lanCard}${secretsCard}</div>`;
}

/* What every box shares. Only this computer may change it: it hands out
   secrets and decides what every sandboxed session gets. */
function sharedPart() {
  const report = app.feed.sandbox;
  const locked = !app.feed.thisComputer;
  const key = report.sshKey || "";
  const packages = report.packages || { apt: [], pip: [], npm: [] };
  const secrets: string[] = report.secrets || [];

  const newCard = aboutCard(ICON.plus, "New sessions", `
      ${switchRow("sandboxNew", "Start new sessions in the sandbox",
        "Sessions started from the New menu. The folder has to be a git repository.", report.newSessions, locked)}`);

  const keyCard = aboutCard(ICON.pair, "SSH key", key ? `
      <p class="about-card__body">The boxes' own key. Add the public half to
        <span class="md-mono">~/.ssh/authorized_keys</span> on the device, and allow its address for the session.</p>
      <div class="sbx-key md-mono">${escapeHtml(key)}</div>
      <div class="about-card__actions">
        <button class="button button--tonal md-state" type="button" id="sshCopy">${ICON.copy} Copy public key</button>
        <button class="button button--outlined md-state" type="button" id="sshDelete"${locked ? " disabled" : ""}>Remove</button>
      </div>` : `
      <p class="about-card__body">One key for every box, made here and kept in the sandbox's own home.
        Your own keys are never in the box.</p>
      <div class="about-card__actions">
        <button class="button button--tonal md-state" type="button" id="sshCreate"${locked ? " disabled" : ""}>${ICON.plus} Create key</button>
      </div>`);

  const secretStore = aboutCard(ICON.gate, "Secrets", `
      <p class="about-card__body">Passwords and tokens a session can be given, so they never have to be typed
        into the chat. The values stay on this computer and are never shown again.</p>
      ${secrets.length ? `<div class="sbx-lan__list">${secrets.map((name) =>
        removable(name, "data-secret-remove", locked)).join("")}</div>` : ""}
      <form class="sbx-secret__add" id="secretAdd">
        <input class="md-mono" name="name" type="text" autocomplete="off" spellcheck="false"
          placeholder="DEVICE_PASSWORD"${locked ? " disabled" : ""}>
        <input name="value" type="password" autocomplete="new-password" placeholder="Value"${locked ? " disabled" : ""}>
        <button class="button button--tonal md-state" type="submit"${locked ? " disabled" : ""}>Save</button>
      </form>`);

  const kinds = (["apt", "pip", "npm"] as const);
  const packagesCard = aboutCard(ICON.download, "Packages", `
      <p class="about-card__body">Installed into the image for every box, so they are there from the start.
        Changing them rebuilds the image in the background; running boxes get them when they next start.</p>
      ${kinds.filter((k) => packages[k].length).map((k) => `<div class="sbx-pkg">
          <span class="sbx-pkg__kind">${k}</span>
          <div class="sbx-lan__list">${packages[k].map((name) =>
            removable(name, `data-pkg-remove-${k}`, locked)).join("")}</div>
        </div>`).join("")}
      ${report.building ? `<p class="about-card__body sbx-note">Building the image…</p>`
        : report.buildError ? `<p class="about-card__body sbx-note sbx-note--bad">Build failed: ${escapeHtml(report.buildError)}</p>` : ""}
      <form class="sbx-pkg__add" id="pkgAdd">
        <select name="kind"${locked ? " disabled" : ""}>${kinds.map((k) => `<option value="${k}">${k}</option>`).join("")}</select>
        <input class="md-mono" name="name" type="text" autocomplete="off" spellcheck="false"
          placeholder="${PACKAGE_HINT.apt}"${locked ? " disabled" : ""}>
        <button class="button button--tonal md-state" type="submit"${locked ? " disabled" : ""}>Add</button>
      </form>`);

  return `<h3 class="about-section-title">${ICON.shield}Every sandbox</h3>
    ${locked ? `<p class="about-switch__note">Only this computer can change these.</p>` : ""}
    <div class="about-grid sbx-grid">${newCard}${keyCard}${secretStore}${packagesCard}</div>`;
}

/* --- wiring */

async function post(url: string, body: object, control?: HTMLButtonElement | HTMLInputElement | null) {
  if (control) control.disabled = true;
  try {
    const response = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    showSnackbar(data.message || (data.ok ? "Saved" : "That did not work"));
    return data;
  } catch (error) {
    showSnackbar("Could not reach the server");
    return {};
  } finally {
    if (control) control.disabled = false;
    reloadState();
  }
}

export function wireSandbox(pane: HTMLElement, session) {
  const id = session.sessionId;
  const on = (selector: string, event: string, handler: (el: HTMLElement, e: Event) => void) => {
    for (const el of pane.querySelectorAll<HTMLElement>(selector)) el.addEventListener(event, (e) => handler(el, e));
  };
  const field = (form: HTMLFormElement, name: string) => form.elements.namedItem(name) as HTMLInputElement;

  // Local addresses: the whole list goes each time; the server checks every
  // entry and restarts the box to apply it.
  const lanNow = () => [...(ownedFor(session).lan || [])];
  on("[data-lan-remove]", "click", (el) =>
    post("/api/owned/sandbox/lan", { sessionId: id, allow: lanNow().filter((x) => x !== el.dataset.lanRemove) },
      el as HTMLButtonElement));
  on("#lanAdd", "submit", async (el, e) => {
    e.preventDefault();
    const input = field(el as HTMLFormElement, "entry");
    const entry = input.value.trim();
    if (!entry) return;
    const data = await post("/api/owned/sandbox/lan", { sessionId: id, allow: [...lanNow(), entry] },
      el.querySelector("button"));
    if (data.ok) input.value = "";
  });

  // This session's secrets.
  on("[data-secret]", "change", async (el) => {
    const box = el as HTMLInputElement;
    const given = new Set(ownedFor(session).secrets || []);
    if (box.checked) given.add(box.dataset.secret); else given.delete(box.dataset.secret);
    const data = await post("/api/owned/sandbox/secrets", { sessionId: id, names: [...given] }, box);
    if (!data.ok) box.checked = !box.checked;
  });

  // Every sandbox.
  on("#sandboxNew", "change", async (el) => {
    const box = el as HTMLInputElement;
    const data = await post("/api/sandbox/settings", { newSessions: box.checked }, box);
    if (!data.ok) box.checked = !box.checked;
  });
  on("#sshCreate", "click", (el) => post("/api/sandbox/sshkey", { action: "create" }, el as HTMLButtonElement));
  on("#sshDelete", "click", (el) => {
    if (confirm("Remove the sandbox's SSH key? Devices that trust it will stop letting boxes in.")) {
      post("/api/sandbox/sshkey", { action: "delete" }, el as HTMLButtonElement);
    }
  });
  on("#sshCopy", "click", () => copyText(app.feed.sandbox?.sshKey || "", "Public key copied"));
  on("[data-secret-remove]", "click", (el) => {
    const name = el.dataset.secretRemove;
    if (confirm(`Remove ${name}? Every session it was given to loses it.`)) {
      post("/api/sandbox/secrets", { action: "delete", name }, el as HTMLButtonElement);
    }
  });
  on("#secretAdd", "submit", async (el, e) => {
    e.preventDefault();
    const form = el as HTMLFormElement;
    const name = field(form, "name");
    const value = field(form, "value");
    const data = await post("/api/sandbox/secrets",
      { action: "set", name: name.value.trim(), value: value.value }, form.querySelector("button"));
    // The value goes from the page whether it was saved or not.
    value.value = "";
    if (data.ok) name.value = "";
  });
  const packagesNow = () => {
    const p = app.feed.sandbox?.packages || { apt: [], pip: [], npm: [] };
    return { apt: [...p.apt], pip: [...p.pip], npm: [...p.npm] };
  };
  for (const kind of ["apt", "pip", "npm"] as const) {
    on(`[data-pkg-remove-${kind}]`, "click", (el) => {
      const next = packagesNow();
      next[kind] = next[kind].filter((x) => x !== el.getAttribute(`data-pkg-remove-${kind}`));
      post("/api/sandbox/packages", next, el as HTMLButtonElement);
    });
  }
  const pkgForm = pane.querySelector<HTMLFormElement>("#pkgAdd");
  if (pkgForm) {
    const kind = pkgForm.elements.namedItem("kind") as HTMLSelectElement;
    const name = field(pkgForm, "name");
    kind.addEventListener("change", () => { name.placeholder = PACKAGE_HINT[kind.value] || ""; });
    pkgForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const entry = name.value.trim();
      if (!entry) return;
      const next = packagesNow();
      next[kind.value] = [...next[kind.value], entry];
      const data = await post("/api/sandbox/packages", next, pkgForm.querySelector("button"));
      if (data.ok) name.value = "";
    });
  }
}
