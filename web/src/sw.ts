/* The panel's service worker. It exists for one reason: a phone's browser will
   not let a page show a notification itself — Chrome on Android throws on
   `new Notification()` and wants one shown through a service worker — so the
   panel shows every notification through this, desktop included.

   It caches nothing and answers no fetches: the panel is a live view of the
   machine, and a stale copy of it is worse than none. */

/// <reference lib="webworker" />
const sw = self as unknown as ServiceWorkerGlobalScope;

sw.addEventListener("install", () => { sw.skipWaiting(); });
sw.addEventListener("activate", (event) => { event.waitUntil(sw.clients.claim()); });

/* Going there is the point of being told: bring a panel window forward and
   open the session the notification was about. With no panel open, open one —
   it lands on the session named in the address. */
sw.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const id = event.notification.data?.sessionId || "";
  event.waitUntil((async () => {
    const open = await sw.clients.matchAll({ type: "window", includeUncontrolled: true });
    const panel = open.find((c) => new URL(c.url).origin === sw.location.origin);
    if (panel) {
      await (panel as WindowClient).focus();
      panel.postMessage({ kind: "open-session", sessionId: id });
      return;
    }
    await sw.clients.openWindow(id ? `/?session=${encodeURIComponent(id)}` : "/");
  })());
});
