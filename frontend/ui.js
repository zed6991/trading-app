// Shared helpers for the newer screens (Code, Research, Agent, Settings, extra Lab tabs).
export const $ = (id) => document.getElementById(id);
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

export async function api(path, method = "GET", body) {
  const res = await fetch(path, body === undefined ? { method }
    : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Something went wrong (${res.status})`);
  return data;
}

export const level = () => Number(document.body.dataset.level) || 1;
export const unlocked = (n) => document.body.classList.contains(`unlock-${n}`) || n <= 1;
export const learnRefresh = () => window.dispatchEvent(new Event("learn:refresh"));
export const term = (slug, text) => `<span class="term" data-term="${slug}" tabindex="0" role="button">${text}</span>`;
export const money = (n, d) => {
  if (n === null || n === undefined || Number.isNaN(n)) return "–";
  const a = Math.abs(n);
  const digits = d ?? (a < 100 ? 2 : 0);
  return `${n < 0 ? "-" : ""}$${a.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
};
export const pct = (n, d = 1) => (n === null || n === undefined ? "–" : `${n > 0 ? "+" : ""}${Number(n).toFixed(d)}%`);
export const num = (n, d = 2) => (n === null || n === undefined ? "–" : Number(n).toFixed(d));
export const tone = (n) => (n > 0 ? "up" : n < 0 ? "down" : "");
export const day = (t) => new Date(t * 1000).toISOString().slice(0, 10);
export const COLORS = ["#3b82f6", "#f0b90b", "#16c784", "#f0465a", "#c084fc", "#38bdf8", "#fb923c", "#a3e635", "#e879f9"];

export function storageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
export function storageSet(k, v) { try { localStorage.setItem(k, v); } catch { /* preference only */ } }

export function card(k, v, s = "", cls = "") {
  return `<div class="lab-card ${cls}"><div class="k">${k}</div><div class="v">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`;
}

export function legend(items) {
  return `<div class="lab-legend">${items.map((i) => `<span><i style="background:${i.color}"></i>${esc(i.label)}</span>`).join("")}</div>`;
}

function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
const charts = new Map();

/** Line chart in element `id`. series: [{data: [{time, value}], color, width?, dashed?}] */
export function lineChart(id, series) {
  const box = document.getElementById(id);
  const LC = window.LightweightCharts;
  if (!box || !LC) return;
  charts.get(id)?.remove();
  const chart = LC.createChart(box, {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: css("--text-dim"), fontFamily: css("--font") },
    grid: { vertLines: { color: css("--grid") }, horzLines: { color: css("--grid") } },
    rightPriceScale: { borderColor: css("--border") },
    timeScale: { borderColor: css("--border"), minBarSpacing: 0.001 },
    crosshair: { mode: 0 }, handleScroll: false, handleScale: false,
  });
  for (const s of series) {
    const seen = new Set();
    const data = s.data.filter((p) => Number.isFinite(p.value) && !seen.has(p.time) && seen.add(p.time))
      .sort((a, b) => a.time - b.time);
    const line = chart.addLineSeries({ color: s.color, lineWidth: s.width || 2, priceLineVisible: false,
      lastValueVisible: false, lineStyle: s.dashed ? 2 : 0 });
    line.setData(data);
  }
  chart.timeScale().fitContent();
  charts.set(id, chart);
}

/** Horizontal bars for labelled values (e.g. weights, importance). */
export function bars(rows, { max = null, fmt = (v) => num(v), signed = false } = {}) {
  const m = max ?? Math.max(1e-9, ...rows.map((r) => Math.abs(r.value)));
  return `<div class="hbars">${rows.map((r) => {
    const w = Math.min(100, (Math.abs(r.value) / m) * 100);
    const neg = signed && r.value < 0;
    return `<div class="hbar"><span class="hbar-l" title="${esc(r.title || r.label)}">${esc(r.label)}</span>
      <span class="hbar-t"><i class="${neg ? "neg" : ""}" style="width:${w}%"></i></span><span class="hbar-v mono">${fmt(r.value)}</span></div>`;
  }).join("")}</div>`;
}

export function assetOptions(assets, selected, filter = () => true) {
  const list = assets.filter(filter);
  const group = (label, items) => (items.length ? `<optgroup label="${label}">${items.map((a) =>
    `<option value="${a.symbol}" ${a.symbol === selected ? "selected" : ""}>${esc(a.name)} (${esc(a.symbol.replace(/USDT$/, ""))})</option>`).join("")}</optgroup>` : "");
  return group("Stocks and ETFs", list.filter((a) => a.kind !== "crypto")) + group("Crypto", list.filter((a) => a.kind === "crypto"));
}

/** Two-click confirm for risky buttons. Returns true on the second click within 4 s. */
export function armed(btn, text = "Click again to confirm") {
  if (btn.dataset.armed) { delete btn.dataset.armed; btn.textContent = btn.dataset.label; return true; }
  btn.dataset.armed = "1";
  btn.dataset.label = btn.textContent;
  btn.textContent = text;
  setTimeout(() => { if (btn.dataset.armed) { delete btn.dataset.armed; btn.textContent = btn.dataset.label; } }, 4000);
  return false;
}

export function aiSource(src, cost) {
  if (!src) return "";
  const where = src.startsWith("cloud:") ? `cloud model ${src.slice(6)}` : src.startsWith("local:") ? `your local AI (${src.slice(6)})` : src;
  return `Answered by ${esc(where)}${cost ? ` · cost $${Number(cost).toFixed(4)}` : src.startsWith("local:") ? " · free" : ""}`;
}

/** Run when a view becomes visible (body.view-NAME), and on level changes. */
export function onView(name, fn) {
  let was = false;
  const check = () => {
    const now = document.body.classList.contains(`view-${name}`);
    if (now && !was) fn();
    was = now;
  };
  new MutationObserver(check).observe(document.body, { attributes: true, attributeFilter: ["class"] });
  check();
}
