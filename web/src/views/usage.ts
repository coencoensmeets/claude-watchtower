
import { MODE_LABELS, isAmbiguous, isRemembered, windowSays } from "../sessions/facts.js";
import { app, chat, spend } from "../state.js";
import { ago, duration, escapeHtml, shorten, tokens } from "../ui/format.js";
import { ICON } from "../ui/icons.js";

/* ==========================================================================
   Usage — what this session has asked of the models, and what that is worth
   at list price. Read out of the transcript, which is the only place the
   figures exist: every reply Claude Code writes down carries the usage the
   API reported for it.
   ========================================================================== */

/* Named here only to say them in the footnote; the server does the arithmetic. */
const CACHE_WRITE_5M = 1.25;
const CACHE_WRITE_1H = 2;

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
      ? `A session started from inside another writes no transcript of its own, so there
         is nothing here to total. Its usage lands in the transcript of the session that
         started it.`
      : "No model requests recorded yet — this session has not asked Claude anything."}</p>`;
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
        ? `Its last request carried <span class="md-mono">${tokens(context)}</span> tokens of
           context${spend.usage.contextModel ? ` into <span class="md-mono">${escapeHtml(spend.usage.contextModel)}</span>` : ""},
           which is ${full}% of that model's ${tokens(limit)} window.`
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
      <p class="md-body-medium">Work this session handed to agents of its own. It is the
        same bill, kept apart because it is not the conversation you are reading.</p>
      ${usageTable(spend.usage.agentModels)}
    </section>` : ""}

    <section class="section">
      <h3 class="section__title md-title-small">About these figures</h3>
      <p class="md-body-medium">Totalled from every reply in this session's transcript, at
        Anthropic's published per-token prices — a cache write costs ${CACHE_WRITE_5M}× fresh
        input for five minutes or ${CACHE_WRITE_1H}× for an hour, a cache read a tenth of it.
        What you are actually charged may be less: a Claude subscription bills a plan rather
        than tokens, and a negotiated rate is not the list one.${spend.usage.unpriced.length
          ? ` No price is known here for
            ${spend.usage.unpriced.map((m) => `<span class="md-mono">${escapeHtml(m)}</span>`).join(", ")},
            so its tokens are counted but its cost is not.` : ""}</p>
      ${spanStart ? `<p class="md-body-small fact-note">First request
        ${escapeHtml(ago(Date.now() / 1000 + app.skew - spanStart))}, last
        ${escapeHtml(ago(Date.now() / 1000 + app.skew - spanEnd))}.</p>` : ""}
    </section>`;
}

export function aboutPanel(session, host) {
  const win = session.window;
  const muted = app.mutedSessions.has(session.sessionId);
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
         <span class="meta-sep">·</span> <span class="fact-note">${session.status === "stopped"
           ? "the last one it wrote down" : "as of the last time it wrote its mode down"}</span>`
      : "not written down yet"],
    ["Process", session.pid ? `<span class="md-mono">pid ${session.pid}</span>` : "not running"],
    ["Claude Code", escapeHtml(session.version || "—")],
    ["Session id", `<span class="md-mono">${escapeHtml(session.sessionId)}</span>
      <button class="icon-button md-state fact-copy" data-copy="id" type="button"
        title="Copy session id" aria-label="Copy session id">${ICON.copy}</button>${
      session.kind === "child"
        ? ` <span class="meta-sep">·</span> <span class="fact-note">the panel's own:
            a nested session publishes none</span>` : ""}`],
    ["Started", `${started.toLocaleString()} <span class="meta-sep">·</span> up ${duration(Date.now() / 1000 + app.skew - session.startedAt)}`],
    ["Transcript", chat.transcript?.path ? `<span class="md-mono">${escapeHtml(shorten(chat.transcript.path, 2))}</span>` : "—"],
  ];
  // A stopped session owns no window, so that section would only mislead.
  const windowSection = session.status === "stopped" ? "" : `
    <section class="section">
      <h3 class="section__title md-title-small">Window</h3>
      <div class="row">
        <div class="row__grow">
          <p class="md-body-medium">${isAmbiguous(win)
            ? `${win.candidates?.length || 2} windows look equally likely — ${windowSays(win, "long")}, so the panel will not guess. Identify it and the terminal will say which one it is.`
            : win
              ? `Paired with <span class="md-mono">${escapeHtml(win.title || win.id)}</span> — ${windowSays(win, "long")}.`
              : "No window is paired yet. Pair one and a single click brings it to the front."}</p>
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
      <h3 class="section__title md-title-small">Keeping it</h3>
      <label class="switch">
        <input type="checkbox" id="stickyToggle" ${session.sticky ? "checked" : ""}>
        <span class="switch__track"><span class="switch__thumb">
          <svg viewBox="0 0 24 24" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
        </span></span>
        <span class="switch__label md-body-medium">${session.kind === "child"
          ? `Keep this one in the dashboard after it closes, so its folder stays here and a
             session can be started up in it again — a nested session leaves no conversation
             behind to keep`
          : `Keep this one in the dashboard after it closes, so the
             conversation stays here and can be started up again`}</span>
      </label>
    </section>
    <section class="section">
      <h3 class="section__title md-title-small">Notifications</h3>
      <label class="switch">
        <input type="checkbox" id="muteToggle" ${muted ? "" : "checked"}>
        <span class="switch__track"><span class="switch__thumb">
          <svg viewBox="0 0 24 24" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
        </span></span>
        <span class="switch__label md-body-medium">Notify me when this session waits for an answer</span>
      </label>
    </section>
    <section class="section">
      <h3 class="section__title md-title-small">Facts</h3>
      <dl class="facts md-body-small">
        ${facts.map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${value}</dd>`).join("")}
      </dl>
    </section>
    <section class="section">
      <h3 class="section__title md-title-small">End session</h3>
      <div class="row">
        <div class="row__grow">
          <p class="md-body-medium">${session.alive
            ? `Closes this Claude Code the way <span class="md-mono">/exit</span> would, leaving ${escapeHtml(host.label)} open. The transcript stays on disk.`
            : "This session's process has already gone; it will drop off the list shortly."}</p>
        </div>
        <div><button class="button button--danger-outlined md-state" data-act="end"${session.alive ? "" : " disabled"}>${ICON.power} End session</button></div>
      </div>
    </section>
    <section class="section">
      <button class="button button--text md-state" id="openAppearance">Appearance settings</button>
    </section>`;
}

/* ------------------------------------------------------------ notifications */
if ("Notification" in window && Notification.permission === "default") {
  document.addEventListener("click", () => Notification.requestPermission(), { once: true });
}
