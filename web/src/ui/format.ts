/* -------------------------------------------------------------------- format */
export function duration(seconds) {
  seconds = Math.max(0, Math.floor(seconds));
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  if (h) return `${h}h${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}
export function shorten(path, keep = 2) {
  const home = path.replace(/^\/home\/[^/]+/, "~");
  const parts = home.split("/");
  return parts.length > keep + 2 ? `${parts[0]}/…/${parts.slice(-keep).join("/")}` : home;
}
/* A one-line hint has room for a phrase, not a paragraph. Cut on the character
   rather than the path segment — a commit subject has no segments. */
export function clip(text, max) {
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}
export function clockOf(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.valueOf()) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
export function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* Commit ages run to days and weeks, which duration() — built for how long a
   session has been in a state — would render as three-figure hours. */
export function ago(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  const w = Math.floor(d / 7);
  return w < 6 ? `${w}w ago` : `${Math.floor(d / 30)}mo ago`;
}

/* Plural agreement, for the handful of places that count something. Named away
   from `count` because two functions here already use that for a local. */
export function plural(n, noun) {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

/* Token counts run to eight figures within an afternoon, so they are shortened.
   The exact number is never the question; the order of magnitude is. */
export function tokens(n) {
  n = n || 0;
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  return n.toLocaleString();
}
