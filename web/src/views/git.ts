import { run } from "../net.js";
import { refreshDetail } from "../refresh.js";
import { app, commitDrafts, repo } from "../state.js";
import { askConfirm, askText } from "../ui/ask.js";
import { detailPane } from "../ui/dom.js";
import { ago, clip, escapeHtml, plural } from "../ui/format.js";
import { ICON } from "../ui/icons.js";
import { openMenu } from "../ui/menu.js";
import type { MenuItem } from "../ui/menu.js";
import type { GitFile } from "../types.js";
import { showSnackbar } from "../ui/snackbar.js";

/* Reading a repository costs a subprocess or three, unlike the transcript's file
   read, so this runs on its own much slower clock rather than on every poll.

   Slow on purpose. A working tree does not change on its own — a session or you
   changes it — and everything the panel itself does re-reads immediately, as does
   opening the tab or switching session. So this interval only governs how long a
   change made *elsewhere* takes to show up, which is worth far less than running
   git against somebody's repository every couple of seconds all day. */
let gitPolledAt = 0;
const GIT_POLL_MS = 20000;

export function gitStamp(g) {
  // upstream belongs here: publishing a branch changes nothing else about the
  // repository, and without it the header would go on saying "no upstream".
  return [g?.ok, g?.isRepo, g?.canWrite, g?.head ?? "", g?.branch ?? "", g?.upstream ?? "",
          g?.detached, g?.ahead ?? 0, g?.behind ?? 0,
          g?.stashes ?? 0, g?.commits?.[0]?.sha ?? "", g?.commits?.length ?? -1,
          // The branch menu is built when it opens, from whatever this holds, so a
          // branch appearing or going has to reach the pane.
          (g?.branches?.local ?? []).map((b) => `${b.name}${b.current ? "*" : ""}`).join(","),
          (g?.branches?.remote ?? []).map((b) => b.name).join(","),
          // Each side keeps its own column, filled with a dot when it is empty.
          // Run them together and staging a modified file reads the same either
          // way — "M" and "M" — so the pane would never notice it had moved.
          (g?.files ?? []).map((f) =>
            `${f.path}:${f.staged ?? "."}${f.unstaged ?? "."}${f.untracked ? "?" : ""}${f.conflicted ? "!" : ""}`
          ).join(",")].join("|");
}

export async function fetchGit(force = false) {
  const id = app.selectedId;
  if (!id || repo.gitBusy) return;
  // Nobody is reading a hidden tab. A Git tab left open behind another window
  // would otherwise go on running git for the rest of the day; coming back to it
  // reads once, immediately.
  if (!force && document.hidden) return;
  if (!force && repo.gitFor === id && Date.now() - gitPolledAt < GIT_POLL_MS) return;
  repo.gitBusy = true;
  gitPolledAt = Date.now();
  try {
    const response = await fetch(`/api/git?sessionId=${encodeURIComponent(id)}`, { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    if (app.selectedId !== id) return;             // selection moved on while fetching
    const changed = gitStamp(data) !== gitStamp(repo.git) || repo.gitFor !== id;
    repo.git = data;
    repo.gitFor = id;
    if (changed) {
      refreshDetail(true);
      // An open diff describes a file that has just moved, so it is read again
      // rather than left showing what the file used to say.
      if (repo.diffOpen) fetchDiff();
    }
  } catch (error) {
    /* leave the previous reading on screen */
  } finally {
    repo.gitBusy = false;
  }
}

/* ------------------------------------------------- git: opening and acting */

export function closeDiff() {
  repo.diffOpen = null;
  repo.diffText = null;
  repo.diffNote = "";
}

/* Clicking a row shows its diff under it, and clicking the same row again puts
   it away — one open at a time, because the pane is one column wide. */
function toggleDiff(path, staged) {
  if (repo.diffOpen && repo.diffOpen.path === path && repo.diffOpen.staged === staged) {
    closeDiff();
    refreshDetail(true);
    return;
  }
  repo.diffOpen = { path, staged };
  repo.diffText = null;
  repo.diffNote = "";
  refreshDetail(true);
  fetchDiff();
}

async function fetchDiff() {
  const id = app.selectedId;
  const want = repo.diffOpen;
  if (!id || !want) return;
  const query = new URLSearchParams({ sessionId: id, path: want.path, staged: want.staged ? "1" : "0" });
  try {
    const response = await fetch(`/api/git/diff?${query}`, { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    // The row may have been closed, or another one opened, while this was in the air.
    if (app.selectedId !== id || repo.diffOpen !== want) return;
    repo.diffText = data.text || "";
    repo.diffNote = data.message || (data.ok ? "No line changes to show" : "Could not read that diff");
  } catch (error) {
    if (repo.diffOpen !== want) return;
    repo.diffText = "";
    repo.diffNote = "Could not reach the server";
  }
  refreshDetail(true);
}

/* Every writing git action goes through here: one at a time, the answer in a
   snackbar, and a fresh reading afterwards so the list matches the repository
   again without waiting for the next poll. */
async function gitDo(action, extra, button) {
  const id = app.selectedId;
  if (!id || repo.gitActing) return false;
  repo.gitActing = true;
  if (button) button.disabled = true;
  let ok = false;
  try {
    const response = await fetch("/api/git", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: id, action, ...extra }),
    });
    const data = await response.json().catch(() => ({}));
    ok = Boolean(data.ok);
    showSnackbar(data.message || (ok ? "Done" : "That did not work"), ok ? 4000 : 8000);
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    repo.gitActing = false;
    if (button) button.disabled = false;
    // The reading is stale the moment anything above succeeded — and after a
    // failure it is worth confirming that nothing moved.
    fetchGit(true);
  }
  return ok;
}

/* Lane assignment: the same walk a commit graph is always drawn from.

   `lanes` holds, per column, the sha that column is currently waiting to reach.
   A commit takes the lane that was waiting for it — or a free one if nothing
   was — and hands that lane to its first parent; a merge's remaining parents
   open lanes of their own. Anything else still waiting for this commit was a
   second child, and its lane closes here.

   Each row records the lane state either side of it, which is what lets the
   rail below draw a line that actually joins up with its neighbours. */
function layoutGraph(commits) {
  const lanes = [];
  const rows = [];
  const take = (sha) => {
    const free = lanes.indexOf(null);
    if (free !== -1) { lanes[free] = sha; return free; }
    lanes.push(sha);
    return lanes.length - 1;
  };
  for (const commit of commits) {
    const before = [...lanes];
    let lane = lanes.indexOf(commit.sha);
    if (lane === -1) lane = take(commit.sha);
    // A second child of this commit sits in another lane; it ends here.
    const merged = [];
    for (let i = 0; i < lanes.length; i++) {
      if (i !== lane && lanes[i] === commit.sha) { merged.push(i); lanes[i] = null; }
    }
    lanes[lane] = commit.parents[0] ?? null;
    const forks = [];
    for (const parent of commit.parents.slice(1)) {
      // A parent already expected elsewhere reuses that lane rather than opening
      // a second column for the same line of history.
      const existing = lanes.indexOf(parent);
      forks.push(existing !== -1 ? existing : take(parent));
    }
    // Trailing empties would leave the rail padded with blank columns.
    while (lanes.length && lanes[lanes.length - 1] === null) lanes.pop();
    rows.push({ commit, lane, merged, forks, before, after: [...lanes] });
  }
  return rows;
}

const LANE_COLOURS = [
  "var(--md-sys-color-primary)", "var(--md-sys-color-tertiary)",
  "var(--md-extended-color-waiting-color, var(--md-sys-color-secondary))",
  "var(--md-extended-color-idle-color, var(--md-sys-color-error))",
  "var(--md-sys-color-secondary)",
];
const laneColour = (index) => LANE_COLOURS[index % LANE_COLOURS.length];

const LANE_W = 14;      // horizontal distance between lanes
const ROW_H = 52;       // must equal .git-commit's height, or the lanes break at each join

/* One row's slice of the graph, drawn as its own SVG: the lanes passing
   straight through, the diagonals joining a fork or a merge to this commit, and
   the dot itself. Drawing per row rather than one tall SVG keeps the rail
   aligned with its text however the subject wraps. */
function commitRail(row, width) {
  const x = (lane) => lane * LANE_W + LANE_W / 2;
  const mid = ROW_H / 2;
  const parts = [];
  const line = (x1, y1, x2, y2, lane) =>
    `<path d="M${x1} ${y1}${x1 === x2 ? `V${y2}` : ` C${x1} ${(y1 + y2) / 2} ${x2} ${(y1 + y2) / 2} ${x2} ${y2}`}"
       fill="none" stroke="${laneColour(lane)}" stroke-width="2" stroke-linecap="round"/>`;

  // Lanes that neither start nor stop here pass straight through behind it.
  const span = Math.max(row.before.length, row.after.length);
  for (let i = 0; i < span; i++) {
    if (i === row.lane || row.merged.includes(i) || row.forks.includes(i)) continue;
    if (row.before[i] && row.after[i]) parts.push(line(x(i), 0, x(i), ROW_H, i));
  }
  // This commit's own lane: up to whatever pointed at it, down to its parent.
  if (row.before[row.lane]) parts.push(line(x(row.lane), 0, x(row.lane), mid, row.lane));
  if (row.after[row.lane]) parts.push(line(x(row.lane), mid, x(row.lane), ROW_H, row.lane));
  // A second child arriving from another lane, and a merge parent leaving for one.
  for (const i of row.merged) parts.push(line(x(i), 0, x(row.lane), mid, i));
  for (const i of row.forks) parts.push(line(x(row.lane), mid, x(i), ROW_H, i));

  return `<svg class="git-commit__rail" width="${width}" height="${ROW_H}" viewBox="0 0 ${width} ${ROW_H}" aria-hidden="true">
    ${parts.join("")}
    <circle cx="${x(row.lane)}" cy="${mid}" r="4" fill="${laneColour(row.lane)}"/>
  </svg>`;
}

/* The two status letters, as git itself writes them: staged on the left,
   unstaged on the right. */
/* One letter for what happened to a file, as the editor labels it: the side of
   the status pair that this group is showing, not both at once. A file changed
   in the index and again in the tree appears in both groups, and each row then
   says what its own group is holding. */
const MARK_LABELS = {
  M: "modified", A: "added", D: "deleted", R: "renamed", C: "copied",
  U: "untracked", T: "type changed",
};

function fileMark(file, staged) {
  // The editor marks a conflict apart from every ordinary letter, and so does
  // this: it is the one row that has to be dealt with before anything else can
  // happen. C stays what git means by it, a copy.
  if (file.conflicted) return { mark: "!", label: "conflicted" };
  if (file.untracked) return { mark: "U", label: "untracked" };
  const letter = (staged ? file.staged : file.unstaged) || "M";
  return { mark: letter, label: MARK_LABELS[letter] || letter };
}

/* A row, and under it the diff if this is the row that is open. `staged` says
   which side of the file this row stands for, so its actions and its diff both
   act on the right one. */
function fileRow(file, staged) {
  const { mark, label } = fileMark(file, staged);
  // git reports a directory it will not look inside — a nested repository, a
  // worktree — as one entry with a trailing slash. Split on the segment before
  // it, and keep the slash on the name so the row still reads as a folder.
  const folder = file.path.endsWith("/");
  const trimmed = folder ? file.path.slice(0, -1) : file.path;
  const cut = trimmed.lastIndexOf("/");
  const dir = cut === -1 ? "" : trimmed.slice(0, cut);
  const base = (cut === -1 ? trimmed : trimmed.slice(cut + 1)) + (folder ? "/" : "");
  const open = repo.diffOpen && repo.diffOpen.path === file.path && repo.diffOpen.staged === staged;
  const can = repo.git?.canWrite;
  const act = (action: string, icon: string, title: string, danger = false) =>
    `<button class="scm-icon md-state${danger ? " scm-icon--danger" : ""}" type="button"
       data-git="${action}" title="${escapeHtml(title)}" aria-label="${escapeHtml(`${title} — ${file.path}`)}">${icon}</button>`;

  return `<div class="git-file" data-mark="${mark}" data-path="${escapeHtml(file.path)}"
      data-staged="${staged ? "1" : "0"}"${open ? ` data-open="1"` : ""}>
      <button class="git-file__open md-state" type="button" data-git="diff"
        title="${escapeHtml(`${file.path} — ${label}`)}" aria-expanded="${open}">
        <span class="git-file__name md-body-medium">${escapeHtml(base)}</span>
        <span class="git-file__dir md-body-small"><bdi>${escapeHtml(dir)}</bdi></span>
        ${file.origPath ? `<span class="git-file__from md-body-small md-mono">← ${escapeHtml(file.origPath)}</span>` : ""}
      </button>
      ${can ? `<div class="scm-actions">
        ${staged ? act("unstage", ICON.minus, "Unstage changes")
          // Staging a conflicted file is how the resolution is recorded; git will
          // not restore an unmerged path, so discard is not offered on one.
          : file.conflicted ? act("stage", ICON.plus, "Stage — marks this conflict resolved")
          : `${act("discard", ICON.discard, "Discard changes", true)}${act("stage", ICON.plus, "Stage changes")}`}
      </div>` : ""}
      <span class="git-file__xy md-mono md-body-small" title="${escapeHtml(label)}">${escapeHtml(mark)}</span>
    </div>
    ${open ? diffPane() : ""}`;
}

/* One patch, coloured by what each line does to the file. Shared by the diff a
   Git row opens and the change a chat message carries, because they are the same
   thing read from two places — and colouring them differently would say they
   were not. */
export function diffBody(text) {
  const lines = String(text ?? "").split("\n");
  // The last line of a patch is the newline before EOF, not a line of its own.
  if (lines[lines.length - 1] === "") lines.pop();
  return lines.map((line) => {
    const kind = line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ")
        || line.startsWith("index ") || line.startsWith("new file") || line.startsWith("deleted file")
        || line.startsWith("rename ") || line.startsWith("similarity ") ? "meta"
      : line.startsWith("@@") ? "hunk"
      : line.startsWith("+") ? "add"
      : line.startsWith("-") ? "del" : "";
    return `<span class="scm-diff__line${kind ? ` scm-diff__line--${kind}` : ""}">${escapeHtml(line) || " "}</span>`;
  }).join("");
}

/* The same patch again, as two files side by side — what an editor shows when
   you open a change, and what this shows when you open one in the conversation.

   A unified patch is one column because it has to be: it is a text format, and
   the sign in the first character is the only room it has to say what happened.
   On screen there is room for the thing the sign is standing in for, which is
   two versions of the file with the differences lined up against each other. The
   removed line and the line that replaced it are then the same row, and reading
   the change is looking across rather than remembering what the last line said.

   The pairing is the whole of the work: a run of removals and the run of
   additions that follows it are the same edit written twice, so they are zipped
   — first removed against first added, second against second — and whichever run
   is longer leaves rows with nothing on the other side. That is what an editor
   shows too, and it is honest: the file really does not have a line there. */
function diffRows(text) {
  const rows = [];
  let before = 0, after = 0;
  let dels = [], adds = [];
  // A run ends at the first line that is not part of it, and both halves of the
  // run have to be in hand before either can be placed — that is the pairing.
  const flush = () => {
    for (let i = 0; i < Math.max(dels.length, adds.length); i++) {
      rows.push({ kind: dels[i] && adds[i] ? "both" : dels[i] ? "del" : "add",
                  before: dels[i], after: adds[i] });
    }
    dels = []; adds = [];
  };
  const lines = String(text ?? "").split("\n");
  // The newline before EOF is not a line of the file.
  if (lines[lines.length - 1] === "") lines.pop();
  for (const line of lines) {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/.exec(line);
    if (hunk) {
      flush();
      before = Number(hunk[1]); after = Number(hunk[2]);
      rows.push({ kind: "gap", text: line });
      continue;
    }
    // A patch from git carries its own headers, and two of them start with the
    // characters that mean added and removed. They are about the file rather
    // than about a line in it, so they belong to neither side.
    if (/^(\+\+\+ |--- |diff |index |new file|deleted file|rename |similarity )/.test(line)) continue;
    if (line.startsWith("+")) { adds.push({ n: after++, text: line.slice(1) }); continue; }
    if (line.startsWith("-")) { dels.push({ n: before++, text: line.slice(1) }); continue; }
    // "\ No newline at end of file" belongs to the line above it, not to a line
    // of its own, and there is no side of the file to put it on.
    if (line.startsWith("\\")) continue;
    flush();
    rows.push({ kind: "same", before: { n: before++, text: line.slice(1) },
                after: { n: after++, text: line.slice(1) } });
  }
  flush();
  return rows;
}

export function sideBySide(text) {
  const cell = (row, which) => {
    const has = row[which];
    const tone = has ? "" : " diff2__line--gone";
    const numTone = has ? "" : " diff2__num--gone";
    return `<span class="diff2__num diff2__num--${which}${numTone}" aria-hidden="true">${has ? has.n : ""}</span>
      <span class="diff2__line diff2__line--${which}${tone}">${has ? escapeHtml(has.text) || " " : ""}</span>`;
  };
  const body = diffRows(text).map((row) => {
    if (row.kind === "gap") {
      return `<span class="diff2__gap md-label-small md-mono">${escapeHtml(row.text)}</span>`;
    }
    const kind = row.kind === "same" ? "same" : row.kind === "both" ? "both" : row.kind;
    // A row that is both a removal and an addition wears both tints, one a side.
    const classes = ["diff2__row--" + kind,
                     ...(row.before && row.kind !== "same" ? ["diff2__row--del"] : []),
                     ...(row.after && row.kind !== "same" ? ["diff2__row--add"] : [])];
    return `<span class="diff2__row ${classes.join(" ")}">${
      cell(row, "before")}${cell(row, "after")}</span>`;
  }).join("");
  return `<div class="diff2-wrap">
      <div class="diff2 md-mono md-body-small" role="group" aria-label="The change, before and after" tabindex="0">
        <span class="diff2__head diff2__head--before md-label-small">before</span>
        <span class="diff2__head diff2__head--after md-label-small">after</span>
        ${body}
      </div>
    </div>`;
}

/* The open diff, coloured by what each line does to the file. */
function diffPane() {
  if (repo.diffText === null) return `<p class="git-empty md-body-small">Reading the diff…</p>`;
  if (!repo.diffText) return `<p class="git-empty md-body-small">${escapeHtml(repo.diffNote || "No line changes to show")}</p>`;
  return `<pre class="scm-diff md-mono md-body-small" tabindex="0">${diffBody(repo.diffText)}</pre>`;
}

/* Changes and history are two tabs rather than one scroll, because the history
   is the longer of the two by far and having it below the file list put the
   thing you check most often above a graph you have to scroll past. Both read
   the same fetch, so switching between them costs nothing. */

/* Neither tab is worth showing without knowing which branch it is describing,
   so the same header opens both. */
function gitHead(state) {
  const here = state.branch || (state.head ? state.head.slice(0, 7) : "no commits");
  // The branch is a button when there is somewhere to go: the editor's status-bar
  // branch works this way, and a repository with one branch and no remote has
  // nothing to offer but the new-branch line, which is still worth offering.
  const badge = state.canWrite
    ? `<button class="git-badge git-badge--button md-state md-label-large" type="button" data-git="branch-menu"
         title="Switch branch, or start a new one" aria-haspopup="menu">
        ${ICON.branch}<span class="md-mono">${escapeHtml(here)}</span>${ICON.chevron}
       </button>`
    : `<span class="git-badge md-label-large">${ICON.branch}<span class="md-mono">${escapeHtml(here)}</span></span>`;
  // The counts say what they would do, and do it: the arrow you are looking at
  // when you think "push that" is the arrow itself.
  // Filled, not quiet: an unpushed commit is something to do, and a transparent
  // arrow beside a row of transparent arrows reads as one more fact about the
  // repository. Push borrows the colour this panel already uses for "this one
  // needs you"; pull is work coming the other way, so it takes the primary tone.
  const drift = (key, arrow, count, verb, title) => {
    if (!count) return "";
    const label = `<span class="md-mono">${arrow}${count}</span>`;
    return state.canWrite
      ? `<button class="git-badge git-badge--button git-badge--drift md-state md-label-medium"
           type="button" data-way="${key}" data-git="${key}" title="${escapeHtml(title)}">
          ${label}<span class="git-badge__verb">${verb}</span>
         </button>`
      : `<span class="git-badge git-badge--drift md-label-medium" data-way="${key}"
           title="${escapeHtml(title)}">${label}<span class="git-badge__verb">${verb}</span></span>`;
  };
  return `
    <div class="git-head">
      ${badge}
      ${state.detached ? `<span class="git-badge git-badge--quiet md-body-small">detached HEAD</span>` : ""}
      ${state.upstream ? `<span class="git-badge git-badge--quiet md-body-small md-mono">${escapeHtml(state.upstream)}</span>` : ""}
      ${drift("push", "↑", state.ahead, "to push",
        `Push ${plural(state.ahead, "commit")} to ${state.upstream || "the remote"}`)}
      ${drift("pull", "↓", state.behind, "to pull",
        `Pull ${plural(state.behind, "commit")} from ${state.upstream || "the remote"}`)}
      ${state.stashes ? `<span class="git-badge git-badge--quiet md-body-small">${state.stashes} stashed</span>` : ""}
      ${gitHeadActions(state)}
    </div>`;
}

/* The header's own buttons: sync, and the overflow that holds everything a
   repository can be told to do that is not about one file. Sync is the editor's
   one button for "catch up, then hand over" — pull what is waiting, push what is
   not there yet — and it says which way the traffic is going. */
function gitHeadActions(state) {
  if (!state.canWrite) {
    return `<span class="git-badge git-badge--quiet md-body-small scm-actions" style="opacity:1;margin-inline-start:auto"
      title="This panel is serving read-only, so it can show the repository but not change it">read-only</span>`;
  }
  const sync = !state.detached && (state.upstream
    ? (state.ahead || state.behind
        ? `Sync — pull ${state.behind || 0}, push ${state.ahead || 0}`
        : "Sync — nothing waiting either way")
    : "Publish this branch — it has no upstream yet");
  return `<div class="scm-actions" style="opacity:1;margin-inline-start:auto">
      ${sync ? `<button class="scm-icon md-state" type="button" data-git="sync" title="${escapeHtml(sync)}" aria-label="${escapeHtml(sync)}">${ICON.sync}</button>` : ""}
      <button class="scm-icon md-state" type="button" data-git="menu" title="More git actions" aria-label="More git actions" aria-haspopup="menu">${ICON.more}</button>
    </div>`;
}

/* Whatever both git tabs should show instead of themselves — still loading, not
   readable — or null when there is real data to draw. */
function gitNotice(session) {
  if (!repo.git || repo.gitFor !== session.sessionId) {
    return `<p class="git-empty md-body-medium">Reading the repository…</p>`;
  }
  if (!repo.git.ok) {
    return `<p class="git-empty md-body-medium">${escapeHtml(repo.git.message || "Could not read this repository")}</p>`;
  }
  return null;
}

/* The groups the editor shows, in its order: what still has to be resolved
   first, then what is going into the next commit, then everything else. A file
   with changes on both sides is in two of them, once per side. */
function gitGroups(files) {
  return {
    merge: files.filter((f) => f.conflicted),
    staged: files.filter((f) => f.staged && !f.conflicted),
    changes: files.filter((f) => (f.unstaged || f.untracked) && !f.conflicted),
  };
}

export function gitPanel(session) {
  const notice = gitNotice(session);
  if (notice) return notice;

  const groups = gitGroups(repo.git.files);
  const can = repo.git.canWrite;
  const act = (action: string, icon: string, title: string, danger = false) =>
    `<button class="scm-icon md-state${danger ? " scm-icon--danger" : ""}" type="button"
       data-git="${action}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">${icon}</button>`;

  // A group's buttons carry no paths of their own: the handler reads the group
  // back out of the same split, so what a click acts on is whatever the list is
  // showing at the moment of the click rather than whatever it showed at paint.
  const group = (key: string, title: string, files: GitFile[], staged: boolean, actions = "") => files.length ? `
    <section class="scm-group" data-group="${key}">
      <header class="scm-group__head">
        <h3 class="scm-group__title md-label-medium">${escapeHtml(title)}<span class="scm-count md-label-small">${files.length}</span></h3>
        ${can ? `<div class="scm-actions">${actions}</div>` : ""}
      </header>
      <div class="git-files">${files.map((f) => fileRow(f, staged)).join("")}</div>
    </section>` : "";

  return `
    ${gitHead(repo.git)}
    ${can ? commitBox(session, groups) : ""}
    ${group("merge", "Merge changes", groups.merge, false,
      act("stage-group", ICON.plus, "Stage all — marks these conflicts resolved"))}
    ${group("staged", "Staged changes", groups.staged, true,
      act("unstage-group", ICON.minus, "Unstage all"))}
    ${group("changes", "Changes", groups.changes, false,
      act("discard-group", ICON.discard, "Discard all changes", true) + act("stage-group", ICON.plus, "Stage all changes"))}
    ${repo.git.files.length ? "" : `<p class="git-empty md-body-medium">Nothing changed — the working tree is clean.</p>`}`;
}

/* Why the commit button cannot be pressed, or null when it can. One answer, in
   one place: it is needed once when the pane is painted and again on every
   keystroke, and two copies of it would eventually disagree. */
function commitBlocker(session, groups) {
  if (groups.merge.length) return "Resolve the conflicts first";
  if (!groups.staged.length && !groups.changes.length) return "Nothing to commit";
  if (!(commitDrafts.get(session.sessionId) || "").trim()) return "A commit needs a message";
  return null;
}

/* The message and the button that uses it.

   The button says what it will actually do, which depends on what is staged:
   with nothing staged the editor offers to stage everything and commit that in
   one go, and saying so on the button is better than a dialog after the click. */
function commitBox(session, groups) {
  const message = commitDrafts.get(session.sessionId) || "";
  const blocked = commitBlocker(session, groups);
  const label = groups.staged.length ? "Commit"
    : groups.changes.length ? `Commit all ${groups.changes.length}`
    : "Commit";
  const where = repo.git.branch ? ` on ${repo.git.branch}` : "";
  return `
    <div class="scm-commit">
      <div class="scm-commit__box">
        <textarea class="scm-commit__field md-body-medium" id="commitField" rows="2"
          placeholder="Message — ${escapeHtml(`Ctrl+Enter to commit${where}`)}"
          aria-label="Commit message">${escapeHtml(message)}</textarea>
        <button class="scm-icon md-state scm-commit__ai" type="button" data-git="suggest"
          ${repo.suggesting ? `data-busy="1" disabled title="Writing a message…"`
            : `title="Let Claude write the message from the diff"`}
          aria-label="Let Claude write the commit message">${ICON.sparkle}</button>
      </div>
      <div class="scm-commit__row">
        <button class="button button--filled md-state scm-commit__go" type="button" data-git="commit"
          ${blocked ? `disabled title="${escapeHtml(blocked)}"` : `title="${escapeHtml(`Commit${where}`)}"`}>
          ${ICON.check}${escapeHtml(label)}
        </button>
        <button class="button button--filled md-state scm-commit__more" type="button" data-git="commit-menu"
          title="Other ways to commit" aria-label="Other ways to commit" aria-haspopup="menu">${ICON.chevron}</button>
      </div>
    </div>`;
}

/* The message box grows with what is typed, up to the point where the file list
   below it would be pushed off screen. */
const COMMIT_MAX = 180;
function growCommit(field) {
  field.style.height = "auto";
  field.style.height = `${Math.min(COMMIT_MAX, Math.max(56, field.scrollHeight))}px`;
  field.style.overflowY = field.scrollHeight > COMMIT_MAX ? "auto" : "hidden";
}

/* Whether the commit button can be pressed changes as the message is typed,
   which is far too often to rebuild the pane for. */
function syncCommitButton(session) {
  const button = detailPane.querySelector<HTMLButtonElement>("[data-git='commit']");
  if (!button || !repo.git) return;
  const blocked = commitBlocker(session, gitGroups(repo.git.files || []));
  button.disabled = Boolean(blocked);
  button.title = blocked || `Commit${repo.git.branch ? ` on ${repo.git.branch}` : ""}`;
}

/* Commit, and then — for the split button's other entries — push or sync what
   was just committed. The message is only forgotten once git has taken it. */
async function doCommit(session, button, { amend = false, then = null } = {}) {
  const message = (commitDrafts.get(session.sessionId) || "").trim();
  const groups = gitGroups(repo.git?.files || []);
  if (groups.merge.length) {
    showSnackbar("Resolve the conflicts first, then commit");
    return;
  }
  if (!message && !amend) {
    showSnackbar("A commit needs a message");
    detailPane.querySelector<HTMLTextAreaElement>("#commitField")?.focus();
    return;
  }
  // Nothing staged is the editor's "commit all": the Changes group goes in as it
  // stands, which is what the button already said it would do.
  const stageAll = !groups.staged.length && groups.changes.length > 0;
  const ok = await gitDo("commit", { message, amend, stageAll }, button);
  if (!ok) return;
  commitDrafts.delete(session.sessionId);
  const field = detailPane.querySelector<HTMLTextAreaElement>("#commitField");
  if (field) { field.value = ""; growCommit(field); }
  if (then) await gitDo(then, {}, button);
}

/* The sparkle: a headless Claude reads the diff and writes the message.

   It takes ten or twenty seconds, which is long enough that the button has to
   say it is working — and long enough that the answer may land after the pane
   has been rebuilt underneath it, so the message goes into the draft first and
   into the field only if the field is still there. */
async function suggestMessage(session, button) {
  if (repo.gitActing) return;
  const typed = (commitDrafts.get(session.sessionId) || "").trim();
  if (typed) {
    const replace = await askConfirm({
      headline: "Replace the message?",
      body: "What Claude writes goes into the box instead of what you have typed.",
      confirmLabel: "Replace", danger: false,
    });
    if (!replace) return;
  }

  repo.gitActing = true;
  repo.suggesting = true;
  button.disabled = true;
  button.dataset.busy = "1";
  showSnackbar("Reading the diff and writing a message…", 90000);
  try {
    const response = await fetch("/api/git", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: session.sessionId, action: "suggestMessage" }),
    });
    const data = await response.json().catch(() => ({}));
    if (!data.ok || !data.text) {
      showSnackbar(data.message || "Could not write a message", 8000);
      return;
    }
    commitDrafts.set(session.sessionId, data.text);
    const field = detailPane.querySelector<HTMLTextAreaElement>("#commitField");
    if (field) {
      field.value = data.text;
      growCommit(field);
      field.focus();
      // The caret at the end, because the next thing anyone does with a written
      // message is edit it.
      field.setSelectionRange(data.text.length, data.text.length);
    }
    syncCommitButton(session);
    showSnackbar("Message written — read it before you commit", 5000);
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    repo.gitActing = false;
    repo.suggesting = false;
    const live = detailPane.querySelector<HTMLButtonElement>("[data-git='suggest']");
    if (live) {
      live.disabled = false;
      delete live.dataset.busy;
      live.title = "Let Claude write the message from the diff";
    }
  }
}

/* What the arrow beside Commit opens: the same list the editor keeps there. */
function commitMenuItems(session) {
  const upstream = repo.git?.upstream;
  return [
    { key: "commit", icon: ICON.check, label: "Commit",
      run: (el) => doCommit(session, el) },
    { key: "commit-push", icon: ICON.upload, label: "Commit & push",
      hint: upstream || "publishes the branch",
      run: (el) => doCommit(session, el, { then: "push" }) },
    { key: "commit-sync", icon: ICON.sync, label: "Commit & sync",
      hint: "pulls first, then pushes",
      run: (el) => doCommit(session, el, { then: "sync" }) },
    { divider: true },
    { key: "amend", icon: ICON.pencil, label: "Commit (amend)",
      hint: repo.git?.commits?.[0]?.subject ? clip(repo.git.commits[0].subject, 34) : "rewrites the last commit",
      disabled: !repo.git?.commits?.length,
      run: (el) => doCommit(session, el, { amend: true }) },
  ];
}

/* The branch list, in the editor's order: the two ways to start a branch, then
   the branches themselves — local ones by how recently they were committed to,
   because that is the handful you are actually moving between, then the remote
   ones nobody here has a local copy of yet. */
function branchMenuItems(session) {
  const local = repo.git?.branches?.local || [];
  const remote = repo.git?.branches?.remote || [];
  const items: MenuItem[] = [
    { key: "new", icon: ICON.plus, label: "Create new branch…", hint: "from here",
      run: () => createBranch(session, null) },
    // No hint on this one: with one, the label is what gets ellipsised, and
    // "Create new branch …" reads like the line above it.
    { key: "new-from", icon: ICON.branch, label: "Create new branch from…",
      disabled: !local.length && !remote.length,
      // Anchored to the badge, not to the item that was just clicked: that item
      // belongs to a menu which is already closing, and a hidden element has no
      // position to hang the next menu off.
      run: () => openGitMenu(detailPane.querySelector("[data-git='branch-menu']"),
                             "Start the branch from", startPointItems(session)) },
  ];
  const now = Date.now() / 1000 + app.skew;
  if (local.length) {
    items.push({ divider: true });
    for (const branch of local.slice(0, 40)) {
      items.push({
        key: `local:${branch.name}`, icon: branch.current ? ICON.check : ICON.branch,
        label: branch.name,
        hint: branch.current ? "you are here" : ago(now - branch.at),
        disabled: branch.current,
        run: (el) => switchTo(session, branch.name, el),
      });
    }
  }
  if (remote.length) {
    items.push({ divider: true });
    for (const branch of remote.slice(0, 40)) {
      items.push({
        key: `remote:${branch.name}`, icon: ICON.download, label: branch.name,
        hint: "check out and track",
        run: (el) => switchTo(session, branch.name, el),
      });
    }
  }
  // A repository with nothing but the branch you are on says so. Two create
  // lines and then silence reads as a list that failed to load — which is also
  // what it looks like when the panel is a server old enough not to send one.
  if (local.length <= 1 && !remote.length) {
    items.push({ divider: true });
    items.push({
      key: "none", icon: ICON.branch, disabled: true,
      label: repo.git?.branches ? "No other branches yet" : "Branch list unavailable",
      hint: repo.git?.branches ? "" : "restart the panel",
    });
  }
  return items;
}

/* The same list again, but picking one names where a new branch starts rather
   than where HEAD goes. */
function startPointItems(session) {
  const local = repo.git?.branches?.local || [];
  const remote = repo.git?.branches?.remote || [];
  return [...local, ...remote].slice(0, 60).map((branch) => ({
    key: `from:${branch.name}`, icon: ICON.branch, label: branch.name,
    hint: branch.current ? "where you are" : "",
    run: () => createBranch(session, branch.name),
  }));
}

async function createBranch(session, from) {
  const name = await askText({
    headline: "Name the branch",
    body: from
      ? `It starts from <span class="md-mono">${escapeHtml(from)}</span>.`
      : "It starts from where you are now.",
    placeholder: "feature/something",
    confirmLabel: "Create branch",
  });
  if (!name) return;
  await gitDo("switch", { branch: name, create: true, ...(from ? { from } : {}) },
    detailPane.querySelector("[data-git='branch-menu']"));
}

/* Switching branches rewrites the files a session is working in, so a session
   that is mid-turn gets a word first — the panel knows that much, and the editor
   never did. git's own refusals (uncommitted work in the way, the branch held by
   another worktree) come back as they are. */
async function switchTo(session, branch, button) {
  if (session.status === "busy") {
    const go = await askConfirm({
      headline: "This session is working right now",
      body: `Switching to <span class="md-mono">${escapeHtml(branch)}</span> changes the files
             <span class="md-mono">${escapeHtml(session.name)}</span> is editing underneath it.`,
      confirmLabel: "Switch anyway",
    });
    if (!go) return;
  }
  await gitDo("switch", { branch }, button);
}

/* And what the header's overflow opens: everything about the repository rather
   than about one file. */
function gitMenuItems(session) {
  const files = repo.git?.files || [];
  return [
    { key: "pull", icon: ICON.download, label: "Pull", hint: repo.git?.behind ? `${repo.git.behind} waiting` : repo.git?.upstream || "no upstream",
      disabled: !repo.git?.upstream,
      run: (el) => gitDo("pull", {}, el) },
    { key: "push", icon: ICON.upload, label: repo.git?.upstream ? "Push" : "Publish branch",
      hint: repo.git?.ahead ? `${repo.git.ahead} to push` : repo.git?.upstream || "sets the upstream",
      disabled: repo.git?.detached,
      run: (el) => gitDo("push", {}, el) },
    { key: "sync", icon: ICON.sync, label: "Sync", hint: "pull, then push", disabled: repo.git?.detached,
      run: (el) => gitDo("sync", {}, el) },
    { key: "fetch", icon: ICON.download, label: "Fetch", hint: "just look",
      run: (el) => gitDo("fetch", {}, el) },
    { divider: true },
    { key: "stage-all", icon: ICON.plus, label: "Stage all changes", disabled: !files.length,
      run: (el) => gitDo("stageAll", {}, el) },
    { key: "unstage-all", icon: ICON.minus, label: "Unstage everything",
      disabled: !files.some((f) => f.staged),
      run: (el) => gitDo("unstageAll", {}, el) },
    { key: "discard-all", icon: ICON.discard, label: "Discard all changes…", danger: true,
      disabled: !files.length,
      run: (el) => discardWithConfirm(files.map((f) => f.path), files, el, { all: true }) },
    { divider: true },
    { key: "stash", icon: ICON.stash, label: "Stash all changes", disabled: !files.length,
      hint: "including new files",
      run: (el) => gitDo("stash", { message: commitDrafts.get(session.sessionId) || "" }, el) },
    { key: "stash-pop", icon: ICON.stash, label: "Restore latest stash",
      disabled: !repo.git?.stashes, hint: repo.git?.stashes ? `${repo.git.stashes} stashed` : "nothing stashed",
      run: (el) => gitDo("stashPop", {}, el) },
  ];
}

/* Discarding is the one thing here that git cannot give back, so it says exactly
   what it is about to lose before it does it — and says "delete" rather than
   "discard" for a file that has never been committed, because that is what
   happens to it. */
async function discardWithConfirm(
  paths: string[], files: GitFile[], button: HTMLButtonElement, { all = false } = {},
) {
  const byPath = new Map((files || []).map((f) => [f.path, f]));
  const untracked = paths.filter((p) => byPath.get(p)?.untracked);
  const tracked = paths.filter((p) => !byPath.get(p)?.untracked);
  const one = paths.length === 1;
  const name = (p) => `<span class="md-mono">${escapeHtml(p)}</span>`;
  const parts = [];
  if (tracked.length) {
    parts.push(one ? `Changes in ${name(tracked[0])} go back to the last staged version.`
      : `Changes in ${tracked.length} file${tracked.length === 1 ? "" : "s"} go back to the last staged version.`);
  }
  if (untracked.length) {
    parts.push(one ? `${name(untracked[0])} has never been committed, so discarding it deletes it.`
      : `${untracked.length} file${untracked.length === 1 ? "" : "s"} that were never committed are deleted.`);
  }
  parts.push("This cannot be undone.");
  const ok = await askConfirm({
    headline: one ? "Discard this change?" : `Discard ${paths.length} changes?`,
    body: parts.join(" "),
    confirmLabel: untracked.length && !tracked.length ? "Delete" : "Discard",
  });
  if (!ok) return;
  if (all) return gitDo("discardAll", { includeUntracked: true }, button);
  return gitDo("discard", { paths }, button);
}

/* Where the pointer went, translated into one of the actions above. Delegated
   from the buttons the panel just drew, so a rebuild carries no listeners over. */
function onGitAction(session, button) {
  const action = button.dataset.git;
  const row = button.closest(".git-file");
  const path = row?.dataset.path;
  const staged = row?.dataset.staged === "1";
  const groupKey = button.closest(".scm-group")?.dataset.group;
  const groupFiles = groupKey ? gitGroups(repo.git?.files || [])[groupKey] || [] : [];
  const groupPaths = groupFiles.map((f) => f.path);

  switch (action) {
    case "diff": return toggleDiff(path, staged);
    case "stage": return gitDo("stage", { paths: [path] }, button);
    case "unstage": return gitDo("unstage", { paths: [path] }, button);
    case "discard": return discardWithConfirm([path], repo.git?.files || [], button);
    case "stage-group": return gitDo("stage", { paths: groupPaths }, button);
    case "unstage-group": return gitDo("unstage", { paths: groupPaths }, button);
    case "discard-group": return discardWithConfirm(groupPaths, groupFiles, button);
    case "sync": return gitDo(repo.git?.upstream ? "sync" : "push", {}, button);
    case "push": return gitDo("push", {}, button);
    case "pull": return gitDo("pull", {}, button);
    case "branch-menu": return openGitMenu(button, repo.git?.branch || "Branches", branchMenuItems(session));
    case "suggest": return suggestMessage(session, button);
    case "commit": return doCommit(session, button);
    case "commit-menu": return openGitMenu(button, "Commit", commitMenuItems(session));
    case "menu": return openGitMenu(button, repo.git?.branch || "This repository", gitMenuItems(session));
    default: return undefined;
  }
}

/* Both git menus hang off their own button rather than the pointer, which is
   where a menu opened from a toolbar belongs. */
function openGitMenu(button, title, items) {
  const box = button.getBoundingClientRect();
  openMenu({ title, label: `${title} actions`, items }, box.left, box.bottom + 4);
}

export function wireGit(session) {
  const field = detailPane.querySelector<HTMLTextAreaElement>("#commitField");
  if (field) {
    growCommit(field);
    if (repo.commitCaret) {
      if (repo.commitCaret.focused) {
        field.focus();
        field.setSelectionRange(repo.commitCaret.start, repo.commitCaret.end);
      }
      repo.commitCaret = null;
    }
    field.addEventListener("input", () => {
      commitDrafts.set(session.sessionId, field.value);
      growCommit(field);
      syncCommitButton(session);
    });
    field.addEventListener("keydown", (event) => {
      // Ctrl+Enter commits, as it does in the editor. Plain Enter is a newline:
      // a commit message has a body, and this box is where it gets written.
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        doCommit(session, detailPane.querySelector("[data-git='commit']"));
      }
    });
  }
  for (const button of detailPane.querySelectorAll("[data-git]")) {
    button.addEventListener("click", () => onGitAction(session, button));
  }
}

export function historyPanel(session) {
  const notice = gitNotice(session);
  if (notice) return notice;

  const rows = layoutGraph(repo.git.commits || []);
  const width = Math.max(1, rows.reduce((w, r) => Math.max(w, r.before.length, r.after.length), 1)) * LANE_W;
  const now = Date.now() / 1000 + app.skew;

  return `
    ${gitHead(repo.git)}
    ${rows.length ? `<div class="git-graph" style="grid-template-columns: ${width}px 1fr;">
      ${rows.map((row) => `
        <div class="git-commit">
          ${commitRail(row, width)}
          <div class="git-commit__body">
            <div class="git-commit__subject md-body-medium">${escapeHtml(row.commit.subject || "(no message)")}</div>
            <div class="git-commit__meta md-body-small">
              ${row.commit.refs.map((ref) => {
                const head = ref.startsWith("HEAD");
                const name = ref.replace(/^HEAD -> /, "");
                return `<span class="git-ref${head ? " git-ref--head" : ""} md-mono">${escapeHtml(name)}</span>`;
              }).join("")}
              <span class="md-mono">${escapeHtml(row.commit.short)}</span>
              <span class="meta-sep">·</span>${escapeHtml(row.commit.author)}
              <span class="meta-sep">·</span>${escapeHtml(ago(now - row.commit.at))}
            </div>
          </div>
        </div>`).join("")}
    </div>` : `<p class="git-empty md-body-medium">No commits yet.</p>`}`;
}
