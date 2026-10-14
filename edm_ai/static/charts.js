// Dependency-free SVG charts (CSP-safe: colours come from CSS classes, text from textContent).
const NS = "http://www.w3.org/2000/svg";
const s = (tag, attrs = {}, ...kids) => { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); for (const c of kids) if (c != null) n.append(c); return n; };
const txt = (x, y, str, cls, anchor = "middle") => { const t = s("text", { x, y, class: cls, "text-anchor": anchor }); t.textContent = str; return t; };
const nice = (m) => { if (m <= 5) return 5; const p = 10 ** Math.floor(Math.log10(m)); return Math.ceil(m / p) * p; };

export function barChart(items, { w = 420, h = 230, label = (i) => i.label, value = (i) => i.value, unit = "" } = {}) {
  const pad = { l: 34, r: 8, t: 12, b: 26 }, iw = w - pad.l - pad.r, ih = h - pad.t - pad.b, max = nice(Math.max(1, ...items.map(value)));
  const svg = s("svg", { viewBox: `0 0 ${w} ${h}`, class: "chart", role: "img", "aria-label": "막대 그래프" });
  for (let i = 0; i <= 4; i++) { const y = pad.t + ih - (ih * i) / 4; svg.append(s("line", { x1: pad.l, x2: w - pad.r, y1: y, y2: y, class: "ch-grid" }), txt(pad.l - 6, y + 4, String(Math.round((max * i) / 4)), "ch-ax", "end")); }
  const bw = iw / items.length;
  items.forEach((it, i) => {
    const v = value(it), bh = (ih * v) / max, x = pad.l + i * bw + bw * 0.18;
    const bar = s("rect", { x, y: pad.t + ih - bh, width: bw * 0.64, height: Math.max(bh, v ? 2 : 0), rx: 4, class: "ch-bar" });
    const tip = s("title"); tip.textContent = `${label(it)}: ${v}${unit}`; bar.append(tip); svg.append(bar);
    if (v) svg.append(txt(x + (bw * 0.64) / 2, pad.t + ih - bh - 4, String(v), "ch-val"));
    svg.append(txt(x + (bw * 0.64) / 2, h - 8, label(it), "ch-ax"));
  });
  return svg;
}

export function lineChart(items, { w = 420, h = 230, label = (i) => i.label, value = (i) => i.value } = {}) {
  const pad = { l: 34, r: 10, t: 12, b: 26 }, iw = w - pad.l - pad.r, ih = h - pad.t - pad.b, max = nice(Math.max(1, ...items.map(value)));
  const svg = s("svg", { viewBox: `0 0 ${w} ${h}`, class: "chart", role: "img", "aria-label": "추이 그래프" });
  for (let i = 0; i <= 4; i++) { const y = pad.t + ih - (ih * i) / 4; svg.append(s("line", { x1: pad.l, x2: w - pad.r, y1: y, y2: y, class: "ch-grid" }), txt(pad.l - 6, y + 4, String(Math.round((max * i) / 4)), "ch-ax", "end")); }
  const pts = items.map((it, i) => [pad.l + (items.length > 1 ? (iw * i) / (items.length - 1) : iw / 2), pad.t + ih - (ih * value(it)) / max]);
  if (pts.length > 1) {
    svg.append(s("polygon", { points: `${pad.l},${pad.t + ih} ${pts.map((p) => p.join(",")).join(" ")} ${pts.at(-1)[0]},${pad.t + ih}`, class: "ch-area" }), s("polyline", { points: pts.map((p) => p.join(",")).join(" "), class: "ch-line", fill: "none" }));
  }
  pts.forEach(([x, y], i) => { const c = s("circle", { cx: x, cy: y, r: 3.2, class: "ch-dot" }); const t = s("title"); t.textContent = `${label(items[i])}: ${value(items[i])}`; c.append(t); svg.append(c); });
  const step = Math.max(1, Math.ceil(items.length / 5));
  items.forEach((it, i) => { if (i % step === 0 || i === items.length - 1) svg.append(txt(pts[i][0], h - 8, label(it), "ch-ax")); });
  return svg;
}

export function donut(items, { size = 160, colors = 4 } = {}) {
  const total = items.reduce((a, i) => a + i.value, 0) || 1, r = size / 2 - 14, c = size / 2, circ = 2 * Math.PI * r;
  const svg = s("svg", { viewBox: `0 0 ${size} ${size}`, class: "chart donut", role: "img", "aria-label": "구성비" });
  let off = 0;
  svg.append(s("circle", { cx: c, cy: c, r, class: "ch-ring-bg", fill: "none", "stroke-width": 20 }));
  items.forEach((it, i) => {
    const len = (circ * it.value) / total;
    const seg = s("circle", { cx: c, cy: c, r, fill: "none", "stroke-width": 20, "stroke-dasharray": `${len} ${circ - len}`, "stroke-dashoffset": -off, transform: `rotate(-90 ${c} ${c})`, class: "ch-seg s" + (i % colors) });
    const t = s("title"); t.textContent = `${it.label}: ${it.value}`; seg.append(t); svg.append(seg); off += len;
  });
  svg.append(txt(c, c + 6, String(total), "ch-center"));
  return svg;
}
