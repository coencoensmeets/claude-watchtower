import { app } from "../state.js";
import { clip } from "../ui/format.js";
import type { Session } from "../types.js";

/* ------------------------------------------------------------- state map */

/** A state a row can be drawn in. `label`/`short`/`prefix` are written below;
    `colour`/`container`/`onContainer` are filled in by the loop at the foot of
    this file, which is why they are optional here and read as certain
    everywhere else. */
export interface StateLook {
  label: string;
  short: string;
  /** The custom-colour family to take the three roles from, or null for the
      neutral outline. Unset where `sys` or `tertiary` decides instead. */
  prefix?: string | null;
  sys?: boolean;
  tertiary?: boolean;
  colour?: string;
  container?: string;
  onContainer?: string;
}

/** Named rather than `string` so a typo in a state name is an error, and so
    the two draw-only states cannot be mistaken for a status. */
export type StateKey =
  | "waiting" | "busy" | "idle" | "offline" | "stopped" | "here" | "compacting";

export const STATE: Record<StateKey, StateLook> = {
  waiting: { label: "Needs an answer", short: "answer", prefix: "--md-extended-color-waiting" },
  busy: { label: "Working", short: "working", prefix: "--md-sys-color-primary", sys: true },
  idle: { label: "Waiting", short: "waiting", prefix: "--md-extended-color-idle" },
  offline: { label: "Closed", short: "closed", prefix: null },
  // A kept session whose process has gone. Not lost, just not running.
  stopped: { label: "Stopped", short: "stopped", prefix: null },
  // The same thing, told apart because it matters: a session the panel was
  // handed. Nothing is running, which is what a panel-run session looks like
  // between turns — it is a process only while it is answering. Drawn as its own
  // state because "Stopped" made a working session read as a dead one.
  here: { label: "Runs from here", short: "here", tertiary: true },
  // Compaction is a turn like any other from the pipe's side, so on `status`
  // alone it reads as busy — and Working is the one thing it is not doing. It is
  // throwing the middle of the conversation away, which is worth knowing while
  // it happens and worth not confusing with a turn you asked for. Drawn only,
  // never a status: it is deliberately out of STATE_ORDER, so it gets no filter
  // chip and no lamp of its own, and nothing counts a compacting session twice.
  compacting: { label: "Compacting", short: "compacting", sys: true },
};
/* `shell` is not shown as a state of its own. It is what Claude Code writes on
   the way into a foreground command *and* what it leaves behind when a turn
   ends, so on screen it was a second colour for the same thing: waiting. The raw
   status still drives behaviour (a message can be queued for a shell session);
   only the display folds it in. */
const STATE_ALIAS: Record<string, StateKey> = { shell: "idle" };
export const stateKeyOf = (status: string): StateKey =>
  STATE_ALIAS[status] || (status as StateKey);
const stateOf = (status: string): StateLook => STATE[stateKeyOf(status)] || STATE.idle;
/* What to draw for a session, which is not always what to reason about it with.
   An adopted session's status stays `stopped` — nothing is running, and every
   check that turns on that is still right — but calling it Stopped on screen was
   what made "make interactive" look like it had only stopped things. */
export const drawnStateOf = (session: Session): StateLook => {
  const owned = (app.feed.owned || {})[session.sessionId] || {};
  // Before anything else, because it is the more particular thing to say: a
  // compacting session is busy, and saying Working over it hides the one turn
  // the panel knows the subject of. See STATE.compacting.
  if (owned.compact?.running) return STATE.compacting;
  if (!owned.here) return stateOf(session.status);
  // Held and running: it is working or waiting, and says so in the ordinary
  // words — there is a process and it is doing one of those two things, which is
  // what the row is for. Held but not running: nothing is behind it yet.
  return owned.running ? stateOf(session.status) : STATE.here;
};

/* The prompt a session is standing on, whichever channel it came up on: a
   permission gate or a question the panel is holding open over its own pipe, or
   a question read out of a terminal session's transcript. One shape, because
   the row, the detail pane and the notification all want the same three facts —
   which kind it is, what to call it, and whether it is the same prompt as the
   one that was there a second ago. */
export function standingAsk(session: Session) {
  const gate = ((app.feed.owned || {})[session.sessionId] || {}).ask;
  if (gate) {
    const header = gate.input?.questions?.[0]?.header;
    return gate.asks
      ? { kind: "question", label: `asks “${clip(header || "a question", 40)}”`,
          key: `gate:${gate.requestId}` }
      : { kind: "permission", label: `wants to run ${clip(gate.name || "a tool", 32)}`,
          key: `gate:${gate.requestId}` };
  }
  // A terminal session's question is read out of its transcript, so there is no
  // request id to key on — the question itself is the only identity it has.
  const asked = session.question?.questions?.[0];
  if (asked) {
    const header = asked.header || asked.question || "a question";
    return { kind: "question", label: `asks “${clip(header, 40)}”`, key: `said:${clip(header, 80)}` };
  }
  // Blocked on something the panel cannot read. Which kind of prompt a terminal
  // session is standing on is not knowable from outside — Claude Code writes a
  // permission gate down nowhere — so the badge says only that there is one and
  // that it has to be answered where it was asked.
  if (stateKeyOf(session.status) === "waiting") {
    return { kind: "prompt", label: "waiting at its own prompt", key: "prompt" };
  }
  return null;
}
export const ASK_WORD = { question: "question", permission: "permission", prompt: "prompt" };
export const ASK_ICON = { question: "ask", permission: "gate", prompt: "ask" };

/* When the state you can see began. The server times the raw status, which
   restarts every time a waiting session dips through `shell`; on screen nothing
   happened, so the clock should not jump back to zero. */
export function displaySince(session: Session): number {
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
  } else if (entry.tertiary) {
    entry.colour = "var(--md-sys-color-tertiary)";
    entry.container = "var(--md-sys-color-tertiary-container)";
    entry.onContainer = "var(--md-sys-color-on-tertiary-container)";
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
export const STATE_ORDER: StateKey[] = ["waiting", "busy", "idle", "offline", "stopped"];
