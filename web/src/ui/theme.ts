/* ==========================================================================
   Dynamic colour — one seed generates the entire scheme.
   ========================================================================== */
export const DEFAULT_SEED = "#E8288F";
export const SEED_PRESETS = [
  { hex: "#E8288F", name: "Pink" }, { hex: "#7C4DFF", name: "Violet" },
  { hex: "#00A18F", name: "Teal" }, { hex: "#F4511E", name: "Coral" },
  { hex: "#3D5AFE", name: "Blue" }, { hex: "#7CB342", name: "Green" },
];
export const CONTRAST_LEVELS = [
  { key: "standard", label: "Standard", value: 0 },
  { key: "medium", label: "Medium", value: 0.5 },
  { key: "high", label: "High", value: 1 },
];
/* `plenty` is not a session state: it is the green a plan figure takes while
   there is still room, and it comes from here so it is hue-spaced against the
   seed and the other two like they are. Keep it last so adding it did not move
   the hues waiting and idle already had. */
export const STATE_BASE_HUES = { waiting: "#FF8A00", idle: "#5B6BC0", plenty: "#12A150" };
const MIN_HUE_GAP = 35;
export const MAX_CUSTOM_CHROMA = 48;

export const SYS_ROLES = [
  "primary", "onPrimary", "primaryContainer", "onPrimaryContainer",
  "secondary", "onSecondary", "secondaryContainer", "onSecondaryContainer",
  "tertiary", "onTertiary", "tertiaryContainer", "onTertiaryContainer",
  "error", "onError", "errorContainer", "onErrorContainer",
  "surface", "onSurface", "onSurfaceVariant", "surfaceDim", "surfaceBright",
  "surfaceContainerLowest", "surfaceContainerLow", "surfaceContainer",
  "surfaceContainerHigh", "surfaceContainerHighest",
  "outline", "outlineVariant", "inverseSurface", "inverseOnSurface", "inversePrimary",
  "shadow", "scrim",
];
export const kebab = (name) => name.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
const hueDistance = (a, b) => { const d = Math.abs(a - b) % 360; return d > 180 ? 360 - d : d; };

export function firstFreeHue(baseHue, occupied) {
  for (let delta = 0; delta <= 180; delta += 5) {
    for (const sign of delta === 0 ? [1] : [1, -1]) {
      const candidate = (baseHue + sign * delta + 360) % 360;
      if (occupied.every((hue) => hueDistance(candidate, hue) >= MIN_HUE_GAP)) return candidate;
    }
  }
  return baseHue;
}
export function customRoles(palette, dark) {
  return dark
    ? { color: palette.tone(80), onColor: palette.tone(20), container: palette.tone(30), onContainer: palette.tone(90) }
    : { color: palette.tone(40), onColor: palette.tone(100), container: palette.tone(90), onContainer: palette.tone(10) };
}

/* The four things a session can be announced for, in the order they are worth
   knowing about. Kept as a list rather than as four booleans so the page, the
   stored shape and the check in `announce` cannot drift apart.

   Each carries the notification it actually produces, because that is the
   clearest description of a notification there is: a label can only paraphrase
   when it fires, while the sample shows you the thing that will appear in the
   corner of your screen. `label` completes the sentence the heading starts. */
export const NOTIFY_KINDS = [
  { key: "permission",
    label: "wants permission to use a tool",
    says: ["Claude Watchtower needs permission", "Wants to run Bash"] },
  { key: "question",
    label: "asks you a multiple-choice question",
    says: ["Claude Watchtower has a question", "Asks “Which database?”"] },
  { key: "prompt",
    label: "stops at a prompt only its own terminal can answer",
    says: ["Claude Watchtower needs you", "Waiting at its own prompt"] },
  { key: "done",
    label: "finishes a turn and goes quiet",
    says: ["Claude Watchtower is done", "Finished, and waiting at its prompt"] },
];
export const allNotifying = () => Object.fromEntries(NOTIFY_KINDS.map((k) => [k.key, true]));
