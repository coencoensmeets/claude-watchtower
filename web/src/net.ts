/* Asking the server to do something, and saying how it went.

   One request at a time, whatever asked for it: app.inFlight is the panel's
   single lock, so a second button cannot fire while the first is still out.
   Every path ends by reloading the state, because anything worth POSTing has
   changed something worth reading back. */

import { app } from "./state.js";
import { reloadState } from "./refresh.js";
import { showSnackbar } from "./ui/snackbar.js";

export async function run(url, session, button, waitingMessage, extra) {
  if (app.inFlight) return;
  app.inFlight = url;
  button.disabled = true;
  if (waitingMessage) showSnackbar(waitingMessage, 44000);
  try {
    const response = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: session.sessionId, ...extra }),
    });
    const data = await response.json().catch(() => ({}));
    showSnackbar(data.message || (response.ok ? "Done" : "That did not work"));
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    app.inFlight = null;
    button.disabled = false;
    reloadState();
  }
}
