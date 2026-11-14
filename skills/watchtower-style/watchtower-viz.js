/* Watchtower charts: GENERATED from docs/design/styleguide.html by
   tools/style_kit.py. Load after watchtower.css; see SKILL.md. */

/* =========================================================================
   VIZ START: watchtower-viz, small house-style charts in plain SVG.
   tools/style_kit.py copies this block into the watchtower-style skill.

   WTViz.line(figure, { title, sub, x: [...labels], series: [{ name, values }], area, unit })
   WTViz.columns(figure, { title, sub, x, series, stacked, highlight, unit })
   WTViz.ranked(figure, { title, sub, items: [{ label, value }], unit })
   WTViz.scatter(figure, { title, sub, xLabel, yLabel, series: [{ name, points: [[x, y]] }] })
   WTViz.heat(figure, { title, sub, rows: [...], cols: [...], values: [[...]] })
   WTViz.spark(svgElement, values)

   Every chart gets the card head, a legend when there are two or more
   series, a hover layer, and a "Show table" toggle.
   ========================================================================= */
const WTViz = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const W = 600, H = 260, M = { t: 12, r: 64, b: 28, l: 44 };
  const col = (i) => `var(--viz-${(i % 8) + 1})`;
  const fmt = (v, unit = "") => {
    const a = Math.abs(v);
    const s = a >= 1e6 ? (v / 1e6).toFixed(a >= 1e7 ? 0 : 1) + "M" : a >= 1e4 ? (v / 1e3).toFixed(0) + "K" : v.toLocaleString();
    return unit === "$" ? "$" + s : s + unit;
  };
  function ticks(max, n = 4) {
    const raw = max / n, p = 10 ** Math.floor(Math.log10(raw || 1));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) || p * 10;
    const out = [];
    for (let v = 0; v <= max + step * 0.001; v += step) out.push(v);
    if (out[out.length - 1] < max) out.push(out[out.length - 1] + step);
    return out;
  }
  function el(tag, attrs = {}, parent) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (parent) parent.appendChild(node);
    return node;
  }
  function text(parent, x, y, str, attrs = {}) {
    const t = el("text", { x, y, ...attrs }, parent);
    t.textContent = str;
    return t;
  }
  // The card around every chart: head, legend, plot, tooltip, table toggle.
  function frame(fig, o, legend, legendKind = "box") {
    fig.textContent = "";
    fig.classList.add("viz");
    const head = document.createElement("figcaption");
    head.className = "viz__head";
    const words = document.createElement("div");
    const t = document.createElement("div"); t.className = "viz__title"; t.textContent = o.title || "";
    const s = document.createElement("div"); s.className = "viz__sub"; s.textContent = o.sub || "";
    words.append(t, s); head.append(words); fig.append(head);
    if (legend && legend.length > 1) {
      const lg = document.createElement("div"); lg.className = "legend";
      legend.forEach((name, i) => {
        const span = document.createElement("span"); const sw = document.createElement("i");
        if (legendKind === "line") sw.className = "line";
        sw.style.setProperty("--c", col(i)); span.append(sw, document.createTextNode(name)); lg.append(span);
      });
      fig.append(lg);
    }
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": o.title || "Chart" });
    fig.append(svg);
    const tip = document.createElement("div"); tip.className = "viz__tip"; fig.append(tip);
    const foot = document.createElement("div"); foot.className = "viz__foot";
    const note = document.createElement("span"); note.textContent = o.note || "";
    const btn = document.createElement("button"); btn.type = "button"; btn.textContent = "Show table";
    foot.append(note, btn); fig.append(foot);
    return { svg, tip, btn };
  }
  function tableToggle(fig, btn, header, rows) {
    let table = null;
    btn.addEventListener("click", () => {
      if (table) { table.remove(); table = null; btn.textContent = "Show table"; return; }
      table = document.createElement("table");
      const tr = table.insertRow(); header.forEach((h) => { const th = document.createElement("th"); th.textContent = h; tr.append(th); });
      rows.forEach((r) => { const row = table.insertRow(); r.forEach((c) => { row.insertCell().textContent = c; }); });
      fig.append(table); btn.textContent = "Hide table";
    });
  }
  function showTip(fig, tip, x, y, head, rows) {
    tip.textContent = "";
    const h = document.createElement("div"); h.className = "h"; h.textContent = head; tip.append(h);
    rows.forEach(([name, value, c]) => {
      const r = document.createElement("div"); r.className = "r";
      const i = document.createElement("i"); i.style.setProperty("--c", c);
      const b = document.createElement("b"); b.textContent = value;
      const s = document.createElement("span"); s.textContent = name;
      r.append(i, b, s); tip.append(r);
    });
    const box = fig.getBoundingClientRect(), tw = tip.offsetWidth || 160;
    const left = Math.min(Math.max(8, x - box.left + 14), box.width - tw - 8);
    tip.style.left = left + "px"; tip.style.top = Math.max(8, y - box.top - 20) + "px";
    tip.classList.add("on");
  }
  function axes(svg, max, plotW, plotH, unit) {
    const g = el("g", {}, svg), tv = ticks(max);
    const top = tv[tv.length - 1];
    tv.forEach((v) => {
      const y = M.t + plotH - (v / top) * plotH;
      el("line", { class: v === 0 ? "axis-line" : "grid-line", x1: M.l, x2: M.l + plotW, y1: y, y2: y }, g);
      text(g, M.l - 8, y + 4, fmt(v, unit), { "text-anchor": "end", class: "tick" });
    });
    return top;
  }
  // A bar with a 4px rounded tip and a square base.
  function bar(x, y, w, h, r = 4) {
    r = Math.min(r, h, w / 2);
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  }

  function line(fig, o) {
    const { svg, tip, btn } = frame(fig, o, o.series.map((s) => s.name), "line");
    const plotW = W - M.l - M.r, plotH = H - M.t - M.b;
    const top = axes(svg, Math.max(...o.series.flatMap((s) => s.values)), plotW, plotH, o.unit);
    const X = (i) => M.l + (i / (o.x.length - 1)) * plotW, Y = (v) => M.t + plotH - (v / top) * plotH;
    const every = Math.ceil(o.x.length / 6);
    const lastX = o.x.length - 1;
    o.x.forEach((lab, i) => { if ((i % every === 0 && lastX - i >= every) || i === lastX) text(svg, X(i), H - 6, lab, { "text-anchor": "middle" }); });
    o.series.forEach((s, si) => {
      const d = s.values.map((v, i) => `${i ? "L" : "M"}${X(i)},${Y(v)}`).join("");
      if (o.area) el("path", { d: `${d}L${X(s.values.length - 1)},${Y(0)}L${X(0)},${Y(0)}Z`, style: `fill:${col(si)};opacity:.12` }, svg);
      el("path", { d, fill: "none", style: `stroke:${col(si)}`, "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round" }, svg);
      const last = s.values.length - 1;
      el("circle", { cx: X(last), cy: Y(s.values[last]), r: 4.5, style: `fill:${col(si)};stroke:var(--wt-surface)`, "stroke-width": 2 }, svg);
      if (o.series.length <= 4) text(svg, X(last) + 10, Y(s.values[last]) + 4, fmt(s.values[last], o.unit), { class: "end-label" });
    });
    // The crosshair and its dots are hidden, not parked off the plot: the
    // svg does not clip, so anything parked would still be drawn.
    const cross = el("line", { class: "cross", y1: M.t, y2: M.t + plotH, x1: 0, x2: 0, visibility: "hidden" }, svg);
    const dots = o.series.map((s, si) => el("circle", { r: 4.5, cx: 0, cy: 0, visibility: "hidden", style: `fill:${col(si)};stroke:var(--wt-surface)`, "stroke-width": 2 }, svg));
    const hit = el("rect", { x: M.l, y: M.t, width: plotW, height: plotH, fill: "transparent", tabindex: 0 }, svg);
    const at = (i, ev) => {
      cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("visibility", "visible");
      dots.forEach((d, si) => { d.setAttribute("cx", X(i)); d.setAttribute("cy", Y(o.series[si].values[i])); d.setAttribute("visibility", "visible"); });
      const r = svg.getBoundingClientRect();
      showTip(fig, tip, ev ? ev.clientX : r.left + (X(i) / W) * r.width, ev ? ev.clientY : r.top + 40, o.x[i],
        o.series.map((s, si) => [s.name, fmt(s.values[i], o.unit), col(si)]).sort((a, b) => parseFloat(b[1]) - parseFloat(a[1])));
    };
    let cur = o.x.length - 1;
    hit.addEventListener("pointermove", (ev) => {
      const r = svg.getBoundingClientRect(), px = ((ev.clientX - r.left) / r.width) * W;
      cur = Math.round(((px - M.l) / plotW) * (o.x.length - 1)); cur = Math.max(0, Math.min(o.x.length - 1, cur)); at(cur, ev);
    });
    hit.addEventListener("keydown", (ev) => { if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") { cur = Math.max(0, Math.min(o.x.length - 1, cur + (ev.key === "ArrowLeft" ? -1 : 1))); at(cur); ev.preventDefault(); } });
    const off = () => { tip.classList.remove("on"); cross.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); };
    hit.addEventListener("pointerleave", off); hit.addEventListener("blur", off);
    tableToggle(fig, btn, ["", ...o.series.map((s) => s.name)], o.x.map((x, i) => [x, ...o.series.map((s) => fmt(s.values[i], o.unit))]));
  }

  function columns(fig, o) {
    const { svg, tip, btn } = frame(fig, o, o.series.map((s) => s.name));
    const plotW = W - M.l - 16, plotH = H - M.t - M.b;
    const totals = o.x.map((_, i) => o.stacked ? o.series.reduce((a, s) => a + s.values[i], 0) : Math.max(...o.series.map((s) => s.values[i])));
    const top = axes(svg, Math.max(...totals), plotW + 16 - 16, plotH, o.unit);
    const band = plotW / o.x.length, groups = o.stacked ? 1 : o.series.length;
    const bw = Math.min(24, (band * 0.7) / groups), Y = (v) => (v / top) * plotH;
    o.x.forEach((lab, i) => {
      const cx = M.l + band * i + band / 2;
      text(svg, cx, H - 6, lab, { "text-anchor": "middle" });
      let base = M.t + plotH;
      o.series.forEach((s, si) => {
        const v = s.values[i], h = Y(v);
        let x = cx - (bw * groups + 2 * (groups - 1)) / 2 + (o.stacked ? 0 : si * (bw + 2));
        let fill = col(si);
        if (o.highlight !== undefined && o.series.length === 1) fill = i === o.highlight ? col(0) : "var(--viz-muted)";
        const top_ = base - h;
        const isTop = !o.stacked || si === o.series.length - 1;
        const p = el("path", { class: "mark", d: isTop ? bar(x, top_, bw, h) : `M${x},${base}V${top_ + 1}H${x + bw}V${base}Z`, style: `fill:${fill}`, tabindex: 0 }, svg);
        if (o.stacked) base = top_ - 2;
        const show = (ev) => { const r = p.getBoundingClientRect(); showTip(fig, tip, ev?.clientX ?? r.right, ev?.clientY ?? r.top, lab,
          o.stacked ? o.series.map((ss, k) => [ss.name, fmt(ss.values[i], o.unit), col(k)]).reverse() : [[s.name, fmt(v, o.unit), fill]]); };
        p.addEventListener("pointermove", show); p.addEventListener("focus", () => show());
        p.addEventListener("pointerleave", () => tip.classList.remove("on")); p.addEventListener("blur", () => tip.classList.remove("on"));
      });
      if (o.highlight === i) text(svg, cx, M.t + plotH - Y(totals[i]) - 8, fmt(totals[i], o.unit), { "text-anchor": "middle", class: "end-label" });
    });
    tableToggle(fig, btn, ["", ...o.series.map((s) => s.name)], o.x.map((x, i) => [x, ...o.series.map((s) => fmt(s.values[i], o.unit))]));
  }

  function ranked(fig, o) {
    const { svg, tip, btn } = frame(fig, o);
    const items = [...o.items].sort((a, b) => b.value - a.value);
    const rowH = 32, h = items.length * rowH + 8, labelW = 130, plotW = W - labelW - 70;
    svg.setAttribute("viewBox", `0 0 ${W} ${h}`);
    const max = items[0].value;
    items.forEach((it, i) => {
      const y = 4 + i * rowH, w = Math.max(2, (it.value / max) * plotW);
      text(svg, 0, y + 17, it.label, {});
      const r = Math.min(4, w / 2);
      const p = el("path", { class: "mark", tabindex: 0, style: `fill:${i === 0 ? col(0) : "var(--viz-muted)"}`,
        d: `M${labelW},${y + 2}H${labelW + w - r}Q${labelW + w},${y + 2} ${labelW + w},${y + 2 + r}V${y + 22 - r}Q${labelW + w},${y + 22} ${labelW + w - r},${y + 22}H${labelW}Z` }, svg);
      text(svg, labelW + w + 8, y + 17, fmt(it.value, o.unit), { class: i === 0 ? "end-label tick" : "tick" });
      const show = (ev) => { const b = p.getBoundingClientRect(); showTip(fig, tip, ev?.clientX ?? b.right, ev?.clientY ?? b.top, it.label, [[o.sub || "", fmt(it.value, o.unit), i === 0 ? col(0) : "var(--viz-muted)"]]); };
      p.addEventListener("pointermove", show); p.addEventListener("focus", () => show());
      p.addEventListener("pointerleave", () => tip.classList.remove("on")); p.addEventListener("blur", () => tip.classList.remove("on"));
    });
    tableToggle(fig, btn, ["", o.sub || "Value"], items.map((it) => [it.label, fmt(it.value, o.unit)]));
  }

  function scatter(fig, o) {
    const { svg, tip, btn } = frame(fig, o, o.series.map((s) => s.name));
    const plotW = W - M.l - 16, plotH = H - M.t - M.b - 10;
    const all = o.series.flatMap((s) => s.points);
    const top = axes(svg, Math.max(...all.map((p) => p[1])), plotW, plotH);
    const xt = ticks(Math.max(...all.map((p) => p[0])), 5), xmax = xt[xt.length - 1];
    xt.forEach((v) => text(svg, M.l + (v / xmax) * plotW, H - 16, fmt(v), { "text-anchor": "middle", class: "tick" }));
    if (o.xLabel) text(svg, M.l + plotW, H - 1, o.xLabel, { "text-anchor": "end" });
    o.series.slice(0, 3).forEach((s, si) => s.points.forEach(([x, y]) => {
      const cx = M.l + (x / xmax) * plotW, cy = M.t + plotH - (y / top) * plotH;
      el("circle", { cx, cy, r: 5, style: `fill:${col(si)};stroke:var(--wt-surface)`, "stroke-width": 2 }, svg);
      const hit = el("circle", { cx, cy, r: 12, fill: "transparent", tabindex: 0, class: "mark" }, svg);
      const show = (ev) => { const b = hit.getBoundingClientRect(); showTip(fig, tip, ev?.clientX ?? b.right, ev?.clientY ?? b.top, s.name,
        [[o.xLabel || "x", fmt(x), col(si)], [o.yLabel || "y", fmt(y), col(si)]]); };
      hit.addEventListener("pointermove", show); hit.addEventListener("focus", () => show());
      hit.addEventListener("pointerleave", () => tip.classList.remove("on")); hit.addEventListener("blur", () => tip.classList.remove("on"));
    }));
    tableToggle(fig, btn, ["Series", o.xLabel || "x", o.yLabel || "y"], o.series.flatMap((s) => s.points.map((p) => [s.name, fmt(p[0]), fmt(p[1])])));
  }

  function heat(fig, o) {
    const { svg, tip, btn } = frame(fig, o);
    const lw = 40, cw = (W - lw) / o.cols.length, ch = 26, h = o.rows.length * ch + 22;
    svg.setAttribute("viewBox", `0 0 ${W} ${h}`);
    const flat = o.values.flat(), max = Math.max(...flat);
    o.rows.forEach((r, ri) => {
      text(svg, 0, ri * ch + 17, r, {});
      o.cols.forEach((c, ci) => {
        const v = o.values[ri][ci], step = v === 0 ? 0 : Math.min(5, Math.ceil((v / max) * 5));
        const fill = step === 0 ? "var(--wt-surface-2)" : `var(--seq-${step + 1})`;
        const rect = el("rect", { class: "mark", x: lw + ci * cw + 1, y: ri * ch + 1, width: cw - 2, height: ch - 2, rx: 4, style: `fill:${fill}`, tabindex: 0 }, svg);
        const show = (ev) => { const b = rect.getBoundingClientRect(); showTip(fig, tip, ev?.clientX ?? b.right, ev?.clientY ?? b.top, `${r} ${c}`, [[o.unitName || "", fmt(v), fill]]); };
        rect.addEventListener("pointermove", show); rect.addEventListener("focus", () => show());
        rect.addEventListener("pointerleave", () => tip.classList.remove("on")); rect.addEventListener("blur", () => tip.classList.remove("on"));
      });
    });
    o.cols.forEach((c, ci) => { if (ci % 3 === 0) text(svg, lw + ci * cw + cw / 2, h - 4, c, { "text-anchor": "middle" }); });
    tableToggle(fig, btn, ["", ...o.cols], o.rows.map((r, ri) => [r, ...o.values[ri].map((v) => fmt(v))]));
  }

  function spark(svg, values) {
    svg.setAttribute("viewBox", "0 0 120 32"); svg.setAttribute("preserveAspectRatio", "none");
    const max = Math.max(...values), min = Math.min(...values), X = (i) => (i / (values.length - 1)) * 120, Y = (v) => 28 - ((v - min) / (max - min || 1)) * 24;
    el("path", { d: values.map((v, i) => `${i ? "L" : "M"}${X(i)},${Y(v)}`).join(""), fill: "none", stroke: "var(--viz-muted)", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke" }, svg);
    const l = values.length - 1;
    // A zero-length round-capped stroke stays a circle when the box stretches.
    el("path", { d: `M${X(l)},${Y(values[l])}h0`, stroke: "var(--wt-surface)", "stroke-width": 12, "stroke-linecap": "round", "vector-effect": "non-scaling-stroke" }, svg);
    el("path", { d: `M${X(l)},${Y(values[l])}h0`, stroke: "var(--wt-act)", "stroke-width": 8, "stroke-linecap": "round", "vector-effect": "non-scaling-stroke" }, svg);
  }

  return { line, columns, ranked, scatter, heat, spark, color: col, format: fmt };
})();
/* VIZ END */
