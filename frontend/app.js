/* Paper Trader AI — dashboard client (vanilla ES module, no build step).
 * Talks to the same-origin FastAPI backend: REST under /api and a WebSocket feed at /ws.
 * Charts use the global `LightweightCharts` (v4.2.0 standalone build). */

const LC = window.LightweightCharts;

// =====================================================================================
// DOM + misc helpers
// =====================================================================================
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
/** Escape any value for safe insertion into HTML. */
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ESC_MAP[c]);
}

/** Small element factory. Strings become text nodes (never parsed as HTML). */
function el(tag, props, ...children) {
  const node = document.createElement(tag);
  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value);
      else if (["value", "checked", "disabled", "hidden", "selected"].includes(key)) node[key] = value;
      else node.setAttribute(key, value === true ? "" : String(value));
    }
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function hexToRgba(hex, alpha) {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(full, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

const storage = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem(`ptai.${key}`);
      return raw == null ? fallback : JSON.parse(raw);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(`ptai.${key}`, JSON.stringify(value));
    } catch {
      /* storage unavailable: preferences just won't persist */
    }
  },
};

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

// =====================================================================================
// Number / time formatting
// =====================================================================================
const isNum = (v) => typeof v === "number" && Number.isFinite(v);

function pricePrecision(price) {
  const a = Math.abs(price);
  if (!isNum(a) || a === 0) return 2;
  if (a >= 100) return 2;
  if (a >= 1) return 4;
  if (a >= 0.01) return 6;
  return 8;
}

function fmtPrice(p) {
  if (!isNum(p)) return "—";
  const d = pricePrecision(p);
  return p.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

const usdFormatter = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
function fmtUsd(v, { sign = false } = {}) {
  if (!isNum(v)) return "—";
  const s = usdFormatter.format(Math.abs(v));
  if (v < 0 && Math.abs(v) >= 0.005) return `-${s}`;
  return sign && v >= 0.005 ? `+${s}` : s;
}

function fmtPct(v, { digits = 2, sign = true } = {}) {
  if (!isNum(v)) return "—";
  const fixed = v.toFixed(digits);
  return `${sign && v > 0 && Number(fixed) !== 0 ? "+" : ""}${fixed}%`;
}

function fmtQty(q) {
  if (!isNum(q)) return "—";
  const a = Math.abs(q);
  const d = a >= 1000 ? 2 : a >= 1 ? 4 : 6;
  return q.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: d });
}

function fmtNum(v, digits = 2) {
  if (!isNum(v)) return "—";
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

const compactFormatter = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 });
function fmtCompact(v) {
  return isNum(v) ? compactFormatter.format(v) : "—";
}

/** Timestamps may be seconds (float) or milliseconds; normalise to seconds. */
function toSec(ts) {
  const n = typeof ts === "string" ? Number(ts) : ts;
  if (!isNum(n)) return NaN;
  return n > 1e12 ? n / 1000 : n;
}

function fmtTime(ts, { date = false } = {}) {
  const s = toSec(ts);
  if (!isNum(s)) return "—";
  const d = new Date(s * 1000);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const sameDay = d.toDateString() === new Date().toDateString();
  if (sameDay && !date) return time;
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

function fmtAgo(ts) {
  const s = toSec(ts);
  if (!isNum(s)) return "";
  const diff = Math.max(0, Date.now() / 1000 - s);
  if (diff < 45) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  return `${Math.round(diff / 86400)} d ago`;
}

function signClass(v) {
  if (!isNum(v) || Math.abs(v) < 1e-9) return "";
  return v > 0 ? "up" : "down";
}

function intervalSeconds(interval) {
  const m = /^(\d+)([smhdwM])$/.exec(interval || "");
  if (!m) return 60;
  const unit = { s: 1, m: 60, h: 3600, d: 86400, w: 604800, M: 2592000 }[m[2]];
  return Number(m[1]) * unit;
}

/** Round to a sensible number of decimals for an input box (avoids 1.2300000004). */
function roundForInput(v, ref) {
  if (!isNum(v)) return "";
  const d = pricePrecision(isNum(ref) ? ref : v);
  return String(Number(v.toFixed(d)));
}

// =====================================================================================
// Toasts
// =====================================================================================
function toast(message, kind = "info", title = "") {
  const box = $("#toasts");
  const node = el("div", { class: `toast toast-${kind}`, role: "status" },
    title ? el("div", { class: "toast-title", text: title }) : null,
    el("div", { class: "toast-msg", text: message }));
  box.append(node);
  while (box.children.length > 5) box.firstElementChild.remove();
  requestAnimationFrame(() => node.classList.add("show"));
  const remove = () => {
    node.classList.remove("show");
    setTimeout(() => node.remove(), 250);
  };
  node.addEventListener("click", remove);
  setTimeout(remove, kind === "error" ? 8000 : 4500);
}

function showMsg(node, text, kind = "error") {
  if (!node) return;
  if (!text) {
    node.hidden = true;
    node.textContent = "";
    return;
  }
  node.className = `form-msg ${kind}`;
  node.textContent = text;
  node.hidden = false;
}

// =====================================================================================
// REST client
// =====================================================================================
async function api(path, { method = "GET", body } = {}) {
  const opts = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (err) {
    throw new Error(`Network error: ${err.message || err}`);
  }
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    let detail = data && typeof data === "object" && "detail" in data ? data.detail : null;
    if (Array.isArray(detail)) {
      detail = detail.map((d) => `${(d.loc || []).filter((x) => x !== "body").join(".")}: ${d.msg}`).join("; ");
    } else if (detail && typeof detail !== "string") {
      detail = JSON.stringify(detail);
    }
    throw new Error(detail || (typeof data === "string" && data.length < 200 ? data : `HTTP ${res.status}`));
  }
  return data;
}

// =====================================================================================
// App state
// =====================================================================================
const S = {
  symbols: [],
  interval: "1m",
  ivSec: 60,
  active: null,
  account: null,
  engine: { auto_trading: false, ai_filter: false, strategies: {} },
  risk: null,
  status: { market_connected: false, ai_available: false, ai_model: "" },
  prices: {},
  ref: {}, // symbol -> {price, time} reference for % change
  strategies: [],
  trades: [], // newest first
  tradeIds: new Set(),
  logs: [], // newest first
  analyses: {}, // symbol -> latest analysis
  analyzing: new Set(),
  equity: [], // ascending [{time, value, live?}]
  candles: [], // active symbol candles
  wsUp: false,
  side: "buy",
  overlays: storage.get("overlays", { sma20: true, sma50: true, bb: false }),
  lowerPane: storage.get("lowerPane", "rsi"),
  bottomTab: storage.get("bottomTab", "positions"),
};

const MAX_TRADES = 500;
const MAX_LOGS = 500;
const CANDLE_LIMIT = 1000;

// =====================================================================================
// Chart helpers
// =====================================================================================
function tickMarkFormatter(time, type) {
  const d = new Date(Number(time) * 1000);
  switch (type) {
    case 0: return String(d.getFullYear());
    case 1: return d.toLocaleDateString([], { month: "short" });
    case 2: return d.toLocaleDateString([], { day: "numeric", month: "short" });
    case 4: return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
    default: return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  }
}

function chartBaseOptions() {
  return {
    layout: {
      background: { type: "solid", color: cssVar("--bg-panel") },
      textColor: cssVar("--text-dim"),
      fontFamily: "Inter, system-ui, sans-serif",
      fontSize: 11,
    },
    grid: {
      vertLines: { color: cssVar("--grid") },
      horzLines: { color: cssVar("--grid") },
    },
    crosshair: {
      mode: LC.CrosshairMode.Normal,
      vertLine: { color: cssVar("--crosshair"), labelBackgroundColor: cssVar("--bg-elev") },
      horzLine: { color: cssVar("--crosshair"), labelBackgroundColor: cssVar("--bg-elev") },
    },
    rightPriceScale: { borderColor: cssVar("--border"), minimumWidth: 80 },
    timeScale: {
      borderColor: cssVar("--border"),
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 4,
      tickMarkFormatter,
    },
    localization: { timeFormatter: (t) => fmtTime(t, { date: true }) },
    handleScale: { axisPressedMouseMove: true },
  };
}

function deepMerge(target, source) {
  for (const [k, v] of Object.entries(source || {})) {
    if (v && typeof v === "object" && !Array.isArray(v) && typeof target[k] === "object" && target[k] !== null) {
      deepMerge(target[k], v);
    } else {
      target[k] = v;
    }
  }
  return target;
}

/** Keep a chart sized to its container (ResizeObserver). */
function autoResize(chart, container) {
  const ro = new ResizeObserver((entries) => {
    const r = entries[0].contentRect;
    if (r.width > 0 && r.height > 0) chart.resize(Math.floor(r.width), Math.floor(r.height));
  });
  ro.observe(container);
  return ro;
}

function makeChart(container, extra = {}) {
  const rect = container.getBoundingClientRect();
  const opts = deepMerge(chartBaseOptions(), extra);
  opts.width = Math.max(1, Math.floor(rect.width));
  opts.height = Math.max(1, Math.floor(rect.height));
  const chart = LC.createChart(container, opts);
  autoResize(chart, container);
  return chart;
}

function lineOpts(color, extra = {}) {
  return {
    color,
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
    ...extra,
  };
}

function priceFormatFor(price) {
  const precision = pricePrecision(price);
  return { type: "price", precision, minMove: Number((1 / 10 ** precision).toFixed(precision)) };
}

function candleToBar(c) {
  return { time: c.time, open: c.open, high: c.high, low: c.low, close: c.close };
}

function volumeBar(c) {
  const up = c.close >= c.open;
  return { time: c.time, value: c.volume, color: up ? COLORS.volUp : COLORS.volDown };
}

const COLORS = {};
function loadColors() {
  COLORS.up = cssVar("--up");
  COLORS.down = cssVar("--down");
  COLORS.volUp = hexToRgba(COLORS.up, 0.32);
  COLORS.volDown = hexToRgba(COLORS.down, 0.32);
  COLORS.histUp = hexToRgba(COLORS.up, 0.55);
  COLORS.histDown = hexToRgba(COLORS.down, 0.55);
  COLORS.accent = cssVar("--accent");
  COLORS.sma20 = cssVar("--sma20");
  COLORS.sma50 = cssVar("--sma50");
  COLORS.bb = cssVar("--bb");
  COLORS.rsi = cssVar("--rsi");
  COLORS.macd = cssVar("--macd");
  COLORS.macdSignal = cssVar("--macd-signal");
  COLORS.bh = cssVar("--bh");
  COLORS.faint = cssVar("--text-faint");
  COLORS.warn = cssVar("--warn");
}

// =====================================================================================
// Main chart (price pane + synced indicator pane)
// =====================================================================================
const M = {
  main: null, lower: null,
  candle: null, volume: null, sma20: null, sma50: null, bbU: null, bbL: null,
  spacer: null, rsi: null, macd: null, macdSig: null, macdHist: null,
  ind: new Map(), // time -> {rsi, macd, sig, hist, sma20, sma50, bbU, bbL}
  posLines: [], posKey: "",
  hover: null, // which chart the pointer is over: "main" | "lower" | null
  hoverMain: false,
};

function initMainCharts() {
  M.main = makeChart($("#chart-main"), {
    timeScale: { visible: false },
    rightPriceScale: { scaleMargins: { top: 0.08, bottom: 0.22 } },
  });
  M.candle = M.main.addCandlestickSeries({
    upColor: COLORS.up, downColor: COLORS.down, borderVisible: false,
    wickUpColor: COLORS.up, wickDownColor: COLORS.down,
  });
  M.volume = M.main.addHistogramSeries({
    priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false,
  });
  M.main.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  M.sma20 = M.main.addLineSeries(lineOpts(COLORS.sma20));
  M.sma50 = M.main.addLineSeries(lineOpts(COLORS.sma50));
  M.bbU = M.main.addLineSeries(lineOpts(COLORS.bb, { lineStyle: LC.LineStyle.Dashed }));
  M.bbL = M.main.addLineSeries(lineOpts(COLORS.bb, { lineStyle: LC.LineStyle.Dashed }));

  M.lower = makeChart($("#chart-lower"), { rightPriceScale: { scaleMargins: { top: 0.12, bottom: 0.08 } } });
  // Invisible whitespace series carrying every candle time, so both charts share identical
  // logical indices and the time scales can be synced by logical range.
  M.spacer = M.lower.addLineSeries({ visible: false, priceLineVisible: false, lastValueVisible: false });
  M.rsi = M.lower.addLineSeries(lineOpts(COLORS.rsi, {
    lineWidth: 2, lastValueVisible: true, crosshairMarkerVisible: true,
    priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
  }));
  M.rsi.createPriceLine({ price: 70, color: hexToRgba(COLORS.down, 0.7), lineWidth: 1, lineStyle: LC.LineStyle.Dashed, axisLabelVisible: true, title: "" });
  M.rsi.createPriceLine({ price: 30, color: hexToRgba(COLORS.up, 0.7), lineWidth: 1, lineStyle: LC.LineStyle.Dashed, axisLabelVisible: true, title: "" });
  M.rsi.createPriceLine({ price: 50, color: hexToRgba(COLORS.faint, 0.6), lineWidth: 1, lineStyle: LC.LineStyle.Dotted, axisLabelVisible: false, title: "" });
  M.macdHist = M.lower.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
  M.macd = M.lower.addLineSeries(lineOpts(COLORS.macd, { lineWidth: 2, lastValueVisible: true }));
  M.macdSig = M.lower.addLineSeries(lineOpts(COLORS.macdSignal, { lastValueVisible: true }));

  syncTimeScales(M.main, M.lower);
  setupCrosshair();
  applyOverlays();
  applyLowerPane();
}

function syncTimeScales(a, b) {
  let syncing = false;
  const link = (src, dst) => {
    src.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (syncing || !range) return;
      syncing = true;
      try {
        dst.timeScale().setVisibleLogicalRange(range);
      } catch {
        /* the other chart may have no data yet */
      }
      syncing = false;
    });
  };
  link(a, b);
  link(b, a);
}

function setupCrosshair() {
  const mainBox = $("#chart-main");
  const lowerBox = $("#chart-lower");
  mainBox.addEventListener("mouseenter", () => { M.hover = "main"; });
  lowerBox.addEventListener("mouseenter", () => { M.hover = "lower"; });
  mainBox.addEventListener("mouseleave", () => { if (M.hover === "main") M.hover = null; clearOtherCrosshair(M.lower); renderLegend(); renderLowerLegend(); });
  lowerBox.addEventListener("mouseleave", () => { if (M.hover === "lower") M.hover = null; clearOtherCrosshair(M.main); renderLegend(); renderLowerLegend(); });

  M.main.subscribeCrosshairMove((param) => {
    if (M.hover !== "main") return;
    const time = param && param.time != null ? param.time : null;
    renderLegend(time);
    renderLowerLegend(time);
    if (time == null) return clearOtherCrosshair(M.lower);
    const v = M.ind.get(time);
    const series = S.lowerPane === "rsi" ? M.rsi : M.macd;
    const value = v ? (S.lowerPane === "rsi" ? v.rsi : v.macd) : undefined;
    if (isNum(value) && typeof M.lower.setCrosshairPosition === "function") M.lower.setCrosshairPosition(value, time, series);
    else clearOtherCrosshair(M.lower);
  });
  M.lower.subscribeCrosshairMove((param) => {
    if (M.hover !== "lower") return;
    const time = param && param.time != null ? param.time : null;
    renderLegend(time);
    renderLowerLegend(time);
    if (time == null) return clearOtherCrosshair(M.main);
    const c = findCandle(time);
    if (c && typeof M.main.setCrosshairPosition === "function") M.main.setCrosshairPosition(c.close, time, M.candle);
    else clearOtherCrosshair(M.main);
  });
}

function clearOtherCrosshair(chart) {
  if (chart && typeof chart.clearCrosshairPosition === "function") chart.clearCrosshairPosition();
}

function findCandle(time) {
  const arr = S.candles;
  let lo = 0;
  let hi = arr.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const t = arr[mid].time;
    if (t === time) return arr[mid];
    if (t < time) lo = mid + 1;
    else hi = mid - 1;
  }
  return null;
}

function applyOverlays() {
  M.sma20.applyOptions({ visible: !!S.overlays.sma20 });
  M.sma50.applyOptions({ visible: !!S.overlays.sma50 });
  M.bbU.applyOptions({ visible: !!S.overlays.bb });
  M.bbL.applyOptions({ visible: !!S.overlays.bb });
  $("#ov-sma20").checked = !!S.overlays.sma20;
  $("#ov-sma50").checked = !!S.overlays.sma50;
  $("#ov-bb").checked = !!S.overlays.bb;
  renderLegend();
}

function applyLowerPane() {
  const rsi = S.lowerPane === "rsi";
  M.rsi.applyOptions({ visible: rsi });
  M.macd.applyOptions({ visible: !rsi });
  M.macdSig.applyOptions({ visible: !rsi });
  M.macdHist.applyOptions({ visible: !rsi });
  $$("#lower-toggle button").forEach((b) => b.classList.toggle("active", b.dataset.pane === S.lowerPane));
  renderLowerLegend();
}

function applyIndicators(ind) {
  ind = ind || {};
  const pack = (arr) => (Array.isArray(arr) ? arr.filter((p) => isNum(p.value)) : []);
  const sma20 = pack(ind.sma20);
  const sma50 = pack(ind.sma50);
  const bbU = pack(ind.bb_upper);
  const bbL = pack(ind.bb_lower);
  const rsi = pack(ind.rsi14);
  const macd = pack(ind.macd);
  const sig = pack(ind.macd_signal);
  const hist = pack(ind.macd_hist);

  M.sma20.setData(sma20);
  M.sma50.setData(sma50);
  M.bbU.setData(bbU);
  M.bbL.setData(bbL);
  M.rsi.setData(rsi);
  M.macd.setData(macd);
  M.macdSig.setData(sig);
  M.macdHist.setData(hist.map((p) => ({ time: p.time, value: p.value, color: p.value >= 0 ? COLORS.histUp : COLORS.histDown })));

  const map = new Map();
  const put = (arr, key) => {
    for (const p of arr) {
      let row = map.get(p.time);
      if (!row) map.set(p.time, (row = {}));
      row[key] = p.value;
    }
  };
  put(sma20, "sma20"); put(sma50, "sma50"); put(bbU, "bbU"); put(bbL, "bbL");
  put(rsi, "rsi"); put(macd, "macd"); put(sig, "sig"); put(hist, "hist");
  M.ind = map;
  renderLegend();
  renderLowerLegend();
}

function renderLegend(time = null) {
  const node = $("#legend");
  if (!node) return;
  const c = time != null ? findCandle(time) : S.candles[S.candles.length - 1];
  if (!S.active) {
    node.innerHTML = "";
    return;
  }
  const parts = [`<span class="lg-sym">${esc(S.active)}</span><span>${esc(S.interval)}</span>`];
  if (c) {
    const chg = c.open ? (c.close / c.open - 1) * 100 : NaN;
    const cls = c.close >= c.open ? "up" : "down";
    parts.push(
      `<span>O <b class="${cls}">${esc(fmtPrice(c.open))}</b></span>`,
      `<span>H <b class="${cls}">${esc(fmtPrice(c.high))}</b></span>`,
      `<span>L <b class="${cls}">${esc(fmtPrice(c.low))}</b></span>`,
      `<span>C <b class="${cls}">${esc(fmtPrice(c.close))}</b></span>`,
      `<span class="${signClass(chg)}">${esc(fmtPct(chg))}</span>`,
      `<span>Vol <b>${esc(fmtCompact(c.volume))}</b></span>`,
    );
    const v = M.ind.get(c.time) || {};
    if (S.overlays.sma20 && isNum(v.sma20)) parts.push(`<span style="color:var(--sma20)">SMA20 ${esc(fmtPrice(v.sma20))}</span>`);
    if (S.overlays.sma50 && isNum(v.sma50)) parts.push(`<span style="color:var(--sma50)">SMA50 ${esc(fmtPrice(v.sma50))}</span>`);
    if (S.overlays.bb && isNum(v.bbU) && isNum(v.bbL)) parts.push(`<span style="color:var(--bb)">BB ${esc(fmtPrice(v.bbU))} / ${esc(fmtPrice(v.bbL))}</span>`);
  }
  node.innerHTML = parts.join("");
}

function renderLowerLegend(time = null) {
  const node = $("#lower-legend");
  if (!node) return;
  let t = time;
  if (t == null) {
    // last time that has indicator values
    const last = S.candles.length ? S.candles[S.candles.length - 1].time : null;
    t = last != null && M.ind.has(last) ? last : lastKey(M.ind);
  }
  const v = (t != null && M.ind.get(t)) || {};
  if (S.lowerPane === "rsi") {
    const r = v.rsi;
    const cls = isNum(r) ? (r >= 70 ? "down" : r <= 30 ? "up" : "") : "";
    node.innerHTML = `<span style="color:var(--rsi)">RSI 14</span> <b class="${cls}">${esc(isNum(r) ? r.toFixed(2) : "—")}</b>`;
  } else {
    const d = isNum(S.prices[S.active]) ? Math.min(8, pricePrecision(S.prices[S.active]) + 1) : 4;
    const f = (x) => (isNum(x) ? x.toFixed(d) : "—");
    node.innerHTML = `<span>MACD 12 26 9</span> <span style="color:var(--macd)">${esc(f(v.macd))}</span>`
      + ` <span style="color:var(--macd-signal)">${esc(f(v.sig))}</span>`
      + ` <span class="${signClass(v.hist)}">${esc(f(v.hist))}</span>`;
  }
}

function lastKey(map) {
  let k = null;
  for (const key of map.keys()) k = key;
  return k;
}

let chartLoadSeq = 0;
async function loadChart(sym, { fit = false } = {}) {
  const seq = ++chartLoadSeq;
  let data;
  try {
    data = await api(`/api/candles/${encodeURIComponent(sym)}?limit=${CANDLE_LIMIT}`);
  } catch (err) {
    toast(err.message, "error", `Could not load ${sym} candles`);
    return;
  }
  if (seq !== chartLoadSeq || sym !== S.active) return;
  const candles = (data && data.candles) || [];
  S.candles = candles.slice();
  setRefPrice(sym, candles);
  const last = candles[candles.length - 1];
  if (last) {
    S.prices[sym] = last.close;
    updateTab(sym);
  }
  const fmt = priceFormatFor(last ? last.close : 1);
  M.candle.applyOptions({ priceFormat: fmt });
  for (const s of [M.sma20, M.sma50, M.bbU, M.bbL]) s.applyOptions({ priceFormat: fmt });
  const macdPrec = Math.min(8, fmt.precision + 1);
  const macdFmt = { type: "price", precision: macdPrec, minMove: Number((1 / 10 ** macdPrec).toFixed(macdPrec)) };
  for (const s of [M.macd, M.macdSig, M.macdHist]) s.applyOptions({ priceFormat: macdFmt });

  M.candle.setData(candles.map(candleToBar));
  M.volume.setData(candles.map(volumeBar));
  M.spacer.setData(candles.map((c) => ({ time: c.time })));
  applyIndicators(data.indicators);
  refreshMarkers();
  M.posKey = "";
  updatePositionLines();
  if (fit && candles.length) {
    const n = candles.length;
    M.main.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 160), to: n + 4 });
  }
  renderLegend();
  updateOrderEstimate();
}

const refreshIndicators = debounce(async () => {
  const sym = S.active;
  if (!sym) return;
  try {
    const data = await api(`/api/candles/${encodeURIComponent(sym)}?limit=${CANDLE_LIMIT}`);
    if (sym !== S.active) return;
    applyIndicators(data.indicators);
  } catch (err) {
    console.warn("indicator refresh failed", err);
  }
}, 600);

function refreshMarkers() {
  if (!M.candle) return;
  if (!S.candles.length) {
    M.candle.setMarkers([]);
    return;
  }
  const first = S.candles[0].time;
  const last = S.candles[S.candles.length - 1].time;
  const markers = [];
  for (const t of S.trades) {
    if (t.symbol !== S.active) continue;
    const sec = toSec(t.ts);
    if (!isNum(sec)) continue;
    const time = Math.floor(sec / S.ivSec) * S.ivSec;
    if (time < first || time > last) continue;
    const buy = t.side === "buy";
    const reason = t.reason && t.reason !== "signal" ? ` ${t.reason}` : "";
    markers.push({
      time,
      position: buy ? "belowBar" : "aboveBar",
      color: buy ? COLORS.up : COLORS.down,
      shape: buy ? "arrowUp" : "arrowDown",
      text: `${buy ? "B" : "S"}${reason}`,
    });
  }
  markers.sort((a, b) => a.time - b.time);
  M.candle.setMarkers(markers);
}

function updatePositionLines() {
  if (!M.candle) return;
  const pos = (S.account?.positions || []).find((p) => p.symbol === S.active);
  const key = pos ? `${S.active}|${pos.avg_price}|${pos.stop_loss}|${pos.take_profit}` : `${S.active}|none`;
  if (key === M.posKey) return;
  M.posKey = key;
  for (const line of M.posLines) M.candle.removePriceLine(line);
  M.posLines = [];
  if (!pos) return;
  const add = (price, color, title, style) => {
    if (!isNum(price)) return;
    M.posLines.push(M.candle.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title }));
  };
  add(pos.avg_price, COLORS.accent, "Entry", LC.LineStyle.Dashed);
  add(pos.stop_loss, COLORS.down, "SL", LC.LineStyle.Dotted);
  add(pos.take_profit, COLORS.up, "TP", LC.LineStyle.Dotted);
}

function onTick(data) {
  if (!data || !data.symbol || !data.candle) return;
  const { symbol, candle } = data;
  if (isNum(candle.close)) S.prices[symbol] = candle.close;
  updateTab(symbol);
  if (symbol !== S.active) return;

  const arr = S.candles;
  const last = arr[arr.length - 1];
  if (last && candle.time < last.time) return; // stale tick
  if (last && last.time === candle.time) arr[arr.length - 1] = candle;
  else {
    arr.push(candle);
    if (arr.length > CANDLE_LIMIT * 2) arr.splice(0, arr.length - CANDLE_LIMIT);
  }
  try {
    M.candle.update(candleToBar(candle));
    M.volume.update(volumeBar(candle));
    M.spacer.update({ time: candle.time });
  } catch (err) {
    console.warn("chart update failed", err);
  }
  if (candle.closed) refreshIndicators();
  if (M.hover == null) {
    renderLegend();
  }
  updateOrderEstimate();
  document.title = `${fmtPrice(candle.close)} ${symbol} · Quant Path`;
}

// =====================================================================================
// Symbol tabs
// =====================================================================================
function renderTabs() {
  const box = $("#symbol-tabs");
  box.replaceChildren();
  for (const sym of S.symbols) {
    const btn = el("button", { type: "button", class: "sym-tab", role: "tab", dataset: { symbol: sym }, onclick: () => selectSymbol(sym) },
      el("span", { class: "sym-name" }, el("span", { text: sym.replace(/USDT$/, "") }), el("i", { class: "sym-pos", hidden: true })),
      el("span", { class: "sym-line" }, el("span", { class: "sym-px", text: "—" }), el("span", { class: "sym-chg", text: "" })));
    box.append(btn);
    updateTab(sym);
  }
  highlightActiveTab();
}

function highlightActiveTab() {
  $$(".sym-tab").forEach((b) => {
    const on = b.dataset.symbol === S.active;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
}

const prevTabPrice = {};
function updateTab(sym) {
  const tab = $(`.sym-tab[data-symbol="${CSS.escape(sym)}"]`);
  if (!tab) return;
  const price = S.prices[sym];
  const pxNode = $(".sym-px", tab);
  pxNode.textContent = fmtPrice(price);
  const prev = prevTabPrice[sym];
  if (isNum(prev) && isNum(price) && prev !== price) {
    pxNode.classList.remove("flash-up", "flash-down");
    void pxNode.offsetWidth; // restart transition
    pxNode.classList.add(price > prev ? "flash-up" : "flash-down");
    setTimeout(() => pxNode.classList.remove("flash-up", "flash-down"), 350);
  }
  prevTabPrice[sym] = price;
  const ref = S.ref[sym];
  const chgNode = $(".sym-chg", tab);
  if (ref && isNum(price) && ref.price) {
    const pct = (price / ref.price - 1) * 100;
    chgNode.textContent = fmtPct(pct);
    chgNode.className = `sym-chg ${signClass(pct)}`;
    tab.title = `${sym}: change since ${fmtTime(ref.time, { date: true })}`;
  } else {
    chgNode.textContent = "";
  }
  const hasPos = (S.account?.positions || []).some((p) => p.symbol === sym);
  $(".sym-pos", tab).hidden = !hasPos;
}

/** Reference price for the tab % change: open of the first candle within the last 24 h. */
function setRefPrice(sym, candles) {
  if (!candles || !candles.length) return;
  const cutoff = Date.now() / 1000 - 86400;
  const c = candles.find((x) => x.time >= cutoff) || candles[0];
  S.ref[sym] = { price: c.open, time: c.time };
  updateTab(sym);
}

async function loadRefPrices() {
  await Promise.all(S.symbols.filter((s) => s !== S.active && !S.ref[s]).map(async (sym) => {
    try {
      const data = await api(`/api/candles/${encodeURIComponent(sym)}?limit=${CANDLE_LIMIT}`);
      const candles = data.candles || [];
      setRefPrice(sym, candles);
      if (!isNum(S.prices[sym]) && candles.length) {
        S.prices[sym] = candles[candles.length - 1].close;
        updateTab(sym);
      }
    } catch (err) {
      console.warn("ref price load failed", sym, err);
    }
  }));
}

async function selectSymbol(sym, { force = false } = {}) {
  if (!S.symbols.includes(sym)) return;
  if (sym === S.active && !force) return;
  S.active = sym;
  storage.set("symbol", sym);
  highlightActiveTab();
  $("#ot-symbol").textContent = sym;
  $("#strat-symbol").textContent = sym;
  showMsg($("#ot-msg"), "");
  M.posKey = "";
  S.candles = [];
  M.ind = new Map();
  stratDirty = false;
  renderStrategyPanel();
  renderAI();
  loadLatestAnalysis(sym);
  renderLegend();
  await loadChart(sym, { fit: true });
}

// =====================================================================================
// Header: account metrics, status, engine toggles
// =====================================================================================
function computeDayStart() {
  const a = S.account;
  if (a && isNum(a.day_start_equity)) return a.day_start_equity;
  if (!S.equity.length) return null;
  const dayStart = Math.floor(Date.now() / 86400000) * 86400; // UTC midnight (broker rolls days in UTC)
  let before = null;
  let firstToday = null;
  for (const p of S.equity) {
    if (p.time < dayStart) before = p;
    else if (!firstToday) firstToday = p;
  }
  if (before) return before.value;
  if (firstToday) return firstToday.value;
  return null;
}

function setMetric(id, text, cls = "") {
  const node = $(id);
  node.textContent = text;
  node.className = node.className.replace(/\b(up|down)\b/g, "").trim() + (cls ? ` ${cls}` : "");
}

function renderHeader() {
  const a = S.account;
  if (!a) return;
  setMetric("#m-equity", fmtUsd(a.equity));
  setMetric("#m-cash", fmtUsd(a.cash));
  const total = isNum(a.equity) && isNum(a.starting_cash) ? a.equity - a.starting_cash : NaN;
  setMetric("#m-total", fmtUsd(total, { sign: true }), signClass(total));
  setMetric("#m-total-pct", fmtPct(a.total_return_pct), signClass(a.total_return_pct));
  const ds = computeDayStart();
  if (isNum(ds) && ds > 0 && isNum(a.equity)) {
    const day = a.equity - ds;
    setMetric("#m-day", fmtUsd(day, { sign: true }), signClass(day));
    setMetric("#m-day-pct", fmtPct((day / ds) * 100), signClass(day));
  } else {
    setMetric("#m-day", "—");
    setMetric("#m-day-pct", "");
  }
  $("#halt-box").hidden = !a.halted;
  $("#halt-badge").title = a.halt_reason ? `Halted: ${a.halt_reason}` : "Trading halted";
}

function setStatusItem(id, state, title) {
  const node = $(id);
  node.classList.remove("ok", "bad", "warn");
  if (state) node.classList.add(state);
  if (title) node.title = title;
}

function renderStatus() {
  setStatusItem("#st-ws", S.wsUp ? "ok" : "bad", S.wsUp ? "Dashboard feed connected" : "Dashboard feed disconnected — reconnecting");
  if (!S.wsUp) {
    setStatusItem("#st-market", "", "Market status unknown (feed disconnected)");
    setStatusItem("#st-stocks", "", "Market status unknown (feed disconnected)");
    setStatusItem("#st-ai", "", "AI status unknown (feed disconnected)");
    return;
  }
  const st = S.status || {};
  const crypto = st.crypto_connected ?? st.market_connected;
  setStatusItem("#st-market", crypto ? "ok" : "bad", crypto ? "Crypto prices live (Binance)" : "Crypto prices disconnected");
  const us = st.us_market || {};
  $("#st-stocks-label").textContent = us.open ? "Stocks · open" : "Stocks · closed";
  setStatusItem("#st-stocks", st.stocks_connected ? (us.open ? "ok" : "warn") : "bad",
    `${st.stocks_connected ? "US stock prices loading (Yahoo Finance, may be delayed up to 15 min)" : "US stock prices unavailable"}. ${us.label || ""}`);
  const model = st.ai_model || "";
  if (st.ai_enabled === false) {
    $("#st-ai-label").textContent = "AI off";
    setStatusItem("#st-ai", "", "AI is switched off in Settings: the app uses its rule-based analysis");
    return;
  }
  $("#st-ai-label").textContent = model ? `AI · ${model.length > 22 ? `${model.slice(0, 21)}…` : model}` : "AI";
  setStatusItem("#st-ai", st.ai_available ? "ok" : "warn",
    st.ai_available ? `Ollama model ${model} available` : `Ollama model ${model || ""} unavailable — rule-based fallback in use`);
}

function applyEngine(cfg) {
  if (!cfg || typeof cfg !== "object") return;
  S.engine = { auto_trading: !!cfg.auto_trading, ai_filter: !!cfg.ai_filter, strategies: cfg.strategies || {} };
  $("#tg-auto").checked = S.engine.auto_trading;
  $("#tg-ai").checked = S.engine.ai_filter;
  renderStrategyPanel();
}

async function toggleEngine(key, input) {
  const value = input.checked;
  input.disabled = true;
  try {
    const cfg = await api("/api/engine", { method: "PUT", body: { [key]: value } });
    applyEngine(cfg);
    toast(`${key === "auto_trading" ? "Auto-trading" : "AI filter"} ${value ? "enabled" : "disabled"}`, "success");
  } catch (err) {
    input.checked = !value;
    toast(err.message, "error", "Engine update failed");
  } finally {
    input.disabled = false;
  }
}

// =====================================================================================
// Account application
// =====================================================================================
function applyAccount(a) {
  if (!a || typeof a !== "object") return;
  S.account = a;
  renderHeader();
  renderPositions();
  updatePositionLines();
  renderAccountTab();
  appendLiveEquity(a.equity);
  updateOrderEstimate();
  for (const sym of S.symbols) updateTab(sym);
}

// =====================================================================================
// Positions table (keyed rows so inline SL/TP edits survive live updates)
// =====================================================================================
const posRows = new Map();

function renderPositions() {
  const list = S.account?.positions || [];
  $("#cnt-positions").textContent = list.length ? String(list.length) : "";
  const body = $("#pos-body");
  const seen = new Set();
  list.forEach((p, i) => {
    seen.add(p.symbol);
    let row = posRows.get(p.symbol);
    if (!row) {
      row = createPosRow(p.symbol);
      posRows.set(p.symbol, row);
    }
    updatePosRow(row, p);
    if (body.children[i] !== row.tr) body.insertBefore(row.tr, body.children[i] || null);
  });
  for (const [sym, row] of posRows) {
    if (!seen.has(sym)) {
      row.tr.remove();
      posRows.delete(sym);
    }
  }
  $("#pos-empty").hidden = list.length > 0;
}

function createPosRow(sym) {
  const r = { sym, dirty: false };
  const td = (cls) => el("td", { class: cls || "" });
  r.cSym = el("td", { class: "txt" }, el("button", { type: "button", class: "sym-link", text: sym, onclick: () => selectSymbol(sym) }));
  r.cQty = td("r"); r.cAvg = td("r"); r.cMark = td("r"); r.cValue = td("r"); r.cPnl = td("r"); r.cPct = td("r");
  r.sl = el("input", { type: "number", step: "any", min: "0", placeholder: "none", "aria-label": `${sym} stop loss` });
  r.tp = el("input", { type: "number", step: "any", min: "0", placeholder: "none", "aria-label": `${sym} take profit` });
  r.cStrat = td("txt reason"); r.cOpened = td();
  r.save = el("button", { type: "button", class: "btn btn-xs btn-primary", text: "Save", disabled: true, onclick: () => saveBrackets(r) });
  r.close = el("button", { type: "button", class: "btn btn-xs btn-sell", text: "Close", onclick: () => closePosition(r) });
  const markDirty = () => {
    r.dirty = true;
    r.save.disabled = false;
    r.sl.classList.add("dirty");
    r.tp.classList.add("dirty");
  };
  for (const input of [r.sl, r.tp]) {
    input.addEventListener("input", markDirty);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") saveBrackets(r);
      if (e.key === "Escape") {
        r.dirty = false;
        r.save.disabled = true;
        r.sl.classList.remove("dirty", "invalid");
        r.tp.classList.remove("dirty", "invalid");
        if (r.pos) {
          r.sl.value = roundForInput(r.pos.stop_loss, r.pos.avg_price);
          r.tp.value = roundForInput(r.pos.take_profit, r.pos.avg_price);
        }
        input.blur();
      }
    });
  }
  r.tr = el("tr", null, r.cSym, r.cQty, r.cAvg, r.cMark, r.cValue, r.cPnl, r.cPct,
    el("td", null, r.sl), el("td", null, r.tp), r.cStrat, r.cOpened, el("td", null, el("div", { class: "actions" }, r.save, r.close)));
  return r;
}

function updatePosRow(r, p) {
  r.pos = p;
  r.cQty.textContent = fmtQty(p.qty);
  r.cAvg.textContent = fmtPrice(p.avg_price);
  r.cMark.textContent = fmtPrice(p.mark);
  r.cValue.textContent = fmtUsd(p.market_value);
  r.cPnl.textContent = fmtUsd(p.unrealized_pnl, { sign: true });
  r.cPnl.className = `r ${signClass(p.unrealized_pnl)}`;
  r.cPct.textContent = fmtPct(p.pnl_pct);
  r.cPct.className = `r ${signClass(p.pnl_pct)}`;
  r.cStrat.textContent = p.strategy || "manual";
  r.cOpened.textContent = fmtTime(p.opened_at);
  if (!r.dirty) {
    if (document.activeElement !== r.sl) r.sl.value = roundForInput(p.stop_loss, p.avg_price);
    if (document.activeElement !== r.tp) r.tp.value = roundForInput(p.take_profit, p.avg_price);
  }
}

function parseOptionalPrice(input) {
  const raw = input.value.trim();
  input.classList.remove("invalid");
  if (raw === "") return null;
  const v = Number(raw);
  if (!isNum(v) || v <= 0) {
    input.classList.add("invalid");
    throw new Error("Prices must be positive numbers (leave blank for none)");
  }
  return v;
}

async function saveBrackets(r) {
  let body;
  try {
    body = { stop_loss: parseOptionalPrice(r.sl), take_profit: parseOptionalPrice(r.tp) };
  } catch (err) {
    toast(err.message, "error", `${r.sym} brackets`);
    return;
  }
  r.save.disabled = true;
  try {
    const pos = await api(`/api/positions/${encodeURIComponent(r.sym)}`, { method: "PUT", body });
    r.dirty = false;
    r.sl.classList.remove("dirty");
    r.tp.classList.remove("dirty");
    if (pos && S.account) {
      const target = (S.account.positions || []).find((p) => p.symbol === r.sym);
      if (target) Object.assign(target, { stop_loss: pos.stop_loss ?? null, take_profit: pos.take_profit ?? null });
      renderPositions();
      updatePositionLines();
    }
    toast(`SL ${body.stop_loss == null ? "none" : fmtPrice(body.stop_loss)} · TP ${body.take_profit == null ? "none" : fmtPrice(body.take_profit)}`, "success", `${r.sym} brackets updated`);
  } catch (err) {
    r.save.disabled = false;
    toast(err.message, "error", `${r.sym} brackets`);
  }
}

async function closePosition(r) {
  r.close.disabled = true;
  r.close.textContent = "Closing…";
  try {
    const fill = await api(`/api/positions/${encodeURIComponent(r.sym)}/close`, { method: "POST" });
    if (!S.wsUp && fill) {
      addTrade(fill, { live: true });
    }
  } catch (err) {
    toast(err.message, "error", `Close ${r.sym} failed`);
  } finally {
    r.close.disabled = false;
    r.close.textContent = "Close";
  }
}

// =====================================================================================
// Trade log
// =====================================================================================
function tradeRow(t, isNew = false) {
  const notional = isNum(t.qty) && isNum(t.price) ? t.qty * t.price : NaN;
  const side = t.side === "buy" ? "buy" : "sell";
  const tr = el("tr", { class: isNew ? "new-row" : "" });
  tr.innerHTML =
    `<td>${esc(fmtTime(t.ts))}</td>`
    + `<td class="txt"><button type="button" class="sym-link">${esc(t.symbol)}</button></td>`
    + `<td><span class="side side-${side}">${esc(String(t.side || "").toUpperCase())}</span></td>`
    + `<td class="r">${esc(fmtQty(t.qty))}</td>`
    + `<td class="r">${esc(fmtPrice(t.price))}</td>`
    + `<td class="r">${esc(fmtUsd(notional))}</td>`
    + `<td class="r">${esc(fmtUsd(t.fee))}</td>`
    + `<td class="r ${signClass(t.pnl)}">${t.pnl == null ? "—" : esc(fmtUsd(t.pnl, { sign: true }))}</td>`
    + `<td class="txt reason">${esc(t.reason || "")}</td>`
    + `<td class="txt reason">${esc(t.strategy || (t.reason === "manual" ? "manual" : ""))}</td>`;
  $(".sym-link", tr).addEventListener("click", () => selectSymbol(t.symbol));
  return tr;
}

function renderTrades() {
  const body = $("#trades-body");
  body.replaceChildren(...S.trades.map((t) => tradeRow(t)));
  $("#trades-empty").hidden = S.trades.length > 0;
  $("#cnt-trades").textContent = S.trades.length ? String(S.trades.length) : "";
}

function addTrade(t, { live = false } = {}) {
  if (!t || typeof t !== "object") return;
  if (t.id != null) {
    if (S.tradeIds.has(t.id)) return;
    S.tradeIds.add(t.id);
  }
  S.trades.unshift(t);
  const body = $("#trades-body");
  body.prepend(tradeRow(t, live));
  while (S.trades.length > MAX_TRADES) {
    const old = S.trades.pop();
    if (old && old.id != null) S.tradeIds.delete(old.id);
    body.lastElementChild?.remove();
  }
  $("#trades-empty").hidden = true;
  $("#cnt-trades").textContent = String(S.trades.length);
  if (t.symbol === S.active) refreshMarkers();
  if (live) {
    const buy = t.side === "buy";
    const pnl = t.pnl == null ? "" : ` · PnL ${fmtUsd(t.pnl, { sign: true })}`;
    toast(`${fmtQty(t.qty)} @ ${fmtPrice(t.price)}${pnl} (${t.reason || "fill"})`, buy ? "buy" : "sell", `${buy ? "Bought" : "Sold"} ${t.symbol}`);
  }
}

async function loadTrades() {
  try {
    const list = await api("/api/trades?limit=200");
    S.trades = Array.isArray(list) ? list.slice(0, MAX_TRADES) : [];
    S.tradeIds = new Set(S.trades.filter((t) => t.id != null).map((t) => t.id));
    renderTrades();
    refreshMarkers();
  } catch (err) {
    toast(err.message, "error", "Could not load trades");
  }
}

// =====================================================================================
// Event log
// =====================================================================================
function logRow(l) {
  const level = String(l.level || "info").toLowerCase();
  return el("div", { class: `ev lvl-${level.replace(/[^a-z]/g, "")}` },
    el("span", { class: "ev-time", text: fmtTime(l.ts) }),
    el("span", { class: "ev-level", text: level }),
    el("span", { class: "ev-msg", text: l.message ?? "" }));
}

function renderLogs() {
  const list = $("#events-list");
  list.replaceChildren(...S.logs.map(logRow));
  $("#events-empty").hidden = S.logs.length > 0;
}

function addLog(l) {
  if (!l || typeof l !== "object") return;
  S.logs.unshift(l);
  const list = $("#events-list");
  list.prepend(logRow(l));
  while (S.logs.length > MAX_LOGS) {
    S.logs.pop();
    list.lastElementChild?.remove();
  }
  $("#events-empty").hidden = true;
  const level = String(l.level || "").toLowerCase();
  if (level === "error" || level === "critical") toast(String(l.message ?? ""), "error", "Engine error");
  else if (level === "warning" && /halt/i.test(String(l.message))) toast(String(l.message ?? ""), "warn", "Risk");
}

async function loadLogs() {
  try {
    const list = await api("/api/logs?limit=200");
    S.logs = Array.isArray(list) ? list.slice(0, MAX_LOGS) : [];
    renderLogs();
  } catch (err) {
    toast(err.message, "error", "Could not load event log");
  }
}

// =====================================================================================
// Equity tab
// =====================================================================================
const EQ = { chart: null, series: null, fitted: false };

function initEquityChart() {
  EQ.chart = makeChart($("#chart-equity"), { rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0.08 } } });
  EQ.series = EQ.chart.addAreaSeries({
    lineColor: COLORS.accent,
    topColor: hexToRgba(COLORS.accent, 0.28),
    bottomColor: hexToRgba(COLORS.accent, 0.02),
    lineWidth: 2,
    priceFormat: { type: "price", precision: 2, minMove: 0.01 },
  });
}

function sanitizeEquity(list) {
  const out = [];
  for (const p of Array.isArray(list) ? list : []) {
    const time = Math.floor(toSec(p.time));
    if (!isNum(time) || !isNum(p.value)) continue;
    if (out.length && out[out.length - 1].time >= time) {
      if (out[out.length - 1].time === time) out[out.length - 1].value = p.value;
      continue;
    }
    out.push({ time, value: p.value });
  }
  return out;
}

async function loadEquity() {
  try {
    const list = await api("/api/equity?limit=2000");
    S.equity = sanitizeEquity(list);
    EQ.series.setData(S.equity.map(({ time, value }) => ({ time, value })));
    EQ.chart.timeScale().fitContent();
    EQ.fitted = S.bottomTab === "equity";
    renderEquitySummary();
    renderHeader();
  } catch (err) {
    toast(err.message, "error", "Could not load equity history");
  }
}

function appendLiveEquity(value) {
  if (!isNum(value) || !EQ.series) return;
  const now = Math.floor(Date.now() / 1000);
  const last = S.equity[S.equity.length - 1];
  try {
    if (last && last.live && now - last.time < 10) {
      last.value = value;
      EQ.series.update({ time: last.time, value });
    } else if (!last || now > last.time) {
      const p = { time: now, value, live: true };
      S.equity.push(p);
      if (S.equity.length > 20000) S.equity.splice(0, S.equity.length - 20000);
      EQ.series.update({ time: p.time, value: p.value });
    }
  } catch (err) {
    console.warn("equity update failed", err);
  }
  renderEquitySummary();
}

function renderEquitySummary() {
  const node = $("#eq-summary");
  if (!S.equity.length) {
    node.textContent = "No equity history yet";
    return;
  }
  const first = S.equity[0].value;
  const last = S.equity[S.equity.length - 1].value;
  let peak = -Infinity;
  let maxDd = 0;
  for (const p of S.equity) {
    peak = Math.max(peak, p.value);
    if (peak > 0) maxDd = Math.max(maxDd, (peak - p.value) / peak);
  }
  const chg = first ? (last / first - 1) * 100 : NaN;
  node.innerHTML = `${esc(fmtUsd(last))} · <span class="${signClass(chg)}">${esc(fmtPct(chg))}</span> since ${esc(fmtTime(S.equity[0].time, { date: true }))} · max DD ${esc(fmtPct(-maxDd * 100, { sign: false }))}`;
}

// =====================================================================================
// Order ticket
// =====================================================================================
function setSide(side) {
  S.side = side;
  $$("#ot-side button").forEach((b) => b.classList.toggle("active", b.dataset.side === side));
  const btn = $("#ot-submit");
  btn.textContent = side === "buy" ? "Buy" : "Sell / close position";
  btn.className = `btn btn-block ${side === "buy" ? "btn-buy" : "btn-sell"}`;
  const qty = $("#ot-qty");
  qty.disabled = side === "sell";
  qty.placeholder = side === "sell" ? "Closes the full position" : "Auto (risk-sized)";
  if (side === "sell") qty.value = "";
  showMsg($("#ot-msg"), "");
  updateOrderEstimate();
}

function updateOrderEstimate() {
  const node = $("#ot-est");
  if (!node) return;
  const sym = S.active;
  const price = S.prices[sym];
  if (!sym || !isNum(price)) {
    node.textContent = "—";
    return;
  }
  const pos = (S.account?.positions || []).find((p) => p.symbol === sym);
  if (S.side === "sell") {
    node.textContent = pos ? `Sell ${fmtQty(pos.qty)} @ ~${fmtPrice(price)} ≈ ${fmtUsd(pos.qty * price)}` : `No open ${sym} position`;
    return;
  }
  const raw = $("#ot-qty").value.trim();
  if (raw) {
    const q = Number(raw);
    node.textContent = isNum(q) && q > 0 ? `${fmtQty(q)} @ ~${fmtPrice(price)} ≈ ${fmtUsd(q * price)}` : "Enter a positive quantity";
    return;
  }
  const pct = S.risk?.max_position_pct;
  const eq = S.account?.equity;
  if (isNum(pct) && isNum(eq)) {
    const notional = Math.min(pct * eq, S.account?.cash ?? Infinity);
    node.textContent = `Auto: ${fmtPct(pct * 100, { digits: 1, sign: false })} of equity ≈ ${fmtUsd(notional)} (${fmtQty(notional / price)})`;
  } else {
    node.textContent = `Last ${fmtPrice(price)}`;
  }
}

async function submitOrder() {
  const msg = $("#ot-msg");
  showMsg(msg, "");
  const sym = S.active;
  if (!sym) return;
  let qty = null;
  if (S.side === "buy") {
    const raw = $("#ot-qty").value.trim();
    if (raw !== "") {
      qty = Number(raw);
      if (!isNum(qty) || qty <= 0) {
        showMsg(msg, "Quantity must be a positive number, or blank to auto-size by risk.");
        return;
      }
    }
  }
  const btn = $("#ot-submit");
  const label = btn.textContent;
  btn.disabled = true;
  btn.replaceChildren(el("span", { class: "spinner" }), "Submitting…");
  try {
    const fill = await api("/api/orders", { method: "POST", body: { symbol: sym, side: S.side, qty } });
    showMsg(msg, `Filled: ${String(fill.side || S.side).toUpperCase()} ${fmtQty(fill.qty)} ${sym} @ ${fmtPrice(fill.price)}`
      + (fill.pnl != null ? ` · PnL ${fmtUsd(fill.pnl, { sign: true })}` : ""), "ok");
    $("#ot-qty").value = "";
    if (!S.wsUp) addTrade(fill, { live: true });
  } catch (err) {
    showMsg(msg, err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
    updateOrderEstimate();
  }
}

// =====================================================================================
// Strategy parameter forms (shared by sidebar Strategy panel and Backtest form)
// =====================================================================================
function findStrategy(name) {
  return S.strategies.find((s) => s.name === name) || null;
}

function prettyKey(key) {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function fillStrategySelect(select) {
  const current = select.value;
  select.replaceChildren(...S.strategies.map((s) => el("option", { value: s.name, text: s.name })));
  if (current && findStrategy(current)) select.value = current;
}

function buildParams(container, strategy, values, onInput) {
  container.replaceChildren();
  if (!strategy) return;
  for (const [key, def] of Object.entries(strategy.params || {})) {
    const value = values && values[key] !== undefined ? values[key] : def;
    const id = `${container.id}-${key}`;
    if (typeof def === "boolean") {
      const input = el("input", { type: "checkbox", id, checked: !!value, dataset: { key, kind: "bool" } });
      if (onInput) input.addEventListener("change", onInput);
      container.append(el("label", { class: "field field-check chk", for: id }, input, prettyKey(key)));
    } else if (typeof def === "number") {
      const input = el("input", { type: "number", id, step: "any", value: String(value), dataset: { key, kind: "num" } });
      if (onInput) input.addEventListener("input", onInput);
      container.append(el("div", { class: "field" }, el("label", { for: id, text: prettyKey(key) }), input));
    } else {
      const input = el("input", { type: "text", id, value: value == null ? "" : String(value), dataset: { key, kind: "str" } });
      if (onInput) input.addEventListener("input", onInput);
      container.append(el("div", { class: "field" }, el("label", { for: id, text: prettyKey(key) }), input));
    }
  }
}

function readParams(container) {
  const out = {};
  for (const input of $$("input[data-key]", container)) {
    const { key, kind } = input.dataset;
    input.classList.remove("invalid");
    if (kind === "bool") out[key] = input.checked;
    else if (kind === "num") {
      const v = Number(input.value);
      if (input.value.trim() === "" || !isNum(v)) {
        input.classList.add("invalid");
        throw new Error(`${prettyKey(key)} must be a number`);
      }
      out[key] = v;
    } else out[key] = input.value;
  }
  return out;
}

// ---- sidebar Strategy panel
let stratDirty = false;

function renderStrategyPanel(force = false) {
  if (stratDirty && !force) return;
  const select = $("#strat-select");
  fillStrategySelect(select);
  const sym = S.active;
  const cfg = sym ? S.engine.strategies?.[sym] : null;
  const name = cfg && findStrategy(cfg.name) ? cfg.name : S.strategies[0]?.name;
  if (!name) {
    $("#strat-params").replaceChildren();
    $("#strat-desc").textContent = S.strategies.length ? "" : "Loading strategies…";
    return;
  }
  select.value = name;
  const strat = findStrategy(name);
  $("#strat-desc").textContent = strat?.description || "";
  buildParams($("#strat-params"), strat, cfg && cfg.name === name ? cfg.params : null, () => { stratDirty = true; });
}

function onStrategySelect() {
  stratDirty = true;
  const name = $("#strat-select").value;
  const strat = findStrategy(name);
  const cfg = S.engine.strategies?.[S.active];
  $("#strat-desc").textContent = strat?.description || "";
  buildParams($("#strat-params"), strat, cfg && cfg.name === name ? cfg.params : null, () => { stratDirty = true; });
  showMsg($("#strat-msg"), "");
}

function resetStrategyDefaults() {
  const strat = findStrategy($("#strat-select").value);
  buildParams($("#strat-params"), strat, null, () => { stratDirty = true; });
  stratDirty = true;
}

async function saveStrategy() {
  const msg = $("#strat-msg");
  showMsg(msg, "");
  const sym = S.active;
  const name = $("#strat-select").value;
  if (!sym || !name) return;
  let params;
  try {
    params = readParams($("#strat-params"));
  } catch (err) {
    showMsg(msg, err.message);
    return;
  }
  const btn = $("#strat-save");
  btn.disabled = true;
  try {
    const strategies = { ...(S.engine.strategies || {}), [sym]: { name, params } };
    const cfg = await api("/api/engine", { method: "PUT", body: { strategies } });
    stratDirty = false;
    applyEngine(cfg);
    showMsg(msg, `${sym} now runs ${name}`, "ok");
    setTimeout(() => showMsg(msg, ""), 3500);
  } catch (err) {
    showMsg(msg, err.message);
  } finally {
    btn.disabled = false;
  }
}

// =====================================================================================
// Risk limits form
// =====================================================================================
const RISK_FIELDS = [
  { key: "max_position_pct", label: "Max position", pct: true },
  { key: "max_open_positions", label: "Max open positions", int: true },
  { key: "stop_loss_pct", label: "Stop loss", pct: true },
  { key: "take_profit_pct", label: "Take profit", pct: true },
  { key: "trailing_stop_pct", label: "Trailing stop", pct: true },
  { key: "daily_loss_limit_pct", label: "Daily loss limit", pct: true },
];
let riskDirty = false;

function buildRiskForm() {
  const form = $("#risk-form");
  form.replaceChildren();
  for (const f of RISK_FIELDS) {
    const input = el("input", {
      type: "number", id: `risk-${f.key}`, name: f.key, min: "0", step: f.int ? "1" : "any",
      max: f.pct ? "100" : null, dataset: { key: f.key },
    });
    input.addEventListener("input", () => {
      riskDirty = true;
      input.classList.add("dirty");
    });
    const control = f.pct ? el("div", { class: "input-suffix" }, input, el("span", { text: "%" })) : input;
    form.append(el("div", { class: "field" }, el("label", { for: input.id, text: f.label }), control));
  }
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    saveRisk();
  });
}

function fillRiskForm(force = false) {
  if (!S.risk || (riskDirty && !force)) return;
  for (const f of RISK_FIELDS) {
    const input = $(`#risk-${f.key}`);
    const v = S.risk[f.key];
    input.classList.remove("dirty", "invalid");
    input.value = !isNum(v) ? "" : f.pct ? String(Number((v * 100).toFixed(4))) : String(v);
  }
  riskDirty = false;
}

function applyRisk(limits) {
  if (!limits || typeof limits !== "object") return;
  S.risk = limits;
  fillRiskForm();
  updateOrderEstimate();
}

async function saveRisk() {
  const msg = $("#risk-msg");
  showMsg(msg, "");
  const body = {};
  for (const f of RISK_FIELDS) {
    const input = $(`#risk-${f.key}`);
    input.classList.remove("invalid");
    const raw = input.value.trim();
    const v = Number(raw);
    if (raw === "" || !isNum(v) || v < 0 || (f.int && !Number.isInteger(v)) || (f.pct && v > 100)) {
      input.classList.add("invalid");
      showMsg(msg, `${f.label}: ${f.int ? "enter a whole number ≥ 0" : "enter a percentage between 0 and 100"}`);
      return;
    }
    body[f.key] = f.pct ? Number((v / 100).toFixed(8)) : v;
  }
  const btn = $("#risk-save");
  btn.disabled = true;
  try {
    const limits = await api("/api/risk", { method: "PUT", body });
    riskDirty = false;
    applyRisk(limits);
    fillRiskForm(true);
    showMsg(msg, "Risk limits saved", "ok");
    setTimeout(() => showMsg(msg, ""), 3000);
  } catch (err) {
    showMsg(msg, err.message);
  } finally {
    btn.disabled = false;
  }
}

// =====================================================================================
// AI analysis card
// =====================================================================================
function renderAI() {
  const body = $("#ai-body");
  const sym = S.active;
  const btn = $("#ai-run");
  const busy = sym && S.analyzing.has(sym);
  btn.disabled = !sym || busy;
  btn.replaceChildren(...(busy ? [el("span", { class: "spinner" }), "Analyzing…"] : ["Analyze now"]));
  const a = sym ? S.analyses[sym] : null;
  if (!a) {
    body.replaceChildren(el("div", { class: "ai-empty", text: busy ? `Asking the model about ${sym}…` : `No analysis for ${sym || "this symbol"} yet. Click “Analyze now”.` }));
    return;
  }
  const bias = ["bullish", "bearish", "neutral"].includes(a.bias) ? a.bias : "neutral";
  const rec = ["buy", "sell", "hold"].includes(a.recommendation) ? a.recommendation : "hold";
  const conf = isNum(a.confidence) ? Math.max(0, Math.min(1, a.confidence)) : 0;
  const levels = a.key_levels || {};
  const source = String(a.source || "unknown");
  body.innerHTML =
    `<div class="ai-pills">`
    + `<span class="pill pill-${esc(bias)}">${esc(bias)}</span>`
    + `<span class="pill pill-${esc(rec)}">${esc(rec)}</span>`
    + `</div>`
    + `<div class="conf"><span>Confidence</span><div class="conf-bar"><div class="conf-fill" style="width:${(conf * 100).toFixed(0)}%"></div></div><span class="mono">${esc((conf * 100).toFixed(0))}%</span></div>`
    + `<p class="ai-summary">${esc(a.summary || "")}</p>`
    + `<div class="ai-levels">`
    + `<div class="ai-level"><span>Support</span><span class="mono up">${esc(fmtPrice(levels.support))}</span></div>`
    + `<div class="ai-level"><span>Resistance</span><span class="mono down">${esc(fmtPrice(levels.resistance))}</span></div>`
    + `</div>`
    + `<div class="ai-meta"><span title="${esc(source)}">${esc(source === "rules" ? "rule-based fallback" : source)}${isNum(a.latency_ms) ? ` · ${esc(a.latency_ms)} ms` : ""}</span>`
    + `<span title="${esc(fmtTime(a.ts, { date: true }))}">${esc(fmtAgo(a.ts))}</span></div>`;
}

async function loadLatestAnalysis(sym) {
  if (S.analyses[sym]) return;
  try {
    const list = await api(`/api/analyses?symbol=${encodeURIComponent(sym)}&limit=1`);
    if (Array.isArray(list) && list[0] && !S.analyses[sym]) {
      S.analyses[sym] = list[0];
      if (sym === S.active) renderAI();
    }
  } catch (err) {
    console.warn("analysis load failed", err);
  }
}

function applyAnalysis(a) {
  if (!a || !a.symbol) return;
  const prev = S.analyses[a.symbol];
  if (prev && toSec(prev.ts) > toSec(a.ts)) return;
  S.analyses[a.symbol] = a;
  if (a.symbol === S.active) renderAI();
}

async function runAnalysis() {
  const sym = S.active;
  if (!sym || S.analyzing.has(sym)) return;
  S.analyzing.add(sym);
  renderAI();
  try {
    const a = await api(`/api/ai/analyze/${encodeURIComponent(sym)}`, { method: "POST" });
    applyAnalysis(a);
  } catch (err) {
    toast(err.message, "error", `AI analysis for ${sym} failed`);
  } finally {
    S.analyzing.delete(sym);
    if (sym === S.active) renderAI();
  }
}

// =====================================================================================
// Backtest
// =====================================================================================
const BT = { price: null, candle: null, equity: null, strat: null, bh: null, running: false, timer: null, needsFit: false };

function initBacktestForm() {
  const select = $("#bt-strategy");
  fillStrategySelect(select);
  const rebuild = () => buildParams($("#bt-params"), findStrategy(select.value), null);
  select.onchange = rebuild;
  if (!$("#bt-params").children.length) rebuild();
  $("#bt-symbols").replaceChildren(...S.symbols.map((s) => el("option", { value: s })));
  if (!$("#bt-symbol").value && S.active) $("#bt-symbol").value = S.active;
}

function ensureBacktestCharts() {
  if (BT.price) return;
  BT.price = makeChart($("#bt-price"), { rightPriceScale: { scaleMargins: { top: 0.08, bottom: 0.08 } } });
  BT.candle = BT.price.addCandlestickSeries({
    upColor: COLORS.up, downColor: COLORS.down, borderVisible: false, wickUpColor: COLORS.up, wickDownColor: COLORS.down,
  });
  BT.equity = makeChart($("#bt-equity"), { rightPriceScale: { scaleMargins: { top: 0.08, bottom: 0.08 } } });
  BT.bh = BT.equity.addLineSeries(lineOpts(COLORS.bh, { lineWidth: 1, lineStyle: LC.LineStyle.Dashed, priceFormat: { type: "price", precision: 2, minMove: 0.01 } }));
  BT.strat = BT.equity.addLineSeries(lineOpts(COLORS.accent, { lineWidth: 2, lastValueVisible: true, priceFormat: { type: "price", precision: 2, minMove: 0.01 } }));
}

async function runBacktest(e) {
  e.preventDefault();
  if (BT.running) return;
  const msg = $("#bt-msg");
  showMsg(msg, "");
  const symbol = $("#bt-symbol").value.trim().toUpperCase();
  const interval = $("#bt-interval").value;
  const bars = Number($("#bt-bars").value);
  const strategy = $("#bt-strategy").value;
  const cash = Number($("#bt-cash").value);
  if (!symbol) return showMsg(msg, "Enter a symbol, e.g. SPY or BTCUSDT");
  if (!Number.isInteger(bars) || bars < 50 || bars > 5000) return showMsg(msg, "Bars must be a whole number between 50 and 5000");
  if (!isNum(cash) || cash <= 0) return showMsg(msg, "Starting cash must be positive");
  if (!strategy) return showMsg(msg, "Pick a strategy");
  let params;
  try {
    params = readParams($("#bt-params"));
  } catch (err) {
    return showMsg(msg, err.message);
  }
  const risk = {};
  for (const [id, key] of [["#bt-sl", "stop_loss_pct"], ["#bt-tp", "take_profit_pct"]]) {
    const raw = $(id).value.trim();
    if (raw === "") continue;
    const v = Number(raw);
    if (!isNum(v) || v < 0 || v > 100) return showMsg(msg, "Stop loss / take profit must be percentages between 0 and 100");
    risk[key] = v / 100;
  }
  const body = { symbol, interval, bars, strategy, params, cash };
  if (Object.keys(risk).length) body.risk = risk;

  BT.running = true;
  const btn = $("#bt-run");
  btn.disabled = true;
  btn.replaceChildren(el("span", { class: "spinner" }), "Running…");
  $("#bt-placeholder").hidden = true;
  $("#bt-loading").hidden = false;
  const started = Date.now();
  const text = $("#bt-loading-text");
  text.textContent = `Fetching ${bars} × ${interval} bars of ${symbol} and simulating ${strategy}…`;
  BT.timer = setInterval(() => {
    text.textContent = `Fetching ${bars} × ${interval} bars of ${symbol} and simulating ${strategy}… ${Math.round((Date.now() - started) / 1000)} s`;
  }, 1000);
  try {
    const res = await api("/api/backtest", { method: "POST", body });
    renderBacktest(res, body);
  } catch (err) {
    showMsg(msg, err.message);
    if ($("#bt-output").hidden) $("#bt-placeholder").hidden = false;
  } finally {
    clearInterval(BT.timer);
    BT.running = false;
    btn.disabled = false;
    btn.textContent = "Run backtest";
    $("#bt-loading").hidden = true;
  }
}

function mcard(label, value, cls = "", sub = "") {
  return el("div", { class: "mcard" },
    el("span", { class: "mcard-label", text: label }),
    el("span", { class: `mcard-value ${cls}`, text: value }),
    sub ? el("span", { class: "mcard-sub", text: sub }) : null);
}

function renderBacktest(res, req) {
  const m = res.metrics || {};
  const candles = Array.isArray(res.candles) ? res.candles : [];
  const trades = Array.isArray(res.trades) ? res.trades : [];
  const alpha = isNum(m.total_return_pct) && isNum(m.buy_hold_return_pct) ? m.total_return_pct - m.buy_hold_return_pct : NaN;
  const dd = isNum(m.max_drawdown_pct) ? -Math.abs(m.max_drawdown_pct) : NaN;
  let pf = "—";
  if (isNum(m.profit_factor)) pf = fmtNum(m.profit_factor, 2);
  else if (m.profit_factor == null && (m.num_trades || 0) > 0 && (m.win_rate_pct || 0) > 0) pf = "∞";

  $("#bt-metrics").replaceChildren(
    mcard("Total return", fmtPct(m.total_return_pct), signClass(m.total_return_pct), `${req.symbol} · ${req.interval} · ${candles.length} bars`),
    mcard("Buy & hold", fmtPct(m.buy_hold_return_pct), signClass(m.buy_hold_return_pct), `Alpha ${fmtPct(alpha)}`),
    mcard("Max drawdown", fmtPct(dd, { sign: false }), dd < 0 ? "down" : ""),
    mcard("Sharpe", fmtNum(m.sharpe, 2), signClass(m.sharpe), "annualised"),
    mcard("Win rate", fmtPct(m.win_rate_pct, { digits: 1, sign: false })),
    mcard("Profit factor", pf),
    mcard("Trades", isNum(m.num_trades) ? String(m.num_trades) : "—"),
    mcard("Final equity", fmtUsd(m.final_equity), signClass((m.final_equity ?? 0) - req.cash), `from ${fmtUsd(req.cash)}`),
  );

  $("#bt-output").hidden = false;
  $("#bt-placeholder").hidden = true;
  ensureBacktestCharts();

  const last = candles[candles.length - 1];
  BT.candle.applyOptions({ priceFormat: priceFormatFor(last ? last.close : 1) });
  BT.candle.setData(candles.map(candleToBar));
  const markers = (Array.isArray(res.markers) ? res.markers : [])
    .filter((mk) => isNum(mk.time))
    .map((mk) => ({
      time: mk.time,
      position: mk.position === "aboveBar" ? "aboveBar" : "belowBar",
      color: mk.color || (mk.position === "aboveBar" ? COLORS.down : COLORS.up),
      shape: mk.shape === "arrowDown" ? "arrowDown" : "arrowUp",
      text: mk.text == null ? "" : String(mk.text),
    }))
    .sort((a, b) => a.time - b.time);
  BT.candle.setMarkers(markers);
  BT.price.timeScale().fitContent();

  const curve = sanitizeEquity(res.equity_curve);
  BT.strat.setData(curve);
  const base = candles.length ? candles[0].close : null;
  const bh = base ? sanitizeEquity(candles.map((c) => ({ time: c.time, value: (req.cash * c.close) / base }))) : [];
  BT.bh.setData(bh);
  BT.equity.timeScale().fitContent();
  BT.needsFit = S.bottomTab !== "backtest";

  $("#bt-trades").replaceChildren(...trades.map((t) => {
    const tr = el("tr");
    const side = t.side === "buy" ? "buy" : "sell";
    tr.innerHTML =
      `<td>${esc(fmtTime(t.time ?? t.ts, { date: true }))}</td>`
      + `<td><span class="side side-${side}">${esc(String(t.side || "").toUpperCase())}</span></td>`
      + `<td class="r">${esc(fmtQty(t.qty))}</td>`
      + `<td class="r">${esc(fmtPrice(t.price))}</td>`
      + `<td class="r">${esc(fmtUsd(t.fee))}</td>`
      + `<td class="r ${signClass(t.pnl)}">${t.pnl == null ? "—" : esc(fmtUsd(t.pnl, { sign: true }))}</td>`
      + `<td class="txt reason">${esc(t.reason || "")}</td>`;
    return tr;
  }));
  if (!trades.length) $("#bt-trades").append(el("tr", null, el("td", { colspan: "7", class: "txt reason", text: "No trades were triggered in this period." })));
}

// =====================================================================================
// Account tab
// =====================================================================================
function renderAccountTab() {
  const a = S.account;
  if (!a) return;
  $("#acct-summary").replaceChildren(
    mcard("Equity", fmtUsd(a.equity)),
    mcard("Cash", fmtUsd(a.cash)),
    mcard("Starting cash", fmtUsd(a.starting_cash)),
    mcard("Total return", fmtPct(a.total_return_pct), signClass(a.total_return_pct)),
    mcard("Realized PnL", fmtUsd(a.realized_pnl, { sign: true }), signClass(a.realized_pnl)),
    mcard("Unrealized PnL", fmtUsd(a.unrealized_pnl, { sign: true }), signClass(a.unrealized_pnl)),
    mcard("Fees paid", fmtUsd(a.fees_paid)),
    mcard("Open positions", String((a.positions || []).length)),
  );
  $("#acct-status-text").textContent = a.halted
    ? `Trading is HALTED${a.halt_reason ? `: ${a.halt_reason}` : ""}. New entries are blocked until you resume.`
    : "Trading is active. The daily loss limit halts new entries automatically when breached.";
  $("#acct-resume").hidden = !a.halted;
  const cashInput = $("#reset-cash");
  if (!cashInput.value && isNum(a.starting_cash)) cashInput.value = String(a.starting_cash);
}

let resetArmTimer = null;
function disarmReset() {
  const btn = $("#reset-btn");
  clearTimeout(resetArmTimer);
  btn.classList.remove("confirming");
  btn.dataset.armed = "";
  btn.textContent = "Reset account";
}

async function onResetClick() {
  const btn = $("#reset-btn");
  const msg = $("#reset-msg");
  const raw = $("#reset-cash").value.trim();
  const cash = raw === "" ? null : Number(raw);
  if (cash !== null && (!isNum(cash) || cash <= 0)) {
    showMsg(msg, "Starting cash must be a positive number");
    return;
  }
  if (btn.dataset.armed !== "1") {
    showMsg(msg, "");
    btn.dataset.armed = "1";
    btn.classList.add("confirming");
    btn.textContent = `Confirm reset to ${cash == null ? "default" : fmtUsd(cash)}`;
    clearTimeout(resetArmTimer);
    resetArmTimer = setTimeout(disarmReset, 5000);
    return;
  }
  disarmReset();
  btn.disabled = true;
  try {
    const account = await api("/api/account/reset", { method: "POST", body: cash == null ? {} : { cash } });
    applyAccount(account);
    showMsg(msg, `Account reset to ${fmtUsd(account?.starting_cash ?? cash)}`, "ok");
    toast("Paper account reset", "success");
    await Promise.all([loadEquity(), loadTrades()]);
  } catch (err) {
    showMsg(msg, err.message);
  } finally {
    btn.disabled = false;
  }
}

async function resumeTrading(btn) {
  btn.disabled = true;
  try {
    const account = await api("/api/account/resume", { method: "POST" });
    applyAccount(account);
    toast("Trading resumed", "success");
  } catch (err) {
    toast(err.message, "error", "Resume failed");
  } finally {
    btn.disabled = false;
  }
}

// =====================================================================================
// Bottom tabs
// =====================================================================================
function showBottomTab(name) {
  if (!$(`#tab-${name}`)) name = "positions";
  S.bottomTab = name;
  storage.set("bottomTab", name);
  $$(".tabbar .tab").forEach((b) => {
    const on = b.dataset.tab === name;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  $$(".tab-pane").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "equity" && EQ.chart && !EQ.fitted) {
    requestAnimationFrame(() => {
      EQ.chart.timeScale().fitContent();
      EQ.fitted = true;
    });
  }
  if (name === "backtest") {
    initBacktestForm();
    if (BT.price && BT.needsFit) {
      requestAnimationFrame(() => {
        BT.price.timeScale().fitContent();
        BT.equity.timeScale().fitContent();
        BT.needsFit = false;
      });
    }
  }
}

// =====================================================================================
// State application (REST /api/state and WS snapshot)
// =====================================================================================
function applyState(st) {
  if (!st || typeof st !== "object") return;
  if (st.interval) {
    S.interval = st.interval;
    S.ivSec = intervalSeconds(st.interval);
  }
  if (st.prices && typeof st.prices === "object") {
    for (const [sym, p] of Object.entries(st.prices)) if (isNum(p)) S.prices[sym] = p;
  }
  if (st.status) applyStatus(st.status);
  if (st.risk) applyRisk(st.risk);
  if (st.engine) applyEngine(st.engine);
  if (st.account) applyAccount(st.account);
  const symbols = Array.isArray(st.symbols) ? st.symbols : [];
  const changed = symbols.join(",") !== S.symbols.join(",");
  if (changed) {
    S.symbols = symbols;
    renderTabs();
    $("#bt-symbols").replaceChildren(...S.symbols.map((s) => el("option", { value: s })));
  }
  if (!S.active || !S.symbols.includes(S.active)) {
    const pref = storage.get("symbol", null);
    const target = pref && S.symbols.includes(pref) ? pref : S.symbols[0];
    if (target) selectSymbol(target, { force: true });
  } else {
    for (const sym of S.symbols) updateTab(sym);
  }
}

function applyStatus(st) {
  if (!st || typeof st !== "object") return;
  S.status = { ...S.status, ...st };
  renderStatus();
}

// =====================================================================================
// WebSocket client
// =====================================================================================
const WS = { sock: null, attempts: 0, pingTimer: null, retryTimer: null, snapshots: 0, lastMsg: 0 };

function wsUrl() {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
}

function setWsUp(up) {
  S.wsUp = up;
  renderStatus();
}

function connectWS() {
  clearTimeout(WS.retryTimer);
  if (WS.sock && (WS.sock.readyState === WebSocket.OPEN || WS.sock.readyState === WebSocket.CONNECTING)) return;
  let sock;
  try {
    sock = new WebSocket(wsUrl());
  } catch (err) {
    console.warn("WebSocket construct failed", err);
    scheduleReconnect();
    return;
  }
  WS.sock = sock;
  sock.onopen = () => {
    WS.attempts = 0;
    WS.lastMsg = Date.now();
    setWsUp(true);
    clearInterval(WS.pingTimer);
    WS.pingTimer = setInterval(() => {
      if (sock.readyState !== WebSocket.OPEN) return;
      if (Date.now() - WS.lastMsg > 65000) {
        // nothing (not even a pong) for a long time: the connection is dead
        try { sock.close(); } catch { /* ignore */ }
        return;
      }
      try {
        sock.send(JSON.stringify({ type: "ping" }));
      } catch { /* close handler will reconnect */ }
    }, 20000);
  };
  sock.onmessage = (ev) => {
    WS.lastMsg = Date.now();
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    try {
      handleMessage(msg);
    } catch (err) {
      console.error("WS handler error", msg && msg.type, err);
    }
  };
  sock.onclose = () => {
    if (WS.sock !== sock) return;
    WS.sock = null;
    clearInterval(WS.pingTimer);
    setWsUp(false);
    scheduleReconnect();
  };
  sock.onerror = () => {
    try { sock.close(); } catch { /* ignore */ }
  };
}

function scheduleReconnect() {
  clearTimeout(WS.retryTimer);
  const delay = Math.min(15000, 500 * 2 ** WS.attempts) + Math.random() * 400;
  WS.attempts = Math.min(WS.attempts + 1, 10);
  WS.retryTimer = setTimeout(connectWS, delay);
}

function handleMessage(msg) {
  if (!msg || typeof msg !== "object") return;
  const { type, data } = msg;
  switch (type) {
    case "snapshot": {
      const reconnect = WS.snapshots > 0;
      WS.snapshots += 1;
      applyState(data);
      if (reconnect) resyncAfterReconnect();
      break;
    }
    case "tick": onTick(data); break;
    case "account": applyAccount(data); break;
    case "trade": addTrade(data, { live: true }); break;
    case "log": addLog(data); break;
    case "analysis": applyAnalysis(data); break;
    case "engine": applyEngine(data); break;
    case "risk": applyRisk(data); break;
    case "status": applyStatus(data); break;
    case "pong": break;
    default: break;
  }
}

/** After a reconnect, refetch everything that may have been missed while offline. */
async function resyncAfterReconnect() {
  const tasks = [loadTrades(), loadLogs(), loadEquity()];
  if (S.active) tasks.push(loadChart(S.active));
  await Promise.all(tasks);
}

// =====================================================================================
// Boot
// =====================================================================================
function bindUI() {
  $("#tg-auto").addEventListener("change", (e) => toggleEngine("auto_trading", e.target));
  $("#tg-ai").addEventListener("change", (e) => toggleEngine("ai_filter", e.target));
  $("#btn-resume").addEventListener("click", (e) => resumeTrading(e.currentTarget));
  $("#acct-resume").addEventListener("click", (e) => resumeTrading(e.currentTarget));

  for (const [id, key] of [["#ov-sma20", "sma20"], ["#ov-sma50", "sma50"], ["#ov-bb", "bb"]]) {
    $(id).addEventListener("change", (e) => {
      S.overlays[key] = e.target.checked;
      storage.set("overlays", S.overlays);
      applyOverlays();
    });
  }
  $$("#lower-toggle button").forEach((b) => b.addEventListener("click", () => {
    S.lowerPane = b.dataset.pane;
    storage.set("lowerPane", S.lowerPane);
    applyLowerPane();
  }));

  $$("#ot-side button").forEach((b) => b.addEventListener("click", () => setSide(b.dataset.side)));
  $("#ot-qty").addEventListener("input", updateOrderEstimate);
  $("#ot-qty").addEventListener("keydown", (e) => { if (e.key === "Enter") submitOrder(); });
  $("#ot-submit").addEventListener("click", submitOrder);

  $("#ai-run").addEventListener("click", runAnalysis);

  $("#strat-select").addEventListener("change", onStrategySelect);
  $("#strat-defaults").addEventListener("click", resetStrategyDefaults);
  $("#strat-save").addEventListener("click", saveStrategy);

  buildRiskForm();
  $("#risk-save").addEventListener("click", saveRisk);
  $("#risk-revert").addEventListener("click", () => {
    fillRiskForm(true);
    showMsg($("#risk-msg"), "");
  });

  $$(".tabbar .tab").forEach((b) => b.addEventListener("click", () => showBottomTab(b.dataset.tab)));
  $("#bottom-expand").addEventListener("click", () => {
    const on = document.body.classList.toggle("bottom-expanded");
    $("#bottom-expand").title = on ? "Collapse panel" : "Expand panel";
    storage.set("bottomExpanded", on);
  });
  if (storage.get("bottomExpanded", false)) document.body.classList.add("bottom-expanded");

  $("#bt-form").addEventListener("submit", runBacktest);
  $("#reset-btn").addEventListener("click", onResetClick);
  $("#reset-btn").addEventListener("blur", () => setTimeout(disarmReset, 150));

  window.addEventListener("online", () => { if (!S.wsUp) connectWS(); });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && !S.wsUp) {
      WS.attempts = 0;
      connectWS();
    }
  });
  // keep relative times in the AI card fresh
  setInterval(() => { if (S.active && S.analyses[S.active]) renderAI(); }, 30000);
}

async function loadInitial() {
  try {
    const [state, strategies] = await Promise.all([api("/api/state"), api("/api/strategies")]);
    S.strategies = Array.isArray(strategies) ? strategies : [];
    renderStrategyPanel(true);
    fillStrategySelect($("#bt-strategy"));
    applyState(state);
    await Promise.all([loadTrades(), loadLogs(), loadEquity()]);
    initBacktestForm();
    loadRefPrices();
  } catch (err) {
    toast(`${err.message}. Retrying…`, "error", "Backend unavailable");
    setTimeout(loadInitial, 4000);
  }
}

function boot() {
  if (!LC) {
    document.body.prepend(el("div", { class: "form-msg error", text: "Charting library failed to load (unpkg.com unreachable?). Reload to retry." }));
    return;
  }
  loadColors();
  bindUI();
  initMainCharts();
  initEquityChart();
  setSide("buy");
  showBottomTab(S.bottomTab);
  renderStatus();
  renderAI();
  connectWS();
  loadInitial();
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();
