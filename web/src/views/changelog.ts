/* ==========================================================================
   The changelog, read out of the checkout the panel is running from.

   Not fetched from the internet and not built into the page: it is the
   CHANGELOG.md sitting beside server.py, so it is the changelog of the version
   you are actually running — which is the only one worth reading in a panel
   that updates itself. Asked for on every open rather than held, because the
   file changes exactly when the panel updates and a held copy would be the old
   one.

   It sits in its own scrim rather than inside the update dialog. That dialog
   answers "should I take this release"; this answers "what has changed over all
   of them", which is worth reading on a panel with nothing to update to.
   ========================================================================== */

import { ago } from "../ui/format.js";
import { renderMarkdown } from "../ui/markdown.js";
import { showSnackbar } from "../ui/snackbar.js";

export const changelogScrim = document.getElementById("changelogScrim");
const changelogBody = document.getElementById("changelogBody");
const changelogAge = document.getElementById("changelogAge");
const closeChangelog = document.getElementById("closeChangelog");

/* Whether the checkout has one at all. A tarball, or a copy with the file
   removed, answers 404 once and the button goes rather than offering an empty
   dialog every time. */
let changelogGone = false;
export const changelogMissing = () => changelogGone;

let loading = false;

export async function openChangelog(open: boolean) {
  changelogScrim.dataset.open = String(open);
  if (!open) return;
  closeChangelog.focus();
  await load();
}

async function load() {
  if (loading) return;
  loading = true;
  // Said before the request rather than after it: a file off a local disk is
  // usually there before the frame is drawn, and a flash of "reading" is worse
  // than nothing — so this only ever shows on a read that is genuinely slow.
  if (!changelogBody.innerHTML) {
    changelogBody.innerHTML = `<p class="md-body-medium">Reading the changelog…</p>`;
  }
  try {
    const response = await fetch("/api/changelog", { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      changelogGone = response.status === 404;
      changelogBody.innerHTML = "";
      changelogAge.textContent = "";
      openChangelog(false);
      showSnackbar(data.message || "Could not read the changelog");
      return;
    }
    // Through the same renderer the conversation uses, so a heading is a
    // heading and nothing in the file can reach the page as markup.
    changelogBody.innerHTML = renderMarkdown(data.text || "");
    changelogAge.textContent = data.at
      ? `written ${ago(Date.now() / 1000 - data.at)}`
      : "";
    changelogBody.scrollTop = 0;
  } catch (error) {
    changelogBody.innerHTML = "";
    openChangelog(false);
    showSnackbar("Could not reach the server");
  } finally {
    loading = false;
  }
}

closeChangelog.addEventListener("click", () => openChangelog(false));
changelogScrim.addEventListener("click", (event) => {
  if (event.target === changelogScrim) openChangelog(false);
});
/* Escape closes it, and stops there: this module is imported before main.ts
   attaches its own Escape handler, so without halting the event the same press
   would also close the settings page underneath. */
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || changelogScrim.dataset.open !== "true") return;
  event.stopImmediatePropagation();
  openChangelog(false);
});
