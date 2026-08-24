/* What the box sends by path: pictures pasted, and files dropped without one.

   A message is text on every transport the panel has, so a picture travels as
   a path: it is written into the session's own folder and the message names it.
   What is here is the browser half — reading the clipboard, holding what is
   attached to the session it was attached for, and putting the paths into the
   text on the way out. */

import { growField, sentAs, setSayDraft, slashOf, syncCmdBar, terminalOnly } from "../main.js";
import { refreshDetail, reloadState } from "../refresh.js";
import { app } from "../state.js";
import { detailPane } from "./dom.js";
import { showSnackbar } from "./snackbar.js";
import { ownedFor } from "../views/owned.js";

/* ==========================================================================
   Pictures, pasted into the box.

   A message is text on every transport the panel has: the messaging socket takes
   a string and so does a held pipe, and neither has anywhere to put a PNG. So a
   picture does not travel in the message — it travels as a file, and the message
   says where the file is. Ctrl-V a screenshot here and it is written into the
   session's own folder, under .claude, and its path goes out on the end of what
   you typed; the session opens it with the tool it opens any file with.

   Which is worth being plain about, because it is not the same trick as the
   terminal's: pasting into Claude Code's own prompt carries the image itself.
   Here the picture is on disk, the session reads it from there, and it stays
   readable for as long as the conversation that mentions it is worth re-reading
   — a fortnight, after which the next paste sweeps it up.

   The upload does not wait for Send. It starts on the paste, so the round trip
   happens while you are still writing the sentence about it, and what is drawn
   above the box is the file as it stands: saving, saved, or why not.
   ========================================================================== */

/* What a browser puts on the clipboard for a screenshot. The server keeps its
   own copy of this list and is the one that decides; this is only so the box can
   ignore a paste it knows is not a picture without a round trip. */
const PASTE_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"]);
/* Matches PASTE_MAX_BYTES on the server. Checked here too, so an oversize paste
   is refused before it is read into a string a third larger than it. */
const PASTE_MAX_BYTES = 12 * 1024 * 1024;
/* Matches DROP_MAX_BYTES on the server. A drop that had no path is whatever was
   downloaded rather than a screenshot, so it is allowed to be bigger. */
const DROP_MAX_BYTES = 32 * 1024 * 1024;

/* Per session, like the draft it is going to be sent with, and for the same
   reason: switching away puts the pictures aside with the sentence they belong
   to and coming back brings both out again. */
const sayImages = new Map();
let pasteSeq = 0;

export const imagesFor = (sessionId) => sayImages.get(sessionId) || [];

/* Part of the pane's signature, so the strip repaints when a paste lands or a
   save comes back. Without it the poll's own render would see nothing changed
   and the picture you just pasted would not appear until something else moved. */
export const imagesStamp = (sessionId) => imagesFor(sessionId)
  .map((shot) => `${shot.id}${shot.path ? "+" : shot.failed ? "!" : "…"}`).join(",");

/* One row of the strip, before anything has been saved. `kind` is what it will
   read as in the message and what the strip draws for it: a picture has a
   thumbnail and a name it does not have yet, a file has its name from the start
   and no picture to show. */
const attachment = (kind, file, url) => ({
  id: `shot${++pasteSeq}`, kind, url, type: file.type, bytes: file.size,
  path: "", name: kind === "file" ? file.name : "", failed: false, why: "",
});

function setSayImages(sessionId, list) {
  if (list.length) sayImages.set(sessionId, list);
  else sayImages.delete(sessionId);
}

export function dropImage(sessionId, id) {
  const gone = imagesFor(sessionId).find((shot) => shot.id === id);
  if (gone) URL.revokeObjectURL(gone.url);
  setSayImages(sessionId, imagesFor(sessionId).filter((shot) => shot.id !== id));
  refreshDetail();
}

/* The thumbnails are the browser's own copy of what you pasted, not the file on
   disk read back: the picture is already here, and asking the server to serve it
   again would be a second road into somebody's checkout for no gain. */
function clearImages(sessionId) {
  for (const shot of imagesFor(sessionId)) URL.revokeObjectURL(shot.url);
  setSayImages(sessionId, []);
}

const readAsBase64 = (file) => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onerror = () => reject(new Error("unreadable"));
  // A data URL is base64 already, which is the form the JSON body wants — so the
  // comma is the whole of the conversion.
  reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
  reader.readAsDataURL(file);
});

/* One pasted picture, from the clipboard to a path. The row is drawn before the
   upload starts, because a screenshot that vanished for a second on its way to
   the server is indistinguishable from a paste that did not take. */
export async function attachPicture(session, file) {
  const sessionId = session.sessionId;
  if (!PASTE_TYPES.has(file.type)) {
    showSnackbar("That is not a kind of picture the panel can save");
    return;
  }
  if (file.size > PASTE_MAX_BYTES) {
    showSnackbar(`That picture is larger than ${Math.round(PASTE_MAX_BYTES / (1024 * 1024))} MB`);
    return;
  }
  const shot = attachment("image", file, URL.createObjectURL(file));
  setSayImages(sessionId, [...imagesFor(sessionId), shot]);
  refreshDetail();
  const fail = (why) => { shot.failed = true; shot.why = why; };
  try {
    const response = await fetch("/api/paste-image", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId, mime: file.type, data: await readAsBase64(file) }),
    });
    const data = await response.json().catch(() => ({}));
    if (response.ok && data.ok && data.path) {
      shot.path = data.path;
      shot.name = data.path.split("/").pop();
    } else {
      fail(data.message || "The panel could not save it");
    }
  } catch (error) {
    fail("Could not reach the server");
  }
  // The pane may be showing another session by now, and this session's strip
  // will be right whenever it comes back — the row was mutated in place.
  if (imagesFor(sessionId).includes(shot)) refreshDetail();
}

/* One dropped file, from the drag to a path — for a drop that carried no path of
   its own. Dragged out of Chrome's downloads, out of a mail client, out of
   anything holding bytes rather than a file: there is nothing to name, so the
   panel writes a copy into the session's folder and names that.

   The same shape as a paste and deliberately so — the row appears before the
   upload starts, the strip says where it got to, and Send waits for it. What
   differs is only that the name is known from the beginning, because this one
   came with one. */
export async function attachFile(session, file) {
  const sessionId = session.sessionId;
  if (file.size > DROP_MAX_BYTES) {
    showSnackbar(`That file is larger than ${Math.round(DROP_MAX_BYTES / (1024 * 1024))} MB`);
    return;
  }
  const shot = attachment("file", file, "");
  setSayImages(sessionId, [...imagesFor(sessionId), shot]);
  refreshDetail();
  const fail = (why) => { shot.failed = true; shot.why = why; };
  try {
    const response = await fetch("/api/drop-file", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId, name: file.name, data: await readAsBase64(file) }),
    });
    const data = await response.json().catch(() => ({}));
    if (response.ok && data.ok && data.path) {
      shot.path = data.path;
      // The name the panel gave it, which is not always the name it was dropped
      // under: a second drop of the same name is a second file.
      shot.name = data.path.split("/").pop();
    } else {
      fail(data.message || "The panel could not save it");
    }
  } catch (error) {
    fail("Could not reach the server");
  }
  if (imagesFor(sessionId).includes(shot)) refreshDetail();
}

/* The clipboard, filtered down to what is worth uploading. A copied region of a
   web page arrives as HTML *and* a bitmap; a copied file arrives as a file. Both
   are pictures here. Text is left alone entirely — the browser's own paste is
   what should happen to it, so this returns nothing and does not intervene. */
/* Whether a file is one the paste route can save. Asked of a dropped file by
   kind rather than by identity: `getAsFile()` hands back a fresh object every
   call, so a picture off a drag is never the same object twice and comparing
   them would send every dropped screenshot down the wrong route. */
export const isPicture = (file) => PASTE_TYPES.has(file?.type);

export function picturesOn(clipboard) {
  const items = [...(clipboard?.items || [])];
  return items
    .filter((item) => item.kind === "file" && PASTE_TYPES.has(item.type))
    .map((item) => item.getAsFile())
    .filter(Boolean);
}

/* What the paths look like on the end of the message.

   A line each, named rather than bare, because the session is being told that
   these came from you just now and are not files it went looking for. And after
   the sentence, not before: what you asked for reads first. */
function withImages(body, shots) {
  if (!shots.length) return body;
  // Named for how it got here, because that is the difference the session might
  // care about: a pasted picture exists nowhere else, where a saved copy of a
  // dropped file came from somewhere you had a moment ago.
  const lines = shots.map((shot) => shot.kind === "file"
    ? `[Dropped file: ${shot.path}]` : `[Pasted image: ${shot.path}]`).join("\n");
  return body ? `${body}\n\n${lines}` : lines;
}

export async function sendMessage(session, button, own = false) {
  const field = detailPane.querySelector("#sayField");
  if (!field || !button) return;
  const text = field.value.trim();
  const shots = imagesFor(session.sessionId);
  // A picture on its own is a message: "what do you make of this" is often the
  // whole of it, and there is nothing to type.
  if (!text && !shots.length) return;
  // A path that has not been written yet would go out pointing at nothing, so
  // the send waits for the upload rather than racing it. It takes a moment.
  if (shots.some((shot) => !shot.path && !shot.failed)) {
    showSnackbar("Give the picture a moment to save");
    return;
  }
  const saved = shots.filter((shot) => shot.path);
  // Nothing typed and nothing that saved: the failed rows say why already.
  if (!text && !saved.length) return;
  const asked = slashOf(text);
  // A terminal-only command would go out as a sentence nobody acts on, so it is
  // stopped here rather than sent into the dark.
  if (asked && terminalOnly(asked.name, session)) {
    showSnackbar(`/${asked.name} only works at this session's own prompt`);
    return;
  }
  // What actually goes over the wire: a request for a skill by name, if that is
  // what was typed, since an injected turn is never expanded the way the
  // terminal expands one. The box keeps what you wrote, and so does the draft.
  const sent = withImages(sentAs(text, session), saved);
  // Clear optimistically so typing the next one is not blocked on the round trip,
  // and put it back if the send fails — a lost message is worse than a stale box.
  field.value = "";
  setSayDraft(session.sessionId, "");
  // The strip goes with the box. The files stay on disk — the message that names
  // them is on its way — and the thumbnails are let go of here.
  clearImages(session.sessionId);
  growField(field);
  syncCmdBar(session);
  const restore = () => {
    setSayDraft(session.sessionId, text);
    // The pane may have moved to another session while the send was in flight —
    // the text goes back into the map either way, but only into a box that is
    // still this session's.
    if (detailPane.dataset.sessionId !== session.sessionId) return;
    const live = detailPane.querySelector("#sayField");
    if (live && !live.value) { live.value = text; growField(live); }
  };
  if (app.inFlight) { restore(); return; }
  // Two destinations, and the difference is who runs the turn: a session of ours
  // takes it here, a session in a terminal is sent to. There used to be a third —
  // start it in a terminal and let the message follow once it was listening — and
  // it was a choice nobody wanted to make. Send does not need a qualifier: if the
  // session is not up, running it is part of sending, not a separate decision.
  const url = own ? "/api/owned/say" : "/api/say";
  app.inFlight = url;
  button.disabled = true;
  try {
    const response = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: session.sessionId, text: sent,
                             ...(own ? { mode: ownedFor(session).mode || "default" } : {}) }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) restore();
    showSnackbar(data.message || (response.ok ? "Sent" : "That did not send"));
  } catch (error) {
    restore();
    showSnackbar("Could not reach the server");
  } finally {
    app.inFlight = null;
    button.disabled = false;
    reloadState();
  }
}
