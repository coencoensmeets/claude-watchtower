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

/* Notifications go out through a service worker. A phone's browser will not
   show one any other way — Chrome on Android throws on `new Notification()` —
   and it costs a desktop nothing. Only in a secure context, which is where a
   browser hands out notifications at all: localhost, or https over Tailscale. */
let worker: Promise<ServiceWorkerRegistration | null> = Promise.resolve(null);
// Why there is no worker, when there is not, for the test button to say.
let workerFailed = "";
if ("serviceWorker" in navigator && window.isSecureContext) {
  worker = navigator.serviceWorker.register("/sw.js", { scope: "/" })
    .then(() => navigator.serviceWorker.ready)
    .catch((error) => { workerFailed = String(error?.message || error); return null; });
  // A notification tapped while the panel is open: the worker brings this
  // window forward and says which session to open.
  navigator.serviceWorker.addEventListener("message", (event) => {
    if (event.data?.kind === "open-session" && event.data.sessionId) {
      selectSession(event.data.sessionId);
    }
  });
}
// Opened from a notification with no panel open: the session is in the address,
// read already as the selection, and taken back out so a reload is ordinary.
if (new URLSearchParams(location.search).has("session")) {
  const url = new URL(location.href);
  url.searchParams.delete("session");
  history.replaceState(null, "", url);
}

/* One notification, through the worker when there is one and the page's own
   constructor when there is not (plain http on a desktop, say). Resolves to ""
   when it went out, or why it did not. */
async function notify(title: string, options: NotificationOptions & { renotify?: boolean },
                      sessionId: string): Promise<string> {
  const reg = await worker;
  let why = workerFailed ? `the service worker did not start (${workerFailed})` : "";
  if (reg) {
    try {
      await reg.showNotification(title, { ...options, data: { sessionId } });
      return "";
    } catch (error) { why = String(error?.message || error); }
  }
  try {
    const note = new Notification(title, options);
    note.onclick = () => { window.focus(); note.close(); if (sessionId) selectSession(sessionId); };
    return "";
  } catch (error) {
    // This browser will only show one through a worker, and there is none.
    return why || String(error?.message || error);
  }
}

/* The test button in Settings: one notification now, so "allowed" can be seen
   to mean something on this device. Resolves to what to say about it. */
export async function testNotification(): Promise<string> {
  if (!("Notification" in window)) return "This browser cannot show notifications";
  if (Notification.permission !== "granted") return "Notifications are not allowed in this browser";
  const why = await notify("Watchtower", {
    body: "Notifications reach this device. This is what a session needing you looks like.",
    tag: "watchtower-test",
  }, "");
  return why ? `It did not show: ${why}` : "Sent — it should be on screen now";
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
    notify(title, {
      body: `${said} — ${shorten(session.cwd)}`,
      tag: session.sessionId,
      // A second thing to say about the same session replaces the first on
      // screen, and should still buzz the phone rather than swap in silently.
      renotify: true,
      // A prompt the panel is holding runs out and is refused if nobody
      // answers, so it stays on screen until it is looked at. A terminal
      // session's prompt waits as long as you do, and a finished turn is not
      // waiting on anything at all.
      requireInteraction: !!ask && ask.key.startsWith("gate:"),
    }, session.sessionId);
  }
  for (const key of [...lastAnnounced.keys()]) if (!seen.has(key)) lastAnnounced.delete(key);
}

/* The dot is the worst state on the list, or the neutral one when the tally
   is not to hand — repainting on a theme change has no counts to pass. */
export function paintFavicon(counts?: Record<string, number>) {
  const read = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const dot = counts?.waiting ? read("--md-extended-color-waiting-color")
    : counts?.busy ? read("--md-sys-color-primary") : read("--md-extended-color-idle-color");
  const back = read("--md-sys-color-surface-container-highest");
  if (!dot || !back) return;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
    <rect width="32" height="32" rx="9" fill="${back}"/><circle cx="16" cy="16" r="7" fill="${dot}"/></svg>`;
  let link = document.querySelector<HTMLLinkElement>("link[rel='icon']");
  if (!link) { link = document.createElement("link"); link.rel = "icon"; document.head.appendChild(link); }
  link.href = "data:image/svg+xml," + encodeURIComponent(svg);
}
