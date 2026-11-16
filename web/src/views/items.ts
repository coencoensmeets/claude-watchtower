/* ==========================================================================
   The work items page — everything assigned to you, on every GitHub and
   GitLab this machine is logged in to, and one press to be working on any of it.

   This is a launcher, not a report. The moment it exists for is the one before
   there is a session: you have four projects and eighteen things assigned to
   you, and what you want is to pick one and be writing code. So the whole row
   is the button, the folder each project lives in is stated before you press
   anything, and the list is ordered the way you would work through it rather
   than the way GitLab returns it.

   It draws the same row the GitLab tab draws — the priority rail, the scoped
   labels in the project's own colours — because they are the same object seen
   from two places, and one of them being prettier than the other is just a
   thing nobody got round to.
   ========================================================================== */

import { refreshDetail } from "../refresh.js";
import { app, work } from "../state.js";
import { detailPane, itemsButton, panes } from "../ui/dom.js";
import { escapeHtml, shorten } from "../ui/format.js";
import { ICON } from "../ui/icons.js";
import { asLabels, forgeName, isPriority, labelChip, ordered, priorityOf, since, startOn, talk } from "./gitlab.js";

export async function fetchItems(force = false) {
  if (work.busy) return;
  work.busy = true;
  if (force) { work.items = null; refreshDetail(); }
  try {
    const response = await fetch(`/api/forge/items${force ? "?force=1" : ""}`,
                                 { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    work.items = Array.isArray(data.items) ? data.items : [];
    work.trouble = Array.isArray(data.trouble) ? data.trouble : [];
    work.canWrite = Boolean(data.canWrite);
    work.canPick = Boolean(data.canPick);
    work.message = data.ok ? "" : (data.message || "Nothing answered");
  } catch (error) {
    work.items = [];
    work.message = "Could not reach the server";
  } finally {
    work.busy = false;
    refreshDetail();
  }
}

const keyOf = (item) => `${item.host}/${item.project}#${item.iid}`;

/* Grouped by project, because the project is the folder, and the folder is what
   makes the press work at all. Within a group the items are in the order you
   would take them; the groups themselves are ordered by their most urgent item,
   so the thing to do next is at the top of the page rather than wherever that
   project's name happens to sort. A project with no checkout on this machine
   goes last however urgent it is — it cannot be started on without a detour. */
function byProject(items) {
  const groups = new Map();
  for (const item of items) {
    const key = `${item.host}/${item.project}`;
    if (!groups.has(key)) {
      groups.set(key, { host: item.host, project: item.project,
                        folder: item.folder, items: [] });
    }
    groups.get(key).items.push(item);
  }
  const ranked = [...groups.values()].map((group) => ({
    ...group,
    items: ordered(group.items),
    rank: Math.min(...group.items.map((i) => priorityOf(i).rank)),
  }));
  return ranked.sort((a, b) =>
    Number(Boolean(b.folder)) - Number(Boolean(a.folder)) || a.rank - b.rank
    || a.project.localeCompare(b.project));
}

function itemRow(item, ready) {
  const key = keyOf(item);
  const starting = work.starting === key;
  const priority = priorityOf(item);
  const labels = asLabels(item).filter((l) =>
    !isPriority(l.name));
  return `
    <li class="gl-row gl-row--launch" data-key="${escapeHtml(key)}">
      <button class="gl-row__open md-state" type="button" data-start="${escapeHtml(key)}"
        ${starting ? "disabled" : ""}
        title="${ready ? "Start a session on this, in that folder"
                       : "Pick the folder, then start a session on it"}">
        <span class="gl-row__rail" data-priority="${
          escapeHtml(priority.name.toLowerCase())}" aria-hidden="true"></span>
        <span class="gl-row__body">
          <span class="gl-row__head">
            <span class="gl-row__num md-mono md-label-medium">#${item.iid}</span>
            <span class="gl-row__title md-body-medium">${escapeHtml(item.title)}</span>
          </span>
          <span class="gl-row__meta md-label-small">
            ${priority.name ? `<span class="gl-priority" data-priority="${
              escapeHtml(priority.name.toLowerCase())}">${escapeHtml(priority.name)}</span>` : ""}
            ${labels.map(labelChip).join("")}
            ${item.milestone ? `<span class="gl-meta-fact">${ICON.flag}${
              escapeHtml(item.milestone)}</span>` : ""}
            <span class="gl-meta-fact gl-meta-fact--quiet">${item.updatedAt
              ? escapeHtml(since(item.updatedAt)) : ""}</span>
          </span>
        </span>
        ${talk(item)}
        <span class="gl-go md-label-large" data-ready="${ready}">
          ${starting ? ICON.sync : ready ? ICON.play : ICON.folder}
          <span class="gl-go__word">${starting ? "Starting…"
            : ready ? "Start" : "Pick a folder"}</span>
        </span>
      </button>
      <a class="gl-row__out md-state" href="${escapeHtml(item.webUrl || "#")}"
         target="_blank" rel="noopener" title="Open #${item.iid} in ${forgeName(item.forge)}"
         aria-label="Open #${item.iid} in ${forgeName(item.forge)}">${ICON.focus}</a>
    </li>`;
}

/* The folder is the precondition for everything on this page, so it is stated
   in the group's own header rather than repeated on every row — and when there
   is not one, that is the headline of the group rather than a footnote on it. */
function group(entry) {
  const ready = Boolean(entry.folder);
  return `
    <section class="wi-group" data-ready="${ready}">
      <header class="wi-group__head">
        <h3 class="wi-group__title md-title-small">${escapeHtml(entry.project)}</h3>
        <span class="wi-group__count md-label-small">${entry.items.length}</span>
        <span class="wi-group__where md-label-small">${ready
          ? `${ICON.folder}${escapeHtml(shorten(entry.folder, 2))}`
          : `<span class="wi-group__missing">${ICON.folder}no checkout here yet</span>`}</span>
        <span class="wi-group__host md-label-small">${escapeHtml(entry.host)}</span>
      </header>
      <ul class="gl-list gl-list--items">${entry.items.map((item) =>
        itemRow(item, ready)).join("")}</ul>
    </section>`;
}

export function itemsPage() {
  const items = work.items;
  const filtered = !work.query ? items : (items || []).filter((item) =>
    `${item.title} #${item.iid} ${item.project} ${asLabels(item).map(
      (l) => l.name).join(" ")}`
      .toLowerCase().includes(work.query.toLowerCase()));

  const body = items === null
    ? `<p class="git-empty md-body-medium">Asking every GitHub and GitLab you are logged in to…</p>`
    : work.message
      ? `<div class="git-empty md-body-medium"><p>${escapeHtml(work.message)}</p>
         <p class="fact-note">The panel reads these through <span class="md-mono">gh</span> and
         <span class="md-mono">glab</span>. <b>Settings → Work items</b> logs them in.</p></div>`
      : !items.length
        ? `<p class="git-empty md-body-medium">Nothing is assigned to you right now.</p>`
        : !filtered.length
          ? `<p class="git-empty md-body-medium">Nothing matches that.</p>`
          : byProject(filtered).map(group).join("");

  // The count belongs in the header's supporting line, where the settings page
  // puts the sentence saying what its page is for.
  const lead = items?.length
    ? `${items.length} thing${items.length === 1 ? "" : "s"} assigned to you${
        work.query ? `, ${filtered.length} shown` : ""}`
    : "";

  // The same furniture the settings page is built from — a detail header and a
  // tab panel — because this is the other full-pane page in the panel and the
  // two should not be two different kinds of thing. The pane's own scrolling
  // lives on .tab-panel; a page that skips it scrolls the whole pane instead.
  return `
    <header class="detail-header detail-header--plain">
      <div class="detail-header__top">
        <div class="detail-header__text">
          <div class="detail-header__title"><h2 class="md-headline-small">Work items</h2></div>
          <div class="detail-header__meta"><span class="md-body-small">${lead
            ? `${escapeHtml(lead)} — pressing one opens a terminal in its folder, briefed`
            : "Everything assigned to you, on every GitHub and GitLab this machine can read"}</span></div>
        </div>
        <div class="detail-header__actions">
          <button class="button button--outlined md-state" id="closeItems">Done</button>
        </div>
      </div>
    </header>
    <div class="tab-panel">
      <div class="gl-bar">
        <input class="gl-search md-body-medium" id="itemSearch" type="search"
               placeholder="Filter by title, project or label" value="${escapeHtml(work.query)}"
               aria-label="Filter work items">
        <button class="scm-icon md-state" type="button" id="itemsRefresh"
          title="Ask them all again" aria-label="Ask them all again">${ICON.refresh}</button>
      </div>
      ${(work.trouble || []).map((t) => `<p class="wi-trouble md-body-small">${
        escapeHtml(t.message)}</p>`).join("")}
      ${body}
    </div>`;
}

/* The page is redrawn from one string like the settings page is, and like that
   one it is skipped when nothing it draws from has moved — the panel repaints on
   every poll, and rebuilding this under a filter being typed into would take the
   field out from under the caret. Everything the page reads is in the stamp. */
function itemsStamp() {
  return ["items", work.busy, work.query, work.starting ?? "", work.message,
          work.canWrite, work.canPick, (work.trouble || []).length,
          work.items === null ? "-" : work.items.map(
            (i) => `${i.host}/${i.project}#${i.iid}:${i.updatedAt}:${i.folder || ""}`).join(",")
         ].join("|");
}

export function paintItems() {
  const stamp = itemsStamp();
  if (detailPane.dataset.signature === stamp) return;
  detailPane.dataset.signature = stamp;
  detailPane.innerHTML = itemsPage();
  wireItems();
}

function wireItems() {
  detailPane.querySelector("#closeItems")?.addEventListener("click", () => openItems(false));
  detailPane.querySelector("#itemsRefresh")?.addEventListener("click", () => fetchItems(true));
  const search = detailPane.querySelector<HTMLInputElement>("#itemSearch");
  search?.addEventListener("input", () => {
    work.query = search.value;
    refreshDetail();
    // The page is rebuilt under the field, so the caret has to be put back —
    // the same problem the session rename has, solved the same way.
    const again = detailPane.querySelector<HTMLInputElement>("#itemSearch");
    again?.focus();
    again?.setSelectionRange(again.value.length, again.value.length);
  });
  for (const button of detailPane.querySelectorAll<HTMLElement>("[data-start]")) {
    button.addEventListener("click", async () => {
      const key = button.dataset.start;
      const item = (work.items || []).find((i) => keyOf(i) === key);
      if (!item || work.starting) return;
      work.starting = key;
      refreshDetail();
      // No folder found for this project, so the desktop's own chooser opens on
      // the server side and the session starts wherever you point it.
      const ok = await startOn(item.host, item.project, item.iid, !item.folder);
      work.starting = null;
      // A folder just chosen by hand is one the panel now knows, so the rest of
      // that project's rows stop offering to ask again.
      if (ok && !item.folder) fetchItems(true);
      else refreshDetail();
    });
  }
}

export function openItems(open) {
  app.showingItems = open;
  if (open) app.showingSettings = false;
  itemsButton.setAttribute("aria-pressed", String(open));
  if (open) panes.dataset.view = "detail";
  // The pane it is leaving is a session's, and that session's signature is
  // still on it — clear it or the rebuild is skipped as a repeat.
  detailPane.dataset.signature = "";
  refreshDetail();
  if (open) {
    detailPane.querySelector<HTMLElement>("#closeItems")?.focus();
    // First opening asks; afterwards the list is whatever it was, and the
    // refresh button is right there.
    if (work.items === null) fetchItems();
  } else {
    itemsButton.focus();
  }
}
