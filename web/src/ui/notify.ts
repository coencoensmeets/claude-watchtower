/* Saying something happened, when the panel is not the window you are in.

   One notification per session per thing worth saying, and never twice for the
   same thing: what each session was last announced as is remembered. */

import { selectSession } from "../main.js";
import { standingAsk } from "../sessions/state.js";
import { app, lastAnnounced, mutedSessions, quietWhenDone } from "../state.js";
import { shorten } from "./format.js";
import { paintNotifyPermission } from "./settings.js";

/* ------------------------------------------------------------ notifications */
/* The browser will only ask on a gesture, so the first click anywhere is taken
   as one. It is not the discoverable way in — that is the button on the
   settings page, which says what is being asked for before it asks — but it
   means somebody who never opens settings is still asked once, rather than
   silently never being told anything. */
if ("Notification" in window && Notification.permission === "default") {
  document.addEventListener("click", () => Notification.requestPermission()
    .then(() => paintNotifyPermission()), { once: true });
}
/* What a session is stopped on, as one string to compare against the last one.

   Keyed on the prompt rather than on the status, because a session can go from
   one prompt straight to the next without passing back through working — a
   permission gate allowed, the next tool gated a moment later — and a status
   that reads `waiting` both times would have let the second one through
   unannounced. */
/* What this session is worth being told about right now, keyed so the same
   thing twice running is told once. A standing prompt outranks everything; a
   turn that has ended is the other thing worth a word.

   `shell` is deliberately not folded into `idle` here, though the display folds
   it: Claude Code writes `shell` both on the way into a foreground command and
   again as a turn ends, so a session dipping through it mid-turn would announce
   itself finished and then carry on working. */
function notifyKey(session) {
  const ask = standingAsk(session);
  if (ask) return ask.key;
  if (session.status === "busy" || session.status === "shell") return "work";
  return session.status === "idle" ? "done" : "";
}

export function announce(sessions) {
  const seen = new Set();
  const can = "Notification" in window && Notification.permission === "granted";
  for (const session of sessions) {
    seen.add(session.sessionId);
    const before = lastAnnounced.get(session.sessionId);
    const now = notifyKey(session);
    lastAnnounced.set(session.sessionId, now);
    // `before === undefined` is the first poll after a page load: everything
    // already on screen would fire at once, which is not news. `work` is the
    // turn still running, which is not an event either.
    if (before === undefined || !now || now === before || now === "work") continue;
    if (mutedSessions.has(session.sessionId) || !can) continue;
    // Finished is only news if we watched it working. A row arriving from
    // offline, or a kept session waking up, lands on `idle` without a turn
    // having happened.
    if (now === "done" && (!before || quietWhenDone.has(session.sessionId))) continue;
    const ask = standingAsk(session);
    // Which kinds are wanted at all is a setting for the whole panel; which
    // sessions may use them is a switch on each session.
    if (!app.settings.notify[ask?.kind || "done"]) continue;
    const title = !ask ? `${session.name} is done`
      : ask.kind === "permission" ? `${session.name} needs permission`
      : ask.kind === "question" ? `${session.name} has a question`
      : `${session.name} needs you`;
    const said = ask ? ask.label[0].toUpperCase() + ask.label.slice(1)
      : "Finished, and waiting at its prompt";
    const note = new Notification(title, {
      body: `${said} — ${shorten(session.cwd)}`,
      tag: session.sessionId,
      // A prompt the panel is holding runs out and is refused if nobody
      // answers, so it stays on screen until it is looked at. A terminal
      // session's prompt waits as long as you do, and a finished turn is not
      // waiting on anything at all.
      requireInteraction: !!ask && ask.key.startsWith("gate:"),
    });
    // The whole point of being told is going there, and the answer is in the
    // panel for a held prompt.
    const id = session.sessionId;
    note.onclick = () => { window.focus(); note.close(); selectSession(id); };
  }
  for (const key of [...lastAnnounced.keys()]) if (!seen.has(key)) lastAnnounced.delete(key);
}

export function paintFavicon(counts) {
  const read = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const dot = counts?.waiting ? read("--md-extended-color-waiting-color")
    : counts?.busy ? read("--md-sys-color-primary") : read("--md-extended-color-idle-color");
  const back = read("--md-sys-color-surface-container-highest");
  if (!dot || !back) return;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
    <rect width="32" height="32" rx="9" fill="${back}"/><circle cx="16" cy="16" r="7" fill="${dot}"/></svg>`;
  let link = document.querySelector("link[rel='icon']");
  if (!link) { link = document.createElement("link"); link.rel = "icon"; document.head.appendChild(link); }
  link.href = "data:image/svg+xml," + encodeURIComponent(svg);
}
