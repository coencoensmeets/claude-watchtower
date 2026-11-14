/* Talking instead of typing: a microphone on the message box.

   The browser's own speech recognition (the Web Speech API) does the listening —
   Chrome, Edge, Safari and Chrome on Android have it; Firefox does not. Nothing
   is installed and nothing new reaches the panel: the words land in the box as
   you say them, exactly as if they had been typed, and Send is still yours to
   press. Chrome hands the audio to Google to transcribe, which is the price of
   having no model on the laptop.

   The button is only drawn where it can work. A browser gives a page the
   microphone only in a secure context — https, or localhost — so on the laptop
   it is there at once, and on a phone it appears once the panel is opened over
   https on its tailnet name (see watchtower/tailnet.py).

   The pane is rebuilt under the box while you talk — a status change, a message
   landing — so nothing here holds on to the field. Each result looks the box up
   again, writes into it, and fires the same `input` event typing would, which is
   what keeps the draft and the box's height right. */

import { detailPane } from "./dom.js";
import { showSnackbar } from "./snackbar.js";

type Recognizer = {
  lang: string; continuous: boolean; interimResults: boolean;
  start(): void; stop(): void; abort(): void;
  onresult: ((event: any) => void) | null;
  onerror: ((event: any) => void) | null;
  onend: (() => void) | null;
};

const Speech = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

export function voiceAvailable(): boolean {
  return !!Speech && window.isSecureContext;
}

/* One recognizer at a time, for one session's box. */
const live = {
  recognizer: null as Recognizer | null,
  sessionId: "",
  // What was in the box before this stretch of talking, which the words go after.
  base: "",
  // Finished phrases from results already folded into `base` — after a send or
  // a hand edit — which are not to be written a second time.
  skip: 0,
  // What this module last put in the box, to notice when somebody else changed it.
  wrote: "",
};

function field(): HTMLTextAreaElement | null {
  if (detailPane.dataset.sessionId !== live.sessionId) return null;
  return detailPane.querySelector<HTMLTextAreaElement>("#sayField");
}

function joined(base: string, said: string): string {
  if (!said) return base;
  if (!base || /\s$/.test(base)) return base + said.trimStart();
  return `${base} ${said.trimStart()}`;
}

function paintButton() {
  const button = detailPane.querySelector<HTMLButtonElement>("[data-act='voice']");
  if (!button) return;
  const on = !!live.recognizer && detailPane.dataset.sessionId === live.sessionId;
  button.setAttribute("aria-pressed", on ? "true" : "false");
  button.title = on ? "Stop listening" : "Talk instead of typing";
  button.setAttribute("aria-label", button.title);
}

export function stopVoice() {
  const recognizer = live.recognizer;
  live.recognizer = null;
  if (recognizer) {
    recognizer.onresult = null;
    try { recognizer.stop(); } catch (error) { /* already stopped */ }
  }
  paintButton();
}

function startVoice(sessionId: string) {
  stopVoice();
  const box = detailPane.querySelector<HTMLTextAreaElement>("#sayField");
  const recognizer: Recognizer = new Speech();
  recognizer.lang = navigator.language || "en-US";
  recognizer.continuous = true;
  recognizer.interimResults = true;
  Object.assign(live, { recognizer, sessionId, base: box?.value || "", skip: 0, wrote: box?.value || "" });

  recognizer.onresult = (event) => {
    const box = field();
    if (!box) return;
    const results = Array.from(event.results as ArrayLike<any>);
    // Sent, or edited by hand, since the last result: what is there now is the
    // new starting point, and the phrases already written are not written again.
    if (box.value !== live.wrote) {
      live.base = box.value;
      live.skip = results.filter((r) => r.isFinal).length;
    }
    let finals = 0;
    let said = "";
    for (const result of results) {
      if (result.isFinal) {
        finals += 1;
        if (finals <= live.skip) continue;
      }
      said += result[0]?.transcript || "";
    }
    const text = joined(live.base, said);
    box.value = text;
    live.wrote = text;
    box.dispatchEvent(new Event("input", { bubbles: true }));
  };
  recognizer.onerror = (event) => {
    const why = event?.error;
    if (why === "not-allowed" || why === "service-not-allowed") {
      showSnackbar("The microphone is blocked — allow it in the browser's settings for this site");
    } else if (why === "network") {
      showSnackbar("Speech recognition needs the internet, and could not reach it");
    } else if (why && why !== "no-speech" && why !== "aborted") {
      showSnackbar(`Listening stopped — ${why}`);
    }
  };
  // Phones stop on their own after a silence. That is the end of the stretch,
  // not something to restart behind your back.
  recognizer.onend = () => {
    if (live.recognizer === recognizer) live.recognizer = null;
    paintButton();
  };
  try {
    recognizer.start();
  } catch (error) {
    live.recognizer = null;
    showSnackbar("Could not start listening");
  }
  paintButton();
}

/* Called each time the composer is drawn: shows the button where it can work,
   carries the listening state across the rebuild, and stops listening when the
   message goes — a phrase landing after Send would start the next message
   with the tail of the last one. */
export function wireVoice(sessionId: string) {
  const button = detailPane.querySelector<HTMLButtonElement>("[data-act='voice']");
  if (!button) return;
  button.hidden = !voiceAvailable();
  if (button.hidden) return;
  // Moving to another session's pane is the end of talking to this one.
  if (live.recognizer && live.sessionId !== sessionId) stopVoice();
  paintButton();
  button.addEventListener("click", () => {
    if (live.recognizer && live.sessionId === sessionId) stopVoice();
    else startVoice(sessionId);
    detailPane.querySelector<HTMLTextAreaElement>("#sayField")?.focus();
  });
  for (const send of detailPane.querySelectorAll<HTMLElement>(".composer__send")) {
    send.addEventListener("click", stopVoice, { capture: true });
  }
}
