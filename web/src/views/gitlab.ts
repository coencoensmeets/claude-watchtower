/* ==========================================================================
   GitLab — the project behind this session's repository, and what is open in
   it. The Git tab next door is about the checkout; this one is about the
   server it came from.

   Two depths in one pane, the way a subagent's conversation stands in front of
   the session's: the list of work items, and one work item in full. Opening an
   item does not leave the panel, because the point of reading it here is that
   the session working on it is one tab away.

   Every reading costs a round trip to somebody else's GitLab, so this does not
   run on the poll. It reads when the tab is opened, when the session changes,
   and when you ask.
   ========================================================================== */

import { refreshDetail } from "../refresh.js";
import { app, forge } from "../state.js";
import { askText } from "../ui/ask.js";
import { copyText } from "../ui/clipboard.js";
import { detailPane } from "../ui/dom.js";
import { ago, escapeHtml } from "../ui/format.js";
import { ICON } from "../ui/icons.js";
import { renderMarkdown } from "../ui/markdown.js";
import { showSnackbar } from "../ui/snackbar.js";

/* `ago` counts a number of seconds, not a moment in time, and everything GitLab
   sends is an ISO timestamp — handing it one straight reads the whole epoch as
   an age and says "690mo ago" on an item touched this morning. */
export const since = (iso) => ago((Date.now() - Date.parse(iso)) / 1000);

/* Slower than the Git tab's twenty seconds, and for a different reason. That
   one is cheap and local; this is a request to a server across the internet,
   for a list that changes when a person assigns you something. The server holds
   its own reading for about as long again, so a faster poll would mostly be
   answered from that cache having spent a request to find out. */
const GITLAB_POLL_MS = 120000;
let polledAt = 0;

export function gitlabStamp(g) {
  if (!g) return "";
  return [g.ok, g.reason ?? "", g.message ?? "", g.canWrite, g.me ?? "",
          g.project?.path ?? "", g.pipeline?.status ?? "",
          (g.items ?? []).map((i) => `${i.iid}:${i.updatedAt}:${i.mine}`).join(","),
          (g.mrs ?? []).map((m) => `${m.iid}:${m.draft}`).join(",")].join("|");
}

export async function fetchGitlab(force = false) {
  const id = app.selectedId;
  if (!id || forge.busy) return;
  if (!force && document.hidden) return;
  if (!force && forge.for === id && Date.now() - polledAt < GITLAB_POLL_MS) return;
  forge.busy = true;
  polledAt = Date.now();
  try {
    const query = new URLSearchParams({ sessionId: id, ...(force ? { force: "1" } : {}) });
    const response = await fetch(`/api/gitlab?${query}`, { cache: "no-store" });
    if (!response.ok && response.status !== 404) throw new Error(String(response.status));
    const data = await response.json();
    if (app.selectedId !== id) return;          // selection moved on while fetching
    const changed = gitlabStamp(data) !== gitlabStamp(forge.data) || forge.for !== id;
    forge.data = data;
    forge.for = id;
    if (changed) refreshDetail(true);
  } catch (error) {
    /* leave the previous reading on screen */
  } finally {
    forge.busy = false;
  }
}

async function fetchItem(iid, force = false) {
  const id = app.selectedId;
  if (!id || forge.detailBusy) return;
  forge.detailBusy = true;
  try {
    const query = new URLSearchParams({ sessionId: id, iid: String(iid),
                                        ...(force ? { force: "1" } : {}) });
    const response = await fetch(`/api/gitlab/item?${query}`, { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (app.selectedId !== id || forge.shown !== iid) return;
    forge.detail = data.ok ? data.item : null;
    forge.detailFor = data.ok ? iid : null;
    if (!data.ok) showSnackbar(data.message || "Could not read that work item", 6000);
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    forge.detailBusy = false;
    refreshDetail(true);
  }
}

/* ------------------------------------------------------------- the pieces */

/* A pipeline is the one thing here with a colour worth carrying: it is the
   answer to "can I push on top of this", and a word alone makes you read it. */
const PIPE_TONE = {
  success: "good", failed: "bad", running: "busy", pending: "wait",
  canceled: "muted", skipped: "muted", manual: "wait", created: "wait",
};

/* A label, drawn in the colour the project gave it.

   GitLab's scoped labels are `key::value`, and they are the spine of a board
   that uses them: `priority::high` is not a word, it is a field and its
   setting. So the two halves are drawn as two halves — the key in a quiet
   recess of the label's own colour, the value filled in it — which is how
   GitLab draws them, and which makes a row of six labels scannable by key
   instead of a hedge of identical pills. A plain label stays one pill. */
export function labelChip(label) {
  const colour = /^#[0-9a-f]{3,8}$/i.test(label.colour || "") ? label.colour : "";
  const on = /^#[0-9a-f]{3,8}$/i.test(label.onColour || "") ? label.onColour : "#fff";
  const style = colour ? ` style="--label: ${colour}; --on-label: ${on}"` : "";
  const scoped = label.name.includes("::");
  if (!scoped) {
    return `<span class="gl-label"${style}>${escapeHtml(label.name)}</span>`;
  }
  const cut = label.name.indexOf("::");
  return `<span class="gl-label gl-label--scoped"${style}>
    <span class="gl-label__key">${escapeHtml(label.name.slice(0, cut))}</span
    ><span class="gl-label__value">${escapeHtml(label.name.slice(cut + 2))}</span>
  </span>`;
}

/* Priority is a label like any other to GitLab, and not one to the person
   reading the list: it is the order to do things in. So it is lifted out of the
   labels and made the sort, highest first, with everything unlabelled after the
   labelled — an item nobody has triaged is not urgent, but it is not invisible
   either. Within a band, most recently touched first. */
const PRIORITY = ["critical", "urgent", "high", "medium", "low"];

/* A label is an object with a colour when GitLab was asked for the details, and
   a bare string when it was not — the single-issue endpoint takes no
   `with_labels_details`, and an older GitLab ignores it on the list too. Both
   shapes arrive here, so both are turned into the one shape at the edge rather
   than guarded against at each of the six places that read a label's name. */
export const asLabels = (item) => (item?.labels || []).map((label) =>
  typeof label === "string" ? { name: label, colour: "", onColour: "" } : label)
  .filter((label) => label?.name);

export const priorityOf = (item) => {
  const found = asLabels(item).map((l) => l.name)
    .find((n) => n.toLowerCase().startsWith("priority::"));
  const rank = found ? PRIORITY.indexOf(found.split("::")[1].toLowerCase()) : -1;
  return { name: found ? found.split("::")[1] : "", rank: rank === -1 ? PRIORITY.length : rank };
};

export function ordered(items) {
  return [...items].sort((a, b) => {
    const gap = priorityOf(a).rank - priorityOf(b).rank;
    if (gap) return gap;
    return String(b.updatedAt || "").localeCompare(String(a.updatedAt || ""));
  });
}

export function faces(people, me = "") {
  if (!people?.length) return "";
  // Assigned to you alone, on a list of things assigned to you: the initials
  // would be the same on every row, which is a column of noise rather than a
  // fact. Somebody else, or several people, is worth drawing.
  if (people.length === 1 && me && people[0].username === me) return "";
  return `<span class="gl-faces">${people.slice(0, 3).map((p) =>
    `<span class="gl-face" title="${escapeHtml(p.name)}">${escapeHtml(p.initials)}</span>`
  ).join("")}</span>`;
}

function itemRow(item, me = "") {
  const priority = priorityOf(item);
  const rest = asLabels(item).filter((l) =>
    !l.name.toLowerCase().startsWith("priority::"));
  return `
    <li class="gl-row" data-iid="${item.iid}" data-rank="${priority.rank}">
      <button class="gl-row__open md-state" type="button" data-act="open-item" data-iid="${item.iid}">
        <span class="gl-row__rail" data-priority="${escapeHtml(priority.name.toLowerCase())}"
              aria-hidden="true"></span>
        <span class="gl-row__body">
          <span class="gl-row__head">
            <span class="gl-row__num md-mono md-label-medium">#${item.iid}</span>
            <span class="gl-row__title md-body-medium">${escapeHtml(item.title)}</span>
          </span>
          <span class="gl-row__meta md-label-small">
            ${priority.name ? `<span class="gl-priority" data-priority="${
              escapeHtml(priority.name.toLowerCase())}">${escapeHtml(priority.name)}</span>` : ""}
            ${rest.map(labelChip).join("")}
            ${item.milestone ? `<span class="gl-meta-fact">${ICON.flag}${escapeHtml(item.milestone)}</span>` : ""}
            ${item.comments ? `<span class="gl-meta-fact">${ICON.chat}${item.comments}</span>` : ""}
            <span class="gl-meta-fact gl-meta-fact--quiet">${item.updatedAt
              ? escapeHtml(since(item.updatedAt)) : ""}</span>
          </span>
        </span>
        ${faces(item.assignees, me)}
      </button>
    </li>`;
}

/* Why there is nothing to show, said in the words of the thing to do about it.
   Each of these is something you can go and fix, and only one is about the
   panel, so each one names the command or the fact rather than the failure. */
function gitlabNotice(data) {
  if (!data) return `<p class="git-empty md-body-medium">Asking GitLab…</p>`;
  if (data.ok) return "";
  const host = escapeHtml(data.project?.host || "");
  const fix = {
    "no-cli": `Install the GitLab CLI — <span class="md-mono">glab</span> — and this tab reads the project with it.`,
    "not-logged-in": `Run <span class="md-mono">glab auth login --hostname ${host}</span> in a terminal, then ask again.`,
    unauthorized: `Run <span class="md-mono">glab auth login --hostname ${host}</span> — the token it has was turned down.`,
    "not-gitlab": `This tab is for repositories hosted on a GitLab. Nothing to fix.`,
    offline: `It may be behind a VPN that is not up.`,
  }[data.reason] || "";
  return `<div class="git-empty md-body-medium">
    <p>${escapeHtml(data.message || "GitLab did not answer")}</p>
    ${fix ? `<p class="fact-note">${fix}</p>` : ""}
  </div>`;
}

/* --------------------------------------------------------------- compose */

/* Filing a work item, in the pane rather than in a dialog — the same shape the
   Git tab's commit box has, and for the same reason: what you are writing sits
   above the list it is about, and a dialog would cover the one thing you might
   want to read while writing it. */
function composeCard(data) {
  if (!forge.composing) return "";
  // The labels this project actually uses, taken from what is open in it —
  // GitLab has a labels endpoint, but the ones already on the board are the
  // ones you are going to reach for, and they cost no extra request. Kept as
  // whole labels rather than names so the picker draws them the way the rows
  // do: picking `priority::high` should look like the thing it will become.
  const seen = new Map<string, any>();
  for (const item of data.items || []) {
    for (const label of asLabels(item)) {
      if (!seen.has(label.name)) seen.set(label.name, label);
    }
  }
  const labels = [...seen.values()].sort((a, b) => a.name.localeCompare(b.name)).slice(0, 18);
  const picked = new Set(forge.draft.labels.split(",").map((s) => s.trim()).filter(Boolean));
  return `
    <form class="gl-compose" id="glCompose">
      <input class="gl-compose__title md-title-small" id="glTitle" name="title"
             placeholder="What needs doing" autocomplete="off"
             value="${escapeHtml(forge.draft.title)}" aria-label="Title">
      <textarea class="gl-compose__body md-body-medium" id="glBody" name="body" rows="3"
        placeholder="Anything more — what should happen, and how you would know it had"
        aria-label="Description">${escapeHtml(forge.draft.body)}</textarea>
      ${labels.length ? `<div class="gl-compose__labels">
        ${labels.map((label) => `<button class="gl-label-pick md-state" type="button"
            data-label="${escapeHtml(label.name)}"
            aria-pressed="${picked.has(label.name)}"
          >${labelChip(label)}</button>`).join("")}
      </div>` : ""}
      <div class="gl-compose__foot">
        <span class="gl-compose__says md-label-small">Filed on
          <span class="md-mono">${escapeHtml(data.project?.path || "")}</span>, assigned to you</span>
        <button class="button button--text md-state" type="button" data-act="compose-cancel">Cancel</button>
        <button class="button button--filled md-state" type="submit" id="glFile"
          ${forge.filing || !forge.draft.title.trim() ? "disabled" : ""}>${
          forge.filing ? "Filing…" : "File it"}</button>
      </div>
    </form>`;
}

/* ------------------------------------------------------------ one item */

/* GitLab hands back label colours on the list endpoint and not on the one for a
   single issue — `with_labels_details` is a list parameter. The colours are
   already here, though: an item is only ever opened from the list it is in. So
   the detail borrows them rather than spending a request on the project's label
   registry to learn what it was just told. */
function coloured(labels, from) {
  const known = new Map();
  for (const item of from || []) {
    for (const label of asLabels(item)) {
      if (label.colour) known.set(label.name, label);
    }
  }
  return (labels || []).map((label) =>
    label.colour ? label : known.get(label.name) || label);
}

function detailPanel(data) {
  const item = forge.detailFor === forge.shown ? forge.detail : null;
  const back = `<button class="button button--text md-state gl-detail__back" type="button"
      data-act="close-item">${ICON.back}All work items</button>`;
  if (!item) {
    return `<div class="gl-detail">
      <div class="gl-detail__bar">${back}</div>
      <p class="git-empty md-body-medium">${forge.detailBusy
        ? "Reading it…" : "That work item could not be read."}</p>
    </div>`;
  }
  const priority = priorityOf(item);
  const rest = coloured(asLabels(item), data?.items).filter((l) =>
    !l.name.toLowerCase().startsWith("priority::"));
  const notes = item.notes || [];
  return `
    <article class="gl-detail">
      <div class="gl-detail__bar">
        ${back}
        <a class="gl-detail__out md-label-large" href="${escapeHtml(item.webUrl || "#")}"
           target="_blank" rel="noopener">${ICON.focus}Open in GitLab</a>
      </div>

      <header class="gl-detail__head" data-priority="${escapeHtml(priority.name.toLowerCase())}">
        <h2 class="gl-detail__title md-headline-small">${escapeHtml(item.title)}</h2>
        <p class="gl-detail__who md-body-small">
          <span class="md-mono">${escapeHtml(item.reference || `#${item.iid}`)}</span>
          ${item.author?.name ? ` · opened by ${escapeHtml(item.author.name)}` : ""}
          ${item.createdAt ? ` ${escapeHtml(since(item.createdAt))}` : ""}
        </p>
        ${priority.name || rest.length ? `<div class="gl-detail__labels">
          ${priority.name ? `<span class="gl-priority" data-priority="${
            escapeHtml(priority.name.toLowerCase())}">${escapeHtml(priority.name)} priority</span>` : ""}
          ${rest.map(labelChip).join("")}
        </div>` : ""}
      </header>

      <div class="gl-actions">
        <button class="button button--filled md-state" type="button" data-act="start-item"
          ${forge.acting ? "disabled" : ""}>${ICON.play}Start a session on this</button>
        ${data.canWrite ? `<button class="button button--tonal md-state" type="button"
          data-act="branch-item" ${forge.acting ? "disabled" : ""}>${ICON.branch}Branch for it</button>` : ""}
        <button class="button button--text md-state" type="button" data-act="copy-ref"
          title="Copy ${escapeHtml(item.reference || `#${item.iid}`)}">${ICON.copy}Copy its number</button>
      </div>

      <div class="gl-body md-body-medium">${item.description
        ? renderMarkdown(item.description)
        : `<p class="fact-note">No description. The title is the whole of it.</p>`}</div>

      ${notes.length ? `<section class="gl-notes">
        <h3 class="section__title md-title-small">${notes.length === 1
          ? "One comment" : `Last ${notes.length} comments`}</h3>
        ${notes.map((note) => `<article class="gl-note">
          <header class="gl-note__head md-label-medium">
            <span class="gl-face">${escapeHtml((note.author || "?").split(/[\s._-]+/)
              .map((p) => p[0]).slice(0, 2).join("").toUpperCase())}</span>
            <span>${escapeHtml(note.author || "someone")}</span>
            <span class="gl-note__when md-label-small">${note.at
              ? escapeHtml(since(note.at)) : ""}</span>
          </header>
          <div class="gl-note__body md-body-medium">${renderMarkdown(note.body || "")}</div>
        </article>`).join("")}
      </section>` : ""}
    </article>`;
}

/* --------------------------------------------------------------- the tab */

export function gitlabPanel(session) {
  const data = forge.for === session.sessionId ? forge.data : null;
  const project = data?.project;

  const head = project ? `
    <header class="gl-head">
      <div class="gl-head__where">
        <a class="gl-head__project md-title-small" href="${escapeHtml(project.url)}"
           target="_blank" rel="noopener">${escapeHtml(project.path)}</a>
        <span class="gl-head__host md-label-small">${escapeHtml(project.host)}</span>
      </div>
      <div class="gl-head__acts">
        ${data?.pipeline ? `<a class="gl-pipe md-label-small" data-tone="${
            PIPE_TONE[data.pipeline.status] || "muted"}" target="_blank" rel="noopener"
            href="${escapeHtml(data.pipeline.webUrl || "#")}"
            title="The last pipeline on this branch">${escapeHtml(data.pipeline.status)}</a>` : ""}
        <button class="scm-icon md-state" type="button" id="gitlabRefresh"
          title="Ask GitLab again" aria-label="Ask GitLab again">${ICON.refresh}</button>
      </div>
    </header>` : "";

  const notice = gitlabNotice(data);
  if (notice) return head + notice;

  // One work item, standing in front of the list it came from.
  if (forge.shown !== null) return head + detailPanel(data);

  const items = data.items || [];
  const mine = items.filter((i) => i.mine);
  const shown = forge.scope === "mine" ? mine : items;
  const found = !forge.query ? shown : shown.filter((item) =>
    `${item.title} #${item.iid} ${asLabels(item).map((l) => l.name).join(" ")}`
      .toLowerCase().includes(forge.query.toLowerCase()));

  const chip = (key, label, count) => `<button class="gl-chip md-state md-label-large"
      type="button" data-scope="${key}" aria-pressed="${forge.scope === key}">${label}
      <span class="gl-chip__count">${count}</span></button>`;

  const mrs = data.mrs || [];
  return `
    ${head}
    <div class="gl-bar">
      <div class="gl-chips" role="group" aria-label="Which work items to show">
        ${chip("mine", "Yours", mine.length)}
        ${chip("all", "All open", items.length)}
      </div>
      <input class="gl-search md-body-medium" id="glSearch" type="search"
             placeholder="Filter" value="${escapeHtml(forge.query)}"
             aria-label="Filter these work items">
      ${data.canWrite ? `<button class="button button--tonal md-state md-label-large"
        type="button" data-act="compose">${ICON.plus}New</button>` : ""}
    </div>

    ${composeCard(data)}

    ${found.length
      ? `<ul class="gl-list gl-list--items">${ordered(found).map((item) => itemRow(item, data.me || "")).join("")}</ul>`
      : `<p class="git-empty md-body-medium">${
          forge.query ? "Nothing matches that."
          : forge.scope === "mine"
            ? (items.length
                ? `Nothing here is assigned to you. ${items.length} open in the project — see <b>All open</b>.`
                : "Nothing open in this project.")
            : "Nothing open in this project."}</p>`}

    ${mrs.length ? `<section class="section">
      <h3 class="section__title md-title-small">Merge requests from this branch</h3>
      <ul class="gl-list gl-list--mrs">${mrs.map((mr) => `
        <li class="gl-row gl-row--flat">
          <a class="gl-row__open gl-row__open--link md-state" href="${escapeHtml(mr.webUrl || "#")}"
             target="_blank" rel="noopener">
            <span class="gl-row__rail" aria-hidden="true"></span>
            <span class="gl-row__body">
              <span class="gl-row__head">
                <span class="gl-row__num md-mono md-label-medium">!${mr.iid}</span>
                <span class="gl-row__title md-body-medium">${escapeHtml(mr.title)}</span>
              </span>
              <span class="gl-row__meta md-label-small">
                ${mr.draft ? `<span class="gl-priority" data-priority="draft">draft</span>` : ""}
                <span class="gl-meta-fact gl-meta-fact--quiet">into ${escapeHtml(mr.target || "")}</span>
              </span>
            </span>
          </a>
        </li>`).join("")}</ul>
    </section>` : ""}`;
}

/* ------------------------------------------------------------------ acting */

export async function startOn(host: string, project: string, iid: number, pick = false) {
  try {
    const response = await fetch("/api/gitlab/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host, project, iid, pick }),
    });
    const data = await response.json().catch(() => ({}));
    showSnackbar(data.message || (data.ok ? "Starting it up…" : "That did not work"),
                 data.ok ? 4000 : 8000);
    return Boolean(data.ok);
  } catch (error) {
    showSnackbar("Could not reach the server");
    return false;
  }
}

/* The branch GitLab itself would make for this item: its number, then its title
   in lower case with everything that is not a word turned into a single dash.
   Proposed rather than imposed — it goes in a box you can edit before it runs,
   because a branch name is one of the few things here that outlives the click. */
function branchNameFor(item) {
  const slug = String(item.title || "").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60)
    .replace(/-+$/g, "");
  return `${item.iid}-${slug || "work-item"}`;
}

async function branchFor(item) {
  const name = await askText({
    headline: "Branch for this work item",
    body: `Created from where you are now, in this session's checkout.`,
    value: branchNameFor(item),
    confirmLabel: "Create and switch",
  });
  if (!name) return;
  forge.acting = true;
  refreshDetail(true);
  try {
    const response = await fetch("/api/git", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: app.selectedId, action: "switch",
                             branch: name, create: true }),
    });
    const data = await response.json().catch(() => ({}));
    showSnackbar(data.message || (data.ok ? "On it" : "That did not work"),
                 data.ok ? 5000 : 8000);
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    forge.acting = false;
    refreshDetail(true);
  }
}

async function fileItem(session) {
  const title = forge.draft.title.trim();
  if (!title || forge.filing) return;
  forge.filing = true;
  refreshDetail(true);
  try {
    const response = await fetch("/api/gitlab/issue", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: session.sessionId, title,
                             description: forge.draft.body,
                             labels: forge.draft.labels, assignToMe: true }),
    });
    const data = await response.json().catch(() => ({}));
    showSnackbar(data.message || (data.ok ? "Filed" : "That did not work"),
                 data.ok ? 5000 : 8000);
    if (data.ok) {
      forge.composing = false;
      forge.draft = { title: "", body: "", labels: "" };
      fetchGitlab(true);
    }
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    forge.filing = false;
    refreshDetail(true);
  }
}

export function openItem(iid) {
  forge.shown = iid;
  if (forge.detailFor !== iid) forge.detail = null;
  refreshDetail(true);
  fetchItem(iid);
}

export function closeItem() {
  if (forge.shown === null) return false;
  forge.shown = null;
  refreshDetail(true);
  return true;
}

export function wireGitlab(session) {
  const data = forge.for === session.sessionId ? forge.data : null;
  const on = (selector, event, handler) => {
    for (const el of detailPane.querySelectorAll<HTMLElement>(selector)) {
      el.addEventListener(event, handler);
    }
  };

  detailPane.querySelector("#gitlabRefresh")?.addEventListener("click", () => {
    if (forge.shown !== null) { forge.detail = null; fetchItem(forge.shown, true); return; }
    fetchGitlab(true);
  });

  on('[data-scope]', "click", (event) => {
    forge.scope = (event.currentTarget as HTMLElement).dataset.scope;
    localStorage.setItem("cbu-forge-scope", forge.scope);
    refreshDetail(true);
  });

  const search = detailPane.querySelector<HTMLInputElement>("#glSearch");
  search?.addEventListener("input", () => {
    forge.query = search.value;
    refreshDetail(true);
    // The pane is rebuilt under the field, so the caret is put back where it was.
    const again = detailPane.querySelector<HTMLInputElement>("#glSearch");
    again?.focus();
    again?.setSelectionRange(again.value.length, again.value.length);
  });

  on('[data-act="open-item"]', "click", (event) =>
    openItem(Number((event.currentTarget as HTMLElement).dataset.iid)));
  on('[data-act="close-item"]', "click", () => closeItem());

  on('[data-act="compose"]', "click", () => {
    forge.composing = true;
    refreshDetail(true);
    detailPane.querySelector<HTMLInputElement>("#glTitle")?.focus();
  });
  on('[data-act="compose-cancel"]', "click", () => {
    forge.composing = false;
    refreshDetail(true);
  });

  /* The draft is kept in state rather than read off the form at submit, because
     a poll can rebuild the pane mid-sentence. Each keystroke writes it down and
     the redraw is deliberately not done — the field already shows the letter. */
  const title = detailPane.querySelector<HTMLInputElement>("#glTitle");
  title?.addEventListener("input", () => {
    const was = Boolean(forge.draft.title.trim());
    forge.draft.title = title.value;
    // Only when the submit button's state actually changes, so typing does not
    // rebuild the pane on every letter.
    if (was !== Boolean(title.value.trim())) {
      const file = detailPane.querySelector<HTMLButtonElement>("#glFile");
      if (file) file.disabled = !title.value.trim();
    }
  });
  const body = detailPane.querySelector<HTMLTextAreaElement>("#glBody");
  body?.addEventListener("input", () => { forge.draft.body = body.value; });

  on("[data-label]", "click", (event) => {
    const button = event.currentTarget as HTMLElement;
    const name = button.dataset.label;
    const picked = new Set(forge.draft.labels.split(",").map((s) => s.trim()).filter(Boolean));
    picked.has(name) ? picked.delete(name) : picked.add(name);
    forge.draft.labels = [...picked].join(",");
    button.setAttribute("aria-pressed", String(picked.has(name)));
  });

  detailPane.querySelector("#glCompose")?.addEventListener("submit", (event) => {
    event.preventDefault();
    fileItem(session);
  });

  on('[data-act="start-item"]', "click", async () => {
    const item = forge.detail;
    if (!item || !data?.project) return;
    forge.acting = true;
    refreshDetail(true);
    await startOn(data.project.host, data.project.path, item.iid);
    forge.acting = false;
    refreshDetail(true);
  });

  on('[data-act="branch-item"]', "click", () => {
    if (forge.detail) branchFor(forge.detail);
  });

  on('[data-act="copy-ref"]', "click", () => {
    const item = forge.detail;
    if (!item) return;
    copyText(item.reference || `#${item.iid}`, "Reference copied");
  });
}
