/* ------------------------------------------------------------- state map */
export const STATE = {
  waiting: { label: "Needs an answer", short: "answer", prefix: "--md-extended-color-waiting" },
  busy: { label: "Working", short: "working", prefix: "--md-sys-color-primary", sys: true },
  idle: { label: "Waiting", short: "waiting", prefix: "--md-extended-color-idle" },
  offline: { label: "Closed", short: "closed", prefix: null },
  // A kept session whose process has gone. Not lost, just not running.
  stopped: { label: "Stopped", short: "stopped", prefix: null },
};
/* `shell` is not shown as a state of its own. It is what Claude Code writes on
   the way into a foreground command *and* what it leaves behind when a turn
   ends, so on screen it was a second colour for the same thing: waiting. The raw
   status still drives behaviour (a message can be queued for a shell session);
   only the display folds it in. */
export const STATE_ALIAS = { shell: "idle" };
export const stateKeyOf = (status) => STATE_ALIAS[status] || status;
export const stateOf = (status) => STATE[stateKeyOf(status)] || STATE.idle;

/* When the state you can see began. The server times the raw status, which
   restarts every time a waiting session dips through `shell`; on screen nothing
   happened, so the clock should not jump back to zero. */
export function displaySince(session) {
  const spans = session.trace || [];
  const key = stateKeyOf(session.status);
  let since = session.statusSince;
  for (let i = spans.length - 1; i >= 0; i--) {
    if (stateKeyOf(spans[i].status) !== key) break;
    since = spans[i].from;
  }
  return since;
}
for (const entry of Object.values(STATE)) {
  if (entry.sys) {
    entry.colour = "var(--md-sys-color-primary)";
    entry.container = "var(--md-sys-color-primary-container)";
    entry.onContainer = "var(--md-sys-color-on-primary-container)";
  } else if (entry.prefix) {
    entry.colour = `var(${entry.prefix}-color)`;
    entry.container = `var(${entry.prefix}-container)`;
    entry.onContainer = `var(${entry.prefix}-on-container)`;
  } else {
    entry.colour = "var(--md-sys-color-outline)";
    entry.container = "var(--md-sys-color-surface-container-high)";
    entry.onContainer = "var(--md-sys-color-on-surface-variant)";
  }
}
export const STATE_ORDER = ["waiting", "busy", "idle", "offline", "stopped"];


