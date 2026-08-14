import { snackbar } from "./dom.js";

/* ---------------------------------------------------------------- snackbar */
let snackTimer;
export function showSnackbar(message, life = 3400) {
  snackbar.textContent = message;
  snackbar.dataset.open = "true";
  clearTimeout(snackTimer);
  snackTimer = setTimeout(() => { snackbar.dataset.open = "false"; }, life);
}

/* ------------------------------------------------------------- settings UI */
