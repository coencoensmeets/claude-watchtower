/* ==========================================================================
   Colour settings. The seed and contrast are still read and stored, so an old
   saved setting is harmless, but the palette no longer comes from them: see
   the Watchtower palette below.
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
/* ==========================================================================
   The Watchtower palette — fixed, not generated.

   Colour means state: ember needs an answer, lilac is working, lime is waiting
   on you. Those three are mid-tones with ink on them, and they are the same in
   light and dark, so a session's card reads the same in either. Everything
   else is neutral, and `primary` is the action colour: ink in light, chalk in
   dark, so whatever you can press always stands off the page.
   See docs/design/styleguide.html.
   ========================================================================== */
export const WT_INK = "#1E1F24";
export const WT_STATE = {
  waiting: "#F28C4E",   // ember
  busy: "#A59FE2",      // lilac
  idle: "#CBDA72",      // lime
};
export const WT_SUPPORT = { butter: "#F5CD62", rose: "#E9839B" };
export const WT_TINT = {
  light: { waiting: "#FBE3D3", busy: "#E6E3F8", idle: "#EEF3D2" },
  dark: { waiting: "#3A2A22", busy: "#2B2A3D", idle: "#2C3020" },
};
export const WT_PAGE = { light: "#ECEBF1", dark: "#141518" };
export const WT_SCHEME = {
  light: {
    primary: WT_INK, onPrimary: "#FFFFFF", primaryContainer: "#CBDA72", onPrimaryContainer: WT_INK,
    secondary: "#6C6B77", onSecondary: "#FFFFFF", secondaryContainer: "#E4E2EA", onSecondaryContainer: WT_INK,
    tertiary: "#5E56C4", onTertiary: "#FFFFFF", tertiaryContainer: "#E6E3F8", onTertiaryContainer: "#2A2470",
    error: "#C2334F", onError: "#FFFFFF", errorContainer: "#FAD6DE", onErrorContainer: "#4E0D1C",
    surface: "#FAFAFC", onSurface: WT_INK, onSurfaceVariant: "#5F5E6A", surfaceDim: "#DCDAE3", surfaceBright: "#FFFFFF",
    surfaceContainerLowest: "#FFFFFF", surfaceContainerLow: "#F4F3F7", surfaceContainer: "#EFEEF3",
    surfaceContainerHigh: "#E8E7EE", surfaceContainerHighest: "#E1E0E8",
    outline: "#8C8B97", outlineVariant: "#DCDAE3", inverseSurface: "#2B2C31", inverseOnSurface: "#F2F1F6",
    inversePrimary: "#CBDA72", shadow: "#000000", scrim: "#000000",
  },
  dark: {
    primary: "#F2F1F6", onPrimary: WT_INK, primaryContainer: "#CBDA72", onPrimaryContainer: WT_INK,
    secondary: "#A9A8B4", onSecondary: WT_INK, secondaryContainer: "#2E2F36", onSecondaryContainer: "#F2F1F6",
    tertiary: "#C4BFF2", onTertiary: "#2A2470", tertiaryContainer: "#34324A", onTertiaryContainer: "#E6E3F8",
    error: "#F08DA2", onError: "#4E0D1C", errorContainer: "#5A2532", onErrorContainer: "#FAD6DE",
    surface: "#1E1F24", onSurface: "#F2F1F6", onSurfaceVariant: "#A9A8B4", surfaceDim: "#141518", surfaceBright: "#3A3B42",
    surfaceContainerLowest: "#18191D", surfaceContainerLow: "#222328", surfaceContainer: "#26272D",
    surfaceContainerHigh: "#2E2F36", surfaceContainerHighest: "#37383F",
    outline: "#74737F", outlineVariant: "#34353D", inverseSurface: "#F2F1F6", inverseOnSurface: WT_INK,
    inversePrimary: WT_INK, shadow: "#000000", scrim: "#000000",
  },
};
/* `plenty` is the green a plan figure takes while there is room; not a state. */
export const WT_PLENTY = {
  light: { color: "#4E9A5C", onColor: "#FFFFFF", container: "#DDF0DF", onContainer: "#133A1C" },
  dark: { color: "#8FD49B", onColor: "#133A1C", container: "#23402A", onContainer: "#DDF0DF" },
};

export const kebab = (name) => name.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
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
