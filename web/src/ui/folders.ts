/* ------------------------------------------------------ the folder browser */

/* Picking a folder for a new session from this computer's folders, drawn in the
   panel. The desktop's own chooser is a window on the computer's screen, which is
   no use to someone holding a phone — so off this machine, or where the desktop
   has no chooser, this is what "Another folder…" opens instead.

   Awaited like the ask dialog: resolves to the folder picked, or null if it was
   dismissed. The server lists only folders inside your home, and starts a
   session only in one of those. */

import { escapeHtml } from "./format.js";
import { ICON } from "./icons.js";

export const folderScrim = document.getElementById("folderScrim");
const pathLine = document.getElementById("folderPath");
const list = document.getElementById("folderList") as HTMLUListElement;
const upButton = document.getElementById("folderUp") as HTMLButtonElement;
const hiddenBox = document.getElementById("folderHidden") as HTMLInputElement;
const confirmButton = document.getElementById("folderConfirm") as HTMLButtonElement;

upButton.innerHTML = ICON.back;

let resolveFolder: ((path: string | null) => void) | null = null;
let here = "";
let parent: string | null = null;
let home = "";
let asked = 0;

/* Whether the browser is on the machine the panel runs on. The desktop's chooser
   is only worth opening when someone is sitting in front of it. */
export const onThisMachine = () =>
  ["localhost", "127.0.0.1", "[::1]", "::1"].includes(location.hostname);

export function browseFolder(): Promise<string | null> {
  closeFolders(null);
  here = "";
  list.innerHTML = "";
  pathLine.textContent = "…";
  folderScrim.dataset.open = "true";
  load("");
  confirmButton.focus();
  return new Promise((resolve) => { resolveFolder = resolve; });
}

export function closeFolders(answer: string | null) {
  if (folderScrim.dataset.open !== "true" && !resolveFolder) return;
  folderScrim.dataset.open = "false";
  const done = resolveFolder;
  resolveFolder = null;
  done?.(answer);
}

/* A home folder shown as ~, the way a terminal prompt shows it: on a phone the
   path line is narrow, and /home/<name> is the same on every line of it. */
function tilde(path: string) {
  if (home && (path === home || path.startsWith(home + "/"))) return "~" + path.slice(home.length);
  return path;
}

async function load(path: string) {
  // A slow answer to an earlier tap must not land on top of a later one.
  const mine = ++asked;
  confirmButton.disabled = true;
  try {
    const query = new URLSearchParams();
    if (path) query.set("path", path);
    if (hiddenBox.checked) query.set("hidden", "1");
    const response = await fetch(`/api/folders?${query}`);
    const data = await response.json().catch(() => ({}));
    if (mine !== asked) return;
    if (!data.ok) {
      list.innerHTML = `<li class="folder-dialog__note md-body-medium">${escapeHtml(data.message || "Could not list that folder")}</li>`;
      return;
    }
    here = data.path;
    parent = data.parent;
    home = data.home;
    pathLine.textContent = tilde(here);
    pathLine.title = here;
    upButton.disabled = !parent;
    confirmButton.disabled = false;
    const rows = data.folders.map((f) => `
      <li><button class="folder-dialog__row md-state md-body-large" type="button" data-path="${escapeHtml(f.path)}">
        ${ICON.folder}<span>${escapeHtml(f.name)}</span></button></li>`);
    if (!rows.length) rows.push(`<li class="folder-dialog__note md-body-medium">No folders in here</li>`);
    if (data.more) rows.push(`<li class="folder-dialog__note md-body-medium">and ${data.more} more not shown</li>`);
    list.innerHTML = rows.join("");
    list.scrollTop = 0;
  } catch (error) {
    if (mine === asked) {
      list.innerHTML = `<li class="folder-dialog__note md-body-medium">Could not reach the server</li>`;
    }
  }
}

list.addEventListener("click", (event) => {
  const row = (event.target as HTMLElement).closest<HTMLElement>("[data-path]");
  if (row) load(row.dataset.path);
});
upButton.addEventListener("click", () => { if (parent) load(parent); });
hiddenBox.addEventListener("change", () => load(here));
document.getElementById("folderCancel").addEventListener("click", () => closeFolders(null));
confirmButton.addEventListener("click", () => { if (here) closeFolders(here); });
folderScrim.addEventListener("click", (event) => {
  if (event.target === folderScrim) closeFolders(null);
});
// Caught before the page's own Escape handler, which would otherwise go on to
// drop whatever rows are picked behind the dialog.
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || folderScrim.dataset.open !== "true") return;
  event.stopImmediatePropagation();
  closeFolders(null);
}, true);
