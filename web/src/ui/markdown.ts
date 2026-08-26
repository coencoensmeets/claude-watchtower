import { app } from "../state.js";
import { escapeHtml } from "./format.js";

/* ------------------------------------------------------------------- paths
   A path written into the conversation is a place on this machine, and the
   thing you want from it is almost always to go and look. So the ones the
   panel is confident about become targets: click one and it opens in the
   editor, the same way the header's button opens the session's folder.

   Confident is the operative word — this runs over every line of every
   message, and a false positive turns ordinary prose into a live control. So
   a match must look like nothing else:

     rooted    /tmp/x, ~/.motorcortex/build/…, ./web/src, ../other
     relative  only with a file extension on the end — web/src/main.ts

   which leaves "and/or", "I/O", "km/h", "24/7" and "1/2" alone, all of which
   appear in conversation far more often than a bare relative directory does.
   A trailing :12 or :12:5 comes along as the line to open at, because that is
   how every tool on this machine writes a place in a file.

   Matched against already-escaped text, so a URL cannot be mistaken for a
   path: `https://x/a/b.md` has a colon before the slashes, and a colon is not
   one of the characters a match may begin after. */
/* A segment is made of word characters and the punctuation a real path uses —
   dot, dash, at, plus, tilde — and none of the comma, semicolon or bracket a
   sentence would put against one. */
const PATH_RE =
  /(^|[\s(\[{])((?:~|\.{1,2})?\/[A-Za-z0-9._@+~-][A-Za-z0-9._@+~/-]*|[A-Za-z0-9._@+~-]+(?:\/[A-Za-z0-9._@+~-]+)*\/[A-Za-z0-9._@+~-]*\.[A-Za-z][A-Za-z0-9]{0,7})(:\d+(?::\d+)?)?/g;
/* Prose punctuation that has attached itself to the end of a path. The full
   stop is the awkward one — it is also how an extension starts — but a path
   never ends in one, so whatever trails comes off and stays outside the link. */
const TRAILING = /[.,;:!?)\]}]+$/;

/* A bare file name — no slash at all — is a path too, and `main.ts` is how a
   file usually gets mentioned. It is only allowed inside code marks, where
   writing it was a deliberate act, and only with a suffix off this list: in a
   code span `Array.from`, `app.settings` and `obj.method` all look exactly like
   a file with a four-letter extension, and the list is what tells them apart.
   Prose keeps the stricter rule — it must have a slash in it — because half the
   full stops in a sentence would otherwise be reaching for the disk. */
const SUFFIX = new Set([
  "ts", "tsx", "js", "jsx", "mjs", "cjs", "py", "rb", "go", "rs", "c", "h", "cc",
  "cpp", "hpp", "java", "kt", "swift", "cs", "php", "lua", "sh", "bash", "zsh",
  "sql", "md", "rst", "txt", "log", "json", "yaml", "yml", "toml", "ini", "cfg",
  "conf", "env", "xml", "html", "css", "scss", "svg", "png", "jpg", "jpeg",
  "gif", "webp", "pdf", "csv", "grid", "link", "projpack", "service",
]);
const BARE_RE = /(^|[\s(\[{])([A-Za-z0-9._@+~-]*[A-Za-z0-9_~-]\.([A-Za-z][A-Za-z0-9]{0,7}))(:\d+(?::\d+)?)?/g;

/** Turn every path in a run of escaped text into something that can be opened.

    `bare` allows a file name with no slash in it, which only code marks get. */
export function linkPaths(escaped, bare = false) {
  // Off with the editor button: someone who does not open their sessions in an
  // editor is not helped by half of every message becoming a link to one.
  if (!app.settings.showEditor) return String(escaped);
  // Bare names first, and it has to be that way round: a name with no slash in
  // it cannot appear inside a path that has one — the character before it would
  // be the slash, and a match may only begin after a space or an open bracket —
  // whereas running the slashed pattern first would leave `main.ts` sitting in
  // the attributes of its own link for the second pass to find again.
  const named = bare
    ? String(escaped).replace(BARE_RE, (all, before, name, suffix, where) =>
        (SUFFIX.has(suffix.toLowerCase()) ? mark(all, before, name, where) : all))
    : String(escaped);
  return named.replace(PATH_RE, mark);
}

function mark(all, before, path, where) {
  const trail = path.match(TRAILING);
  const text = trail ? path.slice(0, -trail[0].length) : path;
  // A bare "/", "./" or "..." is punctuation, not a place.
  if (!/[A-Za-z0-9]/.test(text)) return all;
  const line = where ? where.slice(1).split(":")[0] : "";
  const said = `${text}${where || ""}`;
  return `${before}<span class="path-link" role="link" tabindex="0"`
    + ` data-path="${text}"${line ? ` data-line="${line}"` : ""}`
    + ` title="Open ${said} in the editor">${said}</span>${trail ? trail[0] : ""}`;
}

/* Run `fn` over the text of a fragment of HTML and nothing else — not over the
   tags, and not over the text inside an <a>, which is already somewhere to go.
   The splitter keeps the tags as odd-numbered pieces, so what is left is
   exactly the text. */
function outsideLinks(html, fn) {
  let depth = 0;
  return html.split(/(<[^>]*>)/).map((piece, i) => {
    if (i % 2) {
      if (/^<a[\s>]/i.test(piece)) depth++;
      else if (/^<\/a>/i.test(piece)) depth = Math.max(0, depth - 1);
      return piece;
    }
    return depth ? piece : fn(piece);
  }).join("");
}

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
    // A path in an answer is usually written in code marks, so the span is
    // opened as readily as bare prose is — the marks say "this is a name on a
    // machine", which is exactly the case this is for.
    let s = String(raw).replace(/`([^`\n]+)`/g, (all, code) =>
      keep(`<code class="md-mono">${linkPaths(escapeHtml(code), true)}</code>`));
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
    // Last, and only between the tags everything above has added: a path may
    // not be found inside an href, and the text of a link is already a link.
    s = outsideLinks(s, linkPaths);
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
