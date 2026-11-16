import { app, past } from "../state.js";
import { escapeHtml } from "../ui/format.js";
import { ICON } from "../ui/icons.js";

/* ==========================================================================
   Sessions — the other conversations had in this session's folder, and going
   back to one. What `/resume` lists in a terminal: newest first, each named by
   the title Claude gave it, or else by what it was first asked.

   Drawn in the Watchtower way. A row is a tile in the field tone, and the
   whole tile is the press. What it does is said once, at its end: a quiet
   circle at rest that opens into the ink pill with its arrow riding in lime
   when you point at it — the same act the comment card's Done is drawn as, so
   one press reads as one press. Nothing else on a row is coloured. The one
   you are in is the exception, tinted lilac because lilac is what is active.

   Grouped by day, because "the one from yesterday afternoon" is how a
   conversation is remembered, and the time sits in a column of its own at the
   start of the row so the eye can run down it.
   ========================================================================== */

/* What a repaint has to notice: a new conversation, one that moved, one being
   opened, or what the list is filtered for. */
export function pastStamp(data) {
  if (!data) return "";
  return [data.more, past.opening ?? "", past.query,
    ...(data.sessions || []).map((x) => `${x.sessionId}:${x.lastAt}:${x.prompts}:${x.onList}`)].join("|");
}

/* Past this many, a filter is worth its room above the list. */
const FILTER_FROM = 6;

const DAY = 86400;

function dayStart(at) {
  const d = new Date(at * 1000);
  d.setHours(0, 0, 0, 0);
  return d.getTime() / 1000;
}

/* Which shelf a conversation goes on, by the calendar rather than by hours:
   last night at eleven is yesterday, not "today" because it was nine hours ago. */
function groupOf(at, now) {
  const today = dayStart(now);
  if (at >= today) return "Today";
  if (at >= today - DAY) return "Yesterday";
  if (at >= today - 6 * DAY) return "This week";
  if (at >= today - 30 * DAY) return "This month";
  return "Older";
}

/* The time column: a clock time within the week, where the group already says
   the day, and a date further back, where it does not. */
function whenOf(at, group) {
  const d = new Date(at * 1000);
  if (group === "Today" || group === "Yesterday") {
    return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }
  if (group === "This week") {
    return d.toLocaleDateString(undefined, { weekday: "short" });
  }
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function matches(x, query) {
  if (!query) return true;
  const hay = [x.title, x.first, x.last, x.branch].filter(Boolean).join(" ").toLowerCase();
  return query.toLowerCase().split(/\s+/).filter(Boolean).every((word) => hay.includes(word));
}

function pastRow(x, group, canOpen) {
  const name = x.title || x.first;
  // Under a title, what it was first asked; with no title the first prompt is
  // already the name, so the line is left out rather than said twice.
  const asked = x.title ? x.first : null;
  const opening = past.opening === x.sessionId;
  const inert = x.current || (!x.onList && !canOpen);
  const facts = [
    `<span class="past__fact">${x.prompts} ${x.prompts === 1 ? "prompt" : "prompts"}</span>`,
    x.branch ? `<span class="past__fact past__fact--branch md-mono">${ICON.branch}${escapeHtml(x.branch)}</span>` : "",
    x.current ? `<span class="past__here"><i></i>You're here</span>`
      : x.onList ? `<span class="past__fact past__fact--open">On the list</span>` : "",
  ].join("");
  const word = opening ? "Resuming" : x.onList ? "Show" : "Resume";
  const body = `
    <span class="past__when md-label-medium">${escapeHtml(whenOf(x.lastAt, group))}</span>
    <span class="past__text">
      <span class="past__name">${escapeHtml(name)}</span>
      ${asked ? `<span class="past__said md-body-medium">${escapeHtml(asked)}</span>` : ""}
      ${x.last ? `<span class="past__said past__said--last md-body-medium"><span class="past__said-l md-label-small">Last</span>${escapeHtml(x.last)}</span>` : ""}
      <span class="past__facts md-label-medium">${facts}</span>
    </span>
    ${inert ? "" : `<span class="past__act${opening ? " past__act--busy" : ""}" aria-hidden="true">
      <span class="past__word md-label-large">${word}</span>
      <span class="past__ride">${opening ? `<span class="past__spin"></span>` : ICON.up}</span>
    </span>`}`;
  if (inert) {
    return `<li class="past-item"><div class="past${x.current ? " past--current" : " past--inert"}">${body}</div></li>`;
  }
  return `<li class="past-item"><button type="button" class="past" data-past="${escapeHtml(x.sessionId)}"
      ${past.opening ? "disabled" : ""}
      aria-label="${escapeHtml(`${x.onList ? "Show" : "Resume"} ${name}`)}">${body}</button></li>`;
}

function emptyArt(title, line) {
  return `<div class="past-empty">
    <div class="past-empty__art">${ICON.rewind}</div>
    <h3 class="md-title-medium">${title}</h3>
    <p class="md-body-medium">${line}</p>
  </div>`;
}

export function pastPanel(session) {
  const data = past.for === session.sessionId ? past.data : null;
  if (!data) {
    return `<div class="past-loading" aria-busy="true">
      ${[0, 1, 2].map(() => `<div class="past-ghost"></div>`).join("")}
    </div>`;
  }
  if (data.ok === false) return emptyArt("Could not read them", escapeHtml(data.message || "The folder could not be read."));
  const rows = data.sessions || [];
  const others = rows.filter((x) => !x.current);
  const folder = (data.cwd || "").split("/").filter(Boolean).pop() || data.cwd || "";
  if (!others.length) {
    return emptyArt("No other conversations here yet",
      `Every conversation had in <span class="md-mono">${escapeHtml(data.cwd)}</span> is listed here, ready to pick back up — this is the first.`);
  }

  const now = Date.now() / 1000 + app.skew;
  const shown = rows.filter((x) => matches(x, past.query));
  const groups = new Map();
  for (const x of shown) {
    const g = groupOf(x.lastAt, now);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(x);
  }
  const total = others.length + (data.more || 0);

  return `
    <header class="past-top">
      <div class="past-count">
        <span class="past-count__n">${total}</span>
        <span class="past-count__l">
          <span class="md-title-small">${total === 1 ? "other conversation" : "other conversations"}</span>
          <span class="md-body-small">in <span class="md-mono" title="${escapeHtml(data.cwd)}">${escapeHtml(folder)}</span>${
            data.canOpen ? "" : " · picking one back up needs the panel on loopback"}</span>
        </span>
      </div>
      ${rows.length >= FILTER_FROM ? `<div class="past-filter">
        ${ICON.search}
        <input class="past-filter__field md-body-medium" id="pastSearch" type="search"
               placeholder="Filter by title, prompt or branch" value="${escapeHtml(past.query)}"
               aria-label="Filter conversations" autocomplete="off" spellcheck="false">
      </div>` : ""}
    </header>
    ${shown.length ? [...groups].map(([g, list]) => `
      <section class="past-group" aria-label="${escapeHtml(g)}">
        <h3 class="past-group__h md-label-large">${escapeHtml(g)}<span class="past-group__n">${list.length}</span></h3>
        <ul class="past-list">${list.map((x) => pastRow(x, g, data.canOpen)).join("")}</ul>
      </section>`).join("")
      : `<p class="past-none md-body-medium">Nothing here matches <strong>${escapeHtml(past.query)}</strong>.</p>`}
    ${data.more ? `<p class="past-more md-body-small">${data.more} older ${data.more === 1 ? "one is" : "ones are"} not listed — <span class="md-mono">claude --resume</span> in a terminal shows every one.</p>` : ""}`;
}
