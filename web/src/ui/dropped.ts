/* Files dragged into the box.

   Dropping a file on the message box types its path. Not its contents and not a
   copy of it: the session is already sitting in a folder with the file in it, and
   the shortest way to say "read this one" is to name it. So the drop is a typing
   aid — the path lands at the caret, you write the sentence around it, and what
   goes out is the sentence.

   Which is a different thing from a pasted picture (see images.ts). A screenshot
   on the clipboard has no path — it exists nowhere until the panel writes it —
   so it has to be saved before it can be named. A dropped file has a path
   already, and copying it into the session's folder to get one would leave a
   second copy of something that was never in doubt.

   Where the path comes from is worth knowing, because the browser is careful
   about it: a `File` off a drop gives its name and its bytes and never its path.
   The path arrives beside the file, as a `file://` URI on the drag's
   `text/uri-list` — which is what a file manager puts there. When that URI is on
   the drag, the path is the real one and nothing is copied.

   And plenty of drags have no path to give at all — which turns out to be the
   common half, so it is the half that had better work. A file dragged out of
   Chrome's downloads is the ordinary case — the drag carries the bytes and the
   `text/uri-list` is the address it was fetched from, not where it landed — and
   the same is true of a mail attachment or an image dragged off a page. There is
   nothing to name, so those go the way a pasted screenshot goes: the panel
   writes the file into the session's folder and the message names the copy. It
   is a copy, which is the cost of the drag not saying where the original is.

   One caveat the panel cannot check: the path is the file's path on the machine
   running the *browser*. Watch a session on another host from here and the path
   you dropped means nothing to it. The panel says so rather than guessing. */

import { growField, setSayDraft, syncCmdBar } from "../main.js";
import { ui } from "../state.js";
import { attachFile, attachPicture, isPicture } from "./images.js";
import { showSnackbar } from "./snackbar.js";

/* A `file://` URI as a path, or "" for anything this cannot honestly turn into
   one. A URI with a host in it names a file on another machine — `file://nas/x`
   is not `/x` — and quietly dropping the host would produce a path that looks
   right and points somewhere else. */
export function pathOfUri(uri) {
  const text = String(uri || "").trim();
  if (!/^file:\/\//i.test(text)) return "";
  const rest = text.slice("file://".length);
  const slash = rest.indexOf("/");
  if (slash < 0) return "";
  const host = rest.slice(0, slash).toLowerCase();
  if (host && host !== "localhost") return "";
  try {
    return decodeURIComponent(rest.slice(slash));
  } catch (error) {
    return "";                     // half a percent-escape: not a path we know
  }
}

/* Every path the drag is carrying, in the order they were dragged.

   `text/uri-list` is the list, one per line, and its comment lines start with a
   `#`. Some sources send only `text/plain` and put the same URIs in it, so that
   is read too — but only for lines that are `file://` URIs, because plain text
   that is not a path is a text drop and belongs to the browser. */
export function pathsOn(transfer) {
  const seen = [];
  const lists = [transfer?.getData?.("text/uri-list") || "",
                 transfer?.getData?.("text/plain") || ""];
  for (const list of lists) {
    for (const line of list.split(/[\r\n]+/)) {
      if (line.startsWith("#")) continue;
      const path = pathOfUri(line);
      if (path && !seen.includes(path)) seen.push(path);
    }
    // The second list is a fallback, not a supplement: if uri-list answered,
    // text/plain is the same drag said twice.
    if (seen.length) break;
  }
  return seen;
}

/* Whether the drag is one this box should take over at all. Files and paths, yes;
   a selection of text dragged from the conversation above, no — the textarea's
   own drop puts that in better than this could. */
export const dragCarriesFiles = (transfer) =>
  [...(transfer?.types || [])].some((type) => type === "Files" || type === "text/uri-list");

/* A path as it should read in a sentence. Bare, unless it holds something that
   would end it early somewhere downstream — a space, most of all, since a
   sentence naming /home/me/two words reads as two files to anybody, model or
   shell. Double quotes, as a terminal would take them. */
export function quotePath(path) {
  if (!/[\s"'`\\]/.test(path)) return path;
  return `"${path.replace(/(["\\])/g, "\\$1")}"`;
}

/* Put text in at the caret, with a space either side of it when the neighbours
   are not already space. Replacing the selection, because dropping onto
   highlighted text means the same here as typing over it does. */
export function insertAtCaret(field, text) {
  const start = field.selectionStart ?? field.value.length;
  const end = field.selectionEnd ?? start;
  const before = field.value.slice(0, start);
  const after = field.value.slice(end);
  const lead = before && !/\s$/.test(before) ? " " : "";
  const tail = after && !/^\s/.test(after) ? " " : "";
  // A trailing space even at the end of the box: the next thing typed is a word
  // about the file, not part of its name.
  const middle = `${lead}${text}${tail || (after ? "" : " ")}`;
  field.value = `${before}${middle}${after}`;
  const caret = before.length + middle.length;
  field.setSelectionRange(caret, caret);
}

/* The drop, wired onto the message box.

   `ui.droppingOnComposer` holds the pane still while a drag is over it: a poll
   landing mid-drag rebuilds the pane, and the box the file was dropped on would
   be gone by the time it landed. */
export function wireDrop(field, session) {
  const enter = (event) => {
    if (!dragCarriesFiles(event.dataTransfer)) return;
    // Without preventDefault on dragover the browser navigates to the file on
    // the drop and the panel is simply gone.
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    ui.droppingOnComposer = true;
    field.classList.add("is-dropping");
  };
  const leave = () => {
    ui.droppingOnComposer = false;
    field.classList.remove("is-dropping");
  };
  field.addEventListener("dragenter", enter);
  field.addEventListener("dragover", enter);
  field.addEventListener("dragleave", leave);
  field.addEventListener("dragend", leave);
  field.addEventListener("drop", (event) => {
    if (!dragCarriesFiles(event.dataTransfer)) return;   // text: the browser's
    event.preventDefault();
    leave();
    const paths = pathsOn(event.dataTransfer);
    if (paths.length) {
      field.focus();
      insertAtCaret(field, paths.map(quotePath).join(" "));
      setSayDraft(session.sessionId, field.value);
      growField(field);
      syncCmdBar(session);
      return;
    }
    // No path, so this came from somewhere that has none to give — Chrome's
    // downloads, a mail client, an image on a page. The bytes are here though,
    // so the file is saved into the session's folder and the message names the
    // copy: a picture down the paste route, which has a thumbnail waiting for it,
    // and anything else down the drop route.
    const files = [...(event.dataTransfer.files || [])];
    if (files.length) {
      for (const file of files) {
        if (isPicture(file)) attachPicture(session, file);
        else attachFile(session, file);
      }
      return;
    }
    // A drag that offered files and then had none: nothing was dropped that the
    // panel can either name or save.
    showSnackbar("That drop carried nothing the panel could send");
  });
}
