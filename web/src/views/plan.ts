import { spend } from "../state.js";
import { ago, escapeHtml, tokens } from "../ui/format.js";

/* ==========================================================================
   The plan — how much of a subscription has gone, which is the one figure in this
   panel that no file on this machine knows. The server runs Claude Code's own
   `/usage` for it, so the reading is the same one the terminal would give you.
   ========================================================================== */
export const planScrim = document.getElementById("planScrim");
export const planButton = document.getElementById("planButton");
const planChipText = document.getElementById("planChipText");

/* Five seconds and a process per reading, and a figure that moves in points
   over hours: so it is read on the clock rather than on the poll, and not at
   all while nobody is looking at the page. */
const PLAN_POLL_MS = 300_000;
let planBusy = false;
let planPolledAt = 0;
let planGone = false;      // the panel is read-only, so there is nothing to ask

export async function fetchPlan(force) {
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
    spend.plan = await response.json();
    // A reading already in flight elsewhere: come back for its answer shortly
    // rather than waiting on this request.
    if (spend.plan.reading) planPolledAt = Date.now() - PLAN_POLL_MS + 4000;
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
  const limits = spend.plan?.limits || [];
  const session = limits.find((l) => /session/i.test(l.name));
  const week = limits.find((l) => /week/i.test(l.name) && !/\(/.test(l.name))
    || limits.find((l) => /week.*all models/i.test(l.name))
    || limits.find((l) => /week/i.test(l.name));
  return [session, week].filter(Boolean);
}

function paintPlanChip() {
  if (planGone) { planButton.hidden = true; return; }
  planButton.hidden = false;
  planButton.dataset.reading = planBusy || spend.plan?.reading ? "1" : "0";
  const shown = planHeadline();
  if (!shown.length) {
    planChipText.textContent = spend.plan?.ok === false ? "plan?" : "plan";
    planButton.dataset.tight = "0";
    planButton.title = spend.plan?.message || "How much of your plan has gone";
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

export function paintPlanDialog() {
  const head = document.getElementById("planHead");
  const body = document.getElementById("planBody");
  const age = document.getElementById("planAge");
  if (!spend.plan) {
    head.textContent = "Reading your usage — this takes a few seconds.";
    body.innerHTML = "";
    age.textContent = "";
    return;
  }
  head.textContent = spend.plan.headline || spend.plan.message || "";
  const limits = spend.plan.limits || [];
  body.innerHTML = `
    ${limits.length ? limits.map(planBar).join("")
      : spend.plan.text ? `<p class="plan-raw md-body-small">${escapeHtml(spend.plan.text)}</p>`
      : `<p class="md-body-medium">${escapeHtml(spend.plan.message || "Nothing came back.")}</p>`}
    ${(spend.plan.blocks || []).map((block) => `<section class="plan-block">
      <h3 class="plan-block__title md-title-small">${escapeHtml(block.title)}</h3>
      ${block.lines.length ? `<ul class="md-body-small">${block.lines
        .map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>` : ""}
    </section>`).join("")}
    <p class="plan-note md-body-small">Read by running Claude Code's own
      <span class="md-mono">/usage</span>, so it is the same answer the terminal gives — and it
      costs no tokens. The panel never touches your credentials.${limits.length && spend.plan.message
        ? ` The last refresh failed: ${escapeHtml(spend.plan.message)}` : ""}</p>`;
  age.textContent = spend.plan.at
    ? planBusy || spend.plan.reading ? "reading…" : `read ${ago(Date.now() / 1000 - spend.plan.at)}`
    : "";
}

export function openPlan(open) {
  planScrim.dataset.open = String(open);
  if (!open) { planButton.focus(); return; }
  paintPlanDialog();
  document.getElementById("closePlan").focus();
  // Opening it is asking for the figure, so a stale one is refreshed there and
  // then rather than at the next tick of the clock.
  if (!spend.plan || Date.now() / 1000 - (spend.plan.at || 0) > PLAN_POLL_MS / 1000) fetchPlan(true);
}
