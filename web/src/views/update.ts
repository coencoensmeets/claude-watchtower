import { detailPane } from "../ui/dom.js";
import { ago, escapeHtml } from "../ui/format.js";
import { showSnackbar } from "../ui/snackbar.js";
import { changelogMissing, openChangelog } from "./changelog.js";

/* ==========================================================================
   Updating the panel itself.

   The panel is a git checkout and its releases are tags on it, so "is there a
   newer version" needs no update server: the server fetches the tags and
   compares. This side of it is a chip, a dialog with the release notes, and one
   button.

   The chip is on screen only when there is a newer release *and* this checkout
   can take it. Everything else it might have said — up to date, uncommitted
   work, on your own branch — belongs on the settings page, under **Panel
   version**, where you go when you want to know rather than when you need to be
   told. A chip in the app bar is an interruption; it should only ever be there
   when there is something to press.

   Pressing that button replaces the process the page is talking to. So the
   press is a small state machine rather than a request: ask, then wait for the
   panel to answer again on the new code, then reload the page onto it — because
   the code running this function is the old version's, and it has just been
   superseded.
   ========================================================================== */
export const updateScrim = document.getElementById("updateScrim");
const updateButton = document.getElementById("updateButton");
const updateChipText = document.getElementById("updateChipText");
const updateGo = document.getElementById("updateGo") as HTMLButtonElement;
const updateCheck = document.getElementById("updateCheck") as HTMLButtonElement;

/* A check reaches the network. The server holds its answer for hours and this
   only asks every half hour, so between them the remote is contacted about as
   often as a release actually lands. */
const UPDATE_POLL_MS = 1_800_000;
export let update = null;
let updateBusy = false;
let updatePolledAt = 0;
let updateGone = false;    // no checkout, or a read-only panel: nothing to offer

/* Once the update has been asked for, everything on screen is about waiting for
   the panel to come back. */
let installing = false;

export async function fetchUpdate(force) {
  if (updateBusy || updateGone || installing) return;
  if (!force && document.hidden) return;
  if (!force && Date.now() - updatePolledAt < UPDATE_POLL_MS) return;
  updateBusy = true;
  updatePolledAt = Date.now();
  // Both, and before the request rather than after it: the settings page has a
  // button that says *Check for updates*, and a press that leaves it looking
  // unpressed for the length of a network fetch invites a second one.
  paintUpdateChip();
  paintUpdateSetting();
  try {
    const response = await fetch(`/api/update${force ? "?force=1" : ""}`, { cache: "no-store" });
    if (response.status === 403) {
      // Updating runs git and restarts a process, which a panel serving the
      // network does not do. The chip goes rather than sitting there refusing.
      updateGone = true;
      return;
    }
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    // Not a checkout at all — a copied directory, or a tarball. There is no
    // release to move to and there never will be, so stop asking.
    if (data.repo === false) { updateGone = true; return; }
    update = data;
    // A check already in flight on the server: come back for its answer shortly
    // rather than holding this request open for it.
    if (update.checking) updatePolledAt = Date.now() - UPDATE_POLL_MS + 4000;
  } catch (error) {
    /* leave the last reading in place */
  } finally {
    updateBusy = false;
    paintUpdateChip();
    paintUpdateSetting();
    if (updateScrim.dataset.open === "true") paintUpdateDialog();
  }
}

function paintUpdateChip() {
  if (updateGone) { updateButton.hidden = true; return; }
  if (installing) {
    updateButton.hidden = false;
    updateButton.dataset.state = "installing";
    updateChipText.textContent = "restarting…";
    updateButton.title = "The panel is restarting on the new release";
    return;
  }
  // Nothing else. Not "up to date", not the reason a checkout is being left
  // alone: a chip in the app bar is an interruption, and it has earned the space
  // only when there is a release you can actually take. The rest is on the
  // settings page.
  updateButton.hidden = !update?.canUpdate;
  if (updateButton.hidden) return;
  updateButton.dataset.state = "ready";
  updateChipText.textContent = update.latest;
  updateButton.title = `${update.latest} is out — press to see what changed`;
}


/* What the panel is on now, said the way somebody would say it: the release, or
   the nearest release and how far past it, or just the commit. */
function whereWeAre() {
  if (!update) return "";
  if (update.current) return update.current;
  if (update.described) return update.described;
  return "an untagged commit";
}

function paintUpdateDialog() {
  const head = document.getElementById("updateHead");
  const body = document.getElementById("updateBody");
  const age = document.getElementById("updateAge");
  const headline = document.getElementById("updateHeadline");
  if (installing) {
    headline.textContent = "Restarting on the new release";
    head.hidden = false;
    head.textContent = "The panel is coming back on the code it just checked out. "
      + "This page reloads itself as soon as it answers.";
    body.innerHTML = `<p class="update-waiting md-body-medium">waiting for the panel…</p>`;
    age.textContent = "";
    updateGo.disabled = true;
    updateCheck.disabled = true;
    return;
  }
  updateGo.disabled = !update?.canUpdate;
  updateCheck.disabled = false;
  if (!update) {
    headline.textContent = "Looking for a newer release";
    head.hidden = true;
    body.innerHTML = "";
    age.textContent = "";
    return;
  }
  headline.textContent = update.canUpdate ? `${update.latest} is out` : "About this version";
  head.textContent = update.message || update.why || "";
  head.hidden = !head.textContent;

  const notes = update.notes || [];
  body.innerHTML = `
    <dl class="update-facts md-body-medium">
      <dt>On now</dt><dd class="md-mono">${escapeHtml(whereWeAre())}</dd>
      <dt>Newest release</dt><dd class="md-mono">${escapeHtml(update.latest || "none tagged yet")}</dd>
      ${update.behind ? `<dt>Behind by</dt><dd>${update.behind} release${update.behind === 1 ? "" : "s"}</dd>` : ""}
      <dt>Checkout</dt><dd>${escapeHtml(update.detached
        ? "detached — sitting on a commit rather than a branch"
        : update.branch || "unknown")}${update.dirty ? ", with uncommitted work" : ""}</dd>
    </dl>
    ${notes.length ? `<section class="update-notes">
      <h3 class="update-notes__title md-title-small">What changed</h3>
      ${notes.map((note) => `<article class="update-note">
        <h4 class="update-note__head md-label-large">
          <span class="md-mono">${escapeHtml(note.tag)}</span>
          ${note.at ? `<span class="update-note__age md-label-small">${escapeHtml(ago(Date.now() / 1000 - note.at))}</span>` : ""}
        </h4>
        <p class="update-note__subject md-body-medium">${escapeHtml(note.subject || "")}</p>
        ${note.body ? `<p class="update-note__body md-body-small">${escapeHtml(note.body)}</p>` : ""}
      </article>`).join("")}
    </section>` : ""}
    ${update.canUpdate ? runningSays(update.running) : ""}
    ${update.canUpdate ? `<p class="update-warning md-body-small">
      Updating checks the tag out itself, so the checkout ends up detached at
      ${escapeHtml(update.latest)} rather than on ${escapeHtml(update.defaultBranch || "a branch")} —
      <span class="md-mono">git switch ${escapeHtml(update.defaultBranch || "main")}</span> puts it back.
      The panel then rebuilds its frontend and ${update.restart === "systemd"
        ? "asks systemd to restart it" : "restarts itself"}.</p>` : ""}`;
  age.textContent = update.at
    ? updateBusy || update.checking ? "checking…" : `checked ${ago(Date.now() / 1000 - update.at)}`
    : "";
}

/* ==========================================================================
   Panel version, on the settings page.

   The chip only appears when there is something to press, so this is where the
   other answers live: which release you are on, that you are on the newest one,
   or the reason a checkout is being left where it is. And a button, because
   "have I got the latest" is a question people ask on their own schedule rather
   than waiting six hours for the clock to come round.

   Written from here rather than from settings.ts so that a check finishing can
   repaint it without the settings page having to know when a fetch lands. It
   does nothing when the settings page is not the one on screen, which is the
   same shape every other renderer on that page has.
   ========================================================================== */
export function paintUpdateSetting() {
  const box = detailPane.querySelector<HTMLElement>("#updateSection");
  if (!box) return;
  // A tarball has no releases and a read-only panel cannot apply one. Neither
  // wants a section with a button in it that does nothing.
  box.hidden = updateGone;
  if (updateGone) return;

  const checking = updateBusy || update?.checking;
  const state = installing ? "installing"
    : !update ? "unknown"
    : update.canUpdate ? "ready"
    // Nothing tagged at all comes before any reason a checkout is being left
    // alone: "there is uncommitted work" is true but beside the point when there
    // is no release to have moved to in the first place.
    : !update.latest ? "current"
    : update.why ? "held"
    : "current";
  box.dataset.state = state;
  box.innerHTML = `
    <h3 class="section__title md-title-small">Panel version</h3>
    <div class="update-setting">
      <div class="update-setting__text">
        <p class="update-setting__now md-body-medium">On
          <span class="md-mono">${escapeHtml(whereWeAre() || "an unread checkout")}</span></p>
        <p class="update-setting__says md-label-small">${escapeHtml(saysAbout(state))}</p>
      </div>
      <div class="update-setting__acts">
        ${changelogMissing()
          ? ""
          : `<button class="button button--text md-state" id="changelogOpen">Changelog</button>`}
        ${state === "ready"
          ? `<button class="button button--filled md-state" id="updateOpen">See what changed</button>`
          : `<button class="button button--outlined md-state" id="updateRecheck"${
              checking || installing ? " disabled" : ""}>${
              checking ? "Checking…" : "Check for updates"}</button>`}
      </div>
    </div>`;
  box.querySelector("#changelogOpen")?.addEventListener("click", () => openChangelog(true));
  box.querySelector("#updateRecheck")?.addEventListener("click", async () => {
    // Forced, so it goes past the server's own six-hour hold: pressing this is
    // somebody asking, which is exactly the case the hold is not for.
    await fetchUpdate(true);
    if (update?.canUpdate) showSnackbar(`${update.latest} is out`);
    else if (update?.why) showSnackbar(update.why);
    else if (update?.message) showSnackbar(update.message);
    else showSnackbar("Already on the newest release");
  });
  box.querySelector("#updateOpen")?.addEventListener("click", () => openUpdate(true));
}

/* One line under the version saying what the state of it means. */
function saysAbout(state) {
  if (state === "installing") return "Restarting on the new release…";
  if (state === "unknown") {
    return updateBusy ? "Asking the remote what has been released…"
                      : update?.message || "Nothing read yet";
  }
  if (state === "ready") {
    return `${update.latest} is out — ${update.behind} release${update.behind === 1 ? "" : "s"} ahead of this one`;
  }
  if (state === "held") return update.why;
  // Up to date. Worth saying which release that is, and when it was cut: "you
  // are on the newest" reads as an assertion, and the date is the evidence.
  return update.latest
    ? `${update.latest} is the newest release${update.latestAt
        ? `, cut ${ago(Date.now() / 1000 - update.latestAt)}` : ""}`
    : "This repository has no releases tagged yet";
}


/* What a restart would cost, on its own. The survey behind the rest of this is
   held for hours on the server and a turn starts and ends well inside that, so a
   dialog standing open would otherwise be quoting a count from when it opened.
   This asks without forcing, which costs the server a dictionary and no git at
   all, and keeps only the part that moves. */
let runningTimer = null;

async function fetchRunning() {
  if (installing) return;
  try {
    const response = await fetch("/api/update", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    if (!update || !data.running) return;
    if (JSON.stringify(update.running) === JSON.stringify(data.running)) return;
    update.running = data.running;
    if (updateScrim.dataset.open === "true") paintUpdateDialog();
  } catch (error) {
    /* leave the last count on screen */
  }
}

/* What the panel is running, said as a sentence. Only ever about sessions the
   panel runs itself: one in a terminal is its own process and lives through a
   restart of the panel without noticing, and warning about those would teach
   people to ignore the warning that matters.

   Pure, and takes the count rather than reading it, so it can be lifted out and
   checked against every shape of it without a browser — see
   tests/update-check.mjs. */
export function runningSays(running) {
  if (!running?.here) return "";
  const { here, busy, compacting, queued, names } = running;
  const some = here === 1 ? "One session is running here" : `${here} sessions are running here`;
  const listed = names?.length
    ? ` — ${names.map(escapeHtml).join(", ")}${here > names.length ? `, and ${here - names.length} more` : ""}`
    : "";
  const losses = [];
  if (busy) losses.push(`${busy === 1 ? "one is mid-turn" : `${busy} are mid-turn`}, and that turn is cut off`);
  if (compacting) losses.push(compacting === 1 ? "one is compacting" : `${compacting} are compacting`);
  if (queued) losses.push(`${queued} typed-ahead message${queued === 1 ? "" : "s"} would be dropped`);
  // The reassurance is not a footnote: without it this reads as though updating
  // throws the work away, and it does not — the transcripts are Claude Code's
  // files and are exactly where they were.
  const sharp = busy || compacting || queued;
  return `<p class="update-running md-body-small" data-sharp="${sharp ? "1" : "0"}">
    <strong>${some}${listed}.</strong>
    They are stopped when the panel restarts${losses.length ? ` — ${losses.join("; ")}` : ""}.
    Their conversations stay on disk either way, and
    <span class="md-mono">claude --resume</span> in the folder still finds them.</p>`;
}

export function openUpdate(open) {
  updateScrim.dataset.open = String(open);
  if (runningTimer) { clearInterval(runningTimer); runningTimer = null; }
  if (!open) { updateButton.focus(); return; }
  // While it is open the count has to keep up: a turn can start between reading
  // the dialog and pressing the button, and the warning is the whole reason to
  // read it.
  runningTimer = setInterval(fetchRunning, 2000);
  fetchRunning();
  paintUpdateDialog();
  document.getElementById("closeUpdate").focus();
  // Opening it is asking, so a stale reading is refreshed here rather than at
  // the next half-hour.
  if (!update || Date.now() / 1000 - (update.at || 0) > UPDATE_POLL_MS / 1000) fetchUpdate(true);
}

/* The panel we are talking to is about to be replaced, so "did it work" cannot
   be answered by the response alone. Ask /api/state until something answers,
   then reload onto the new frontend. Kept patient: a rebuild of the frontend
   happens before the restart, and on a cold cache that is not instant. */
const WAIT_TRIES = 90;
async function waitForPanel() {
  for (let tries = 0; tries < WAIT_TRIES; tries += 1) {
    await new Promise((done) => setTimeout(done, 1000));
    try {
      const response = await fetch("/api/state", { cache: "no-store" });
      if (response.ok) { location.reload(); return; }
    } catch (error) {
      /* still down — that is the expected half of this loop */
    }
  }
  installing = false;
  paintUpdateChip();
  paintUpdateSetting();
  paintUpdateDialog();
  showSnackbar("The panel has not come back — start it again by hand");
}

async function install() {
  if (!update?.canUpdate || installing) return;
  updateGo.disabled = true;
  try {
    const response = await fetch("/api/update", {
      method: "POST", headers: { "Content-Type": "application/json" },
      // The tag goes back so the server can refuse an update this page never
      // offered — it checks it against what it reads for itself, and does not
      // take it as the thing to check out.
      body: JSON.stringify({ tag: update.latest }),
    });
    const data = await response.json().catch(() => ({}));
    showSnackbar(data.message || (response.ok ? "Updating" : "That did not work"));
    if (!data.restarting) {
      updateGo.disabled = false;
      fetchUpdate(true);
      return;
    }
    installing = true;
    if (runningTimer) { clearInterval(runningTimer); runningTimer = null; }
    paintUpdateChip();
    paintUpdateSetting();
    paintUpdateDialog();
    waitForPanel();
  } catch (error) {
    updateGo.disabled = false;
    showSnackbar("Could not reach the server");
  }
}

updateButton.addEventListener("click", () => openUpdate(true));
document.getElementById("closeUpdate").addEventListener("click", () => openUpdate(false));
updateCheck.addEventListener("click", () => { fetchUpdate(true); paintUpdateDialog(); });
updateGo.addEventListener("click", () => install());
updateScrim.addEventListener("click", (event) => {
  // Not while it is restarting: there is nothing to go back to behind it, and
  // the reload is what closes this dialog.
  if (event.target === updateScrim && !installing) openUpdate(false);
});
