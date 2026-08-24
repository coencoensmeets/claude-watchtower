import { ago, escapeHtml } from "../ui/format.js";

/* ==========================================================================
   The plan — how much of a subscription has gone, which is the one figure in this
   panel that no file on this machine knows. The server runs Claude Code's own
   `/usage` for it, so the reading is the same one the terminal would give you.
   ========================================================================== */
export const planScrim = document.getElementById("planScrim");
const planButton = document.getElementById("planButton");
const planChipText = document.getElementById("planChipText");

/* Five seconds and a process per reading, and a figure that moves in points
   over hours: so it is read on the clock rather than on the poll, and not at
   all while nobody is looking at the page. */
const PLAN_POLL_MS = 300_000;
export let plan = null;
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
      <span>${limit.resets ? `resets ${escapeHtml(limit.resets)}` : "nothing used yet"}</span>
    </div>
  </div>`;
}

function paintPlanDialog() {
  const head = document.getElementById("planHead");
  const body = document.getElementById("planBody");
  const age = document.getElementById("planAge");
  if (!plan) {
    head.textContent = "Reading your usage…";
    head.hidden = false;
    body.innerHTML = "";
    age.textContent = "";
    return;
  }
  const limits = plan.limits || [];
  // `/usage`'s own headline only ever says you are on a subscription, which the
  // dialog is already about. It is worth the line only when something failed.
  head.textContent = limits.length ? (plan.message || "") : (plan.message || plan.headline || "");
  head.hidden = !head.textContent;
  body.innerHTML = `
    ${limits.length ? limits.map(planBar).join("")
      : plan.text ? `<p class="plan-raw md-body-small">${escapeHtml(plan.text)}</p>`
      : `<p class="md-body-medium">Nothing came back.</p>`}
    ${(plan.blocks || []).map((block) => {
      // Figures only. `/usage` pads each block with a sentence or two of
      // caveat, and a line carrying no number is one of those.
      const lines = block.lines.filter((line) => /\d/.test(line));
      return lines.length ? `<section class="plan-block">
        <h3 class="plan-block__title md-title-small">${escapeHtml(block.title)}</h3>
        <ul class="md-body-small">${lines
          .map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>
      </section>` : "";
    }).join("")}`;
  age.textContent = plan.at
    ? planBusy || plan.reading ? "reading…" : `read ${ago(Date.now() / 1000 - plan.at)}`
    : "";
}

export function openPlan(open) {
  planScrim.dataset.open = String(open);
  if (!open) { planButton.focus(); return; }
  paintPlanDialog();
  document.getElementById("closePlan").focus();
  // Opening it is asking for the figure, so a stale one is refreshed there and
  // then rather than at the next tick of the clock.
  if (!plan || Date.now() / 1000 - (plan.at || 0) > PLAN_POLL_MS / 1000) fetchPlan(true);
}

planButton.addEventListener("click", () => openPlan(true));
document.getElementById("closePlan").addEventListener("click", () => openPlan(false));
document.getElementById("planRefresh").addEventListener("click", () => { fetchPlan(true); paintPlanDialog(); });
planScrim.addEventListener("click", (event) => { if (event.target === planScrim) openPlan(false); });
