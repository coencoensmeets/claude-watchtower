import { reloadState } from "./refresh.js";
import { app } from "./state.js";
import { showSnackbar } from "./ui/snackbar.js";
import type { Reply, Session } from "./types.js";

/* One POST at a time, with the button that asked for it disabled while it runs.

   Everything past `button` is optional because most callers have nothing to
   add: a `waitingMessage` is for the handful of actions slow enough that
   silence reads as nothing happening, and `extra` for the ones whose body is
   more than the session it acts on.

   Returns `{}` — not a rejection — when the request never got an answer, so a
   caller can read `data.ok` without also handling a throw. */
export async function run(
  url: string,
  session: Session,
  button: HTMLButtonElement,
  waitingMessage?: string | null,
  extra?: Record<string, unknown>,
): Promise<Reply> {
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
