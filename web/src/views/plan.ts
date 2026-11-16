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
    .map((l) => `<span class="plan-mini">
      <span class="plan-mini__k">${/session/i.test(l.name) ? "5h" : "wk"}</span>
      <span class="plan-mini__bar" data-band="${planBand(l.percent)}"><i style="width: ${l.percent ? Math.max(8, l.percent) : 0}%"></i></span>
      <span class="plan-pct" data-band="${planBand(l.percent)}">${l.percent}%</span>
    </span>`)
    .join("");
  const worst = Math.max(...shown.map((l) => l.percent));
  planButton.dataset.tight = String(planBand(worst));
  planButton.title = `${shown.map((l) => `${l.name}: ${l.percent}% used`).join(", ")} — press for the rest`;
}

/* One scale for every plan figure: room, tight, nearly gone. */
const planBand = (percent) => (percent >= 90 ? 2 : percent >= 75 ? 1 : 0);

/* -------------------------------------------------------------- reset times */
/* `/usage` says when a limit resets as wall time in a named zone — "Nov 14,
   3:09pm (Europe/Amsterdam)", or just "3:09pm (…)" for later today. Turned into
   a moment so the dialog can count down to it and say how far through the
   window you are. Anything it cannot read stays as the words it came as. */
const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];

function zoneOffset(zone, at) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: zone, hourCycle: "h23", year: "numeric", month: "numeric", day: "numeric",
    hour: "numeric", minute: "numeric", second: "numeric",
  }).formatToParts(new Date(at));
  const get = (type) => Number(parts.find((p) => p.type === type)?.value);
  return Date.UTC(get("year"), get("month") - 1, get("day"), get("hour") % 24, get("minute"), get("second")) - at;
}

function resetMoment(text) {
  const m = /^(?:([A-Za-z]{3})[a-z]*\s+(\d{1,2}),?\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(?:\(([^)]+)\))?/i.exec(text || "");
  if (!m) return null;
  const [, mon, day, hh, mm, ampm, zone] = m;
  let hour = Number(hh) % 12 + (/pm/i.test(ampm) ? 12 : 0);
  const now = Date.now();
  // Wall time in the zone, read as though it were UTC, then shifted by the
  // zone's offset at about that moment.
  const wall = (y, mo, d) => Date.UTC(y, mo, d, hour, Number(mm || 0));
  const toUtc = (guess) => {
    if (!zone) return guess + new Date(guess).getTimezoneOffset() * 60_000;
    try { return guess - zoneOffset(zone, guess - zoneOffset(zone, guess)); } catch { return guess + new Date(guess).getTimezoneOffset() * 60_000; }
  };
  const today = new Date(now);
  let at;
  if (mon) {
    const month = MONTHS.indexOf(mon.toLowerCase());
    if (month < 0) return null;
    at = toUtc(wall(today.getUTCFullYear(), month, Number(day)));
    // "Jan 2" read in late December is next year's.
    if (at < now - 180 * 86_400_000) at = toUtc(wall(today.getUTCFullYear() + 1, month, Number(day)));
  } else {
    at = toUtc(wall(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()));
    if (at < now - 3_600_000) at += 86_400_000;
  }
  return Number.isFinite(at) ? at : null;
}

/* "in 1h 12m", "in 3d 4h": the two largest units, which is all a countdown
   to a reset needs. */
function until(ms) {
  const mins = Math.max(0, Math.round(ms / 60_000));
  if (mins < 1) return "any moment";
  if (mins < 60) return `in ${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `in ${hours}h${mins % 60 ? ` ${mins % 60}m` : ""}`;
  return `in ${Math.floor(hours / 24)}d${hours % 24 ? ` ${hours % 24}h` : ""}`;
}

function clockText(at) {
  const when = new Date(at);
  const sameDay = when.toDateString() === new Date().toDateString();
  return when.toLocaleString(undefined, sameDay
    ? { hour: "numeric", minute: "2-digit" }
    : { weekday: "short", hour: "numeric", minute: "2-digit" });
}

/* How long each kind of window runs, so the dialog can say where an even pace
   would have you by now. A limit it does not know the length of goes without. */
function windowLength(name) {
  if (/session/i.test(name)) return 5 * 3_600_000;
  if (/week/i.test(name)) return 7 * 86_400_000;
  return 0;
}

/* "Current week (all models)" → "This week" and "All models": the dialog is
   already about now, so "current" is the one word it can do without. */
function limitNames(name) {
  const m = /^(?:current\s+)?(.*?)\s*(?:\(([^)]+)\))?$/i.exec(name.trim());
  const main = (m?.[1] || name).replace(/^week$/i, "This week").replace(/^session$/i, "This session");
  return { main: main.charAt(0).toUpperCase() + main.slice(1), scope: m?.[2] || "" };
}

function limitFacts(limit) {
  const at = resetMoment(limit.resets);
  const span = windowLength(limit.name);
  const left = at ? at - Date.now() : null;
  const pace = at && span && left <= span ? Math.min(100, Math.max(0, Math.round((1 - left / span) * 100))) : null;
  return { at, left, pace, names: limitNames(limit.name), tight: planBand(limit.percent) };
}

function paceWords(limit, pace) {
  if (pace == null) return "";
  const gap = limit.percent - pace;
  if (Math.abs(gap) <= 5) return "On an even pace";
  return gap > 0 ? `${gap} points ahead of an even pace` : `${-gap} points under an even pace`;
}

function planMeter(limit, facts, big) {
  return `<div class="wt-meter${big ? "" : " wt-meter--thin"}" data-band="${facts.tight}" role="meter"
      aria-valuemin="0" aria-valuemax="100" aria-valuenow="${limit.percent}" aria-label="${escapeHtml(limit.name)}">
    <span class="wt-meter__fill" style="width: ${limit.percent ? Math.max(limit.percent, big ? 3 : 1.5) : 0}%">${big && limit.percent >= 14 ? `${limit.percent}%` : ""}</span>
    ${facts.pace != null ? `<i class="wt-meter__pace" style="left: ${Math.min(98, Math.max(2, facts.pace))}%" title="An even pace would be at ${facts.pace}% by now"></i>` : ""}
  </div>`;
}

function resetLine(limit, facts) {
  if (facts.at) {
    return `<span class="plan-reset">${ICON_CLOCK}<b>Resets ${escapeHtml(until(facts.left))}</b>
      <span>${escapeHtml(clockText(facts.at))}</span></span>`;
  }
  return `<span class="plan-reset">${ICON_CLOCK}<span>${limit.resets
    ? `Resets ${escapeHtml(limit.resets)}` : "Nothing used yet"}</span></span>`;
}

const ICON_CLOCK = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>`;

/* The limit that bites first gets the room: a big figure, a tall meter with the
   even-pace mark on it, and the countdown. */
function planHero(limit) {
  const facts = limitFacts(limit);
  const words = paceWords(limit, facts.pace);
  return `<section class="plan-hero" data-band="${facts.tight}">
    <div class="plan-hero__top">
      <div>
        <div class="plan-hero__name">${escapeHtml(facts.names.main)}${facts.names.scope
          ? ` <span class="plan-scope">${escapeHtml(facts.names.scope)}</span>` : ""}</div>
        ${resetLine(limit, facts)}
      </div>
      <div class="plan-hero__n md-mono">${limit.percent}<small>% used</small></div>
    </div>
    ${planMeter(limit, facts, true)}
    ${words ? `<div class="plan-hero__pace"><i></i>${escapeHtml(words)}</div>` : ""}
  </section>`;
}

function planCard(limit) {
  const facts = limitFacts(limit);
  return `<section class="plan-card" data-band="${facts.tight}">
    <div class="plan-card__name">${escapeHtml(facts.names.main)}${facts.names.scope
      ? `<span class="plan-scope">${escapeHtml(facts.names.scope)}</span>` : ""}</div>
    <div class="plan-card__n md-mono">${limit.percent}<small>%</small></div>
    ${planMeter(limit, facts, false)}
    ${resetLine(limit, facts)}
  </section>`;
}

/* ------------------------------------------------- what is using the plan */
/* `/usage` reports the same breakdown over more than one stretch of time. Which
   one is showing survives the dialog repainting under you. */
let planWindow = 0;

function splitTitle(title) {
  const [head, ...rest] = title.split("·").map((part) => part.trim());
  return { head, rest };
}

/* A line is either a share with advice ("55%  Start fresh … · run /clear"), a
   list of top names ("Top skills: /a 4%, /b 2%"), or a caveat. */
function readBlock(block) {
  const shares = [], tops = [], notes = [];
  for (const line of block.lines) {
    const share = /^\s*(\d{1,3})%\s+(.*)$/.exec(line);
    const top = /^\s*Top ([^:]+):\s*(.*)$/i.exec(line);
    if (share) {
      const [what, ...how] = share[2].split("·").map((s) => s.trim());
      shares.push({ percent: Number(share[1]), what, how: how.join(" · ") });
    } else if (top) {
      const items = top[2].split(/,\s*/).map((item) => {
        const m = /^(.*?)\s+(\d{1,3})%$/.exec(item.trim());
        return m ? { name: m[1], percent: Number(m[2]) } : { name: item.trim(), percent: null };
      }).filter((item) => item.name);
      tops.push({ kind: top[1].trim(), items });
    } else if (line.trim()) {
      notes.push(line.trim());
    }
  }
  return { shares, tops, notes };
}

function contributions(blocks) {
  const windows = blocks.filter((block) => /^last\b/i.test(block.title));
  const caveats = blocks.filter((block) => !/^last\b/i.test(block.title))
    .flatMap((block) => block.lines).filter((line) => line.trim());
  if (!windows.length) return "";
  if (planWindow >= windows.length) planWindow = 0;
  const chosen = windows[planWindow];
  const { head, rest } = splitTitle(chosen.title);
  const read = readBlock(chosen);
  const topShare = Math.max(...read.shares.map((s) => s.percent), 1);
  return `<section class="plan-why">
    <div class="plan-why__head">
      <div>
        <h3 class="plan-why__title">What is using it</h3>
        <p class="plan-why__sub">${rest.map((part) => `<span>${escapeHtml(part)}</span>`).join("")}${
          windows.length < 2 ? `<span>${escapeHtml(head)}</span>` : ""}</p>
      </div>
      ${windows.length > 1 ? `<div class="segmented plan-why__pick" role="group" aria-label="Over what stretch">${windows
        .map((block, i) => `<button type="button" class="segmented__item md-state md-label-large"
          data-plan-window="${i}" aria-pressed="${i === planWindow}">${escapeHtml(splitTitle(block.title).head.replace(/^last\s+/i, ""))}</button>`).join("")}</div>` : ""}
    </div>
    ${read.shares.length ? `<div class="plan-shares">${read.shares.map((s, i) => `<div class="plan-share">
      <div class="plan-share__n md-mono">${s.percent}<small>%</small></div>
      <div class="plan-share__body">
        <div class="plan-share__what">${escapeHtml(s.what)}</div>
        ${s.how ? `<div class="plan-share__how">${linkCommands(s.how)}</div>` : ""}
        <div class="plan-share__bar"><span style="width: ${Math.max(2, s.percent / topShare * 100)}%; --c: ${i === 0 ? "var(--viz-1)" : "var(--viz-muted)"}"></span></div>
      </div>
    </div>`).join("")}</div>` : ""}
    ${read.tops.length ? `<dl class="plan-tops">${read.tops.map((top) => `
      <dt>${escapeHtml(top.kind.charAt(0).toUpperCase() + top.kind.slice(1))}</dt>
      <dd>${top.items.map((item) => `<span class="plan-top"><span class="md-mono">${escapeHtml(item.name)}</span>${
        item.percent != null ? `<b class="md-mono">${item.percent}%</b>` : ""}</span>`).join("")}</dd>`).join("")}
    </dl>` : ""}
    ${[...caveats, ...read.notes].length ? `<p class="plan-why__note">${escapeHtml([...caveats, ...read.notes].join(" · "))}</p>` : ""}
  </section>`;
}

/* "run /clear" — the command is the advice, so it is set as one. */
function linkCommands(text) {
  return escapeHtml(text).replace(/(^|\s)(\/[a-z][\w:-]*)/gi, `$1<kbd>$2</kbd>`);
}

function paintPlanDialog() {
  const head = document.getElementById("planHead");
  const body = document.getElementById("planBody");
  const age = document.getElementById("planAge");
  if (!plan) {
    head.textContent = "Reading your usage…";
    head.hidden = false;
    body.innerHTML = `<div class="plan-skeleton"><i></i><i></i><i></i></div>`;
    age.textContent = "";
    return;
  }
  const limits = plan.limits || [];
  // `/usage`'s own headline only ever says you are on a subscription, which the
  // dialog is already about. It is worth the line only when something failed.
  head.textContent = limits.length ? (plan.message || "") : (plan.message || plan.headline || "");
  head.hidden = !head.textContent;
  // The session's own allowance leads, being the one that runs out within the
  // hour; failing that, whichever is fullest.
  const lead = limits.find((l) => /session/i.test(l.name))
    || [...limits].sort((a, b) => b.percent - a.percent)[0];
  const others = limits.filter((l) => l !== lead);
  body.innerHTML = `
    ${lead ? planHero(lead) : plan.text
      ? `<p class="plan-raw md-body-small">${escapeHtml(plan.text)}</p>`
      : `<p class="md-body-medium">Nothing came back.</p>`}
    ${others.length ? `<div class="plan-cards">${others.map(planCard).join("")}</div>` : ""}
    ${contributions(plan.blocks || [])}`;
  age.textContent = plan.at
    ? planBusy || plan.reading ? "reading…" : `read ${ago(Date.now() / 1000 - plan.at)}`
    : "";
}

/* While it is open the countdowns tick: once a minute is as fine as they go. */
let planTick = 0;

export function openPlan(open) {
  planScrim.dataset.open = String(open);
  clearInterval(planTick);
  const body = document.getElementById("planBody");
  if (!open) { body.dataset.enter = "0"; planButton.focus(); return; }
  // The meters fill in as the dialog arrives, and only then: a repaint while it
  // is open should not replay them.
  body.dataset.enter = "1";
  setTimeout(() => { body.dataset.enter = "0"; }, 1200);
  paintPlanDialog();
  planTick = setInterval(paintPlanDialog, 30_000);
  document.getElementById("closePlan").focus();
  // Opening it is asking for the figure, so a stale one is refreshed there and
  // then rather than at the next tick of the clock.
  if (!plan || Date.now() / 1000 - (plan.at || 0) > PLAN_POLL_MS / 1000) fetchPlan(true);
}

planButton.addEventListener("click", () => openPlan(true));
document.getElementById("closePlan").addEventListener("click", () => openPlan(false));
document.getElementById("planRefresh").addEventListener("click", () => { fetchPlan(true); paintPlanDialog(); });
document.getElementById("planBody").addEventListener("click", (event) => {
  const pick = event.target.closest?.("[data-plan-window]");
  if (!pick) return;
  planWindow = Number(pick.dataset.planWindow) || 0;
  paintPlanDialog();
});
planScrim.addEventListener("click", (event) => { if (event.target === planScrim) openPlan(false); });
