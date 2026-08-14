/* How much rope a session has: the permission mode, read and not set.

   Read, because a session writes the mode into its transcript only when the
   metadata block is re-appended — never when the mode changes. One sitting at
   its prompt keeps saying whatever it last said, which is stale often enough
   that the mode is no longer a pill among the header facts, where it read as
   current. It survives only as a fact in About, next to the caveat.

   Not set, because the panel cannot aim a keypress at a session. There is no
   external setter — Shift+Tab is the only way in — and X11 pairs windows, while
   a terminal window holds tabs and a VS Code window holds terminals. A press
   sent at a window lands in whichever tab has focus, which may be a different
   Claude. Loosening the wrong session's permissions is not a thing to get wrong
   occasionally, so this stays a readout. */
export const MODE_LABELS = {
  default: "Manual",
  acceptEdits: "Accept edits",
  plan: "Plan",
  bypassPermissions: "Bypass",
  auto: "Auto",
  dontAsk: "Don't ask",
};
/* How sure the panel is about a window, said the same way everywhere. An
   ambiguous match is not a match: several windows scored alike — the usual
   cause being one terminal process behind all of them — and the honest move is
   to say so rather than raise one of them and hope. */
export const WINDOW_CONFIDENCE = {
  paired: { short: "paired by you", long: "you chose this one" },
  identified: { short: "confirmed", long: "its terminal pointed to this one" },
  high: { short: "matched", long: "matched automatically" },
  likely: { short: "best guess", long: "a best guess from the process tree" },
  ambiguous: { short: "can't tell yet", long: "several windows look alike from the outside" },
};

export function windowSays(win, key) {
  return (WINDOW_CONFIDENCE[win?.confidence] || WINDOW_CONFIDENCE.likely)[key];
}

export function isAmbiguous(win) {
  return win?.confidence === "ambiguous";
}

/* Both a click and a probe write a pairing, and either can be cleared. */
export function isRemembered(win) {
  return win?.confidence === "paired" || win?.confidence === "identified";
}
