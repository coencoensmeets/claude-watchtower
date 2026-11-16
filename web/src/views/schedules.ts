import { selectSession } from "../main.js";
import { run } from "../net.js";
import { app, sessionById } from "../state.js";
import { detailPane } from "../ui/dom.js";
import { ago, escapeHtml, plural } from "../ui/format.js";
import { reloadState } from "../refresh.js";
import { ICON } from "../ui/icons.js";
import { openMenu, type MenuItem } from "../ui/menu.js";
import { onLongPress } from "../ui/press.js";
import { showSnackbar } from "../ui/snackbar.js";
import type { ScheduledRun, ScheduledTask, Session } from "../types.js";

/* ==========================================================================
   Schedule — things to do in this session's folder, later or on repeat. A
   prompt for Claude, or a shell command; once at a moment, or every so often.
   The panel is the clock (watchtower/schedules.py), and Claude can add to the
   same list through the `schedule_task` MCP tool.

   Drawn like the Sessions tab: a numeral and its label on top, tiles in the
   field tone below, one ink action — New task — and the rest quiet until
   pointed at. A task that failed last time carries an ember dot; one that is
   running, a lime one; a paused one is drawn faded.
   ========================================================================== */

type When = "once" | "repeat";
type Unit = "minutes" | "hours" | "days";

interface Draft {
  id: string | null;
  title: string;
  kind: "claude" | "command";
  text: string;
  when: When;
  at: string;            // a datetime-local value; empty is "now"
  every: string;
  unit: Unit;
  mode: string;
  session: "new" | "same";
  timeout: string;       // minutes
}

const UNIT_SECONDS: Record<Unit, number> = { minutes: 60, hours: 3600, days: 86400 };
const MODES: [string, string][] = [["auto", "Auto"], ["default", "Manual"], ["acceptEdits", "Accept edits"], ["plan", "Plan"]];

function blank(): Draft {
  return { id: null, title: "", kind: "claude", text: "", when: "repeat", at: "", every: "1",
           unit: "hours", mode: "auto", session: "new", timeout: "30" };
}

/* What the tab is doing, kept across repaints; dropped with the folder. */
const sched = {
  folder: "",
  composing: false,
  saving: false,
  draft: blank(),
  // Which task's runs are open, and their full copies off /api/schedules.
  open: null as string | null,
  full: new Map<string, ScheduledTask>(),
  fetching: null as string | null,
  // The task whose Remove has been pressed once and is asking to be pressed again.
  removing: null as string | null,
  focus: null as { id: string; start: number | null; end: number | null } | null,
};

export function schedulesOf(session: Session): ScheduledTask[] {
  return (app.feed.schedules || []).filter((t) => t.cwd === session.cwd);
}

/* What a repaint has to notice. Not what is typed into the form — the form
   keeps its own state, and rebuilding under a typist loses their caret. */
export function scheduleStamp(session: Session): string {
  return [sched.composing, sched.saving, sched.draft.kind, sched.draft.when, sched.draft.session,
    sched.draft.id ?? "", sched.open ?? "", sched.removing ?? "", sched.fetching ?? "",
    sched.open ? sched.full.get(sched.open)?.runs ?? -1 : "",
    // "in 42m" counts down, so the tab is redrawn once a minute.
    Math.floor(now() / 60),
    ...schedulesOf(session).map((t) =>
      `${t.id}:${t.title}:${t.enabled}:${t.next}:${t.runs}:${t.running}:${t.last?.at}:${t.every}:${t.text.length}:${t.starred}`),
  ].join("|");
}

/* -------------------------------------------------------------- the words */

function localInput(epoch: number): string {
  const d = new Date(epoch * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function moment(epoch: number): string {
  const d = new Date(epoch * 1000);
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  const today = new Date();
  const days = Math.round((new Date(d).setHours(0, 0, 0, 0) - today.setHours(0, 0, 0, 0)) / 86400000);
  if (days === 0) return time;
  if (days === 1) return `tomorrow ${time}`;
  if (days === -1) return `yesterday ${time}`;
  const date = d.toLocaleDateString(undefined, { weekday: days > 0 && days < 7 ? "short" : undefined,
    day: "numeric", month: "short" });
  return `${date} ${time}`;
}

function until(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return "in under a minute";
  const m = Math.round(s / 60);
  if (m < 60) return `in ${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `in ${h}h${m % 60 ? ` ${m % 60}m` : ""}`;
  return `in ${Math.round(h / 24)}d`;
}

function cadence(every: number | null): string {
  if (!every) return "Once";
  for (const [unit, size] of [["day", 86400], ["hour", 3600], ["minute", 60]] as [string, number][]) {
    if (every % size === 0) {
      const n = every / size;
      return n === 1 ? `Every ${unit}` : `Every ${n} ${unit}s`;
    }
  }
  return `Every ${every}s`;
}

function now(): number {
  return Date.now() / 1000 + (app.skew || 0);
}

/* ------------------------------------------------------------- the panel */

function composeCard(): string {
  const d = sched.draft;
  const seg = (name: string, value: string, current: string, label: string, icon = "") =>
    `<button type="button" class="sch-seg__item${icon ? " sch-seg__item--ride" : ""} md-label-large" data-sch-set="${name}"
      data-value="${value}" aria-pressed="${current === value}">${icon
        ? `<span class="sch-seg__ride" data-kind="${value}">${icon}</span>` : ""}${label}</button>`;
  // A row's heading: what the row decides, with its glyph in a coloured circle.
  const step = (tone: string, icon: string, label: string) =>
    `<span class="sch-step md-label-large"><span class="sch-step__dot" data-tone="${tone}">${icon}</span>${label}</span>`;
  return `
    <form class="sch-compose" id="schCompose" data-kind="${d.kind}">
      <div class="sch-compose__head">
        <span class="sch-compose__tag md-label-medium">${d.id ? "Editing" : "New task"}</span>
      </div>
      <div class="sch-compose__row">
        <div class="sch-seg" role="group" aria-label="What it does">
          ${seg("kind", "claude", d.kind, "Ask Claude", ICON.sparkle)}
          ${seg("kind", "command", d.kind, "Run a command", ICON.terminal)}
        </div>
      </div>
      <input class="sch-field md-title-small" id="schTitle" name="title" placeholder="Name (optional)"
        value="${escapeHtml(d.title)}" autocomplete="off" maxlength="80">
      <textarea class="sch-field sch-field--area ${d.kind === "command" ? "md-mono" : "md-body-medium"}" id="schText"
        name="text" rows="3" spellcheck="${d.kind === "claude"}" placeholder="${d.kind === "claude"
          ? "What should Claude do? e.g. Check the open merge requests and summarise what needs me"
          : "e.g. npm test, or ./scripts/backup.sh"}">${escapeHtml(d.text)}</textarea>
      <div class="sch-compose__row">
        ${step("lilac", ICON.clock, "When")}
        <div class="sch-seg" role="group" aria-label="How often">
          ${seg("when", "once", d.when, "Once")}
          ${seg("when", "repeat", d.when, "Repeat")}
        </div>
        ${d.when === "repeat" ? `
          <label class="sch-inline md-body-medium">every
            <input class="sch-field sch-field--num" id="schEvery" type="number" min="1" step="1" value="${escapeHtml(d.every)}">
            <select class="sch-field sch-field--select" id="schUnit">
              ${(["minutes", "hours", "days"] as Unit[]).map((u) =>
                `<option value="${u}" ${d.unit === u ? "selected" : ""}>${u}</option>`).join("")}
            </select>
          </label>` : ""}
        <label class="sch-inline md-body-medium">${d.when === "repeat" ? "starting" : "at"}
          <input class="sch-field sch-field--when" id="schAt" type="datetime-local" value="${escapeHtml(d.at)}">
          <span class="sch-hint md-label-small">${d.at ? "" : "empty is now"}</span>
        </label>
      </div>
      ${d.kind === "claude" ? `
        <div class="sch-compose__row">
          ${step("lilac", ICON.chat, "Where")}
          <div class="sch-seg" role="group" aria-label="Which session">
            ${seg("session", "new", d.session, "New session each run")}
            ${seg("session", "same", d.session, "Same session")}
          </div>
          <label class="sch-inline md-body-medium">mode
            <select class="sch-field sch-field--select" id="schMode">
              ${MODES.map(([v, l]) => `<option value="${v}" ${d.mode === v ? "selected" : ""}>${l}</option>`).join("")}
            </select>
          </label>
        </div>` : `
        <div class="sch-compose__row">
          ${step("ember", ICON.stop, "Limit")}
          <label class="sch-inline md-body-medium">stop it after
            <input class="sch-field sch-field--num" id="schTimeout" type="number" min="1" step="1" value="${escapeHtml(d.timeout)}">
            minutes</label>
        </div>`}
      <div class="sch-compose__foot">
        <span class="sch-hint md-label-small">Runs while the panel is running</span>
        <button class="button button--text md-state" type="button" data-sch="cancel">Cancel</button>
        <button class="sch-go md-label-large" type="submit" id="schSave" ${sched.saving ? "disabled" : ""}>
          <span class="sch-go__ride" aria-hidden="true">${sched.saving ? `<span class="sch-go__spin"></span>` : d.id ? ICON.check : ICON.clock}</span>${
          sched.saving ? "Saving…" : d.id ? "Save" : "Schedule it"}</button>
      </div>
    </form>`;
}

function runLine(r: ScheduledRun, session?: boolean): string {
  const at = r.at ? ago(now() - r.at) : "";
  return `<span class="sch-run ${r.ok ? "sch-run--ok" : "sch-run--bad"}">
    <i aria-hidden="true"></i><span>${escapeHtml(r.message || (r.ok ? "Done" : "Failed"))}</span>
    <span class="sch-run__at">${escapeHtml(at)}</span>
    ${session && r.sessionId ? `<button type="button" class="sch-link md-label-medium" data-sch-session="${escapeHtml(r.sessionId)}">Open session</button>` : ""}
  </span>`;
}

function history(task: ScheduledTask): string {
  const full = sched.full.get(task.id);
  if (!full) return `<div class="sch-history sch-history--loading md-body-small">Reading its runs…</div>`;
  const runs = full.history || [];
  if (!runs.length) return `<div class="sch-history md-body-small">It has not run yet.</div>`;
  return `<ol class="sch-history">${runs.map((r) => `
    <li class="sch-history__run">
      <div class="sch-history__head md-label-medium">${runLine(r, true)}
        <span class="sch-history__when">${escapeHtml(moment(r.at))}</span></div>
      ${r.output ? `<pre class="sch-output md-mono">${escapeHtml(r.output)}</pre>` : ""}
    </li>`).join("")}</ol>`;
}

function taskCard(task: ScheduledTask): string {
  const t = now();
  const state = task.running ? "running" : !task.enabled ? (task.every || !task.runs ? "paused" : "done")
    : task.last && !task.last.ok ? "failed" : "on";
  const next = task.enabled && task.next
    ? `<span class="sch-fact sch-fact--next">${ICON.clock}${escapeHtml(moment(task.next))} · ${escapeHtml(until(task.next - t))}</span>`
    : state === "done" ? `<span class="sch-fact">Done</span>` : `<span class="sch-fact">Paused</span>`;
  const where = task.kind === "claude"
    ? `<span class="sch-fact">${task.session === "same" ? "Same session" : "New session each run"} · ${
        escapeHtml((MODES.find(([v]) => v === task.mode) || ["", task.mode || "Auto"])[1])}</span>`
    : "";
  const open = sched.open === task.id;
  const asking = sched.removing === task.id;
  return `<li class="sch-item">
    <article class="sch sch--${state}" data-kind="${task.kind}">
      <span class="sch__icon" aria-hidden="true">${task.kind === "claude" ? ICON.sparkle : ICON.terminal}</span>
      <div class="sch__body">
        <div class="sch__name md-title-small">${escapeHtml(task.title)}${task.running
          ? `<span class="sch__lamp md-label-small"><i></i>Running</span>` : ""}</div>
        <div class="sch__what ${task.kind === "command" ? "md-mono" : "md-body-medium"}">${escapeHtml(task.text)}</div>
        <div class="sch__facts md-label-medium">
          <span class="sch-fact sch-fact--cadence">${task.every ? ICON.sync : ICON.flag}${escapeHtml(cadence(task.every))}</span>
          ${next}${where}
          ${task.createdBy && task.createdBy !== "you" ? `<span class="sch-fact">by ${escapeHtml(task.createdBy)}</span>` : ""}
        </div>
        ${task.last ? `<button type="button" class="sch__last md-label-medium" data-sch="history" data-id="${task.id}"
            aria-expanded="${open}">${runLine(task.last)}<span class="sch__count">${plural(task.runs, "run")}</span>${ICON.chevron}</button>` : ""}
        ${open ? history(task) : ""}
      </div>
      <div class="sch__acts">
        <button type="button" class="sch-act md-state" data-sch="run" data-id="${task.id}" title="Run now"
          aria-label="Run now" ${task.running ? "disabled" : ""}>${ICON.play}</button>
        <button type="button" class="sch-act md-state" data-sch="toggle" data-id="${task.id}"
          title="${task.enabled ? "Pause" : "Resume"}" aria-label="${task.enabled ? "Pause" : "Resume"}">${
          task.enabled ? ICON.pause : ICON.refresh}</button>
        <button type="button" class="sch-act md-state${task.starred ? " sch-act--on" : ""}" data-sch="star" data-id="${task.id}"
          title="${task.starred ? "Unstar" : "Star: keep it under the sessions, even with no session here"}"
          aria-label="${task.starred ? "Unstar" : "Star"}" aria-pressed="${!!task.starred}">${task.starred ? ICON.starFilled : ICON.star}</button>
        <button type="button" class="sch-act md-state" data-sch="edit" data-id="${task.id}" title="Edit"
          aria-label="Edit">${ICON.pencil}</button>
        <button type="button" class="sch-act md-state${asking ? " sch-act--ask" : ""}" data-sch="remove" data-id="${task.id}"
          title="${asking ? "Press again to remove" : "Remove"}" aria-label="Remove">${asking
          ? `<span class="md-label-medium">Remove?</span>` : ICON.trash}</button>
      </div>
    </article>
  </li>`;
}

/* The tally beside the numeral, one chip per state that has any tasks in it,
   each with the state's own colour as its dot. */
function summary(tasks: ScheduledTask[]): string {
  const count = (test: (t: ScheduledTask) => boolean) => tasks.filter(test).length;
  const chips: [string, number, string][] = [
    ["running", count((t) => !!t.running), "running"],
    ["on", count((t) => t.enabled && !t.running && !(t.last && !t.last.ok)), "scheduled"],
    ["failed", count((t) => !t.running && !!t.last && !t.last.ok), "failed last time"],
    ["paused", count((t) => !t.enabled && !t.running), "paused or done"],
  ];
  const shown = chips.filter(([, n]) => n);
  return shown.length ? `<div class="sch-tally">${shown.map(([tone, n, label]) =>
    `<span class="sch-tally__chip md-label-medium" data-tone="${tone}"><i aria-hidden="true"></i>${n} ${label}</span>`).join("")}</div>` : "";
}

export function schedulePanel(session: Session): string {
  if (sched.folder !== session.cwd) {
    sched.folder = session.cwd;
    sched.composing = false;
    sched.draft = blank();
    sched.open = null;
    sched.removing = null;
  }
  const tasks = schedulesOf(session);
  const folder = (session.cwd || "").split("/").filter(Boolean).pop() || session.cwd || "";
  const canSend = app.feed.canSend;
  return `
    <header class="past-top sch-top">
      <div class="past-count">
        <span class="past-count__n">${tasks.length}</span>
        <span class="past-count__l">
          <span class="md-title-small">${tasks.length === 1 ? "scheduled task" : "scheduled tasks"}</span>
          <span class="md-body-small">in <span class="md-mono" title="${escapeHtml(session.cwd)}">${escapeHtml(folder)}</span>${
            canSend ? "" : " · scheduling needs sending switched on"}</span>
        </span>
      </div>
      ${summary(tasks)}
      ${canSend && !sched.composing ? `<button class="sch-go sch-new md-label-large" type="button" data-sch="new">
        <span class="sch-go__ride" aria-hidden="true">${ICON.plus}</span>New task</button>` : ""}
    </header>
    ${sched.composing ? composeCard() : ""}
    ${tasks.length ? `<ul class="sch-list">${tasks.map(taskCard).join("")}</ul>`
      : sched.composing ? "" : `<div class="past-empty">
        <div class="past-empty__art sch-empty__art">${ICON.clock}</div>
        <h3 class="md-title-medium">Nothing scheduled here</h3>
        <p class="md-body-medium">Ask Claude to do something later or every so often — check the build each morning,
          summarise new issues every hour — or run a command on a timer. Claude can schedule tasks here too.</p>
      </div>`}`;
}

/* ------------------------------------------------------------ the wiring */

async function fetchFull(id: string, redraw: () => void): Promise<void> {
  if (sched.fetching === id) return;
  sched.fetching = id;
  try {
    const response = await fetch(`/api/schedules?id=${encodeURIComponent(id)}`, { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (data.task) sched.full.set(id, data.task);
  } catch { /* the row says it is still reading; the next repaint asks again */ }
  sched.fetching = null;
  redraw();
}

function readForm(): void {
  const d = sched.draft;
  const val = (id: string) => detailPane.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(`#${id}`)?.value;
  d.title = val("schTitle") ?? d.title;
  d.text = val("schText") ?? d.text;
  d.at = val("schAt") ?? d.at;
  d.every = val("schEvery") ?? d.every;
  d.unit = (val("schUnit") as Unit) ?? d.unit;
  d.mode = val("schMode") ?? d.mode;
  d.timeout = val("schTimeout") ?? d.timeout;
}

function draftFrom(task: ScheduledTask): Draft {
  let every = "1", unit: Unit = "hours";
  if (task.every) {
    unit = task.every % 86400 === 0 ? "days" : task.every % 3600 === 0 ? "hours" : "minutes";
    every = String(Math.round(task.every / UNIT_SECONDS[unit]));
  }
  return { id: task.id, title: task.title, kind: task.kind, text: task.text,
    when: task.every ? "repeat" : "once", at: localInput(task.next || task.at), every, unit,
    mode: task.mode || "auto", session: task.session || "new",
    timeout: String(Math.round((task.timeout || 1800) / 60)) };
}

async function save(session: Session, button: HTMLButtonElement, redraw: () => void): Promise<void> {
  readForm();
  const d = sched.draft;
  if (!d.text.trim()) {
    showSnackbar(d.kind === "claude" ? "Say what Claude should do" : "Say which command to run");
    detailPane.querySelector<HTMLTextAreaElement>("#schText")?.focus();
    return;
  }
  const at = d.at ? new Date(d.at).getTime() / 1000 : now();
  const every = d.when === "repeat" ? Math.round(Number(d.every) * UNIT_SECONDS[d.unit]) : null;
  if (d.when === "repeat" && !(every >= 60)) {
    showSnackbar("Repeat at most once a minute");
    return;
  }
  const body: Record<string, unknown> = { title: d.title, kind: d.kind, text: d.text, at, every: every || 0 };
  if (d.kind === "claude") Object.assign(body, { mode: d.mode, session: d.session });
  else body.timeout = Math.round(Number(d.timeout || 30) * 60);
  // A new one goes in the folder of the session it is posted with.
  if (d.id) body.id = d.id;
  // A one-off being edited is a one-off switched back on.
  if (d.id) body.enabled = true;
  sched.saving = true;
  redraw();
  const data = await run("/api/schedules", session, button, null, body);
  sched.saving = false;
  if (data.ok) {
    sched.composing = false;
    sched.draft = blank();
    sched.focus = null;
  }
  redraw();
}

export function wireSchedules(session: Session, redraw: () => void): void {
  const root = detailPane;
  const on = (selector: string, handler: (el: HTMLElement, event: Event) => void) => {
    for (const el of root.querySelectorAll<HTMLElement>(selector)) {
      el.addEventListener("click", (event) => handler(el, event));
    }
  };
  const task = (el: HTMLElement) => schedulesOf(session).find((t) => t.id === el.dataset.id);

  on('[data-sch="new"]', () => {
    sched.composing = true;
    sched.draft = blank();
    sched.focus = { id: "schText", start: null, end: null };
    redraw();
  });
  on('[data-sch="cancel"]', () => {
    sched.composing = false;
    sched.draft = blank();
    sched.focus = null;
    redraw();
  });
  on("[data-sch-set]", (el) => {
    readForm();
    (sched.draft as unknown as Record<string, string>)[el.dataset.schSet!] = el.dataset.value!;
    redraw();
  });
  on('[data-sch="run"]', (el) => {
    run("/api/schedules/run", session, el as HTMLButtonElement, null, { id: el.dataset.id });
  });
  on('[data-sch="toggle"]', (el) => {
    const t = task(el);
    if (t) run("/api/schedules/enable", session, el as HTMLButtonElement, null, { id: t.id, enabled: !t.enabled });
  });
  on('[data-sch="star"]', (el) => {
    const t = task(el);
    if (t) run("/api/schedules/star", session, el as HTMLButtonElement, null, { id: t.id, starred: !t.starred });
  });
  on('[data-sch="edit"]', (el) => {
    const t = task(el);
    if (!t) return;
    sched.draft = draftFrom(t);
    sched.composing = true;
    sched.focus = { id: "schText", start: null, end: null };
    redraw();
    detailPane.querySelector(".sch-compose")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });
  on('[data-sch="remove"]', (el) => {
    const id = el.dataset.id!;
    if (sched.removing !== id) {
      sched.removing = id;
      redraw();
      setTimeout(() => { if (sched.removing === id) { sched.removing = null; redraw(); } }, 3500);
      return;
    }
    sched.removing = null;
    if (sched.open === id) sched.open = null;
    run("/api/schedules/remove", session, el as HTMLButtonElement, null, { id });
  });
  on('[data-sch="history"]', (el) => {
    const id = el.dataset.id!;
    sched.open = sched.open === id ? null : id;
    redraw();
    if (sched.open) fetchFull(id, redraw);
  });
  on("[data-sch-session]", (el) => {
    const id = el.dataset.schSession!;
    if (sessionById(id)) selectSession(id);
    else showSnackbar("That session is no longer on the list");
  });

  // The open task's runs are read again when it has run since they were read.
  if (sched.open) {
    const t = schedulesOf(session).find((x) => x.id === sched.open);
    const full = sched.full.get(sched.open);
    if (!t) sched.open = null;
    else if (!full || full.runs !== t.runs || full.last?.at !== t.last?.at) fetchFull(t.id, redraw);
  }

  const form = root.querySelector<HTMLFormElement>("#schCompose");
  if (!form) return;
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    save(session, root.querySelector<HTMLButtonElement>("#schSave")!, redraw);
  });
  form.addEventListener("input", readForm);
  form.addEventListener("change", () => {
    readForm();
    // The "empty is now" hint follows the field without a rebuild.
    const hint = form.querySelector<HTMLElement>("#schAt + .sch-hint");
    if (hint) hint.textContent = sched.draft.at ? "" : "empty is now";
  });
  // Leaving the form for something else on the page lets go of the caret, so a
  // repaint does not pull you back into it. Removed by a rebuild, the field
  // leaves focus on the body, which is not leaving.
  form.addEventListener("focusout", () => {
    setTimeout(() => {
      const active = document.activeElement;
      if (active && active !== document.body && !form.contains(active)) sched.focus = null;
    }, 0);
  });
  // Where the caret was, so a repaint mid-sentence puts it back.
  form.addEventListener("focusin", (event) => {
    const el = event.target as HTMLInputElement;
    if (el.id) sched.focus = { id: el.id, start: null, end: null };
  });
  form.addEventListener("keyup", (event) => {
    const el = event.target as HTMLInputElement;
    if (el.id && "selectionStart" in el) {
      try { sched.focus = { id: el.id, start: el.selectionStart, end: el.selectionEnd }; } catch { /* number fields */ }
    }
  });
  form.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      form.requestSubmit();
    }
    if (event.key === "Escape") root.querySelector<HTMLElement>('[data-sch="cancel"]')?.click();
  });
  if (sched.focus) {
    const el = form.querySelector<HTMLInputElement>(`#${sched.focus.id}`);
    if (el && document.activeElement !== el) {
      el.focus({ preventScroll: true });
      if (sched.focus.start !== null) {
        try { el.setSelectionRange(sched.focus.start, sched.focus.end); } catch { /* not a text field */ }
      } else if (el instanceof HTMLTextAreaElement) {
        el.setSelectionRange(el.value.length, el.value.length);
      }
    }
  }
}

/* ==========================================================================
   Starred tasks — listed under the sessions, like starred sites.

   A task belongs to its folder and runs whether or not a session there is
   open; what it loses with the last session is the tab it is seen in. A star
   keeps it in view. A tap shows it in a session in its folder when there is
   one on the list, and otherwise opens the menu, which has the rest — and a
   right-click or a long press opens that menu either way.
   ========================================================================== */

const starSection = document.getElementById("starredTasks");
const starList = document.getElementById("starredTaskList");

let openInFolder: (cwd: string, item: HTMLButtonElement | null) => void = () => {};

function starredTasks(): ScheduledTask[] {
  return (app.feed.schedules || []).filter((t) => t.starred);
}

function folderSession(task: ScheduledTask): Session | null {
  const here = app.feed.sessions.filter((s) => s.cwd === task.cwd);
  return here.find((s) => s.sessionId === app.selectedId) || here[0] || null;
}

function starredMeta(task: ScheduledTask): string {
  const folder = task.cwd.split("/").filter(Boolean).pop() || task.cwd;
  const state = task.running ? "running"
    : task.enabled && task.next ? until(task.next - now())
    : task.enabled ? "due" : task.every || !task.runs ? "paused" : "done";
  return `${folder} · ${state}`;
}

export function renderStarredTasks(): void {
  if (!starSection || !starList) return;
  const all = starredTasks();
  starSection.hidden = !all.length;
  const signature = all.map((t) => `${t.id}:${t.title}:${starredMeta(t)}:${t.last?.ok}:${t.kind}`).join("|");
  if (starList.dataset.signature === signature) return;
  starList.dataset.signature = signature;
  starList.innerHTML = all.map((t) => `
    <li data-star-task="${escapeHtml(t.id)}">
      <button type="button" class="starred-item starred-item--task md-state${t.last && !t.last.ok ? " starred-item--bad" : ""}${
        t.running ? " starred-item--running" : ""}" title="${escapeHtml(`${t.title} — ${t.cwd}`)}">
        <span class="starred-item__icon sch-kind" data-kind="${t.kind}">${t.kind === "claude" ? ICON.sparkle : ICON.terminal}</span>
        <span class="starred-item__text">
          <span class="starred-item__title md-title-small">${escapeHtml(t.title)}</span>
          <span class="starred-item__meta md-body-small">${escapeHtml(starredMeta(t))}</span>
        </span>
      </button>
    </li>`).join("");
}

async function post(url: string, body: Record<string, unknown>, button?: HTMLButtonElement | null) {
  if (button) button.disabled = true;
  try {
    const response = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    showSnackbar(data.message || (response.ok ? "Done" : "That did not work"));
  } catch {
    showSnackbar("Could not reach the server");
  } finally {
    if (button) button.disabled = false;
    await reloadState();
  }
}

function showTask(task: ScheduledTask, session: Session): void {
  app.tab = "schedule";
  localStorage.setItem("cbu-tab", "schedule");
  sched.open = task.id;
  selectSession(session.sessionId);
}

function openTaskMenu(task: ScheduledTask, x: number, y: number): void {
  const session = folderSession(task);
  const canSend = app.feed.canSend;
  const items: MenuItem[] = [];
  if (session) {
    items.push({ key: "show", icon: ICON.clock, label: "Show in its folder's session", hint: session.name,
      run: () => showTask(task, session) });
  } else {
    items.push({ key: "open", icon: ICON.chat, label: "Open a session in its folder", hint: task.cwd,
      disabled: !canSend, run: (el) => { sched.open = task.id; openInFolder(task.cwd, el); } });
  }
  items.push({ divider: true });
  items.push({ key: "run", icon: ICON.play, label: "Run now", disabled: !canSend || !!task.running,
    run: (el) => post("/api/schedules/run", { id: task.id }, el) });
  items.push({ key: "toggle", icon: task.enabled ? ICON.pause : ICON.refresh, label: task.enabled ? "Pause" : "Resume",
    disabled: !canSend, run: (el) => post("/api/schedules/enable", { id: task.id, enabled: !task.enabled }, el) });
  items.push({ divider: true });
  items.push({ key: "unstar", icon: ICON.star, label: "Unstar", disabled: !canSend,
    run: (el) => post("/api/schedules/star", { id: task.id, starred: false }, el) });
  items.push({ key: "remove", icon: ICON.trash, label: "Remove the task", danger: true, disabled: !canSend,
    run: (el) => post("/api/schedules/remove", { id: task.id }, el) });
  openMenu({ title: task.title, label: `Actions for ${task.title}`, items }, x, y);
}

function taskAt(target: EventTarget | null): ScheduledTask | null {
  const row = (target as Element | null)?.closest?.("li[data-star-task]") as HTMLElement | null;
  return row ? starredTasks().find((t) => t.id === row.dataset.starTask) || null : null;
}

export function wireStarredTasks(hooks: { openInFolder: (cwd: string, item: HTMLButtonElement | null) => void }): void {
  openInFolder = hooks.openInFolder;
  if (!starList) return;
  starList.addEventListener("click", (event) => {
    const task = taskAt(event.target);
    if (!task) return;
    const session = folderSession(task);
    if (session) { showTask(task, session); return; }
    const box = (event.target as Element).closest("button")!.getBoundingClientRect();
    openTaskMenu(task, box.left + 24, box.top + box.height / 2);
  });
  starList.addEventListener("contextmenu", (event) => {
    const task = taskAt(event.target);
    if (!task) return;
    event.preventDefault();
    openTaskMenu(task, event.clientX, event.clientY);
  });
  onLongPress(starList, ({ x, y, target }) => {
    const task = taskAt(target);
    if (task) openTaskMenu(task, x, y);
  });
}
