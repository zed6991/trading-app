// Quant Lab: stats, correlation, portfolio mixer, luck test and backtester (with walk-forward). Daily data.
const LC = window.LightweightCharts;
const $ = (id) => document.getElementById(id);
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
const TABS = ["stats", "corr", "mix", "luck", "backtest"];
const TAB_LEVEL = { stats: 4, corr: 5, mix: 5, luck: 6, backtest: 7 };
const LAB_RISK = { max_position_pct: 1, stop_loss_pct: 0, take_profit_pct: 0, trailing_stop_pct: 0, daily_loss_limit_pct: 0 };
const STRAT_NAMES = {
  sma_cross: "Moving-average crossover (trend)", ema_macd: "MACD with trend filter (trend)",
  rsi_reversion: "RSI dip-buying (mean reversion)", bollinger_breakout: "Bollinger breakout (trend)",
  momentum: "Momentum",
};
const PARAM_NAMES = {
  fast: "Fast average (bars)", slow: "Slow average (bars)", signal: "Signal line (bars)", trend: "Trend average (bars)",
  period: "Lookback (bars)", oversold: "Buy below RSI", overbought: "Sell above RSI", num_std: "Band width (std devs)",
  lookback: "Lookback (bars)", threshold: "Min. rise to buy (0.05 = 5%)",
};
const COLORS = ["#3b82f6", "#f0b90b", "#16c784", "#f0465a", "#c084fc", "#38bdf8", "#fb923c", "#a3e635", "#e879f9"];

let assets = [];
let strategies = [];
let tab = null;
const charts = {}; // container id -> chart

// ---------------------------------------------------------------- helpers
async function api(path, method = "GET", body) {
  const res = await fetch(path, body === undefined ? { method }
    : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Something went wrong");
  return data;
}
const level = () => Number(document.body.dataset.level) || 1;
const unlocked = (t) => document.body.classList.contains(`unlock-${TAB_LEVEL[t]}`) || TAB_LEVEL[t] <= 1;
const nameOf = (s) => assets.find((a) => a.symbol === s)?.name || s;
const isCrypto = (s) => assets.find((a) => a.symbol === s)?.kind === "crypto";
const money = (n) => `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: Math.abs(n) < 100 ? 2 : 0 })}`;
const pct = (n, d = 1) => (n === null || n === undefined ? "–" : `${n > 0 ? "+" : ""}${Number(n).toFixed(d)}%`);
const num = (n, d = 2) => (n === null || n === undefined ? "–" : Number(n).toFixed(d));
const tone = (n) => (n > 0 ? "up" : n < 0 ? "down" : "");
const day = (t) => new Date(t * 1000).toISOString().slice(0, 10);
const learnRefresh = () => window.dispatchEvent(new Event("learn:refresh"));
const term = (slug, text) => `<span class="term" data-term="${slug}" tabindex="0" role="button">${text}</span>`;

function storageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function storageSet(k, v) { try { localStorage.setItem(k, v); } catch { /* preference only */ } }

function assetOptions(selected) {
  const group = (label, list) => `<optgroup label="${label}">${list.map((a) =>
    `<option value="${a.symbol}" ${a.symbol === selected ? "selected" : ""}>${esc(a.name)} (${esc(a.symbol.replace(/USDT$/, ""))})</option>`).join("")}</optgroup>`;
  return group("Stocks and ETFs", assets.filter((a) => a.kind !== "crypto")) + group("Crypto", assets.filter((a) => a.kind === "crypto"));
}

function strategyOptions(selected) {
  return strategies.map((s) => `<option value="${s.name}" ${s.name === selected ? "selected" : ""}>${esc(STRAT_NAMES[s.name] || s.name)}</option>`).join("");
}

function paramInputs(name, prefix) {
  const s = strategies.find((x) => x.name === name);
  if (!s) return "";
  return Object.entries(s.params).map(([k, v]) => `<label>${esc(PARAM_NAMES[k] || k)}
    <input type="number" step="any" data-param="${k}" id="${prefix}-p-${k}" value="${v}"></label>`).join("");
}

function readParams(root) {
  const out = {};
  root.querySelectorAll("[data-param]").forEach((i) => { if (i.value !== "") out[i.dataset.param] = Number(i.value); });
  return out;
}

function card(k, v, s = "", cls = "") {
  return `<div class="lab-card ${cls}"><div class="k">${k}</div><div class="v">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`;
}

function busy(pane, text) {
  pane.querySelector(".lab-out").innerHTML = `<p class="lab-loading">${esc(text)}</p>`;
}
function fail(pane, err) {
  pane.querySelector(".lab-out").innerHTML = `<p class="lab-msg">${esc(err.message || err)}</p>`;
}

// ---------------------------------------------------------------- charts
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function lineChart(id, series) {
  const box = $(id);
  if (!box || !LC) return;
  charts[id]?.remove();
  const chart = LC.createChart(box, {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: css("--text-dim"), fontFamily: css("--font") },
    grid: { vertLines: { color: css("--grid") }, horzLines: { color: css("--grid") } },
    rightPriceScale: { borderColor: css("--border") },
    timeScale: { borderColor: css("--border"), minBarSpacing: 0.001 },
    crosshair: { mode: 0 },
    handleScroll: false, handleScale: false,
  });
  for (const s of series) {
    const line = chart.addLineSeries({ color: s.color, lineWidth: s.width || 2, priceLineVisible: false, lastValueVisible: false,
      title: "", lineStyle: s.dashed ? 2 : 0 });
    line.setData(s.data);
  }
  chart.timeScale().fitContent();
  charts[id] = chart;
}

function legend(items) {
  return `<div class="lab-legend">${items.map((i) => `<span><i style="background:${i.color}"></i>${esc(i.label)}</span>`).join("")}</div>`;
}

/** Histogram of {label, count} bars; optional bell-curve overlay values and highlighted bars. */
function histogram(bars, { overlay = null, highlight = () => "", left = "", right = "" } = {}) {
  const max = Math.max(1, ...bars.map((b) => Math.max(b.count, overlay ? b.normal || 0 : 0)));
  const cols = bars.map((b, i) => `<div class="bar ${highlight(b, i)}" style="height:${(b.count / max) * 100}%" title="${esc(b.label)}: ${b.count}"></div>`).join("");
  let svg = "";
  if (overlay) {
    const n = bars.length;
    const pts = bars.map((b, i) => `${((i + 0.5) / n) * 100},${100 - ((b.normal || 0) / max) * 100}`).join(" ");
    svg = `<svg viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="${css("--warn")}" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg>`;
  }
  return `<div class="hist">${cols}${svg}<div class="axis"><span>${esc(left)}</span><span>${esc(right)}</span></div></div>`;
}

// ---------------------------------------------------------------- stats (level 4)
function buildStats() {
  const pane = $("lab-stats");
  pane.innerHTML = `
    <p class="lab-intro">Everything here is measured on daily ${term("return", "returns")}: how big a typical day is
      (${term("volatility", "volatility")}), how much you got for the bumps (${term("sharpe", "Sharpe")}), the worst fall
      (${term("drawdown", "max drawdown")}) and how often extreme days happen compared with the ${term("normal-distribution", "bell curve")}.</p>
    <form class="lab-form" id="st-form">
      <label>Asset <select id="st-sym">${assetOptions(storageGet("lab:st-sym") || "SPY")}</select></label>
      <label>Years <select id="st-years"><option>1</option><option>3</option><option selected>5</option><option>10</option></select></label>
      <button class="btn btn-primary" type="submit">Show stats</button>
    </form>
    <div class="lab-out"></div>`;
  pane.querySelector("#st-form").addEventListener("submit", (e) => { e.preventDefault(); runStats(); });
  pane.querySelector("#st-sym").addEventListener("change", runStats);
  pane.querySelector("#st-years").addEventListener("change", runStats);
}

async function runStats() {
  const pane = $("lab-stats");
  const sym = $("st-sym").value;
  storageSet("lab:st-sym", sym);
  busy(pane, `Loading ${$("st-years").value} years of daily prices for ${nameOf(sym)}…`);
  try {
    const s = await api(`/api/lab/stats/${sym}?years=${$("st-years").value}`);
    const hist = s.histogram.map((b) => ({ label: `${b.pct}%`, count: b.count, normal: b.normal }));
    const first = s.histogram[0].pct;
    const last = s.histogram[s.histogram.length - 1].pct;
    const next = sym === "SPY" ? "Now pick Bitcoin and compare the volatility and drawdown."
      : isCrypto(sym) ? "Compare with SPY: how much bigger is a typical day?" : "Try a very different asset, e.g. TLT (bonds) or Bitcoin.";
    pane.querySelector(".lab-out").innerHTML = `
      <div class="lab-cards">
        ${card(`$10,000 became`, money(s.example_end), `${pct(s.total_return_pct)} over ${s.years} years (${pct(s.cagr_pct)} a year, ${term("cagr", "CAGR")})`, tone(s.total_return_pct) === "up" ? "good" : "bad")}
        ${card("A typical day", `±${money(s.typical_day_dollars)}`, `on $10,000: ${num(s.vol_daily_pct)}% daily ${term("volatility", "volatility")}`)}
        ${card("Yearly volatility", `${num(s.vol_annual_pct, 1)}%`, `daily × √${s.periods_per_year} (${term("annualised", "annualised")})`)}
        ${card(term("sharpe", "Sharpe ratio"), num(s.sharpe), "yearly return ÷ yearly volatility (no risk-free rate)")}
        ${card(term("drawdown", "Max drawdown"), `-${num(s.max_drawdown_pct, 1)}%`, `${s.drawdown_peak} → ${s.drawdown_trough}: $10,000 at the top was ${money(s.drawdown_example)} at the bottom`, "bad")}
        ${card("Worst day", pct(s.worst_day_pct), s.worst_day)}
        ${card("Best day", pct(s.best_day_pct), s.best_day)}
        ${card("Up days", `${num(s.up_days_pct, 0)}%`, "share of days that closed higher")}
        ${card(term("fat-tails", "Extreme days"), String(s.big_days), `days beyond 3 ${term("standard-deviation", "standard deviations")}. A bell curve expects about ${s.big_days_expected}.`, s.big_days > s.big_days_expected * 2 ? "bad" : "")}
      </div>
      <div class="two-col">
        <div class="lab-box"><h3>Growth of $10,000</h3><p>${esc(s.name)}, ${s.start} to ${s.end}.</p><div class="lab-chart" id="st-chart"></div></div>
        <div class="lab-box"><h3>How big are the daily moves? (${term("distribution", "distribution")})</h3>
          <p>Blue bars: real days. Yellow line: what a ${term("normal-distribution", "bell curve")} with the same volatility predicts. Real markets have more calm days and more extreme days.</p>
          ${histogram(hist, { overlay: true, left: `${first.toFixed(1)}%`, right: `+${last.toFixed(1)}%` })}</div>
      </div>
      <p class="lab-verdict">${esc(next)}</p>`;
    lineChart("st-chart", [{ data: s.curve, color: COLORS[0] }]);
    learnRefresh();
  } catch (err) { fail(pane, err); }
}

// ---------------------------------------------------------------- correlation (level 5)
const CORR_DEFAULT = ["SPY", "QQQ", "TLT", "GLD", "AAPL", "MSFT", "KO", "NVDA", "BTCUSDT"];

function chips(id, selected) {
  return `<div class="chips" id="${id}">${assets.map((a) => `<label class="chip ${selected.includes(a.symbol) ? "on" : ""}">
    <input type="checkbox" value="${a.symbol}" ${selected.includes(a.symbol) ? "checked" : ""}>${esc(a.symbol.replace(/USDT$/, ""))}</label>`).join("")}</div>`;
}

function buildCorr() {
  const pane = $("lab-corr");
  pane.innerHTML = `
    <p class="lab-intro">${term("correlation", "Correlation")} runs from −1 (always opposite) to +1 (always together).
      ${term("beta", "Beta")} says how much each asset tends to move when SPY moves 1%.</p>
    <form class="lab-form" id="co-form">
      ${chips("co-pick", CORR_DEFAULT)}
      <label>Years <select id="co-years"><option>1</option><option selected>3</option><option>5</option></select></label>
      <button class="btn btn-primary" type="submit">Compare</button>
    </form>
    <div class="lab-out"></div>`;
  pane.querySelector("#co-pick").addEventListener("change", (e) => e.target.closest(".chip")?.classList.toggle("on", e.target.checked));
  pane.querySelector("#co-form").addEventListener("submit", (e) => { e.preventDefault(); runCorr(); });
}

function heatColor(v) {
  if (v === null) return "transparent";
  const a = Math.min(1, Math.abs(v));
  return v >= 0 ? `rgba(59,130,246,${0.15 + a * 0.8})` : `rgba(240,140,30,${0.15 + a * 0.8})`;
}

async function runCorr() {
  const pane = $("lab-corr");
  const symbols = [...pane.querySelectorAll("#co-pick input:checked")].map((i) => i.value);
  if (symbols.length < 2) return fail(pane, "Pick at least two assets.");
  busy(pane, "Loading daily prices and comparing…");
  try {
    const r = await api(`/api/lab/correlation?years=${$("co-years").value}`, "POST", { symbols });
    const short = (s) => s.replace(/USDT$/, "");
    const head = `<tr><th></th>${r.symbols.map((s) => `<th>${esc(short(s))}</th>`).join("")}</tr>`;
    const rows = r.symbols.map((s, i) => `<tr><th>${esc(short(s))}</th>${r.matrix[i].map((v, j) =>
      `<td style="background:${i === j ? "var(--bg-elev)" : heatColor(v)}" title="${esc(nameOf(s))} vs ${esc(nameOf(r.symbols[j]))}">${i === j ? "" : num(v)}</td>`).join("")}</tr>`).join("");
    const betas = r.symbols.map((s) => `<tr><td>${esc(nameOf(s))}</td><td>${num(r.beta[s])}</td><td>${num(r.vol_annual_pct[s], 1)}%</td></tr>`).join("");
    const lo = r.lowest_pair;
    const hi = r.highest_pair;
    pane.querySelector(".lab-out").innerHTML = `
      <p class="lab-verdict">Most alike: <b>${esc(nameOf(hi.a))}</b> and <b>${esc(nameOf(hi.b))}</b> (${num(hi.corr)}).
        Least alike: <b>${esc(nameOf(lo.a))}</b> and <b>${esc(nameOf(lo.b))}</b> (${num(lo.corr)}). Low or negative pairs are the
        ones that make ${term("diversification", "diversification")} work. Try them in the Mixer.</p>
      <div class="two-col">
        <div class="lab-box"><h3>Correlation of daily returns</h3><p>${r.start} to ${r.end}, ${r.days} shared trading days. Blue = move together, orange = move opposite.</p>
          <div class="heat-wrap"><table class="heat">${head}${rows}</table></div></div>
        <div class="lab-box"><h3>Beta to SPY and volatility</h3><p>Beta 2 means it tends to move about 2% when SPY moves 1%.</p>
          <table class="lab-table"><tr><th>Asset</th><th>${term("beta", "Beta")}</th><th>Yearly volatility</th></tr>${betas}</table></div>
      </div>`;
    learnRefresh();
  } catch (err) { fail(pane, err); }
}

// ---------------------------------------------------------------- mixer (level 5)
const MIX_DEFAULT = { SPY: 60, TLT: 40 };

function buildMix() {
  const pane = $("lab-mix");
  const rows = assets.map((a) => {
    const w = MIX_DEFAULT[a.symbol] || 0;
    return `<div class="mix-row"><label class="chip ${w ? "on" : ""}"><input type="checkbox" data-sym="${a.symbol}" ${w ? "checked" : ""}>${esc(a.name)}</label>
      <input type="range" min="0" max="100" step="5" value="${w || 20}" data-w="${a.symbol}" ${w ? "" : "disabled"}>
      <span class="mono" data-wv="${a.symbol}">${w ? `${w}%` : ""}</span></div>`;
  }).join("");
  pane.innerHTML = `
    <p class="lab-intro">Build a ${term("portfolio", "portfolio")}: tick 2 to 8 assets and set their ${term("weight", "weights")}.
      The mix is ${term("rebalancing", "rebalanced")} back to these weights every day. Can you make it calmer than every asset in it?</p>
    <div class="lab-box"><div class="mix-rows" id="mx-rows">${rows}</div>
      <form class="lab-form" id="mx-form"><label>Years <select id="mx-years"><option>1</option><option selected>3</option><option>5</option></select></label>
      <button class="btn btn-primary" type="submit">Test this mix</button></form></div>
    <div class="lab-out"></div>`;
  const box = pane.querySelector("#mx-rows");
  box.addEventListener("input", (e) => {
    const sym = e.target.dataset.sym || e.target.dataset.w;
    if (!sym) return;
    const on = box.querySelector(`[data-sym="${sym}"]`).checked;
    const range = box.querySelector(`[data-w="${sym}"]`);
    range.disabled = !on;
    box.querySelector(`[data-sym="${sym}"]`).closest(".chip").classList.toggle("on", on);
    const total = [...box.querySelectorAll("[data-sym]:checked")].reduce((t, c) => t + Number(box.querySelector(`[data-w="${c.dataset.sym}"]`).value), 0);
    box.querySelectorAll("[data-wv]").forEach((n) => {
      const s = n.dataset.wv;
      const checked = box.querySelector(`[data-sym="${s}"]`).checked;
      n.textContent = checked && total ? `${Math.round(Number(box.querySelector(`[data-w="${s}"]`).value) / total * 100)}%` : "";
    });
  });
  pane.querySelector("#mx-form").addEventListener("submit", (e) => { e.preventDefault(); runMix(); });
}

async function runMix() {
  const pane = $("lab-mix");
  const weights = {};
  pane.querySelectorAll("[data-sym]:checked").forEach((c) => {
    const w = Number(pane.querySelector(`[data-w="${c.dataset.sym}"]`).value);
    if (w > 0) weights[c.dataset.sym] = w;
  });
  busy(pane, "Building the mix…");
  try {
    const r = await api(`/api/lab/mix?years=${$("mx-years").value}`, "POST", { weights });
    const m = r.mix;
    const minVol = Math.min(...Object.values(r.assets).map((a) => a.vol_annual_pct));
    const rows = r.symbols.map((s) => {
      const a = r.assets[s];
      return `<tr><td>${esc(nameOf(s))} <span class="mono">${Math.round(r.weights[s] * 100)}%</span></td><td class="${tone(a.cagr_pct)}">${pct(a.cagr_pct)}</td>
        <td>${num(a.vol_annual_pct, 1)}%</td><td>${num(a.sharpe)}</td><td>-${num(a.max_drawdown_pct, 1)}%</td></tr>`;
    }).join("");
    const verdict = r.calmer_than_all
      ? `Done: the mix's yearly volatility (${num(m.vol_annual_pct, 1)}%) is lower than every asset in it (the calmest alone was ${num(minVol, 1)}%). That's diversification.`
      : `The mix (${num(m.vol_annual_pct, 1)}% yearly volatility) isn't calmer than its calmest asset (${num(minVol, 1)}%). Try adding something with low correlation, like TLT or GLD, or less of the jumpiest asset.`;
    const series = [{ data: r.curves.mix, color: "#ffffff", width: 3 }];
    const leg = [{ label: "Your mix", color: "#ffffff" }];
    r.symbols.forEach((s, i) => { series.push({ data: r.curves[s], color: COLORS[i % COLORS.length], width: 1 }); leg.push({ label: nameOf(s), color: COLORS[i % COLORS.length] }); });
    pane.querySelector(".lab-out").innerHTML = `
      <p class="lab-verdict ${r.calmer_than_all ? "good" : ""}">${esc(verdict)}</p>
      <div class="lab-cards">
        ${card("Mix yearly volatility", `${num(m.vol_annual_pct, 1)}%`, `the weighted average of the assets is ${num(r.weighted_avg_vol_pct, 1)}%: diversification saved ${num(r.diversification_benefit_pct, 1)} points`, r.calmer_than_all ? "good" : "")}
        ${card("Mix yearly growth", pct(m.cagr_pct), `${pct(m.total_return_pct)} in total, ${r.start} to ${r.end}`)}
        ${card("Mix Sharpe", num(m.sharpe), "return per unit of volatility")}
        ${card("Mix max drawdown", `-${num(m.max_drawdown_pct, 1)}%`, `worst day ${pct(m.worst_day_pct)}`)}
      </div>
      <div class="lab-box"><h3>Each asset alone vs the mix</h3>
        <table class="lab-table"><tr><th>Asset</th><th>Yearly growth</th><th>Volatility</th><th>Sharpe</th><th>Max drawdown</th></tr>${rows}
        <tr class="hl"><td><b>Your mix</b></td><td class="${tone(m.cagr_pct)}">${pct(m.cagr_pct)}</td><td>${num(m.vol_annual_pct, 1)}%</td><td>${num(m.sharpe)}</td><td>-${num(m.max_drawdown_pct, 1)}%</td></tr></table></div>
      <div class="lab-box"><h3>Growth of $10,000</h3>${legend(leg)}<div class="lab-chart" id="mx-chart"></div></div>`;
    lineChart("mx-chart", series);
    learnRefresh();
  } catch (err) { fail(pane, err); }
}

// ---------------------------------------------------------------- luck test (level 6)
function buildLuck() {
  const pane = $("lab-luck");
  pane.innerHTML = `
    <p class="lab-intro">Is a strategy's result skill or luck? We run 500 ${term("baseline", "random traders")} on the same
      prices. They buy and sell by coin flip, but trade about as often and hold about as long as the strategy, and pay
      the same costs. Results assume all your money is in each trade, with no stop-loss.</p>
    <form class="lab-form" id="lk-form">
      <label>Asset <select id="lk-sym">${assetOptions("SPY")}</select></label>
      <label>Strategy <select id="lk-strat">${strategyOptions("sma_cross")}</select></label>
      <span id="lk-params" class="lab-form" style="margin:0">${paramInputs("sma_cross", "lk")}</span>
      <label>Years <select id="lk-years"><option>3</option><option selected>5</option><option>10</option></select></label>
      <button class="btn btn-primary" type="submit">Run the luck test</button>
    </form>
    <div class="lab-out"></div>
    <div class="lab-box">
      <h3>Best of 20 random strategies</h3>
      <p>Try 20 coin-flip strategies on the first 70% of history, crown the best, then see what it does on the last 30%.
        This is ${term("data-snooping", "data snooping")} in action.</p>
      <button class="btn" type="button" id="lk-many">Run on the asset above</button>
      <div id="lk-many-out"></div>
    </div>`;
  pane.querySelector("#lk-strat").addEventListener("change", (e) => { $("lk-params").innerHTML = paramInputs(e.target.value, "lk"); });
  pane.querySelector("#lk-form").addEventListener("submit", (e) => { e.preventDefault(); runLuck(); });
  pane.querySelector("#lk-many").addEventListener("click", runMany);
}

async function runLuck() {
  const pane = $("lab-luck");
  busy(pane, "Running 500 random traders…");
  try {
    const r = await api("/api/lab/luck", "POST", { symbol: $("lk-sym").value, strategy: $("lk-strat").value,
      params: readParams($("lk-params")), years: Number($("lk-years").value) });
    const bars = r.histogram.map((b) => ({ label: `${b.from}% to ${b.to}%`, count: b.count, from: b.from, to: b.to }));
    const inBin = (v) => (b) => v >= b.from && v <= b.to;
    const realBin = bars.findIndex(inBin(r.strategy_return_pct));
    const holdBin = bars.findIndex(inBin(r.buy_hold_return_pct));
    const cls = r.beat_pct >= 95 ? "good" : r.beat_pct < 75 ? "bad" : "";
    pane.querySelector(".lab-out").innerHTML = `
      <p class="lab-verdict ${cls}">${esc(r.verdict)}</p>
      <div class="lab-cards">
        ${card("Strategy", pct(r.strategy_return_pct), `${r.trades} trades, held ${r.avg_days_held} days on average`)}
        ${card(term("buy-and-hold", "Buy & hold"), pct(r.buy_hold_return_pct), `${r.start} to ${r.end}`)}
        ${card("Typical random trader", pct(r.random_median_pct), `luckiest ${pct(r.random_best_pct)}, unluckiest ${pct(r.random_worst_pct)}`)}
        ${card("Beat", `${num(r.beat_pct, 0)}%`, "of 500 random traders", cls)}
      </div>
      <div class="lab-box"><h3>500 random traders' results</h3>
        <p>Each bar counts random traders in that range. <span style="color:var(--warn)">Yellow</span>: where your strategy landed.
          <span style="color:var(--up)">Green</span>: buy & hold. A ${term("sample-size", "small number of trades")} spreads the random results wide.</p>
        ${histogram(bars, { highlight: (b, i) => (i === realBin ? "real" : i === holdBin ? "mark" : ""), left: pct(bars[0].from, 0), right: pct(bars[bars.length - 1].to, 0) })}</div>`;
    learnRefresh();
  } catch (err) { fail(pane, err); }
}

async function runMany() {
  const out = $("lk-many-out");
  out.innerHTML = `<p class="lab-loading">Testing 20 random strategies…</p>`;
  try {
    const r = await api("/api/lab/many", "POST", { symbol: $("lk-sym").value, years: Number($("lk-years").value) });
    const rows = [...r.rows].sort((a, b) => b.first_pct - a.first_pct).map((x) => `<tr class="${x.id === r.best_id ? "hl" : ""}">
      <td>${esc(x.name)}${x.id === r.best_id ? " (the winner)" : ""}</td><td class="${tone(x.first_pct)}">${pct(x.first_pct)}</td>
      <td class="${tone(x.second_pct)}">${pct(x.second_pct)}</td></tr>`).join("");
    out.innerHTML = `<p class="lab-verdict">${esc(r.verdict)} Buy & hold made ${pct(r.buy_hold_first_pct)} then ${pct(r.buy_hold_second_pct)}.
        ${r.beat_hold_first} of 20 coin-flippers beat buy & hold in the first period.</p>
      <table class="lab-table"><tr><th>${esc(r.name)}</th><th>First period<br><small>${esc(r.first_period)}</small></th>
        <th>Second period<br><small>${esc(r.second_period)}</small></th></tr>${rows}</table>`;
    learnRefresh();
  } catch (err) { out.innerHTML = `<p class="lab-msg">${esc(err.message)}</p>`; }
}

// ---------------------------------------------------------------- backtest + walk-forward (level 7)
function buildBacktest() {
  const pane = $("lab-backtest");
  pane.innerHTML = `
    <p class="lab-intro">Test a ${term("strategy", "strategy")} on past prices, with ${term("costs", "trading costs")} included.
      Signals use each bar's close and trade at the next bar's open, so there's no ${term("look-ahead-bias", "look-ahead")}.
      All the money goes into each trade, with no stop-loss, so it compares fairly with ${term("buy-and-hold", "buy & hold")}.
      Remember ${term("survivorship-bias", "survivorship bias")}: this stock list is today's winners.</p>
    <form class="lab-form" id="bt2-form">
      <label>Asset <select id="bt2-sym">${assetOptions("SPY")}</select></label>
      <label>Strategy <select id="bt2-strat">${strategyOptions("sma_cross")}</select></label>
      <span id="bt2-params" class="lab-form" style="margin:0">${paramInputs("sma_cross", "bt2")}</span>
      <label>Candles <select id="bt2-iv"><option value="1d" selected>Daily</option><option value="1h">Hourly</option></select></label>
      <label>History <select id="bt2-years"><option value="2">2 years</option><option value="5" selected>5 years</option><option value="10">10 years</option></select></label>
      <label>Fee per trade % <input type="number" id="bt2-fee" step="0.01" min="0" placeholder="auto"></label>
      <label>Slippage, in 0.01% steps <input type="number" id="bt2-slip" step="1" min="0" value="2"></label>
      <button class="btn btn-primary" type="submit">Run backtest</button>
      <button class="btn" type="button" id="bt2-wf">Walk-forward test</button>
    </form>
    <div class="lab-out"></div>`;
  pane.querySelector("#bt2-strat").addEventListener("change", (e) => { $("bt2-params").innerHTML = paramInputs(e.target.value, "bt2"); });
  pane.querySelector("#bt2-form").addEventListener("submit", (e) => { e.preventDefault(); runBacktest(false); });
  pane.querySelector("#bt2-wf").addEventListener("click", () => runBacktest(true));
}

function backtestBody() {
  const symbol = $("bt2-sym").value;
  const interval = $("bt2-iv").value;
  const years = Number($("bt2-years").value);
  const perYear = interval === "1d" ? (isCrypto(symbol) ? 365 : 252) : (isCrypto(symbol) ? 365 * 24 : 252 * 7);
  const body = { symbol, interval, bars: Math.min(5000, Math.max(100, perYear * years)), strategy: $("bt2-strat").value,
    params: readParams($("bt2-params")), risk: LAB_RISK, cash: 10000, slippage_bps: Number($("bt2-slip").value || 0) };
  if ($("bt2-fee").value !== "") body.fee_pct = Number($("bt2-fee").value);
  return body;
}

function holdCurve(candles, from, cash) {
  const sub = candles.filter((c) => c.time >= from);
  if (!sub.length) return [];
  const base = sub[0].open;
  return sub.map((c) => ({ time: c.time, value: (c.close / base) * cash }));
}

function metricCards(m, title = "") {
  const beat = m.total_return_pct > m.buy_hold_return_pct;
  return `<div class="lab-cards">
    ${card(`${title}Total return`, pct(m.total_return_pct), `buy & hold: ${pct(m.buy_hold_return_pct)}`, beat ? "good" : "bad")}
    ${card(term("sharpe", "Sharpe"), num(m.sharpe), "return per unit of volatility")}
    ${card(term("drawdown", "Max drawdown"), `-${num(m.max_drawdown_pct, 1)}%`, "worst fall from a high")}
    ${card("Trades", String(m.num_trades), `${term("win-rate", "win rate")} ${num(m.win_rate_pct, 0)}%`)}
    ${card(term("profit-factor", "Profit factor"), m.profit_factor === null ? "no losses" : num(m.profit_factor), "money won ÷ money lost")}
  </div>`;
}

async function runBacktest(walk) {
  const pane = $("lab-backtest");
  const body = backtestBody();
  busy(pane, walk ? "Tuning on the first 70%, then testing once on the last 30%…" : `Backtesting ${nameOf(body.symbol)}…`);
  try {
    const r = await api(walk ? "/api/backtest/walkforward" : "/api/backtest", "POST", body);
    const costs = `Costs used: ${num(r.costs.fee_pct, 2)}% fee per trade + ${num(r.costs.slippage_bps, 0)} hundredths of a percent slippage.`;
    if (!walk) {
      const curve = r.equity_curve;
      pane.querySelector(".lab-out").innerHTML = `${metricCards(r.metrics)}
        <div class="lab-box"><h3>$10,000 over time</h3><p>${esc(costs)}</p>
          ${legend([{ label: STRAT_NAMES[body.strategy] || body.strategy, color: COLORS[0] }, { label: "Buy & hold", color: COLORS[1] }])}
          <div class="lab-chart" id="bt2-chart"></div></div>
        <p class="lab-verdict">Now press <b>Walk-forward test</b>: it tunes this strategy's settings on the first 70% of history and
          tests the winner once on the rest, which it has never seen.</p>`;
      lineChart("bt2-chart", [{ data: curve, color: COLORS[0] }, { data: holdCurve(r.candles, curve[0].time, 10000), color: COLORS[1], dashed: true }]);
    } else {
      const ins = r.in_sample.metrics;
      const outs = r.out_sample.metrics;
      const settings = (p) => Object.entries(p).map(([k, v]) => `${PARAM_NAMES[k] || k}: ${v}`).join(", ");
      const tried = [...r.tried].sort((a, b) => b.metrics.total_return_pct - a.metrics.total_return_pct).map((t) => {
        const best = JSON.stringify(t.params) === JSON.stringify(r.best_params);
        return `<tr class="${best ? "hl" : ""}"><td>${esc(settings(t.params))}${best ? " (picked)" : ""}</td>
          <td class="${tone(t.metrics.total_return_pct)}">${pct(t.metrics.total_return_pct)}</td><td>${num(t.metrics.sharpe)}</td><td>${t.metrics.num_trades}</td></tr>`;
      }).join("");
      const verdict = r.held_up
        ? `It held up: on data it never saw, it made ${pct(outs.total_return_pct)} vs buy & hold's ${pct(outs.buy_hold_return_pct)}. Encouraging, but one test isn't proof. Run the luck test on it too.`
        : r.fell_apart
          ? `It fell apart: ${pct(ins.total_return_pct)} while tuning, ${pct(outs.total_return_pct)} on unseen data. The tuned settings fitted the past, not a real pattern (${"overfitting"}).`
          : `It made ${pct(outs.total_return_pct)} on unseen data, but buy & hold made ${pct(outs.buy_hold_return_pct)}. Doing nothing would have been better.`;
      const curve = r.out_sample.equity_curve;
      pane.querySelector(".lab-out").innerHTML = `
        <p class="lab-verdict ${r.held_up ? "good" : "bad"}">${esc(verdict)}</p>
        <div class="two-col">
          <div class="lab-box"><h3>${term("in-sample", "Tuning period")} (${day(r.tuning_period.start)} to ${day(r.tuning_period.end)})</h3>
            <p>The best of ${r.tried.length} settings, picked by total return.</p>${metricCards(ins)}</div>
          <div class="lab-box"><h3>${term("out-of-sample", "Test period")} (${day(r.test_period.start)} to ${day(r.test_period.end)})</h3>
            <p>The same settings, run once on data they never saw. This is the honest number.</p>${metricCards(outs)}</div>
        </div>
        <div class="lab-box"><h3>Test period: $10,000 over time</h3><p>${esc(costs)}</p>
          ${legend([{ label: "Strategy (picked settings)", color: COLORS[0] }, { label: "Buy & hold", color: COLORS[1] }])}
          <div class="lab-chart" id="bt2-chart"></div></div>
        <div class="lab-box"><h3>Every setting tried in the tuning period</h3>
          <p>The more settings you try, the better the best one looks by luck alone.</p>
          <table class="lab-table"><tr><th>Settings</th><th>Return</th><th>Sharpe</th><th>Trades</th></tr>${tried}</table></div>`;
      lineChart("bt2-chart", [{ data: curve, color: COLORS[0] }, { data: holdCurve(r.candles, curve[0].time, 10000), color: COLORS[1], dashed: true }]);
    }
    learnRefresh();
  } catch (err) { fail(pane, err); }
}

// ---------------------------------------------------------------- tabs + start
const BUILDERS = { stats: buildStats, corr: buildCorr, mix: buildMix, luck: buildLuck, backtest: buildBacktest };
const RUN_ON_OPEN = { stats: runStats };
const built = new Set();

function setTab(next) {
  const avail = TABS.filter(unlocked);
  if (!avail.length) return;
  if (!avail.includes(next)) next = avail[avail.length - 1];
  tab = next;
  storageSet("lab:tab", next);
  document.querySelectorAll("#lab-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === next));
  TABS.forEach((t) => $(`lab-${t}`).classList.toggle("active", t === next));
  if (!built.has(next) && assets.length) {
    BUILDERS[next]();
    built.add(next);
    RUN_ON_OPEN[next]?.();
  }
  Object.values(charts).forEach((c) => c.timeScale().fitContent());
}

async function start() {
  $("lab-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tab]");
    if (b) setTab(b.dataset.tab);
  });
  try {
    const [state, strats] = await Promise.all([api("/api/state"), api("/api/strategies")]);
    assets = state.assets || [];
    strategies = strats;
  } catch { /* retried when the Lab opens */ }
  const pick = () => { if (document.body.classList.contains("view-lab")) setTab(tab || storageGet("lab:tab") || "stats"); };
  new MutationObserver(pick).observe(document.body, { attributes: true, attributeFilter: ["class"] });
  window.addEventListener("learn:level", pick);
  window.addEventListener("lab:open", (e) => { tab = e.detail; storageSet("lab:tab", e.detail); });
  pick();
}

start();
