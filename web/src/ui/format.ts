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
