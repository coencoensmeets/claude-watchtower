import { MODE_LABELS, isAmbiguous, isRemembered } from "../sessions/facts.js";
import { app, chat, mutedSessions, quietWhenDone, spend } from "../state.js";
import { ago, duration, escapeHtml, shorten, tokens } from "../ui/format.js";
import { ICON } from "../ui/icons.js";
import { ownedFor, runsHere } from "./owned.js";

/* ==========================================================================
   Usage — what this session has asked of the models, and what that is worth
   at list price. Read out of the transcript, which is the only place the
   figures exist: every reply Claude Code writes down carries the usage the
   API reported for it.
   ========================================================================== */

/* Money to the cent, except when the whole session has not cost one — a figure
   of "$0.00" beside a page of tokens reads as a bug rather than as cheap. */
function money(n) {
  if (!n) return "$0.00";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/* The headline figure, split so the cents can sit smaller than the dollars:
   the eye wants the order of magnitude, and the cents are there if you look. */
function bigMoney(n) {
  const text = money(n);
  const dot = text.lastIndexOf(".");
  if (dot < 0 || n < 0.01) return escapeHtml(text);
  return `${escapeHtml(text.slice(0, dot))}<small>${escapeHtml(text.slice(dot))}</small>`;
}

/* The figures table is folded away by default, and the panel repaints this tab
   whenever a new request lands, so whether it was open is kept out here. */
let figuresOpen = false;
document.addEventListener("toggle", (event) => {
  const el = event.target;
  if (el instanceof HTMLElement && el.classList.contains("spend-more")) figuresOpen = el.open;
}, true);

/* One scale for every "how full" figure in the panel: room, tight, nearly gone. */
const band = (percent) => (percent >= 90 ? 2 : percent >= 75 ? 1 : 0);

function spendStat(label, value, note) {
  return `<div class="spend-stat">
    <div class="spend-stat__l">${escapeHtml(label)}</div>
    <div class="spend-stat__n">${value}</div>
    ${note ? `<div class="spend-stat__note">${note}</div>` : ""}
  </div>`;
}

/* Where the tokens went, as one bar cut into parts. A sliver that would be too
   thin to see is given a hair of width so it is still there to find; the legend
   carries the real figure. */
function tokenSplit(t) {
  const parts = [
    ["Cache reads", t.cacheRead, "var(--viz-1)"],
    ["Cache writes", t.cacheWrite5m + t.cacheWrite1h, "var(--viz-2)"],
    ["Fresh input", t.input, "var(--viz-3)"],
    ["Output", t.output, "var(--viz-4)"],
  ].filter(([, n]) => n > 0);
  const total = parts.reduce((sum, [, n]) => sum + n, 0);
  if (!total) return "";
  const pct = (n) => n / total * 100;
  const shown = (n) => (pct(n) >= 1 ? `${Math.round(pct(n))}%` : "<1%");
  return `
    <div class="spend-seg" role="img" aria-label="${escapeHtml(parts
      .map(([name, n]) => `${name} ${shown(n)}`).join(", "))}">${parts.map(([name, n, colour]) =>
      `<span style="flex: ${Math.max(n, total * 0.012)} 1 0; --c: ${colour}"
             title="${escapeHtml(`${name}: ${tokens(n)} (${shown(n)})`)}">${pct(n) >= 9 ? shown(n) : ""}</span>`).join("")}
    </div>
    <ul class="spend-legend">${parts.map(([name, n, colour]) => `<li style="--c: ${colour}">
      <i></i><span class="spend-legend__name">${escapeHtml(name)}</span>
      <b>${tokens(n)}</b><span class="spend-legend__pct">${shown(n)}</span>
    </li>`).join("")}</ul>`;
}

/* Every model the session called, longest bar first. The biggest spender takes
   the one colour and the rest stay grey, so the bar that matters is the one you
   see first. */
function modelRanks(models, agents) {
  const rows = [
    ...models.map((row) => ({ ...row, agent: false })),
    ...agents.map((row) => ({ ...row, agent: true })),
  ].sort((a, b) => (b.cost || 0) - (a.cost || 0));
  const top = Math.max(...rows.map((row) => row.cost || 0), 0);
  return `<div class="spend-rank">${rows.map((row, i) => {
    const width = top && row.cost ? Math.max(1.5, row.cost / top * 100) : 0;
    return `<div class="spend-rank__row">
      <div class="spend-rank__label">
        <span class="md-mono">${escapeHtml(row.model)}</span>
        ${row.agent ? `<span class="spend-tag">sub-agents</span>` : ""}
        <span class="spend-rank__meta">${row.requests.toLocaleString()} ${row.requests === 1 ? "request" : "requests"}</span>
      </div>
      <div class="spend-rank__value">${row.priced ? money(row.cost) : `<span class="fact-note">no price known</span>`}</div>
      <div class="spend-rank__bar"><span style="width: ${width}%; --c: ${i === 0 ? "var(--viz-1)" : "var(--viz-muted)"}"></span></div>
    </div>`;
  }).join("")}</div>`;
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

export function usagePanel(session) {
  if (!spend.usage || spend.usageFor !== session.sessionId) {
    return `<p class="git-empty md-body-medium">Reading the transcript…</p>`;
  }
  const u = spend.usage;
  const t = u.totals;
  const every = [...u.models, ...u.agentModels];
  if (!every.length) {
    return `<p class="git-empty md-body-medium">${session.kind === "child"
      ? `No transcript of its own — its usage lands in the session that started it.`
      : "Nothing asked yet."}</p>`;
  }
  const cached = t.cacheRead + t.cacheWrite5m + t.cacheWrite1h;
  // Rounded down: a session that has written any cache at all should not read
  // as though every token came out of one.
  const share = cached ? Math.floor(t.cacheRead / cached * 100) : 0;
  const context = u.context || 0;
  const limit = u.contextWindow || 1_000_000;
  const full = Math.min(100, Math.round(context / limit * 100));
  const spanEnd = u.lastAt ? Date.parse(u.lastAt) / 1000 : null;
  const spanStart = u.firstAt ? Date.parse(u.firstAt) / 1000 : null;
  const span = spanStart && spanEnd ? spanEnd - spanStart : 0;
  // A rate over a couple of minutes is noise, so it waits for a real stretch.
  const perHour = span >= 600 && u.cost ? u.cost / (span / 3600) : 0;
  const fromAgents = u.agentModels.reduce((n, r) => n + r.requests, 0);
  const now = Date.now() / 1000 + app.skew;

  return `
    <div class="spend">
      <section class="spend-hero">
        <div class="spend-hero__main">
          <div class="spend-hero__l">${ICON.coin}<span>Cost so far</span></div>
          <div class="spend-hero__n md-mono">${bigMoney(u.cost)}</div>
          <div class="spend-hero__chips">
            <span class="spend-chip">at list price</span>
            ${span >= 60 ? `<span class="spend-chip">over ${escapeHtml(duration(span))}</span>` : ""}
            ${perHour ? `<span class="spend-chip spend-chip--ink">${escapeHtml(money(perHour))} / hour</span>` : ""}
          </div>
        </div>
        <div class="spend-hero__stats">
          ${spendStat("Requests", t.requests.toLocaleString(),
            fromAgents ? `${fromAgents.toLocaleString()} from sub-agents` : "")}
          ${spendStat("Cache hits", `${share}<small>%</small>`, "of cached input")}
          ${spendStat("Tokens in", tokens(t.input + cached), `${tokens(t.input)} fresh`)}
          ${spendStat("Tokens out", tokens(t.output),
            t.thinking ? `${tokens(t.thinking)} thinking` : "")}
        </div>
      </section>

      <section class="spend-card">
        <div class="spend-card__head">
          <div>
            <h3 class="spend-card__title">Context</h3>
            <p class="spend-card__sub">${context
              ? `What the last request carried${u.contextModel
                ? ` on <span class="md-mono">${escapeHtml(u.contextModel)}</span>` : ""}`
              : "Nothing has gone in yet."}</p>
          </div>
          ${context ? `<div class="spend-card__figure md-mono">${full}<small>%</small></div>` : ""}
        </div>
        ${context ? `<div class="wt-meter" data-band="${band(full)}" role="meter"
             aria-valuemin="0" aria-valuemax="100" aria-valuenow="${full}" aria-label="Context used">
          <span class="wt-meter__fill" style="width: ${Math.max(full, 1.5)}%">${full >= 14 ? `${tokens(context)}` : ""}</span>
        </div>
        <div class="wt-meter__scale md-mono"><span>0</span><span>${tokens(limit / 4)}</span><span>${tokens(limit / 2)}</span><span>${tokens(limit * 3 / 4)}</span><span>${tokens(limit)}</span></div>
        <p class="spend-card__foot">${full >= 75
          ? `Getting close: Claude Code compacts the conversation when it runs out of room.`
          : `${tokens(limit - context)} of room left before Claude Code compacts.`}</p>` : ""}
      </section>

      <section class="spend-card">
        <div class="spend-card__head">
          <div>
            <h3 class="spend-card__title">Where the tokens went</h3>
            <p class="spend-card__sub">${tokens(t.input + cached + t.output)} across ${t.requests.toLocaleString()} ${t.requests === 1 ? "request" : "requests"}</p>
          </div>
        </div>
        ${tokenSplit(t)}
      </section>

      <section class="spend-card">
        <div class="spend-card__head">
          <div>
            <h3 class="spend-card__title">By model</h3>
            <p class="spend-card__sub">${every.length === 1 ? "One model did all of it" : `${every.length} ways in, biggest first`}</p>
          </div>
        </div>
        ${modelRanks(u.models, u.agentModels)}
        <details class="spend-more"${figuresOpen ? " open" : ""}>
          <summary>${ICON.chevron}<span>All the figures</span></summary>
          ${u.models.length ? `<h4 class="spend-more__title">This session</h4>${usageTable(u.models)}` : ""}
          ${u.agentModels.length ? `<h4 class="spend-more__title">Sub-agents</h4>${usageTable(u.agentModels)}` : ""}
        </details>
      </section>

      <p class="spend-note">List price, from this session's transcript. A
        subscription bills a plan, not tokens, so you are likely charged less.${u.unpriced.length
          ? ` No price known for ${u.unpriced
              .map((m) => `<span class="md-mono">${escapeHtml(m)}</span>`).join(", ")}.` : ""}${
        spanStart ? ` First request ${escapeHtml(ago(now - spanStart))}, last
          ${escapeHtml(ago(now - spanEnd))}.` : ""}</p>
    </div>`;
}

const CLOCK = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>`;

/* A switch exactly as the rest of the panel draws one. */
function switchRow(id, label, note, checked, disabled = false) {
  return `<label class="switch about-switch">
    <input type="checkbox" id="${id}" ${checked ? "checked" : ""}${disabled ? " disabled" : ""}>
    <span class="switch__track"><span class="switch__thumb">
      <svg viewBox="0 0 24 24" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
    </span></span>
    <span class="about-switch__text"><span class="switch__label md-body-medium">${label}</span>${
      note ? `<span class="about-switch__note">${note}</span>` : ""}</span>
  </label>`;
}

function aboutCard(icon, title, body, extra = "") {
  return `<section class="about-card${extra}">
    <header class="about-card__head"><span class="about-card__icon">${icon}</span>
      <h3 class="about-card__title">${title}</h3></header>
    ${body}
  </section>`;
}

/* One glance tile: what it is, the figure, and a line under it. */
function glance(icon, label, value, note = "", tone = "") {
  return `<div class="about-glance__tile"${tone ? ` data-tone="${tone}"` : ""}>
    <div class="about-glance__l">${icon}<span>${escapeHtml(label)}</span></div>
    <div class="about-glance__n">${value}</div>
    ${note ? `<div class="about-glance__note">${note}</div>` : ""}
  </div>`;
}

export function aboutPanel(session, host) {
  const win = session.window;
  const muted = mutedSessions.has(session.sessionId);
  // The finished-a-turn switch hangs off the one above it: muting a session
  // silences it altogether, so offering the finer choice underneath would be
  // offering a setting that does nothing.
  const quiet = quietWhenDone.has(session.sessionId);
  // A session the panel is running has a process behind it, however `alive`
  // reads: the process is a pipe of ours, not a terminal.
  const holding = session.alive === false && ownedFor(session).running;
  const started = new Date(session.startedAt * 1000);
  const up = Date.now() / 1000 + app.skew - session.startedAt;
  const running = session.alive || holding;
  const copy = (what, title) => `<button class="icon-button md-state fact-copy" data-copy="${what}" type="button"
        title="${title}" aria-label="${title}">${ICON.copy}</button>`;

  // The figures you look for first, big enough to find without reading.
  const tiles = `<div class="about-glance">
    ${glance(CLOCK, "Up for", escapeHtml(duration(up)),
      escapeHtml(started.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })))}
    ${glance(ICON.power, "Process", session.pid ? `<span class="md-mono">${session.pid}</span>` : "—",
      running ? (holding ? "run by the panel" : "running") : "not running", running ? "live" : "off")}
    ${glance(ICON.desktop, "Runs in", escapeHtml(host.label), session.kind === "child" ? "nested session" : "")}
    ${glance(ICON.gate, "Mode", session.permissionMode
      ? escapeHtml(MODE_LABELS[session.permissionMode] || session.permissionMode) : "—",
      session.permissionMode ? "last written" : "not written down yet")}
  </div>`;

  const facts = [
    ["Working folder", `<span class="md-mono">${escapeHtml(session.cwd)}</span>`, copy("cwd", "Copy folder path")],
    ["Git branch", session.branch ? `<span class="about-branch">${ICON.branch}<span class="md-mono">${escapeHtml(session.branch)}</span></span>` : "—"],
    // Only a nested session has one of these, and it is the fact that explains
    // the two below it: no transcript, and an id the panel made up.
    ...(session.kind === "child" ? [["Started from", session.parentName
      ? `${escapeHtml(session.parentName)} <span class="fact-note">pid ${session.parentPid}</span>`
      : `<span class="fact-note">a session that has since closed</span>`]] : []),
    ["Claude Code", session.version ? `<span class="about-ver">v${escapeHtml(session.version)}</span>` : "—"],
    ["Session id", `<span class="md-mono">${escapeHtml(session.sessionId)}</span>${
      session.kind === "child" ? ` <span class="fact-note">the panel's own</span>` : ""}`, copy("id", "Copy session id")],
    ["Started", escapeHtml(started.toLocaleString())],
    ["Transcript", chat.transcript?.path ? `<span class="md-mono">${escapeHtml(shorten(chat.transcript.path, 2))}</span>` : "—"],
  ];

  // A session with no window of its own — kept, or run by the panel — would only
  // be misled by that card.
  const windowCard = runsHere(session) ? "" : aboutCard(ICON.pair, "Window", `
      <p class="about-card__body">${isAmbiguous(win)
        ? `${win.candidates?.length || 2} windows look equally likely, so the panel will not guess.`
        : win
          ? `Paired with <span class="about-chip md-mono">${escapeHtml(win.title || win.id)}</span>`
          : "Not paired. Pair one to click through to it."}</p>
      ${isAmbiguous(win) && win.candidates?.length
        ? `<ul class="about-candidates">${win.candidates
            .map((c) => `<li class="md-mono">${escapeHtml(c.title || c.id)}</li>`).join("")}</ul>`
        : ""}
      <div class="about-card__actions">${session.tty && !isRemembered(win)
          ? `<button class="button button--tonal md-state" data-act="identify">${ICON.pair} Identify window</button>`
          : ""}${win
        ? `<button class="button button--outlined md-state" data-act="${isRemembered(win) ? "unpair" : "pair"}">${isRemembered(win) ? "Clear pairing" : "Pick the window"}</button>`
        : `<button class="button button--tonal md-state" data-act="pair">${ICON.pair} Pair window</button>`}</div>`,
    win && !isAmbiguous(win) ? " about-card--ok" : "");

  const pinCard = aboutCard(ICON.pin, "Pin", `
      ${switchRow("stickyToggle", "Keep the row after this session closes",
        session.kept && session.alive === false ? `${session.pinned
          ? "Survives a panel restart." : "Lost when the panel restarts."}${session.kind === "child"
          ? " A nested session leaves no conversation to resume." : ""}` : "", session.pinned)}`);

  const notifyCard = aboutCard(muted ? ICON.bellOff : ICON.bell, "Notifications", `
      <div class="about-switches">
        ${switchRow("muteToggle", "When it waits for an answer", "", !muted)}
        ${switchRow("doneToggle", "When it finishes a turn", muted ? "Off while the one above is." : "", !quiet, muted)}
      </div>`);

  // Stopping and removing are the same act, so they are the same card. Which of
  // the two words leads depends only on whether there is still a process to
  // stop; either way the row goes unless it was pinned.
  const nothing = !running && !session.kept;
  const fate = session.pinned
    ? `It is pinned, so the row stays on the dashboard.`
    : `The row comes off the dashboard and the transcript stays on disk.`;
  const endTitle = running || nothing
    ? (holding ? "Stop running it here" : "End session") : "Remove from the dashboard";
  const endBody = session.alive
    ? `Like <span class="md-mono">/exit</span> — ${escapeHtml(host.label)} stays open. ${fate}`
    : holding ? `The panel lets go of it. ${fate}`
    : nothing ? "Already gone; the row will drop off shortly."
    : "Nothing is running it. Type into it and it starts back up, or take the row off the list.";
  const endLabel = running || nothing ? (holding ? "Stop it" : "End session") : "Remove";

  return `<div class="about-tab">
    ${tiles}
    <div class="about-grid">
      ${windowCard}
      ${pinCard}
      ${notifyCard}
    </div>
    ${aboutCard(ICON.info, "Facts", `<dl class="about-facts">
        ${facts.map(([key, value, action]) => `<div class="about-fact">
          <dt>${escapeHtml(key)}</dt><dd><span class="about-fact__v">${value}</span>${action || ""}</dd>
        </div>`).join("")}
      </dl>`)}
    <section class="about-danger">
      <div class="about-danger__icon">${running || nothing ? ICON.power : ICON.trash}</div>
      <div class="about-danger__text">
        <h3 class="about-card__title">${endTitle}</h3>
        <p class="about-card__body">${endBody}</p>
      </div>
      <button class="button button--danger-outlined md-state"
        data-act="${running || nothing ? "end" : "forget"}"${nothing ? " disabled" : ""}>${
        running || nothing ? ICON.power : ICON.trash} ${endLabel}</button>
    </section>
    <button class="about-settings md-state" id="openAppearance" type="button">
      ${ICON.palette}<span><b>Panel settings</b><span>Theme, notifications, phone and more</span></span>${ICON.chevron}
    </button>
  </div>`;
}
