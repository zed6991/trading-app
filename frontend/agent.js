// Agent (Level 13): configure the agent, approve its proposals, read its run log, use the kill switch; plus the capstone.
import { $, api, esc, num, pct, money, tone, card, learnRefresh, term, aiSource, armed, onView, level } from "./ui.js";

let assets = [];
let state = null;
let timer = null;

const ago = (t) => {
  const s = Date.now() / 1000 - t;
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString();
};

function renderConfig() {
  const c = state.config;
  const strat = c.strategy.type === "custom" ? `custom:${c.strategy.name}` : `builtin:${c.strategy.name}`;
  $("ag-config").innerHTML = `
    <div class="lab-form">
      <label class="switch-row"><input type="checkbox" id="ag-enabled" ${c.enabled ? "checked" : ""}> Run on a schedule</label>
      <label>Every <select id="ag-interval">${[15, 30, 60, 240, 1440].map((m) => `<option value="${m}" ${m === c.interval_min ? "selected" : ""}>${m < 60 ? `${m} min` : m === 1440 ? "day" : `${m / 60} h`}</option>`).join("")}</select></label>
      <label>Mode <select id="ag-mode"><option value="approve" ${c.mode === "approve" ? "selected" : ""}>Ask me to approve each trade</option>
        <option value="auto" ${c.mode === "auto" ? "selected" : ""} ${level() < 13 ? "disabled" : ""}>Auto: trade unflagged proposals</option></select></label>
      <label>Strategy <select id="ag-strat">${state.strategies.map((s) => `<option value="${s.type}:${esc(s.name)}" ${`${s.type}:${s.name}` === strat ? "selected" : ""}>${s.type === "custom" ? "My code: " : ""}${esc(s.name.replace(/_/g, " "))}</option>`).join("")}</select></label>
      <label>Risk per trade <span class="input-suffix-inline"><input type="number" id="ag-risk" min="0.1" max="3" step="0.1" value="${num(c.risk_pct * 100, 1)}"> % of account</span></label>
      <label class="switch-row"><input type="checkbox" id="ag-research" ${c.use_research ? "checked" : ""}> Read the news first</label>
    </div>
    <div class="chips" id="ag-watch">${assets.map((a) => `<label class="chip ${c.watchlist.includes(a.symbol) ? "on" : ""}"><input type="checkbox" value="${a.symbol}" ${c.watchlist.includes(a.symbol) ? "checked" : ""}>${esc(a.symbol.replace(/USDT$/, ""))}</label>`).join("")}</div>
    <div class="btn-row-left">
      <button type="button" class="btn btn-primary" id="ag-save">Save settings</button>
      <button type="button" class="btn" id="ag-run">Run now</button>
      <span class="muted" id="ag-msg">${c.enabled && state.next_run ? `Next scheduled run ${new Date(state.next_run * 1000).toLocaleTimeString()}.` : ""} AI check: ${esc(state.ai_route)}</span>
    </div>`;
  $("ag-watch").addEventListener("change", (e) => e.target.closest(".chip")?.classList.toggle("on", e.target.checked));
  $("ag-save").addEventListener("click", save);
  $("ag-run").addEventListener("click", runNow);
}

function proposalCard(p) {
  const ai = p.ai || {};
  return `<article class="ag-prop ${p.flagged ? "flagged" : ""}">
    <header><b class="${p.side === "buy" ? "up" : "down"}">${p.side === "buy" ? "Buy" : "Sell"} ${esc(p.name)}</b>
      <span class="mono">${money(p.notional)}</span>${p.stop_loss ? `<span class="muted">stop ${money(p.stop_loss)}${p.risk_dollars ? ` · risks ~${money(p.risk_dollars)}` : ""}</span>` : ""}
      <span class="muted">${ago(p.created)}</span></header>
    <ul>${p.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
    <p class="ag-ai ${p.flagged ? "down" : ""}">${p.flagged ? "⚠ AI check flagged this: " : "AI check: "}${esc(ai.note || "no note")}</p>
    ${p.status === "pending" ? `<div class="btn-row-left"><button type="button" class="btn btn-buy btn-sm" data-approve="${p.id}">Approve</button>
      <button type="button" class="btn btn-ghost btn-sm" data-reject="${p.id}">Reject</button></div>`
      : `<p class="muted">${esc(p.status)}${p.error ? `: ${esc(p.error)}` : ""}${p.decided ? ` · ${ago(p.decided)}` : ""}</p>`}
  </article>`;
}

function renderLists() {
  $("ag-pending").innerHTML = state.pending.length ? state.pending.map(proposalCard).join("")
    : `<p class="muted">No proposals waiting. Press "Run now", or wait for the schedule.</p>`;
  $("ag-recent").innerHTML = state.recent.length ? state.recent.map(proposalCard).join("") : `<p class="muted">Nothing decided yet.</p>`;
  $("ag-log").innerHTML = state.runs.length ? state.runs.map((r, i) => `<details class="ag-run" ${i === 0 ? "open" : ""}><summary>${new Date(r.ts * 1000).toLocaleString()} ·
      ${r.manual ? "manual" : "scheduled"} · ${r.proposals.length} proposal${r.proposals.length === 1 ? "" : "s"}</summary>
      <table class="lab-table"><tr><th>Asset</th><th>Signal</th><th>News</th><th>Daily vol</th><th>Decision</th></tr>
      ${r.steps.map((s) => `<tr><td>${esc(s.name || s.symbol)}</td><td>${esc(s.signal || "–")}</td>
        <td>${s.news ? (s.news.error ? esc(s.news.error) : `${s.news.average > 0 ? "+" : ""}${num(s.news.average)}`) : "–"}</td>
        <td>${s.risk ? `${num(s.risk.daily_vol_pct)}%` : "–"}</td><td>${esc(s.decision || "")}</td></tr>`).join("")}</table></details>`).join("")
    : `<p class="muted">The agent hasn't run yet.</p>`;
}

async function load() {
  try {
    state = await api("/api/agent");
    renderConfig();
    renderLists();
  } catch (err) {
    $("ag-config").innerHTML = `<p class="lab-msg">${esc(err.message)}</p>`;
  }
  loadCapstone();
}

async function save() {
  const [type, ...rest] = $("ag-strat").value.split(":");
  const name = rest.join(":");
  const body = {
    enabled: $("ag-enabled").checked, mode: $("ag-mode").value, interval_min: Number($("ag-interval").value),
    risk_pct: Number($("ag-risk").value) / 100, use_research: $("ag-research").checked,
    watchlist: [...document.querySelectorAll("#ag-watch input:checked")].map((i) => i.value),
    strategy: type === "custom" ? { type, name } : { type, name, params: (state.config.strategy.name === name ? state.config.strategy.params : {}) },
  };
  try {
    state = await api("/api/agent", "PUT", body);
    renderConfig();
    $("ag-msg").textContent = "Saved.";
    learnRefresh();
  } catch (err) { $("ag-msg").textContent = err.message; }
}

async function runNow() {
  const btn = $("ag-run");
  btn.disabled = true;
  btn.textContent = "Running…";
  $("ag-msg").textContent = "Reading news, running the strategy and checking risk for each asset. This can take a minute.";
  try {
    state = await api("/api/agent/run", "POST");
    renderLists();
    $("ag-msg").textContent = "Done. See the proposals below and the run log.";
    learnRefresh();
  } catch (err) { $("ag-msg").textContent = err.message; }
  btn.disabled = false;
  btn.textContent = "Run now";
}

async function decide(id, approve) {
  try {
    state = await api(`/api/agent/proposals/${id}`, "POST", { approve });
    renderLists();
    learnRefresh();
  } catch (err) { alertBox(err.message); }
}

function alertBox(text) {
  $("ag-killmsg").textContent = text;
}

async function kill(btn) {
  if (!armed(btn, "Click again: stop everything")) return;
  try {
    state = await api("/api/agent/kill", "POST", { sell_positions: $("ag-sell").checked });
    renderConfig();
    renderLists();
    alertBox(`Stopped. Agent off, proposals and waiting orders cancelled${state.sold ? `, ${state.sold} agent position(s) sold` : ""}. Auto-trading is off.`);
    learnRefresh();
  } catch (err) { alertBox(err.message); }
}

// ---------------------------------------------------------------- capstone
async function loadCapstone() {
  const box = $("cap-body");
  try {
    const { status } = await api("/api/capstone");
    if (!status) {
      box.innerHTML = `<p>Write your ${term("trading-plan", "trading plan")} first: what you'll trade, your rules (built-in, your own code, a portfolio
        or the agent), how you size, and when you'd stop. Then run it for 4 weeks. The report compares your paper account with SPY,
        the ${term("benchmark", "benchmark")}.</p>
        <textarea id="cap-plan" rows="5" class="cap-plan" placeholder="Example: I'll hold a risk-parity mix of SPY, TLT and GLD, rebalanced monthly, and let the agent trade my saved momentum strategy on QQQ with 1% risk per trade. I'll stop if the account falls 10%."></textarea>
        <button type="button" class="btn btn-primary" id="cap-start">Start the 4-week capstone</button><p class="lab-msg" id="cap-msg"></p>`;
      $("cap-start").addEventListener("click", startCapstone);
      return;
    }
    const p = Math.min(100, (status.days / status.days_needed) * 100);
    const rep = status.report;
    box.innerHTML = `<p class="muted">Started ${new Date(status.started_at * 1000).toLocaleDateString()}. Plan: <i>${esc(status.plan)}</i></p>
      <div class="l-progress"><div class="l-progress-fill" style="width:${p}%"></div></div>
      <p>Day ${num(status.days, 1)} of ${status.days_needed}${status.ready ? " · ready for your final report" : ""}</p>
      <div class="lab-cards">${card("Your account", pct(status.return_pct, 2), `${money(status.start_equity)} → ${money(status.equity)}`, status.return_pct > status.spy_return_pct ? "good" : "")}
        ${card("SPY", pct(status.spy_return_pct, 2), "the benchmark over the same days")}</div>
      <button type="button" class="btn ${status.ready ? "btn-primary" : ""}" id="cap-report">${status.ready ? "Generate final report" : "Preview the report so far"}</button>
      <div id="cap-report-out">${rep ? reportHtml(rep) : ""}</div>`;
    $("cap-report").addEventListener("click", makeReport);
  } catch (err) {
    box.innerHTML = `<p class="lab-msg">${esc(err.message)}</p>`;
  }
}

function reportHtml(r) {
  const f = r.facts;
  const w = r.writeup;
  return `<div class="lab-box"><h3>${esc(w.headline)}</h3>${r.final ? "" : '<p class="l-tag">Preview: the final report needs 28 days</p>'}
    <p>${esc(w.what_happened)}</p><p>${esc(w.verdict)}</p><p><b>Next:</b> ${esc(w.next)}</p>
    <div class="lab-cards">${card("Return", pct(f.return_pct, 2), `SPY ${pct(f.spy_return_pct, 2)}`, f.beat_spy ? "good" : "bad")}
      ${card("Max drawdown", f.max_drawdown_pct === undefined ? "–" : `-${num(f.max_drawdown_pct, 1)}%`)}
      ${card("Sharpe", f.sharpe === undefined ? "–" : num(f.sharpe), "needs more days to mean much")}
      ${card("Trades", String(f.trades), `${f.agent_trades} by the agent · fees ${money(f.fees)}`)}</div>
    <p class="code-meta">Write-up by ${r.source === "rules" ? "the app's rules" : esc(r.source)}; numbers checked against your account.</p></div>`;
}

async function startCapstone() {
  try {
    await api("/api/capstone/start", "POST", { plan: $("cap-plan").value });
    learnRefresh();
    loadCapstone();
  } catch (err) { $("cap-msg").textContent = err.message; }
}

async function makeReport() {
  $("cap-report-out").innerHTML = `<p class="lab-loading">Writing your report…</p>`;
  try {
    const r = await api("/api/capstone/report", "POST");
    $("cap-report-out").innerHTML = reportHtml(r);
    learnRefresh();
  } catch (err) { $("cap-report-out").innerHTML = `<p class="lab-msg">${esc(err.message)}</p>`; }
}

// ---------------------------------------------------------------- start
$("ag-pending").addEventListener("click", (e) => {
  const a = e.target.closest("[data-approve]");
  const r = e.target.closest("[data-reject]");
  if (a) decide(Number(a.dataset.approve), true);
  if (r) decide(Number(r.dataset.reject), false);
});
$("ag-kill").addEventListener("click", (e) => kill(e.currentTarget));

onView("agent", async () => {
  if (!assets.length) {
    try { assets = (await api("/api/state")).assets || []; } catch { return; }
  }
  await load();
  clearInterval(timer);
  timer = setInterval(() => {
    if (document.body.classList.contains("view-agent") && document.visibilityState === "visible") {
      api("/api/agent").then((s) => { state = s; renderLists(); }).catch(() => {});
    }
  }, 20000);
});
