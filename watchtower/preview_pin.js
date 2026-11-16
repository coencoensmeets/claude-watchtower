/* Watchtower: commenting on a preview, the way the conversation does it.
   Injected by the panel into the HTML it serves under /preview/. It works in
   two places, and does nothing anywhere else:

   In the panel's frame, it tells the panel what passage is selected and
   where, so the panel can raise its Comment chip over it.

   In a tab opened from the panel's Open button, the panel is not around the
   page, so this draws the chip, a small editor and a Send itself. A comment
   written there is handed back to the panel tab that opened it, where it is a
   draft like any other, and Send asks that panel to send the session's drafts.

   Right-clicking a selection, in either place, offers Comment and Copy.

   Commented passages keep a butter highlight, drawn with the CSS Highlight API
   so the page's own DOM is never touched. The page runs sandboxed with an
   opaque origin, so everything goes by postMessage, and the panel only
   believes messages from a frame or a tab it made itself. */
(function () {
  if (window.__wtPin) return;
  var inFrame = window.parent !== window;
  var peer = inFrame ? window.parent : window.opener;
  if (!peer) return;
  window.__wtPin = true;

  var held = {};               // comment id -> Range, for the highlight
  var current = null;          // the selection the chip is offered for
  var offer = null;            // what the selection says, for the tab's own chip
  var frame = 0;
  var seq = 0;

  function say(message) {
    try { peer.postMessage(message, "*"); } catch (e) { /* the panel is gone */ }
  }

  var paint = window.CSS && CSS.highlights && window.Highlight;
  if (paint) {
    var style = document.createElement("style");
    style.setAttribute("data-wt-pin", "");
    style.textContent = "::highlight(wt-comment){background-color:rgba(245,205,98,.5);color:inherit}";
    (document.head || document.documentElement).appendChild(style);
  }
  function repaint() {
    if (!paint) return;
    var ranges = [];
    for (var id in held) ranges.push(held[id]);
    if (ranges.length) CSS.highlights.set("wt-comment", new Highlight(...ranges));
    else CSS.highlights.delete("wt-comment");
  }

  function label(el) {
    var name = el.tagName.toLowerCase();
    if (el.id) return name + "#" + el.id;
    var classes = (typeof el.className === "string" ? el.className : "")
      .trim().split(/\s+/).filter(Boolean).slice(0, 2);
    return name + (classes.length ? "." + classes.join(".") : "");
  }

  /* Where the passage is, as a few steps of ancestry stopping at an id: enough
     for the session to find it in the source it wrote. */
  function selector(node) {
    var el = node && node.nodeType === 1 ? node : node && node.parentElement;
    var parts = [];
    for (var at = el; at && at.nodeType === 1 && parts.length < 4; at = at.parentElement) {
      if (at === document.body || at === document.documentElement) break;
      parts.unshift(label(at));
      if (at.id) break;
    }
    return parts.join(" > ");
  }

  function inUi(node) {
    return ui && node && (node === ui.host || ui.host.contains(node));
  }

  function check() {
    frame = 0;
    // While the tab's editor is open the passage it is about is held; typing
    // in the editor moves the page's selection, and that is not a new one.
    if (editing) return;
    var sel = window.getSelection ? window.getSelection() : null;
    var text = sel && sel.rangeCount && !sel.isCollapsed ? String(sel).trim() : "";
    if (!text || (sel.anchorNode && inUi(sel.anchorNode))) {
      if (current) { current = null; offer = null; inFrame ? say({ wt: "sel-clear" }) : hideChip(); }
      return;
    }
    var range = sel.getRangeAt(0);
    var r = range.getBoundingClientRect();
    current = range.cloneRange();
    offer = {
      text: text.slice(0, 4000),
      where: selector(range.commonAncestorContainer),
      page: location.pathname.split("/").slice(3).join("/") + location.hash,
    };
    var rect = { left: r.left, top: r.top, width: r.width, height: r.height };
    if (inFrame) say({ wt: "sel", text: offer.text, where: offer.where, page: offer.page, rect: rect });
    else if (!editing) showChip(rect);
  }
  function soon() { if (!frame) frame = requestAnimationFrame(check); }

  /* Offered when a selection has settled — the end of a drag, a double-click,
     a shift-arrow — not on every change while it is being made. */
  document.addEventListener("mouseup", function (event) {
    // The left button only: a right-click is the menu's, not the chip's.
    if (event.button !== 0 || inUi(event.target)) return;
    setTimeout(soon, 0);
  }, true);
  document.addEventListener("keyup", function (event) {
    if (event.shiftKey || event.key === "Shift") soon();
  }, true);
  document.addEventListener("selectionchange", function () {
    var sel = window.getSelection();
    if (current && (!sel || sel.isCollapsed)) soon();
  });
  // The chip follows the passage while the page scrolls under it.
  window.addEventListener("scroll", function () { if (current) soon(); }, true);
  window.addEventListener("resize", function () { if (current) soon(); });

  /* Right-click on a selection: Comment and Copy. Anywhere else the browser's
     own menu is left alone — it has Back, Reload and Inspect in it. */
  document.addEventListener("contextmenu", function (event) {
    if (inUi(event.target)) return;
    var sel = window.getSelection ? window.getSelection() : null;
    var text = sel && sel.rangeCount && !sel.isCollapsed ? String(sel).trim() : "";
    if (!text) return;
    event.preventDefault();
    if (frame) { cancelAnimationFrame(frame); frame = 0; }
    var range = sel.getRangeAt(0);
    current = range.cloneRange();
    offer = {
      text: text.slice(0, 4000),
      where: selector(range.commonAncestorContainer),
      page: location.pathname.split("/").slice(3).join("/") + location.hash,
    };
    if (inFrame) say({ wt: "menu", x: event.clientX, y: event.clientY, text: offer.text, where: offer.where, page: offer.page });
    else openMenu(event.clientX, event.clientY);
  }, true);

  function keep(id) {
    if (!current) return;
    held[String(id)] = current;
    current = null;
    offer = null;
    try { window.getSelection().removeAllRanges(); } catch (e) { /* none */ }
    repaint();
    // Messages to the panel arrive in order, so this lands after any report of
    // the selection that was already on its way, and puts away a chip it raised.
    if (inFrame) say({ wt: "sel-clear" });
  }

  window.addEventListener("message", function (event) {
    if (event.source !== peer) return;
    var m = event.data || {};
    if (m.wt === "pin-hello") say({ wt: "pin-ready" });
    else if (m.wt === "sel-take") keep(m.id);
    else if (m.wt === "pin-drop") { delete held[String(m.id)]; repaint(); }
    else if (m.wt === "note-ok") { waiting = null; clearTimeout(lost); }
    else if (m.wt === "note-count") badge(Number(m.n) || 0);
    else if (m.wt === "sent") sent(!!m.ok, String(m.message || ""));
  });

  if (inFrame) { say({ wt: "pin-ready" }); return; }

  /* ------------------------------------------------------- the tab's own UI
     Drawn in a shadow root, so the page's styles cannot reach it and its
     styles cannot reach the page. The Watchtower look in miniature: an ink
     pill with a butter action, and a butter draft card. */
  var ui = null;
  var editing = false;
  var waiting = null;
  var lost = 0;

  function build() {
    var host = document.createElement("div");
    host.setAttribute("data-wt-pin", "");
    host.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647";
    var root = host.attachShadow({ mode: "closed" });
    root.innerHTML = '<style>'
      + ':host{all:initial}*{box-sizing:border-box;font-family:Urbanist,system-ui,sans-serif}'
      + '.chip{position:fixed;display:none;pointer-events:auto;border:0;border-radius:999px;padding:4px;'
      + 'background:#1E1F24;box-shadow:0 10px 24px -10px rgba(0,0,0,.45)}'
      + '.chip button{border:0;border-radius:999px;height:32px;padding:0 14px;background:#F5CD62;color:#1E1F24;'
      + 'font-weight:650;font-size:14px;cursor:pointer}'
      + '.card{position:fixed;display:none;pointer-events:auto;width:min(360px,calc(100vw - 24px));padding:14px 16px 16px;'
      + 'border-radius:20px 20px 20px 8px;background:#FBF1D6;color:#1E1F24;box-shadow:0 18px 40px -16px rgba(0,0,0,.45)}'
      + '.head{display:flex;gap:8px;align-items:center;font-weight:700;font-size:14px;margin-bottom:8px}'
      + '.tag{font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;background:#F5CD62}'
      + '.quote{margin:0 0 10px;padding-left:10px;border-left:3px solid #F5CD62;font-style:italic;font-size:13px;'
      + 'opacity:.8;max-height:3.6em;overflow:hidden}'
      + 'textarea{width:100%;min-height:72px;resize:vertical;border:0;border-radius:16px;padding:12px 14px;'
      + 'font-size:14px;background:#fff;color:#1E1F24;outline:none}'
      + 'textarea:focus{box-shadow:0 0 0 3px #B9A7F5}'
      + '.row{display:flex;gap:8px;justify-content:flex-end;align-items:center;margin-top:10px}'
      + '.hint{margin-right:auto;font-size:11px;opacity:.7}'
      + '.row button{border:0;border-radius:999px;height:36px;padding:0 16px;font-weight:650;font-size:14px;cursor:pointer}'
      + '.cancel{background:transparent;color:#1E1F24}.add{background:#1E1F24;color:#fff}'
      + '.badge{position:fixed;right:16px;bottom:16px;display:none;pointer-events:auto;align-items:center;gap:10px;'
      + 'padding:6px 16px 6px 6px;border-radius:999px;background:#1E1F24;color:#fff;font-size:13px;font-weight:600;'
      + 'box-shadow:0 10px 24px -10px rgba(0,0,0,.45)}'
      + '.badge b{min-width:28px;height:28px;border-radius:999px;display:grid;place-items:center;background:#F5CD62;color:#1E1F24}'
      + '.badge.gone{background:#F2B8B5;color:#1E1F24}'
      + '.badge .send{border:0;border-radius:999px;height:32px;padding:0 14px;margin-left:4px;background:#CBDA72;'
      + 'color:#1E1F24;font-weight:700;font-size:13px;cursor:pointer}'
      + '.badge .send:disabled{opacity:.6;cursor:default}'
      + '.menu{position:fixed;display:none;pointer-events:auto;min-width:180px;padding:6px;border-radius:20px;'
      + 'background:#fff;color:#1E1F24;box-shadow:0 18px 40px -16px rgba(0,0,0,.45)}'
      + '.menu button{display:flex;align-items:center;gap:10px;width:100%;height:40px;padding:0 16px;border:0;'
      + 'border-radius:999px;background:transparent;font-size:14px;font-weight:600;color:inherit;cursor:pointer;text-align:left}'
      + '.menu button:hover,.menu button:focus-visible{background:#F1EFEA;outline:none}'
      + '</style>'
      + '<div class="chip"><button type="button">Comment</button></div>'
      + '<div class="card" role="dialog" aria-label="Comment on this passage">'
      + '<div class="head">You <span class="tag">Draft</span></div><p class="quote"></p>'
      + '<textarea placeholder="What about it?" aria-label="Your comment"></textarea>'
      + '<div class="row"><span class="hint">Enter adds · Ctrl+Enter adds and sends</span>'
      + '<button type="button" class="cancel">Cancel</button><button type="button" class="add">Add</button></div></div>'
      + '<div class="badge" role="status"><b>0</b><span></span><button type="button" class="send">Send to chat</button></div>'
      + '<div class="menu" role="menu" aria-label="What to do with the selected passage">'
      + '<button type="button" role="menuitem" data-act="comment">Comment</button>'
      + '<button type="button" role="menuitem" data-act="copy">Copy</button></div>';
    (document.body || document.documentElement).appendChild(host);
    ui = {
      host: host,
      chip: root.querySelector(".chip"), card: root.querySelector(".card"),
      quote: root.querySelector(".quote"), field: root.querySelector("textarea"),
      badge: root.querySelector(".badge"), send: root.querySelector(".badge .send"),
      menu: root.querySelector(".menu"),
    };
    ui.send.addEventListener("click", function () {
      ui.send.disabled = true;
      ui.send.textContent = "Sending…";
      say({ wt: "notes-send" });
    });
    ui.menu.addEventListener("mousedown", function (event) { event.preventDefault(); });
    ui.menu.addEventListener("click", function (event) {
      var act = event.target.closest && event.target.closest("[data-act]");
      if (!act) return;
      closeMenu();
      if (act.getAttribute("data-act") === "comment") openCard();
      else copySelection();
    });
    ui.chip.addEventListener("mousedown", function (event) { event.preventDefault(); });
    ui.chip.querySelector("button").addEventListener("click", openCard);
    root.querySelector(".cancel").addEventListener("click", closeCard);
    root.querySelector(".add").addEventListener("click", addNote);
    ui.field.addEventListener("keydown", function (event) {
      event.stopPropagation();
      // Ctrl+Enter adds this comment and sends every draft, as it does in the
      // panel; Enter adds it and keeps going.
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); addNote(); sendNow(); }
      else if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); addNote(); }
      else if (event.key === "Escape") { event.preventDefault(); closeCard(); }
    });
  }

  function place(el, rect, below) {
    el.style.display = el === ui.badge ? "flex" : "block";
    var w = el.offsetWidth, h = el.offsetHeight, gap = 8;
    var x = Math.min(Math.max(gap, rect.left + rect.width / 2 - w / 2), innerWidth - w - gap);
    var y = below || rect.top - h - gap < gap ? rect.top + rect.height + gap : rect.top - h - gap;
    el.style.left = Math.round(x) + "px";
    el.style.top = Math.round(Math.min(y, innerHeight - h - gap)) + "px";
  }

  var chipRect = null;
  function showChip(rect) { chipRect = rect; place(ui.chip, rect); }
  function hideChip() { if (ui) ui.chip.style.display = "none"; }

  function openCard() {
    if (!offer || !current) return;
    editing = true;
    hideChip();
    ui.quote.textContent = offer.text.split("\n")[0].slice(0, 200);
    ui.field.value = "";
    place(ui.card, chipRect || { left: innerWidth / 2, top: 80, width: 0, height: 0 }, true);
    ui.field.focus();
  }

  function closeCard() {
    editing = false;
    ui.card.style.display = "none";
  }

  function addNote() {
    var remark = ui.field.value.trim();
    if (!remark || !offer) { if (!remark) closeCard(); return; }
    var id = "tab" + (++seq) + "-" + Date.now().toString(36);
    say({ wt: "note-add", id: id, text: offer.text, where: offer.where, page: offer.page, remark: remark.slice(0, 8000) });
    keep(id);
    offer = null;
    closeCard();
    // The panel answers at once when it is there. A tab whose panel has been
    // closed or reloaded has nobody to hand the comment to, and says so.
    waiting = id;
    clearTimeout(lost);
    lost = setTimeout(function () {
      if (waiting !== id) return;
      delete held[id];
      repaint();
      ui.badge.classList.add("gone");
      ui.send.style.display = "none";
      ui.badge.querySelector("b").textContent = "!";
      ui.badge.querySelector("span").textContent = "The panel that opened this page is gone — open it again from the panel";
      ui.badge.style.display = "flex";
    }, 1500);
  }

  function openMenu(x, y) {
    hideChip();
    var r = current ? current.getBoundingClientRect() : null;
    chipRect = r ? { left: r.left, top: r.top, width: r.width, height: r.height } : null;
    ui.menu.style.display = "block";
    var w = ui.menu.offsetWidth, h = ui.menu.offsetHeight, gap = 8;
    ui.menu.style.left = Math.max(gap, Math.min(x, innerWidth - w - gap)) + "px";
    ui.menu.style.top = Math.max(gap, Math.min(y, innerHeight - h - gap)) + "px";
    ui.menu.querySelector("button").focus();
  }
  function closeMenu() { if (ui) ui.menu.style.display = "none"; }

  /* The page's own copy, while the selection is still standing: a sandboxed
     page has no clipboard object to write to, but it can still copy what is
     selected in it. */
  function copySelection() {
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    if (!ok && navigator.clipboard && offer) navigator.clipboard.writeText(offer.text).catch(function () {});
  }

  function sent(ok, message) {
    if (!ui) return;
    ui.send.disabled = false;
    ui.send.textContent = "Send to chat";
    if (!ok) {
      ui.badge.classList.add("gone");
      ui.badge.querySelector("span").textContent = message || "That did not send";
      ui.badge.style.display = "flex";
      setTimeout(function () { ui.badge.classList.remove("gone"); }, 4000);
    }
  }

  var ready = 0;
  function badge(n) {
    ready = n;
    if (!ui) return;
    ui.badge.classList.remove("gone");
    ui.badge.querySelector("b").textContent = String(n);
    ui.badge.querySelector("span").textContent = n === 1 ? "comment ready" : "comments ready";
    ui.send.style.display = "";
    ui.badge.style.display = n ? "flex" : "none";
  }

  document.addEventListener("mousedown", function (event) {
    if (!inUi(event.target)) { hideChip(); closeMenu(); }
  }, true);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") { closeMenu(); hideChip(); }
    // Ctrl+Enter with comments waiting sends them — but not out of a field the
    // page itself has, where it may mean something of the page's own.
    else if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && ready > 0 && !inUi(event.target)
             && !(event.target.closest && event.target.closest("input, textarea, select, [contenteditable='true']"))) {
      event.preventDefault();
      sendNow();
    }
  }, true);

  function sendNow() {
    if (!ui) return;
    ui.send.disabled = true;
    ui.send.textContent = "Sending…";
    // After the note-add, if there was one: messages arrive in order, so the
    // panel has the comment before it is asked to send.
    say({ wt: "notes-send", now: true });
  }

  if (document.body) build();
  else document.addEventListener("DOMContentLoaded", build);
  say({ wt: "tab-hello" });
})();
