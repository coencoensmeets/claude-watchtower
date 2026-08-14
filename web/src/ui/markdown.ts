import { escapeHtml } from "./format.js";

/* ------------------------------------------------------------------ markdown */
/* A transcript is Markdown, and reading it raw — literal ###, **, and fences —
   is hard work, so the bubbles render it. This is a small line-based renderer:
   headings, lists, fenced and inline code, quotes, tables, rules, emphasis and
   links, which is what a Claude answer actually uses.

   Safety: nothing from a message ever reaches the page as markup. Code is held
   aside before anything else runs, every other scrap is escaped before a tag is
   added, and a link is only made when its target is http, https or mailto. */
const MD_HOLD = "\u0000";

function safeUrl(url) {
  const value = String(url).trim();
  return /^(https?:\/\/|mailto:)[^\s"'<>]+$/i.test(value) ? value : null;
}

export function renderMarkdown(text) {
  const held = [];
  const keep = (html) => `${MD_HOLD}${held.push(html) - 1}${MD_HOLD}`;
  const source = String(text ?? "").replace(/\r\n?/g, "\n");

  // Fenced code first: nothing inside a fence is Markdown. A fence inside a list
  // is indented, so the indent comes off the code and stays on the line — that is
  // what keeps the block inside its list item.
  // The info string is kept on the element rather than thrown away: quoting a
  // passage out of a code block puts the fence back, and it needs the language.
  const body = source.replace(/^([ \t]*)```([^\n]*)\n?([\s\S]*?)(?:^[ \t]*```[ \t]*$|$(?![\s\S]))/gm, (all, pad, info, code) => {
    const flat = pad ? code.replace(new RegExp(`^${pad}`, "gm"), "") : code;
    const lang = String(info || "").trim().split(/\s+/)[0].replace(/[^\w.+-]/g, "").slice(0, 24);
    return pad + keep(`<pre class="md-code"${lang ? ` data-lang="${escapeHtml(lang)}"` : ""}><code class="md-mono">${escapeHtml(flat.replace(/\n$/, ""))}</code></pre>`);
  });

  const inline = (raw) => {
    // Inline code goes the same way, so emphasis cannot reach inside it.
    let s = String(raw).replace(/`([^`\n]+)`/g, (all, code) => keep(`<code class="md-mono">${escapeHtml(code)}</code>`));
    s = escapeHtml(s);
    // An image becomes a link to it: the panel never loads a remote file.
    s = s.replace(/!\[([^\]\n]*)\]\(([^)\s]+)\)/g, (all, alt, url) => `[${alt || "image"}](${url})`);
    s = s.replace(/\[([^\]\n]*)\]\(([^)\s]+)\)/g, (all, label, url) => {
      const href = safeUrl(url.replace(/&amp;/g, "&"));
      return href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${label || escapeHtml(href)}</a>` : all;
    });
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, (all, before, url) => {
      // A sentence's full stop is not part of its link.
      const trail = url.match(/[.,;:!?]+$/)?.[0] || "";
      const bare = url.slice(0, url.length - trail.length);
      const href = safeUrl(bare.replace(/&amp;/g, "&"));
      return href ? `${before}<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${bare}</a>${trail}` : all;
    });
    s = s.replace(/\*\*\*(\S(?:[^*]*\S)?)\*\*\*/g, "<strong><em>$1</em></strong>");
    s = s.replace(/\*\*(\S(?:[^*]*\S)?)\*\*/g, "<strong>$1</strong>");
    // The marks have to hug the words, or `a * b * c` turns into italics.
    s = s.replace(/(^|[^*\w])\*(\S(?:[^*\n]*\S)?)\*/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^_\w])_(\S(?:[^_\n]*\S)?)_(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    return s.replace(/\n/g, "<br>");
  };

  const out = [];
  const stack = [];   // open lists, outermost first
  let para = [];      // lines of the paragraph being read
  let item = [];      // lines of the list item being read
  let quote = [];     // lines of the block quote being read
  let blank = false;  // was the line before this one empty?

  const flushItem = () => { if (item.length) { out.push(inline(item.join("\n"))); item = []; } };
  const flushPara = () => {
    if (stack.length) return flushItem();
    if (para.length) { out.push(`<p>${inline(para.join("\n"))}</p>`); para = []; }
  };
  const flushQuote = () => {
    if (!quote.length) return;
    out.push(`<blockquote>${inline(quote.join("\n"))}</blockquote>`);
    quote = [];
  };
  const closeLists = (indent) => {
    flushItem();
    while (stack.length && stack[stack.length - 1].indent >= indent) {
      const list = stack.pop();
      out.push(`</li></${list.tag}>`);
    }
  };
  const closeAll = () => { flushQuote(); flushPara(); closeLists(0); };
  const add = (line) => (stack.length ? item : para).push(line);

  const lines = body.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const bare = line.trim();

    if (!bare) { flushQuote(); flushItem(); if (!stack.length) flushPara(); blank = true; continue; }

    // A blank line then something flush with the margin is the end of the list;
    // indent the same thing instead and it stays inside the item. The next
    // bullet, of course, carries the list on.
    const startsItem = /^\s*([-*+]|\d+[.)])\s+/.test(line);
    if (blank && stack.length && !startsItem && !/^\s{2,}/.test(line)) closeLists(0);
    blank = false;

    if (bare.startsWith(">")) { flushPara(); flushItem(); quote.push(bare.replace(/^>\s?/, "")); continue; }
    flushQuote();

    // A held code block stands on its own line; inside a list it belongs to the item.
    const only = bare.match(new RegExp(`^${MD_HOLD}(\\d+)${MD_HOLD}$`));
    if (only) { flushItem(); if (!stack.length) flushPara(); out.push(held[Number(only[1])]); continue; }

    const heading = bare.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      closeAll();
      const level = Math.min(heading[1].length + 2, 6);
      const size = heading[1].length <= 2 ? "md-title-medium" : "md-title-small";
      out.push(`<h${level} class="md-head ${size}">${inline(heading[2])}</h${level}>`);
      continue;
    }

    if (/^([-*_])\1{2,}$/.test(bare)) { closeAll(); out.push("<hr>"); continue; }

    // A table needs its header underline on the next line to count as one.
    if (bare.startsWith("|") && /^\|?[\s:|-]+\|[\s:|-]*$/.test((lines[i + 1] || "").trim()) && (lines[i + 1] || "").includes("-")) {
      closeAll();
      const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = cells(bare);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(cells(lines[i++]));
      i--;
      out.push(`<table class="md-table"><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead>`
        + `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }

    const bullet = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
    if (bullet) {
      const indent = bullet[1].length;
      const tag = /\d/.test(bullet[2]) ? "ol" : "ul";
      flushPara();
      flushItem();
      while (stack.length && stack[stack.length - 1].indent > indent) {
        const list = stack.pop();
        out.push(`</li></${list.tag}>`);
      }
      const top = stack[stack.length - 1];
      if (top && top.indent === indent && top.tag === tag) {
        out.push("</li><li>");
      } else {
        if (top && top.indent === indent) { stack.pop(); out.push(`</li></${top.tag}>`); }
        stack.push({ tag, indent });
        out.push(`<${tag}><li>`);
      }
      // A task list reads better as a box than as literal brackets.
      item = [bullet[3].replace(/^\[([ xX])\]\s+/, (all, mark) => (mark === " " ? "☐ " : "☑ "))];
      continue;
    }

    // Not a list line at all: an unindented one ends the list.
    if (stack.length && !/^\s{2,}/.test(line)) { closeLists(0); flushItem(); }
    add(bare);
  }
  closeAll();

  return out.join("").replace(new RegExp(`${MD_HOLD}(\\d+)${MD_HOLD}`, "g"), (all, n) => held[Number(n)]);
}

