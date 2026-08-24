import { reloadState } from "./refresh.js";
import { app } from "./state.js";
import { showSnackbar } from "./ui/snackbar.js";

export async function run(url, session, button, waitingMessage, extra) {
  if (app.inFlight) return {};
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
    return data;
  } catch (error) {
    showSnackbar("Could not reach the server");
    return {};
  } finally {
    app.inFlight = null;
    button.disabled = false;
    reloadState();
  }
}
