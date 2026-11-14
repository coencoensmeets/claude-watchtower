/* ------------------------------------------------------- + New → Describe it… */

/* You say what you want to do; Claude picks the project it belongs in, or
   names a new one; you see where and why, change it if you like, and press
   Start. The deciding is a quick headless model on the server (see
   watchtower/route.py); nothing is made or started until Start.

   Offered only while the Watchtower tools are switched on in Settings →
   Claude skills — this is part of letting Claude drive the panel. */

import { escapeHtml } from "./format.js";
import { ICON } from "./icons.js";
import { showSnackbar } from "./snackbar.js";
import { browseFolder } from "./folders.js";

type Plan = {
  action: "open" | "create";
  folder: string;
  shown: string;
  reason: string;
  title: string;
  setup: string;
  candidates?: { path: string; shown: string }[];
};

const scrim = document.getElementById("routeScrim");
const field = document.getElementById("routeField") as HTMLTextAreaElement;
const planBox = document.getElementById("routePlan");
const go = document.getElementById("routeGo") as HTMLButtonElement;

let plan: Plan | null = null;
let asked = "";
let busy = false;
let started: ((sessionId: string) => void) | null = null;
let enabled = false;

/* Whether to offer it. Read from the same list Settings shows, refreshed each
   time the + New menu opens so a switch flipped there counts from the next. */
export const routeEnabled = () => enabled;
export async function refreshRouteEnabled() {
  try {
    const data = await (await fetch("/api/skills")).json();
    enabled = !!(data.items || []).find((i) => i.id === "mcp")?.on;
  } catch {
    enabled = false;
  }
}
refreshRouteEnabled();

export function openRoute(onStarted: (sessionId: string) => void) {
  started = onStarted;
  plan = null;
  asked = "";
  planBox.hidden = true;
  planBox.innerHTML = "";
  setGo("Find the place");
  scrim.dataset.open = "true";
  setTimeout(() => field.focus(), 60);
}

function closeRoute() {
  scrim.dataset.open = "false";
  busy = false;
}

function setGo(label: string, disabled = false) {
  go.textContent = label;
  go.disabled = disabled;
}

/* The decision, as a card: where, why, and a way to send it somewhere else. */
function drawPlan() {
  if (!plan) return;
  const isNew = plan.action === "create";
  const options = (plan.candidates || []).filter((c) => c.path !== plan.folder);
  planBox.hidden = false;
  planBox.dataset.action = plan.action;
  planBox.innerHTML = `
    <div class="route-plan__card">
      <span class="route-plan__icon">${isNew ? ICON.sparkle : ICON.folder}</span>
      <div class="route-plan__text">
        <div class="route-plan__kind md-label-medium">${isNew ? "New project" : "Open in"}</div>
        <div class="route-plan__where md-title-medium md-mono">${escapeHtml(plan.shown)}</div>
        ${plan.reason ? `<div class="route-plan__why md-body-medium">${escapeHtml(plan.reason)}</div>` : ""}
        ${isNew && plan.setup ? `<div class="route-plan__setup md-body-small">${ICON.sparkle}<span>${escapeHtml(plan.setup)}</span></div>` : ""}
      </div>
    </div>
    <div class="route-plan__change">
      <select class="route-plan__select md-body-medium" id="routeOther" aria-label="Start somewhere else">
        <option value="">Somewhere else…</option>
        ${options.map((c) => `<option value="${escapeHtml(c.path)}">${escapeHtml(c.shown)}</option>`).join("")}
        <option value="__browse">Browse for a folder…</option>
      </select>
    </div>`;
  planBox.querySelector<HTMLSelectElement>("#routeOther").addEventListener("change", async (event) => {
    const pick = (event.target as HTMLSelectElement).value;
    if (!pick) return;
    let path = pick;
    if (pick === "__browse") {
      scrim.dataset.open = "false";
      path = await browseFolder();
      scrim.dataset.open = "true";
      if (!path) { drawPlan(); return; }
    }
    const shown = (plan.candidates || []).find((c) => c.path === path)?.shown || path;
    plan = { ...plan, action: "open", folder: path, shown, setup: "", reason: "Your choice." };
    drawPlan();
  });
  setGo(isNew ? "Create and start" : "Start");
}

async function decide() {
  const text = field.value.trim();
  if (!text) { field.focus(); return; }
  busy = true;
  setGo("Thinking…", true);
  planBox.hidden = false;
  planBox.dataset.action = "";
  planBox.innerHTML = `<div class="route-plan__thinking md-body-medium"><span class="route-plan__dots"><i></i><i></i><i></i></span>Looking through your projects…</div>`;
  try {
    const response = await fetch("/api/route", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
    });
    const data = await response.json().catch(() => ({}));
    if (!data.ok || !data.plan) {
      planBox.innerHTML = `<div class="route-plan__error md-body-medium">${escapeHtml(data.message || "Claude could not decide")}</div>`;
      setGo("Try again");
      return;
    }
    plan = data.plan;
    asked = text;
    drawPlan();
  } catch {
    planBox.innerHTML = `<div class="route-plan__error md-body-medium">Could not reach the server</div>`;
    setGo("Try again");
  } finally {
    busy = false;
  }
}

async function start() {
  if (!plan) return;
  busy = true;
  setGo(plan.action === "create" ? "Creating…" : "Starting…", true);
  try {
    const response = await fetch("/api/route/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: asked, action: plan.action, folder: plan.folder, setup: plan.setup, title: plan.title }),
    });
    const data = await response.json().catch(() => ({}));
    showSnackbar(data.message || (data.ok ? "Started" : "That did not start"));
    if (data.ok && data.sessionId) {
      closeRoute();
      field.value = "";
      started?.(data.sessionId);
    } else {
      setGo(plan.action === "create" ? "Create and start" : "Start");
    }
  } catch {
    showSnackbar("Could not reach the server");
    setGo("Start");
  } finally {
    busy = false;
  }
}

go.addEventListener("click", () => {
  if (busy) return;
  // Edited after Claude answered: the answer was for other words, so ask again.
  if (plan && field.value.trim() === asked) start(); else decide();
});
field.addEventListener("input", () => {
  if (plan && field.value.trim() !== asked) setGo("Find the place again");
});
field.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    go.click();
  }
});
document.getElementById("routeCancel").addEventListener("click", closeRoute);
scrim.addEventListener("click", (event) => { if (event.target === scrim) closeRoute(); });
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || scrim.dataset.open !== "true") return;
  event.stopImmediatePropagation();
  closeRoute();
}, true);
