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

export function usagePanel(session) {
  if (!spend.usage || spend.usageFor !== session.sessionId) {
    return `<p class="git-empty md-body-medium">Reading the transcript…</p>`;
  }
  const t = spend.usage.totals;
  const every = [...spend.usage.models, ...spend.usage.agentModels];
  if (!every.length) {
    return `<p class="git-empty md-body-medium">${session.kind === "child"
      ? `No transcript of its own — its usage lands in the session that started it.`
      : "Nothing asked yet."}</p>`;
  }
  const cached = t.cacheRead + t.cacheWrite5m + t.cacheWrite1h;
  // Rounded down: a session that has written any cache at all should not read
  // as though every token came out of one.
  const share = cached ? Math.floor(t.cacheRead / cached * 100) : 0;
  const context = spend.usage.context || 0;
  const limit = spend.usage.contextWindow || 1_000_000;
  const full = Math.min(100, Math.round(context / limit * 100));
  const spanEnd = spend.usage.lastAt ? Date.parse(spend.usage.lastAt) / 1000 : null;
  const spanStart = spend.usage.firstAt ? Date.parse(spend.usage.firstAt) / 1000 : null;

  return `
    <div class="use-tiles">
      ${usageTile("Cost so far", money(spend.usage.cost), "at list price", true)}
      ${usageTile("Requests", t.requests.toLocaleString(),
        spend.usage.agentModels.length
          ? `${spend.usage.agentModels.reduce((n, r) => n + r.requests, 0).toLocaleString()} from sub-agents`
          : "")}
      ${usageTile("Tokens in", tokens(t.input + cached),
        `${share}% read from cache`)}
      ${usageTile("Tokens out", tokens(t.output),
        t.thinking ? `${tokens(t.thinking)} thinking` : "")}
    </div>

    <section class="section">
      <h3 class="section__title md-title-small">Context</h3>
      <p class="md-body-medium">${context
        ? `Last request: ${full}% of the ${tokens(limit)} window${
            spend.usage.contextModel ? ` on <span class="md-mono">${escapeHtml(spend.usage.contextModel)}</span>` : ""}.`
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
      ${usageTable(spend.usage.models)}
    </section>

    ${spend.usage.agentModels.length ? `<section class="section">
      <h3 class="section__title md-title-small">Sub-agents</h3>
      ${usageTable(spend.usage.agentModels)}
    </section>` : ""}

    <section class="section">
      <p class="md-body-small fact-note">List price, from this session's transcript. A
        subscription bills a plan, not tokens, so you are likely charged less.${spend.usage.unpriced.length
          ? ` No price known for ${spend.usage.unpriced
              .map((m) => `<span class="md-mono">${escapeHtml(m)}</span>`).join(", ")}.` : ""}${
        spanStart ? ` First request ${escapeHtml(ago(Date.now() / 1000 + app.skew - spanStart))}, last
          ${escapeHtml(ago(Date.now() / 1000 + app.skew - spanEnd))}.` : ""}</p>
    </section>`;
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
         <span class="meta-sep">·</span> <span class="fact-note">last written</span>`
      : "not written down yet"],
    ["Process", session.pid ? `<span class="md-mono">pid ${session.pid}</span>` : "not running"],
    ["Claude Code", escapeHtml(session.version || "—")],
    ["Session id", `<span class="md-mono">${escapeHtml(session.sessionId)}</span>
      <button class="icon-button md-state fact-copy" data-copy="id" type="button"
        title="Copy session id" aria-label="Copy session id">${ICON.copy}</button>${
      session.kind === "child"
        ? ` <span class="meta-sep">·</span> <span class="fact-note">the panel's own</span>` : ""}`],
    ["Started", `${started.toLocaleString()} <span class="meta-sep">·</span> up ${duration(Date.now() / 1000 + app.skew - session.startedAt)}`],
    ["Transcript", chat.transcript?.path ? `<span class="md-mono">${escapeHtml(shorten(chat.transcript.path, 2))}</span>` : "—"],
  ];
  // A session with no window of its own — kept, or run by the panel — would only
  // be misled by that section.
  const windowSection = runsHere(session) ? "" : `
    <section class="section">
      <h3 class="section__title md-title-small">Window</h3>
      <div class="row">
        <div class="row__grow">
          <p class="md-body-medium">${isAmbiguous(win)
            ? `${win.candidates?.length || 2} windows look equally likely, so the panel will not guess.`
            : win
              ? `Paired with <span class="md-mono">${escapeHtml(win.title || win.id)}</span>.`
              : "Not paired. Pair one to click through to it."}</p>
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
      <h3 class="section__title md-title-small">Pin</h3>
      <label class="switch">
        <input type="checkbox" id="stickyToggle" ${session.pinned ? "checked" : ""}>
        <span class="switch__track"><span class="switch__thumb">
          <svg viewBox="0 0 24 24" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
        </span></span>
        <span class="switch__label md-body-medium">Keep the row after this session closes</span>
      </label>
      ${session.kept && session.alive === false ? `<p class="hint md-label-small">${session.pinned
          ? `Survives a panel restart.`
          : `Lost when the panel restarts.`}${session.kind === "child"
          ? " A nested session leaves no conversation to resume." : ""}</p>` : ""}
    </section>
    <section class="section">
      <h3 class="section__title md-title-small">Notifications</h3>
      <div class="switch-stack">
        <label class="switch">
          <input type="checkbox" id="muteToggle" ${muted ? "" : "checked"}>
          <span class="switch__track"><span class="switch__thumb">
            <svg viewBox="0 0 24 24" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
          </span></span>
          <span class="switch__label md-body-medium">Notify me when it waits for an answer</span>
        </label>
        <label class="switch">
          <input type="checkbox" id="doneToggle" ${quiet ? "" : "checked"}${muted ? " disabled" : ""}>
          <span class="switch__track"><span class="switch__thumb">
            <svg viewBox="0 0 24 24" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
          </span></span>
          <span class="switch__label md-body-medium">Notify me when it finishes a turn</span>
        </label>
      </div>
    </section>
    <section class="section">
      <h3 class="section__title md-title-small">Facts</h3>
      <dl class="facts md-body-small">
        ${facts.map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${value}</dd>`).join("")}
      </dl>
    </section>
    ${(() => {
      // Stopping and removing are the same act, so they are the same section.
      // Which of the two words leads depends only on whether there is still a
      // process to stop; either way the row goes unless it was pinned.
      const running = session.alive || holding;
      // Neither running nor kept: the process has gone and the row is going with
      // it, so there is nothing here to press.
      const nothing = !running && !session.kept;
      const fate = session.pinned
        ? `It is pinned, so the row stays on the dashboard.`
        : `The row comes off the dashboard and the transcript stays on disk.`;
      const title = running || nothing
        ? (holding ? "Stop running it here" : "End session") : "Remove from the dashboard";
      const body = session.alive
        ? `Like <span class="md-mono">/exit</span> — ${escapeHtml(host.label)} stays open. ${fate}`
        : holding ? `The panel lets go of it. ${fate}`
        : nothing ? "Already gone; the row will drop off shortly."
        : "Nothing is running it. Type into it and it starts back up, or take the row off the list.";
      const label = running || nothing ? (holding ? "Stop it" : "End session") : "Remove";
      return `
    <section class="section">
      <h3 class="section__title md-title-small">${title}</h3>
      <div class="row">
        <div class="row__grow"><p class="md-body-medium">${body}</p></div>
        <div><button class="button button--danger-outlined md-state"
          data-act="${running || nothing ? "end" : "forget"}"${nothing ? " disabled" : ""}>${
          running || nothing ? ICON.power : ICON.trash} ${label}</button></div>
      </div>
    </section>`;
    })()}
    <section class="section">
      <button class="button button--text md-state" id="openAppearance">Panel settings</button>
    </section>`;
}
