import { snackbar } from "./dom.js";

/* ---------------------------------------------------------------- snackbar */
let snackTimer;
export function showSnackbar(message, life = 3400) {
  snackbar.textContent = message;
  snackbar.dataset.open = "true";
  clearTimeout(snackTimer);
  // Long instructions need reading time: about 60ms a character beyond the default.
  const reading = Math.min(12000, String(message).length * 60);
  snackTimer = setTimeout(() => { snackbar.dataset.open = "false"; }, Math.max(life, reading));
}
