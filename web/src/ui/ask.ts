/* ------------------------------------------------------- asking first */

/* One dialog, awaited rather than called back into: the caller reads like the
   sentence it is — ask, and if the answer is yes, do the thing. */
export const askScrim = document.getElementById("askScrim");
let askResolve = null;

/* The same dialog with a field in it, for the one question that needs a word
   back rather than a yes: what to call a new branch. Resolves to the trimmed
   text, or null if it was dismissed. */
export function askText({ headline, body, placeholder = "", value = "", confirmLabel = "Create" }) {
  // The dialog goes up first: opening one closes whatever stood before it, and
  // that closing is what puts the field away — reveal it before, and it is
  // hidden again by the time anyone could type in it.
  const answer = askConfirm({ headline, body, confirmLabel, danger: false });
  const field = document.getElementById("askField") as HTMLInputElement;
  field.hidden = false;
  field.placeholder = placeholder;
  field.value = value;
  field.focus();
  field.select();
  return answer.then((ok) => (ok ? field.value.trim() || null : null));
}

export function askConfirm({ headline, body, confirmLabel = "Confirm", danger = true }) {
  closeAsk(false);
  document.getElementById("askHeadline").textContent = headline;
  document.getElementById("askSupporting").innerHTML = body;
  const confirm = document.getElementById("askConfirm");
  confirm.textContent = confirmLabel;
  // Red is for the answers that lose something. A question that only asks before
  // overwriting a box you can retype is not one of them.
  confirm.classList.toggle("button--danger", danger);
  confirm.classList.toggle("button--filled", !danger);
  askScrim.dataset.open = "true";
  document.getElementById("askCancel").focus();
  return new Promise((resolve) => { askResolve = resolve; });
}

export function closeAsk(answer) {
  askScrim.dataset.open = "false";
  // The field belongs to whichever question asked for it, so it goes away with
  // that question rather than lingering into the next one.
  document.getElementById("askField").hidden = true;
  const resolve = askResolve;
  askResolve = null;
  if (resolve) resolve(answer);
}

// Enter in the field is the same as pressing the confirming button beside it.
document.getElementById("askField").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  closeAsk(true);
});

document.getElementById("askCancel").addEventListener("click", () => closeAsk(false));
document.getElementById("askConfirm").addEventListener("click", () => closeAsk(true));
askScrim.addEventListener("click", (event) => { if (event.target === askScrim) closeAsk(false); });
