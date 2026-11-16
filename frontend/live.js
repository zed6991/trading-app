// Go live (Level 14): readiness checklist, evidence check, Alpaca paper, then real money with a cap.
import { $, api, esc, num, pct, money, tone, learnRefresh, term, armed, onView } from "./ui.js";

let st = null;
let timer = null;

const ROUTE_NAMES = { sim: "The app's paper account", alpaca_paper: "Alpaca paper trading", alpaca_live: "Alpaca, real money" };

function renderRoute() {
  const r = st.route;
  $("lv-route").className = `lv-route lv-${r}`;
  $("lv-route").innerHTML = `<span class="lv-dot"></span><div><b>The agent's orders go to: ${ROUTE_NAMES[r]}</b>
    <p>${r === "alpaca_live" ? `Real money, capped at ${money(st.cap_usd)}. Every order needs your approval on the Agent screen.`
      : r === "alpaca_paper" ? "Real orders at Alpaca with pretend money: your forward test."
      : "Pretend money inside this app. Nothing is sent anywhere."}</p></div>`;
}

function renderChecks() {
  const checks = st.readiness.checks;
  $("lv-checks").innerHTML = `<ol class="lv-checks">${checks.map((c) => `<li class="${c.ok ? "ok" : ""}">
      <span class="lv-tick" aria-hidden="true">${c.ok ? "✓" : ""}</span>
      <div><b>${esc(c.title)}</b><p class="muted">${esc(c.detail)}</p></div></li>`).join("")}</ol>`;
}

function renderEvidence() {
  const ev = st.evidence;
  const rows = ev ? Object.values(ev.symbols) : [];
  const stale = ev && !st.readiness.checks.find((c) => c.id === "evidence").ok && ev.eligible.length;
  $("lv-evidence").innerHTML = `<p>Tests the agent's strategy, with its exact settings, on each stock or ETF on its watchlist:
      the last 30% of history after ${term("costs", "costs")}, against ${term("buy-and-hold", "buy & hold")} and
      ${st.rules.luck_bar}% of random traders. Crypto stays on paper.</p>
    ${rows.length ? `<table class="lab-table lv-ev"><tr><th>Asset</th><th>Strategy</th><th>Buy &amp; hold</th><th>Beat random</th></tr>
      ${rows.map((r) => `<tr class="lv-ev-row"><td><span class="lv-pass ${r.ok ? "ok" : ""}">${r.ok ? "✓" : "✕"}</span>${esc(r.name || r.symbol)}</td>
        <td class="${tone(r.test_return_pct)}">${r.test_return_pct === undefined ? "–" : pct(r.test_return_pct)}</td>
        <td>${r.buy_hold_pct === undefined ? "–" : pct(r.buy_hold_pct)}</td><td>${r.beat_random_pct == null ? "–" : `${num(r.beat_random_pct, 0)}%`}</td></tr>
        <tr class="lv-why"><td colspan="4" class="${r.ok ? "up" : "muted"}">${esc(r.why)}</td></tr>`).join("")}</table>
      <p class="muted">Checked ${new Date(ev.ts * 1000).toLocaleString()}.${stale ? " You've changed the strategy since: check again." : ""}
        Passing doesn't promise profit: you could have picked these settings by looking at the same history. That's why Alpaca paper comes next.</p>`
      : ""}
    <div class="btn-row-left"><button type="button" class="btn btn-primary" id="lv-check">Check my strategy</button>
      <span class="muted" id="lv-check-msg"></span></div>`;
  $("lv-check").addEventListener("click", checkEvidence);
}

function renderBroker() {
  const box = $("lv-broker-box");
  box.hidden = st.route === "sim";
  if (st.route === "sim") return;
  if (st.error) { $("lv-broker").innerHTML = `<p class="lab-msg">${esc(st.error)}</p>`; return; }
  const a = st.account || {};
  const day = a.equity != null && a.last_equity ? a.equity - a.last_equity : null;
  $("lv-broker").innerHTML = `<div class="lab-cards">
      <div class="lab-card"><div class="k">Account value</div><div class="v">${money(a.equity)}</div><div class="s">${esc(a.currency || "USD")}</div></div>
      <div class="lab-card"><div class="k">Today</div><div class="v ${tone(day)}">${day == null ? "–" : money(day)}</div><div class="s">buys pause at -${num(st.rules.daily_loss_pct, 0)}%</div></div>
      <div class="lab-card"><div class="k">The app has in the market</div><div class="v">${money(st.deployed)}</div><div class="s">${st.route === "alpaca_live" ? `cap ${money(st.cap_usd)}` : "paper"}</div></div>
    </div>
    ${(st.positions || []).length ? `<table class="lab-table"><tr><th>Asset</th><th>Shares</th><th>Bought by the app</th><th>Price</th><th>Profit</th></tr>
      ${st.positions.map((p) => `<tr><td>${esc(p.symbol)}</td><td>${num(p.qty, 0)}</td><td>${num(p.app_qty, 0)}</td><td>${money(p.price)}</td>
        <td class="${tone(p.pnl)}">${money(p.pnl)}</td></tr>`).join("")}</table>` : `<p class="muted">No positions at the broker.</p>`}
    <p class="muted">The kill switch on the Agent screen cancels the app's orders here too, and sells only shares the app bought.</p>`;
}

function renderSwitch() {
  const r = st.readiness;
  const live = st.route === "alpaca_live";
  $("lv-switch").innerHTML = `<div class="lv-options">
      <button type="button" class="lv-opt ${st.route === "sim" ? "on" : ""}" data-route="sim"><b>App paper</b><span>Safe default</span></button>
      <button type="button" class="lv-opt ${st.route === "alpaca_paper" ? "on" : ""}" data-route="alpaca_paper" ${r.ready_paper ? "" : "disabled"}>
        <b>Alpaca paper</b><span>${r.ready_paper ? "Forward test" : "Locked: checklist 1-4"}</span></button>
      <button type="button" class="lv-opt danger ${live ? "on" : ""}" data-route="alpaca_live" ${r.ready_live ? "" : "disabled"}>
        <b>Real money</b><span>${r.ready_live ? "Capped, approve each order" : "Locked: whole checklist"}</span></button>
    </div>
    <form class="lv-live-form" id="lv-live-form" hidden>
      <p>Start with money you could lose without it hurting. You can lower the cap any time; the app never puts more than this in the market.</p>
      <label>Cap <span class="input-suffix-inline">$ <input type="number" id="lv-cap" min="100" max="${st.rules.max_cap}" step="100" value="${st.cap_usd}"></span></label>
      <label>Type <b>${esc(st.confirm_phrase)}</b> <input type="text" id="lv-confirm" autocomplete="off" spellcheck="false"></label>
      <button type="submit" class="btn btn-danger">Switch on real money</button>
    </form>
    <p class="muted" id="lv-msg"></p>`;
}

function renderOrders() {
  const rows = st.orders || [];
  $("lv-orders").innerHTML = rows.length ? `<table class="lab-table"><tr><th>When</th><th>Where</th><th>Order</th><th>Status</th><th>Filled</th></tr>
    ${rows.map((o) => `<tr><td>${new Date(o.created * 1000).toLocaleString()}</td><td>${o.route === "alpaca_live" ? '<b class="down">real</b>' : "paper"}</td>
      <td>${o.kind === "stop" ? `stop-loss sell ${num(o.qty, 0)} ${esc(o.symbol)} at ${money(o.stop_price)}` : `${o.side} ${num(o.qty, 0)} ${esc(o.symbol)}`}</td>
      <td>${esc(o.status)}</td><td>${o.filled_qty ? `${num(o.filled_qty, 0)} @ ${money(o.avg_price)}` : "–"}</td></tr>`).join("")}</table>`
    : `<p class="muted">Nothing sent to a broker yet.</p>`;
}

function render() {
  renderRoute(); renderChecks(); renderEvidence(); renderBroker(); renderSwitch(); renderOrders();
}

async function load() {
  try {
    st = await api("/api/live");
    render();
  } catch (err) {
    $("lv-checks").innerHTML = `<p class="lab-msg">${esc(err.message)}</p>`;
  }
}

async function checkEvidence() {
  const btn = $("lv-check");
  btn.disabled = true;
  $("lv-check-msg").textContent = "Testing each asset against buy & hold and random traders…";
  try {
    st = await api("/api/live/evidence", "POST");
    render();
    learnRefresh();
  } catch (err) { $("lv-check-msg").textContent = err.message; btn.disabled = false; }
}

async function setRoute(route, extra = {}) {
  try {
    st = await api("/api/live/route", "PUT", { route, ...extra });
    render();
    learnRefresh();
    $("lv-msg").textContent = `Done: orders now go to ${ROUTE_NAMES[route]}.`;
  } catch (err) { $("lv-msg").textContent = err.message; }
}

$("lv-switch").addEventListener("click", (e) => {
  const b = e.target.closest("[data-route]");
  if (!b || b.disabled) return;
  if (b.dataset.route === "alpaca_live") {
    $("lv-live-form").hidden = false;
    $("lv-confirm").focus();
    return;
  }
  if (b.dataset.route === "sim" && st.route !== "sim" && !armed(b.querySelector("b"), "Click again")) return;
  setRoute(b.dataset.route);
});
$("lv-switch").addEventListener("submit", (e) => {
  e.preventDefault();
  setRoute("alpaca_live", { cap_usd: Number($("lv-cap").value), confirm: $("lv-confirm").value });
});

onView("live", async () => {
  await load();
  clearInterval(timer);
  timer = setInterval(() => {
    if (document.body.classList.contains("view-live") && document.visibilityState === "visible" && st && st.route !== "sim") load();
  }, 30000);
});
