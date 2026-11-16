// Swing desk (Level 14): evening routine, scanner, trade plans with the Time Machine and risk gate, orders, journal.
import { $, api, esc, num, pct, money, tone, card, bars, lineChart, learnRefresh, term, armed, onView, storageGet, storageSet } from "./ui.js";

let st = null;
let journal = null;
let tab = storageGet("swing-tab") || "today";
let scanFilter = "";
let openPlan = null; // id of the plan card that's expanded
let busy = false;

const ROUTE = { sim: "the app's paper account", alpaca_paper: "Alpaca paper trading", alpaca_live: "Alpaca, REAL MONEY" };
const STATUS = { planned: "Plan", working: "Waiting for entry", open: "Open", closing: "Selling", closed: "Closed",
  expired: "Expired", cancelled: "Cancelled", rejected: "Refused" };
const EXIT = { target: "hit the target", stop: "hit the stop", gap_stop: "gapped through the stop", time: "time stop", manual: "closed by hand" };
const usd = (n) => (n == null ? "–" : `$${Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
const setupName = (k) => (k === "own" ? "My own setup" : st?.setups[k]?.name || k);
const R = (v, d = 2) => (v == null ? "–" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}R`);
const when = (t) => new Date(t * 1000).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

function msg(id, text, bad = false) {
  const el = $(id);
  if (el) { el.textContent = text; el.className = bad ? "lab-msg" : "muted"; }
}

async function act(fn, msgId) {
  if (busy) return null;
  busy = true;
  try {
    const out = await fn();
    learnRefresh();
    return out;
  } catch (err) {
    if (msgId) msg(msgId, err.message, true);
    else alertBox(err.message);
    return null;
  } finally { busy = false; }
}

function alertBox(text) {
  const el = $("sw-alert");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(alertBox.t);
  alertBox.t = setTimeout(() => { el.hidden = true; }, 8000);
}

// ------------------------------------------------------------------------------------------ header
function renderHead() {
  const a = st.account || {};
  const s = st.settings;
  const reg = st.regime || {};
  const live = st.route === "alpaca_live";
  $("sw-route").className = `lv-route lv-${st.route}`;
  $("sw-route").innerHTML = `<span class="lv-dot"></span><div><b>Approved orders go to ${ROUTE[st.route]}</b>
    <p>${live ? "Every order needs your confirmation. Only setups with a paper track record can trade."
      : st.route === "alpaca_paper" ? "Real orders at Alpaca with pretend money." : "Pretend money. Change this on Go live (Level 15)."}</p></div>`;
  const heatLimit = (a.equity || 0) * s.max_heat_pct;
  $("sw-cards").innerHTML = [
    card("Account", money(a.equity), `cash ${money(a.cash)}`),
    card(term("portfolio-heat", "Open risk"), money(st.heat), `limit ${money(heatLimit)} (${num(s.max_heat_pct * 100, 1)}%)`, st.heat > heatLimit ? "bad" : ""),
    card("Trades", `${st.active.filter((t) => t.status !== "working").length} open`, `${st.active.filter((t) => t.status === "working").length} waiting · max ${s.max_positions}`),
    card(term("market-regime", "Market trend"), `<span class="sw-regime sw-${esc(reg.label)}">${esc(reg.label || "–")}</span>`,
      reg.pct_vs_200 != null ? `SPY ${pct(reg.pct_vs_200)} vs its 200-day` : ""),
  ].join("");
  document.querySelectorAll("#sw-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".sw-pane").forEach((p) => { p.hidden = p.dataset.pane !== tab; });
  const plans = st.plans.length;
  $("sw-tab-plans").textContent = plans ? `Plans (${plans})` : "Plans";
  $("sw-tab-trades").textContent = st.active.length ? `Trades (${st.active.length})` : "Trades";
}

// ------------------------------------------------------------------------------------------ today
function renderToday() {
  const r = st.routine;
  const reg = st.regime || {};
  const opens = r.opens_at ? new Date(r.opens_at * 1000) : null;
  const closes = r.closes_at ? new Date(r.closes_at * 1000) : null;
  const local = (d) => d.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
  const attention = st.active.filter((t) => t.status === "open").map((t) => {
    const p = t.last_price;
    const notes = [];
    if (p != null) {
      const toStop = (p - t.stop) / (t.entry - t.stop_planned || t.entry - t.stop);
      if (toStop < 0.3) notes.push(`close to its stop (${usd(t.stop)})`);
      if (p >= t.target - 0.3 * (t.entry - t.stop)) notes.push(`close to its target (${usd(t.target)})`);
    }
    if ((t.held_sessions || 0) >= t.time_stop_days - 1) notes.push(`time stop due (${t.held_sessions}/${t.time_stop_days} sessions)`);
    return notes.length ? `<li><b>${esc(t.symbol)}</b>: ${notes.join("; ")}</li>` : "";
  }).join("");
  const unrev = st.recent.filter((t) => t.status === "closed" && !t.review).length;
  $("sw-today").innerHTML = `
    <div class="two-col">
      <div class="lab-box"><h3>Tonight's routine</h3>
        <p>For the session that ${r.market_open ? "is open now" : `opens ${opens ? local(opens) : "next"} your time`}.
          ${closes ? `It closes ${local(closes)} your time.` : ""} About 20 minutes; then the orders work while you sleep.</p>
        <ol class="lv-checks">${r.items.map((i) => `<li class="${i.done ? "ok" : ""}"><span class="lv-tick" aria-hidden="true">${i.done ? "✓" : ""}</span>
          <div><b>${esc(i.title)}</b><p class="muted">${esc(i.how)}</p>
          ${!i.done && i.id === "market" ? `<button type="button" class="btn btn-sm" data-tick="market">I've checked it</button>` : ""}
          ${!i.done && i.id === "review" ? `<button type="button" class="btn btn-sm" data-tick="review">Done</button> <button type="button" class="btn btn-sm btn-ghost" data-goto="trades">Open trades</button>` : ""}
          ${!i.done && i.id === "scan" ? `<button type="button" class="btn btn-sm btn-primary" data-goto="scan">Go to the scan</button>` : ""}
          ${!i.done && i.id === "plan" ? `<button type="button" class="btn btn-sm" data-goto="plans">Write a plan</button> <button type="button" class="btn btn-sm btn-ghost" data-tick="no_trade">No trade today</button>` : ""}
          ${!i.done && i.id === "journal" ? `<button type="button" class="btn btn-sm" data-goto="journal">Open the journal</button>` : ""}
          </div></li>`).join("")}</ol>
        ${r.done ? `<p class="up">Routine done for this session. Nice: consistency is most of the job.</p>` : ""}
      </div>
      <div>
        <div class="lab-box"><h3>The market</h3><p class="sw-regime-detail"><span class="sw-regime sw-${esc(reg.label)}">${esc(reg.label || "unknown")}</span> ${esc(reg.detail || "")}</p>
          ${reg.close ? `<p class="muted">SPY ${usd(reg.close)} · 50-day ${usd(reg.sma50)} · 200-day ${usd(reg.sma200)} · as of ${esc(reg.as_of)}</p>` : ""}</div>
        <div class="lab-box"><h3>Needs attention</h3>
          ${attention ? `<ul class="sw-list">${attention}</ul>` : `<p class="muted">Nothing close to a stop, target or time stop.</p>`}
          ${unrev ? `<p>${unrev} closed trade${unrev === 1 ? "" : "s"} to review in the <a href="#" data-goto="journal">journal</a>.</p>` : ""}
          <div class="btn-row-left"><button type="button" class="btn btn-sm" id="sw-refresh">Check orders now</button><span class="muted" id="sw-refresh-msg"></span></div>
        </div>
      </div>
    </div>`;
}

// ------------------------------------------------------------------------------------------ scan
function renderScan() {
  const s = st.scan;
  const hits = s ? s.hits.filter((h) => !scanFilter || h.setups.includes(scanFilter)) : [];
  $("sw-scan").innerHTML = `
    <div class="btn-row-left"><button type="button" class="btn btn-primary" id="sw-run-scan">Run the scan</button>
      <span class="muted" id="sw-scan-msg">${s ? `Last scan ${when(s.ts)} on prices up to ${esc(s.as_of)}.` : "Scans about 170 big US stocks and ETFs on daily bars. Takes 10-30 seconds."}</span></div>
    ${s ? `<p class="muted">Loaded ${s.loaded} of ${s.universe} charts${s.failed.length ? ` (couldn't load: ${esc(s.failed.slice(0, 8).join(", "))}${s.failed.length > 8 ? "…" : ""})` : ""};
      ${s.thin} skipped as too thin or under $5; ${s.scanned} scanned; <b>${s.hits.length}</b> matched a ${term("setup", "setup")} rule.
      A hit is a chart to look at, not a stock to buy.</p>
      <div class="chips sw-filter">${["", ...Object.keys(st.setups)].map((k) => `<button type="button" class="chip ${scanFilter === k ? "on" : ""}" data-filter="${k}">
        ${k ? `${esc(st.setups[k].name)} (${s.counts[k] || 0})` : `All (${s.hits.length})`}</button>`).join("")}</div>
      ${scanFilter ? `<p class="muted sw-rule">${esc(st.setups[scanFilter].rule)}</p>` : ""}
      ${hits.length ? `<div class="sw-table-wrap"><table class="lab-table sw-hits"><tr><th>Stock</th><th>Setups</th><th>Close</th><th>vs 200-day</th><th>RSI</th><th>Entry</th><th>Stop</th><th>Target</th><th>Risk</th><th></th></tr>
        ${hits.map((h) => `<tr><td><b>${esc(h.symbol)}</b> <span class="muted">${esc(h.name)}</span><div class="muted sw-sector">${esc(h.sector)}</div></td>
          <td>${h.setups.map((k) => `<span class="sw-tag">${esc(st.setups[k].name)}</span>`).join(" ")}</td>
          <td>${usd(h.close)}</td><td class="${tone(h.vs_200)}">${pct(h.vs_200)}</td><td>${num(h.rsi, 0)}</td>
          <td>${usd(h.plan.entry)}</td><td>${usd(h.plan.stop)}</td><td>${usd(h.plan.target)}</td><td>${num(h.risk_pct, 1)}%</td>
          <td><button type="button" class="btn btn-sm" data-plan="${esc(h.symbol)}" data-setup="${esc(h.setups[0])}">Plan it</button></td></tr>`).join("")}</table></div>`
        : `<p class="muted">No charts matched${scanFilter ? " this setup" : ""} today. That happens: no setup is a fine answer.</p>`}` : ""}`;
}

// ------------------------------------------------------------------------------------------ plans
function planForm(prefill = {}) {
  const s = st.settings;
  return `<form class="lab-form sw-plan-form" id="sw-plan-form">
    <label>Stock <input type="text" id="sw-p-sym" value="${esc(prefill.symbol || "")}" placeholder="e.g. AAPL" required maxlength="10" autocomplete="off" spellcheck="false"></label>
    <label>Setup <select id="sw-p-setup">${Object.entries(st.setups).map(([k, v]) => `<option value="${k}" ${prefill.setup === k ? "selected" : ""}>${esc(v.name)}</option>`).join("")}
      <option value="own" ${prefill.setup === "own" ? "selected" : ""}>My own setup</option></select></label>
    <label>Target <span class="input-suffix-inline"><input type="number" id="sw-p-r" min="1" max="5" step="0.5" value="${s.r_target}"> R</span></label>
    <label>Stop <select id="sw-p-rule"><option value="swing">Under the 5-day low (min 1 ATR)</option><option value="atr">2 ATR under the entry</option></select></label>
    <label>Entry (optional) <input type="number" id="sw-p-entry" step="0.01" min="0" placeholder="auto"></label>
    <label>Stop (optional) <input type="number" id="sw-p-stop" step="0.01" min="0" placeholder="auto"></label>
    <label class="sw-thesis">Thesis: why this trade, and what would prove you wrong?
      <textarea id="sw-p-thesis" rows="2" maxlength="500" required placeholder="e.g. Strong uptrend pulled back to its 20-day on light volume. Wrong if it closes under the 5-day low."></textarea></label>
    <button type="submit" class="btn btn-primary">Save plan</button>
    <span class="muted" id="sw-p-msg">Leave entry and stop empty to use the setup's rules: a ${term("buy-stop", "buy-stop")} above the last day's high and a stop under the recent low.</span>
  </form>`;
}

function odds(tm) {
  const seg = (v, cls, label) => (v > 0 ? `<span class="${cls}" style="width:${v}%" title="${label} ${v}%"></span>` : "");
  return `<div class="sw-odds">${seg(tm.target_pct, "t", "target first")}${seg(tm.stop_pct - tm.gap_stop_pct, "s", "stop first")}${seg(tm.gap_stop_pct, "g", "gapped through the stop")}${seg(tm.time_pct, "n", "time stop")}</div>
    <div class="sw-odds-key"><span><i class="t"></i>Target first ${num(tm.target_pct, 1)}%</span><span><i class="s"></i>Stop first ${num(tm.stop_pct, 1)}%${tm.gap_stop_pct ? ` (gap ${num(tm.gap_stop_pct, 1)}%)` : ""}</span><span><i class="n"></i>Time stop ${num(tm.time_pct, 1)}%</span></div>`;
}

function fan(tm, t) {
  const W = 320, H = 110, P = 6;
  const lo = Math.min(t.stop, ...tm.fan.map((f) => f.p5)), hi = Math.max(t.target, ...tm.fan.map((f) => f.p95));
  const x = (i) => P + (i / tm.fan.length) * (W - 2 * P);
  const y = (v) => H - P - ((v - lo) / (hi - lo || 1)) * (H - 2 * P);
  const pts = [{ day: 0, p5: t.entry, p25: t.entry, p50: t.entry, p75: t.entry, p95: t.entry }, ...tm.fan];
  const band = (a, b) => `${pts.map((f, i) => `${x(i)},${y(f[a])}`).join(" ")} ${pts.slice().reverse().map((f, i) => `${x(pts.length - 1 - i)},${y(f[b])}`).join(" ")}`;
  const line = (v, cls) => `<line x1="${P}" x2="${W - P}" y1="${y(v)}" y2="${y(v)}" class="${cls}"/>`;
  return `<svg class="sw-fan" viewBox="0 0 ${W} ${H}" role="img" aria-label="Range of prices in the replays, day by day">
    <polygon points="${band("p95", "p5")}" class="b1"/><polygon points="${band("p75", "p25")}" class="b2"/>
    <polyline points="${pts.map((f, i) => `${x(i)},${y(f.p50)}`).join(" ")}" class="mid"/>
    ${line(t.target, "tgt")}${line(t.stop, "stp")}${line(t.entry, "ent")}</svg>
    <p class="muted sw-fan-key">Where the price was in the replays, day by day: the middle line is the median, the bands hold 50% and 90% of replays. Green: target, red: stop.</p>`;
}

function replayBox(t) {
  const tm = t.replay;
  if (!tm) return "";
  const h = t.history;
  return `<div class="sw-tm"><h4>Time Machine <span class="muted">${tm.replays.toLocaleString()} replays from the last ${tm.pool_days} days, seed ${tm.seed}</span></h4>
    ${odds(tm)}
    <div class="sw-tm-grid"><div>${fan(tm, t)}</div>
      <div><div class="lab-cards sw-mini">${card("Average", R(tm.avg_r), "per trade, after costs")}${card("Typical range", `${R(tm.r_p5, 1)} to ${R(tm.r_p95, 1)}`, "90% of replays")}${card("Worst", R(tm.worst_r, 1), tm.worst_r < -1.05 ? "a gap past the stop" : "")}</div>
        <p class="sw-verdict">${esc(tm.verdict)}</p>${tm.notes.map((n) => `<p class="muted">${esc(n)}</p>`).join("")}</div></div>
    <p class="muted">The replays shuffle this stock's real days, so they know its normal moves but not your ${term("setup", "setup")}. The history below is the setup's own record.</p>
    ${h ? `<h4>The setup's real record on ${esc(t.symbol)} <span class="muted">(${h.years} years)</span></h4>
      <p>Fired ${h.fired} times; taken one at a time: ${h.taken}. Target ${h.counts.target}, stop ${h.counts.stop + h.counts.gap_stop}${h.counts.gap_stop ? ` (${h.counts.gap_stop} gapped)` : ""}, time stop ${h.counts.time}, never triggered ${h.counts.no_fill}.</p>
      <table class="lab-table sw-hist"><tr><th></th><th>Trades</th><th>Win rate</th><th>Average</th><th>Total</th></tr>
        <tr><td>This setup</td><td>${h.setup_stats.trades}</td><td>${h.setup_stats.win_rate == null ? "–" : `${num(h.setup_stats.win_rate, 0)}%`}</td><td class="${tone(h.setup_stats.avg_r)}">${R(h.setup_stats.avg_r)}</td><td>${R(h.setup_stats.total_r, 1)}</td></tr>
        <tr><td>Random days, same exits</td><td>${h.random_stats.trades}</td><td>${h.random_stats.win_rate == null ? "–" : `${num(h.random_stats.win_rate, 0)}%`}</td><td class="${tone(h.random_stats.avg_r)}">${R(h.random_stats.avg_r)}</td><td></td></tr></table>
      ${h.beat_random_pct != null ? `<p>Beat ${num(h.beat_random_pct, 0)}% of same-sized groups of random-day trades.</p>` : ""}
      <p class="sw-verdict">${esc(h.verdict)}</p>` : ""}</div>`;
}

function checkBox(t) {
  const c = t.check;
  if (!c) return "";
  const icon = (x) => (x.ok ? "✓" : x.level === "block" ? "✕" : x.level === "warn" ? "!" : "i");
  return `<div class="sw-check"><h4>Risk check <span class="sw-verdict-badge ${c.verdict === "PASS" ? "pass" : "block"}">${c.verdict}</span>
      <span class="muted">${when(c.ts)}</span></h4><p>${esc(c.summary)}</p>
    <ul class="sw-checks">${c.checks.map((x) => `<li class="${x.ok ? "ok" : x.level}"><span class="sw-ci">${icon(x)}</span><div><b>${esc(x.title)}</b> <span class="muted">${esc(x.detail)}</span></div></li>`).join("")}</ul></div>`;
}

function earningsLine(e) {
  if (!e) return "";
  if (e.status === "estimate") return `<p class="${e.overlap ? "down" : "muted"}">${term("earnings", "Earnings")}: ${esc(e.detail)}</p>`;
  return `<p class="muted">${term("earnings", "Earnings")}: ${esc(e.detail)}</p>`;
}

function planCard(t) {
  const passed = t.check && t.check.verdict === "PASS";
  const live = st.route === "alpaca_live";
  const expanded = openPlan === t.id;
  return `<article class="sw-card" data-id="${t.id}">
    <header><b>${esc(t.symbol)}</b> <span class="muted">${esc(t.name)} · ${esc(t.sector)}</span> <span class="sw-tag">${esc(setupName(t.setup))}</span>
      ${t.setup_fired_today ? "" : `<span class="muted">(the setup's rule didn't fire on the last bar)</span>`}
      ${passed ? `<span class="sw-verdict-badge pass">PASS</span>` : t.check ? `<span class="sw-verdict-badge block">BLOCK</span>` : ""}</header>
    <div class="sw-nums">
      <div><span>Entry (${term("buy-stop", "buy-stop")})</span><b>${usd(t.entry)}</b></div>
      <div><span>${term("stop-loss", "Stop")}</span><b class="down">${usd(t.stop)}</b></div>
      <div><span>Target</span><b class="up">${usd(t.target)}</b></div>
      <div><span>${term("r-multiple", "R")} per share</span><b>${usd(t.risk_per_share)}</b></div>
      <div><span>Target in R</span><b>${num(t.rr, 2)}R</b></div>
      <div><span>Shares</span><b>${t.qty}</b></div>
      <div><span>Loss at the stop</span><b>${usd(t.max_loss)}</b></div>
      <div><span>Position</span><b>${money(t.notional)}</b></div>
      <div><span>${term("time-stop", "Time stop")}</span><b>${t.time_stop_days} days</b></div>
    </div>
    <p class="sw-thesis-text">“${esc(t.thesis)}”</p>
    <p class="muted">Stop: ${esc(t.stop_note)}. Size: ${esc(t.qty_why)}. ${term("atr", "ATR")} ${usd(t.atr)}. The buy-stop waits ${t.valid_days} sessions, then expires.</p>
    ${earningsLine(t.earnings)}
    <div class="btn-row-left">
      <button type="button" class="btn btn-sm" data-replay="${t.id}">${t.replay ? "Replay again" : "Run the Time Machine"}</button>
      <button type="button" class="btn btn-sm" data-check="${t.id}">Risk check</button>
      <button type="button" class="btn btn-sm ${live ? "btn-danger" : "btn-buy"}" data-approve="${t.id}" ${passed ? "" : "disabled"}
        title="${passed ? "" : "Pass the risk check today first"}">${live ? "Send real order" : "Approve"}</button>
      <button type="button" class="btn btn-sm btn-ghost" data-edit="${t.id}">Edit</button>
      <button type="button" class="btn btn-sm btn-ghost" data-cancel="${t.id}">Delete</button>
      <span class="muted" id="sw-msg-${t.id}"></span>
    </div>
    <form class="lab-form sw-edit" data-editform="${t.id}" ${expanded ? "" : "hidden"}>
      <label>Entry <input type="number" step="0.01" name="entry" value="${t.entry}"></label>
      <label>Stop <input type="number" step="0.01" name="stop" value="${t.stop}"></label>
      <label>Target <input type="number" step="0.01" name="target" value="${t.target}"></label>
      <label>Shares <input type="number" step="1" min="0" name="qty" value="${t.qty}"></label>
      <label>Time stop (days) <input type="number" step="1" min="2" max="40" name="time_stop_days" value="${t.time_stop_days}"></label>
      <button type="submit" class="btn btn-sm">Save changes</button>
      <span class="muted">Changing entry or stop re-sizes the trade from your risk. Any change needs a new risk check.</span>
    </form>
    ${replayBox(t)}${checkBox(t)}
  </article>`;
}

function renderPlans(prefill) {
  $("sw-plans").innerHTML = `<div class="lab-box"><h3>New plan</h3>${planForm(prefill)}</div>
    ${st.plans.length ? st.plans.map(planCard).join("") : `<p class="muted">No plans yet. Start from a scan hit, or type a stock above.</p>`}`;
}

// ------------------------------------------------------------------------------------------ trades
function rNow(t) {
  if (t.status !== "open" || t.last_price == null || !t.fill_price) return null;
  return (t.last_price - t.fill_price) / (t.entry - (t.stop_planned ?? t.stop));
}

function tradeRow(t) {
  const now = rNow(t);
  return `<article class="sw-card sw-trade" data-id="${t.id}">
    <header><b>${esc(t.symbol)}</b> <span class="sw-status sw-${t.status}">${STATUS[t.status]}</span> <span class="sw-tag">${esc(setupName(t.setup))}</span>
      ${t.route && t.route !== "sim" ? `<span class="l-tag">${t.route === "alpaca_live" ? "real money" : "Alpaca paper"}</span>` : ""}
      ${now != null ? `<span class="mono ${tone(now)}">${R(now)} now</span>` : ""}</header>
    <div class="sw-nums">
      <div><span>${t.status === "working" ? "Buy above" : "Bought at"}</span><b>${usd(t.fill_price || t.entry)}</b></div>
      <div><span>Stop</span><b class="down">${usd(t.stop)}</b></div>
      <div><span>Target</span><b class="up">${usd(t.target)}</b></div>
      <div><span>Shares</span><b>${t.qty}</b></div>
      <div><span>Last price</span><b>${usd(t.last_price)}</b></div>
      <div><span>${t.status === "working" ? "Waiting" : "Held"}</span><b>${t.status === "working" ? `${t.sessions_waited || 0}/${t.valid_days}` : `${t.held_sessions || 0}/${t.time_stop_days}`} sessions</b></div>
    </div>
    <p class="sw-thesis-text">“${esc(t.thesis)}”</p>
    ${t.stop_history?.length ? `<p class="muted">Stop raised: ${t.stop_history.map((s) => `${usd(s.from)} → ${usd(s.to)}`).join(", ")}</p>` : ""}
    <form class="btn-row-left sw-raise" data-raise="${t.id}">
      ${t.status === "open" || t.status === "working" ? `<label class="input-suffix-inline">Raise stop to $ <input type="number" step="0.01" name="stop" value="${t.stop}"></label><button type="submit" class="btn btn-sm">Raise stop</button>` : ""}
      ${t.status === "open" ? `<button type="button" class="btn btn-sm btn-ghost" data-close="${t.id}">Sell now</button>` : ""}
      ${t.status === "working" ? `<button type="button" class="btn btn-sm btn-ghost" data-cancel="${t.id}">Cancel the order</button>` : ""}
      <span class="muted" id="sw-msg-${t.id}"></span></form>
  </article>`;
}

function doneRow(t) {
  return `<tr><td><b>${esc(t.symbol)}</b> <span class="muted">${esc(setupName(t.setup))}</span></td><td>${STATUS[t.status]}${t.exit_reason ? `: ${EXIT[t.exit_reason] || esc(t.exit_reason)}` : ""}</td>
    <td>${t.fill_price ? usd(t.fill_price) : "–"}</td><td>${t.exit_price ? usd(t.exit_price) : "–"}</td>
    <td class="${tone(t.r_multiple)}">${R(t.r_multiple)}</td><td class="${tone(t.pnl)}">${t.pnl == null ? "–" : usd(t.pnl)}</td>
    <td>${t.exit_ts ? when(t.exit_ts) : when(t.created)}</td></tr>`;
}

function renderTrades() {
  $("sw-trades").innerHTML = `${st.active.length ? st.active.map(tradeRow).join("") : `<p class="muted">No open trades or waiting orders.</p>`}
    <p class="muted">${st.route === "sim" ? "On the paper account, orders are handled on daily bars: a buy-stop fills when the day's high reaches it (at the open if the stock gaps above), the stop and target are checked against each day's low and high, and the time stop sells at that day's close. While the US market is open, prices are checked every few minutes."
      : "At Alpaca the entry is a stop-limit buy that won't chase more than 0.5% past your entry. After it fills, the stop and target go in as one order (one cancels the other)."}</p>
    <div class="lab-box"><h3>Finished</h3>${st.recent.length ? `<div class="sw-table-wrap"><table class="lab-table"><tr><th>Trade</th><th>Result</th><th>Bought</th><th>Sold</th><th>R</th><th>Profit</th><th>When</th></tr>
      ${st.recent.map(doneRow).join("")}</table></div>` : `<p class="muted">Nothing finished yet.</p>`}</div>`;
}

// ------------------------------------------------------------------------------------------ journal
function reviewForm(t) {
  return `<form class="sw-review" data-review="${t.id}">
    <p><b>${esc(t.symbol)}</b> ${esc(setupName(t.setup))}: <span class="${tone(t.r_multiple)}">${R(t.r_multiple)}</span> (${usd(t.pnl)}), ${EXIT[t.exit_reason] || esc(t.exit_reason)}.
      ${t.mae_r != null ? `<span class="muted">Worst point ${R(t.mae_r, 1)}, best ${R(t.mfe_r, 1)} while it was open.</span>` : ""}</p>
    <p class="muted">Your thesis: “${esc(t.thesis)}”</p>
    <label class="sw-thesis">What happened, and what would you repeat or change?<textarea name="note" rows="2" maxlength="1000" required></textarea></label>
    <div class="chips">${st.mistakes.map((m) => `<label class="chip"><input type="checkbox" name="mistake" value="${esc(m)}">${esc(m)}</label>`).join("")}</div>
    <div class="btn-row-left"><label class="switch-row"><input type="checkbox" name="followed" ${t.followed_plan ? "checked" : ""}> I followed my plan</label>
      <button type="submit" class="btn btn-sm btn-primary">Save review</button><span class="muted" id="sw-msg-${t.id}"></span></div>
  </form>`;
}

function renderJournal() {
  const j = journal;
  if (!j) { $("sw-journal").innerHTML = `<p class="muted">Loading…</p>`; return; }
  const o = j.overall;
  const closed = st.recent.filter((t) => t.status === "closed");
  const unrev = closed.filter((t) => !t.review);
  const setups = Object.entries(j.by_setup);
  $("sw-journal").innerHTML = `
    ${unrev.length ? `<div class="lab-box"><h3>To review (${unrev.length})</h3>${unrev.map(reviewForm).join("<hr class=\"sw-hr\">")}</div>` : ""}
    <div class="lab-box"><h3>Your edge, so far</h3>
      ${o.trades ? `<div class="lab-cards">${card("Trades", o.trades, `${o.wins} won · ${o.losses} lost`)}${card(term("win-rate", "Win rate"), `${num(o.win_rate, 0)}%`, o.breakeven_win_rate != null ? `break-even at your win/loss sizes: ${num(o.breakeven_win_rate, 0)}%` : "")}
        ${card(term("expectancy", "Expectancy"), R(o.expectancy_r), "average per trade", o.expectancy_r > 0 ? "good" : "bad")}${card("Total", R(o.total_r, 1), money(o.pnl))}
        ${card("Average win / loss", `${R(o.avg_win_r, 1)} / ${R(o.avg_loss_r, 1)}`, o.profit_factor != null ? `profit factor ${num(o.profit_factor, 2)}` : "")}
        ${card("Plan followed", o.followed_pct == null ? "–" : `${num(o.followed_pct, 0)}%`, "of trades")}</div>
        ${o.positive_in_resamples_pct != null ? `<p>Luck check: resampling your ${o.trades} trades 2,000 times, the average stayed above zero ${num(o.positive_in_resamples_pct, 0)}% of the time${o.positive_in_resamples_pct < 80 ? ": not yet clearly more than luck." : "."}</p>` : ""}
        <p class="sw-verdict">${esc(j.lesson)}</p>
        <div class="sw-curve" id="sw-curve"></div><p class="muted">Running total in R, trade by trade.</p>` : `<p class="muted">${esc(j.lesson)}</p>`}
    </div>
    ${setups.length ? `<div class="lab-box"><h3>By setup</h3><div class="sw-table-wrap"><table class="lab-table"><tr><th>Setup</th><th>Trades</th><th>Win rate</th><th>Avg win</th><th>Avg loss</th><th>Expectancy</th><th>Time Machine said</th></tr>
      ${setups.map(([k, s]) => `<tr><td>${esc(setupName(k))}</td><td>${s.trades}</td><td>${num(s.win_rate, 0)}%</td><td>${R(s.avg_win_r, 1)}</td><td>${R(s.avg_loss_r, 1)}</td>
        <td class="${tone(s.expectancy_r)}">${R(s.expectancy_r)}</td><td>${R(s.expected_r)}</td></tr>`).join("")}</table></div>
      <p class="muted">"Time Machine said" is the average R the replays expected when you planned. A big gap between the two tells you something: luck, a small sample, or how you manage trades.</p></div>` : ""}
    <div class="two-col">
      <div class="lab-box"><h3>Plan followed vs not</h3>${j.followed || j.broke_plan ? `<table class="lab-table"><tr><th></th><th>Trades</th><th>Expectancy</th></tr>
        ${j.followed ? `<tr><td>Followed the plan</td><td>${j.followed.trades}</td><td class="${tone(j.followed.expectancy_r)}">${R(j.followed.expectancy_r)}</td></tr>` : ""}
        ${j.broke_plan ? `<tr><td>Broke the plan</td><td>${j.broke_plan.trades}</td><td class="${tone(j.broke_plan.expectancy_r)}">${R(j.broke_plan.expectancy_r)}</td></tr>` : ""}</table>` : `<p class="muted">Shows up once you've reviewed some trades.</p>`}
        ${Object.keys(j.mistakes).length ? `<h4>Mistakes you tagged</h4>${bars(Object.entries(j.mistakes).map(([k, v]) => ({ label: k, value: v })), { fmt: (v) => v })}` : ""}</div>
      <div class="lab-box"><h3>Track record for real money</h3>
        <p>Go live (Level 15) lets a setup trade real money after ${st.rules.track_trades} closed paper trades, a positive average, a luck check of ${st.rules.track_luck}% and the plan followed ${st.rules.track_followed}% of the time.</p>
        ${Object.keys(j.track_record).length ? `<ul class="lv-checks">${Object.entries(j.track_record).map(([k, r]) => `<li class="${r.eligible ? "ok" : ""}"><span class="lv-tick">${r.eligible ? "✓" : ""}</span><div><b>${esc(setupName(k))}</b><p class="muted">${esc(r.why)}</p></div></li>`).join("")}</ul>` : `<p class="muted">No closed paper trades yet.</p>`}</div>
    </div>
    <div class="lab-box"><h3>Weekly review</h3>
      ${st.weekly ? `<p class="muted">${when(st.weekly.ts)}</p><ul class="sw-list">${st.weekly.points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul><p class="sw-verdict">${esc(st.weekly.tip)}</p>` : `<p class="muted">Once a week (the weekend works well), look back at the week's trades.</p>`}
      <button type="button" class="btn btn-sm" id="sw-weekly">Weekly review</button></div>`;
  if (o.trades) lineChart("sw-curve", [{ data: j.curve, color: "#3b82f6" }]);
}

// ------------------------------------------------------------------------------------------ settings
function renderSettings() {
  const s = st.settings;
  const f = (k, label, v, step, suffix, help) => `<label>${label} <span class="input-suffix-inline"><input type="number" data-set="${k}" step="${step}" value="${v}"> ${suffix}</span><small class="muted">${help}</small></label>`;
  $("sw-settings").innerHTML = `<p>These are your rules. The risk check enforces them; nothing can loosen them mid-trade.</p>
    <div class="sw-settings">
      ${f("risk_pct", term("risk-per-trade", "Risk per trade"), num(s.risk_pct * 100, 2), 0.05, "% of account", "0.25-1% is typical. At 0.5%, ten losses in a row cost about 5%.")}
      ${f("max_heat_pct", term("portfolio-heat", "Total open risk"), num(s.max_heat_pct * 100, 1), 0.5, "% of account", "Everything that could hit its stop at once.")}
      ${f("max_positions", "Most trades at once", s.max_positions, 1, "", "Open plus waiting.")}
      ${f("max_per_sector", `Most per ${term("sector", "sector")}`, s.max_per_sector, 1, "", "Stocks in one sector fall together.")}
      ${f("min_rr", "Smallest target", s.min_rr, 0.5, "R", "The target must be at least this many times the risk.")}
      ${f("max_new_per_day", "New trades a day", s.max_new_per_day, 1, "", "A brake on overtrading.")}
      ${f("r_target", "Default target", s.r_target, 0.5, "R", "Used when you don't type your own.")}
      ${f("valid_days", "Entry waits", s.valid_days, 1, "sessions", "Then the buy-stop expires.")}
      ${f("time_stop_days", term("time-stop", "Time stop"), s.time_stop_days, 1, "sessions", "Sell if neither the stop nor target is hit.")}
      ${f("min_dollar_volume", "Minimum daily trading", s.min_dollar_volume / 1e6, 5, "$ million", "Easy to get in and out.")}
      <label class="switch-row"><input type="checkbox" id="sw-set-earn" ${s.block_earnings ? "checked" : ""}> Block plans likely to hold through an ${term("earnings", "earnings")} report</label>
    </div>
    <div class="btn-row-left"><button type="button" class="btn btn-primary" id="sw-set-save">Save rules</button><span class="muted" id="sw-set-msg"></span></div>`;
}

// ------------------------------------------------------------------------------------------ load and events
function render(prefill) {
  if (!st) return;
  renderHead();
  if (tab === "today") renderToday();
  if (tab === "scan") renderScan();
  if (tab === "plans") renderPlans(prefill);
  if (tab === "trades") renderTrades();
  if (tab === "journal") renderJournal();
  if (tab === "settings") renderSettings();
}

async function load(prefill) {
  try {
    st = await api("/api/swing");
    if (tab === "journal") journal = await api("/api/swing/journal");
    render(prefill);
  } catch (err) {
    $("sw-today").innerHTML = `<p class="lab-msg">${esc(err.message)}</p>`;
  }
}

function setTab(next, prefill) {
  tab = next;
  storageSet("swing-tab", tab);
  load(prefill);
}

$("sw-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (b) setTab(b.dataset.tab);
});

$("swing").addEventListener("click", async (e) => {
  const el = e.target.closest("button, a");
  if (!el) return;
  const d = el.dataset;
  if (d.goto) { e.preventDefault(); setTab(d.goto); return; }
  if (d.tick) { await act(() => api("/api/swing/routine", "POST", { item: d.tick })); load(); return; }
  if (d.filter !== undefined) { scanFilter = d.filter; renderScan(); return; }
  if (d.plan) { setTab("plans", { symbol: d.plan, setup: d.setup }); return; }
  if (el.id === "sw-run-scan") {
    el.disabled = true;
    msg("sw-scan-msg", "Scanning… (10-30 seconds)");
    const out = await act(() => api("/api/swing/scan", "POST"), "sw-scan-msg");
    el.disabled = false;
    if (out) load();
    return;
  }
  if (el.id === "sw-refresh") {
    msg("sw-refresh-msg", "Checking…");
    const out = await act(() => api("/api/swing/update", "POST"), "sw-refresh-msg");
    if (out) { await load(); msg("sw-refresh-msg", out.changed ? `${out.changed} trade(s) updated.` : "Nothing new."); }
    return;
  }
  if (el.id === "sw-weekly") { await act(() => api("/api/swing/weekly", "POST")); load(); return; }
  if (el.id === "sw-set-save") { saveSettings(); return; }
  const id = d.replay || d.check || d.approve || d.cancel || d.close || d.edit;
  if (!id) return;
  const m = `sw-msg-${id}`;
  if (d.edit) { openPlan = openPlan === Number(id) ? null : Number(id); render(); return; }
  if (d.replay) {
    msg(m, "Replaying 10,000 times…");
    if (await act(() => api(`/api/swing/plans/${id}/replay`, "POST"), m)) load();
  } else if (d.check) {
    msg(m, "Checking every rule…");
    if (await act(() => api(`/api/swing/plans/${id}/check`, "POST"), m)) load();
  } else if (d.approve) {
    const live = st.route === "alpaca_live";
    if (!armed(el, live ? "Click again: REAL money" : "Click again to approve")) return;
    if (await act(() => api(`/api/swing/plans/${id}/approve`, "POST", { confirm: live }), m)) { setTab("trades"); }
  } else if (d.cancel) {
    if (!armed(el, "Click again")) return;
    if (await act(() => api(`/api/swing/plans/${id}/cancel`, "POST"), m)) load();
  } else if (d.close) {
    if (!armed(el, "Click again: sell now")) return;
    if (await act(() => api(`/api/swing/plans/${id}/close`, "POST"), m)) load();
  }
});

$("swing").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  if (f.id === "sw-plan-form") {
    const val = (id) => $(id).value.trim();
    const body = { symbol: val("sw-p-sym").toUpperCase(), setup: val("sw-p-setup"), thesis: val("sw-p-thesis"),
      r_target: Number(val("sw-p-r")) || null, stop_rule: val("sw-p-rule"),
      entry: Number(val("sw-p-entry")) || null, stop: Number(val("sw-p-stop")) || null };
    msg("sw-p-msg", "Loading the chart and sizing the trade…");
    const t = await act(() => api("/api/swing/plans", "POST", body), "sw-p-msg");
    if (t) { openPlan = null; load(); }
    return;
  }
  if (f.dataset.editform) {
    const id = f.dataset.editform;
    const body = {};
    const t = st.plans.find((p) => p.id === Number(id));
    for (const k of ["entry", "stop", "target", "qty", "time_stop_days"]) {
      const v = Number(f.elements[k].value);
      if (Number.isFinite(v) && v !== t[k]) body[k] = v;
    }
    if (await act(() => api(`/api/swing/plans/${id}`, "PUT", body), `sw-msg-${id}`)) { openPlan = null; load(); }
    return;
  }
  if (f.dataset.raise) {
    const id = f.dataset.raise;
    if (await act(() => api(`/api/swing/plans/${id}/stop`, "POST", { stop: Number(f.elements.stop.value) }), `sw-msg-${id}`)) load();
    return;
  }
  if (f.dataset.review) {
    const id = f.dataset.review;
    const mistakes = [...f.querySelectorAll("input[name=mistake]:checked")].map((i) => i.value);
    const body = { note: f.elements.note.value.trim(), followed_plan: f.elements.followed.checked, mistakes };
    if (await act(() => api(`/api/swing/plans/${id}/review`, "POST", body), `sw-msg-${id}`)) load();
  }
});

$("swing").addEventListener("change", (e) => e.target.closest(".chip")?.classList.toggle("on", e.target.checked));

async function saveSettings() {
  const body = {};
  document.querySelectorAll("[data-set]").forEach((i) => {
    const k = i.dataset.set;
    let v = Number(i.value);
    if (k === "risk_pct" || k === "max_heat_pct") v /= 100;
    if (k === "min_dollar_volume") v *= 1e6;
    body[k] = v;
  });
  body.block_earnings = $("sw-set-earn").checked;
  const out = await act(() => api("/api/swing/settings", "PUT", body), "sw-set-msg");
  if (out) { msg("sw-set-msg", "Saved."); load(); }
}

window.addEventListener("swing:changed", () => {
  if (document.body.classList.contains("view-swing") && !busy && !document.activeElement?.closest("form")) load();
});

onView("swing", () => load());
