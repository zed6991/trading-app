// Simple view: the default, beginner-friendly screen. One card per coin, each ending in one clear action.
const $ = (id) => document.getElementById(id);
const REFRESH_MS = 5000;
const level = () => Number(document.body.dataset.level) || 1;
const learnRefresh = () => window.dispatchEvent(new Event("learn:refresh"));

const advice = {}; // symbol -> latest answer from /api/advice
let state = null;
let checking = false;

function money(n) {
  const v = Math.abs(n);
  const text = v.toLocaleString("en-US", { maximumFractionDigits: v < 100 ? 2 : 0 });
  return `${n < -0.005 ? "-" : ""}$${text}`;
}

async function api(path, method = "GET", body) {
  const res = await fetch(path, body === undefined ? { method }
    : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Something went wrong");
  return data;
}

// ---------------------------------------------------------------- wording
const positionOf = (symbol) => state?.account.positions.find((p) => p.symbol === symbol);

function verdictText(a) {
  if (a.action === "buy") return { word: "Buy", tone: "up" };
  if (a.action === "sell") return { word: "Sell", tone: "down" };
  return { word: positionOf(a.symbol) ? "Keep it" : "Wait", tone: "wait" };
}

function sureness(c) {
  if (c >= 0.75) return "Quite sure";
  if (c >= 0.5) return "Fairly sure";
  return "Not very sure";
}

function buyPlan() {
  const { account, risk } = state;
  const spend = Math.min(account.cash, account.equity * risk.max_position_pct);
  return {
    spend,
    loss: spend * risk.stop_loss_pct,
    gain: spend * risk.take_profit_pct,
    lossPct: +(risk.stop_loss_pct * 100).toFixed(1),
    gainPct: +(risk.take_profit_pct * 100).toFixed(1),
  };
}

function nextStep() {
  const answers = state.symbols.map((s) => advice[s]).filter(Boolean);
  if (checking && answers.length < state.symbols.length) return "Checking the coins…";
  if (state.account.halted) return "Trading is paused for now, so there's nothing to do.";
  const acts = answers.filter((a) => a.action !== "wait").sort((a, b) => b.confidence - a.confidence);
  if (!acts.length) {
    return state.account.positions.length || !answers.length ? "Nothing to do right now. Waiting is a fine choice."
      : "Nothing looks like a good buy right now. Waiting is a fine choice, or press \"Buy anyway\" on any coin to practise.";
  }
  return `Your next step: ${acts[0].action} ${acts[0].name}.`;
}

// ---------------------------------------------------------------- rendering
function card(symbol) {
  const el = document.createElement("article");
  el.className = "s-card";
  el.dataset.symbol = symbol;
  el.innerHTML = `
    <div class="s-card-head"><h2></h2><span class="s-price mono"></span></div>
    <div class="s-verdict"></div>
    <p class="s-why"></p>
    <p class="s-meta"></p>
    <p class="s-owned" hidden></p>
    <div class="s-exits" hidden>
      <p class="s-exits-text"></p>
      <button type="button" class="btn btn-sm btn-ghost s-exits-edit">Change my exits</button>
      <form class="s-exits-form" hidden autocomplete="off">
        <label>Stop-loss price <input type="number" name="stop_loss" step="any" min="0"></label>
        <label>Take-profit price <input type="number" name="take_profit" step="any" min="0"></label>
        <div class="s-exits-btns"><button type="submit" class="btn btn-sm btn-primary">Save</button>
        <button type="button" class="btn btn-sm btn-ghost s-exits-cancel">Cancel</button></div>
      </form>
    </div>
    <p class="s-plan" hidden></p>
    <button type="button" class="btn btn-block s-act" hidden></button>
    <button type="button" class="btn btn-block btn-ghost s-sell" hidden></button>
    <p class="s-msg" hidden></p>`;
  el.querySelector(".s-act").addEventListener("click", () => act(symbol));
  el.querySelector(".s-sell").addEventListener("click", () => sell(symbol));
  const form = el.querySelector(".s-exits-form");
  el.querySelector(".s-exits-edit").addEventListener("click", () => {
    const pos = positionOf(symbol);
    form.stop_loss.value = pos?.stop_loss ?? "";
    form.take_profit.value = pos?.take_profit ?? "";
    form.hidden = false;
    form.stop_loss.focus();
  });
  el.querySelector(".s-exits-cancel").addEventListener("click", () => { form.hidden = true; });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const num = (v) => (v === "" ? null : Number(v));
    trade(symbol, () => api(`/api/positions/${symbol}`, "PUT",
      { stop_loss: num(form.stop_loss.value), take_profit: num(form.take_profit.value) }));
    form.hidden = true;
  });
  return el;
}

function renderCard(el) {
  const symbol = el.dataset.symbol;
  const a = advice[symbol];
  const pos = positionOf(symbol);
  const field = (cls) => el.querySelector(cls);
  const name = a?.name || symbol.replace(/USDT$/, "");
  const price = state.prices[symbol];

  field("h2").textContent = { BTCUSDT: "Bitcoin", ETHUSDT: "Ethereum", SOLUSDT: "Solana" }[symbol] || name;
  field(".s-price").textContent = price ? money(price) : "";

  const verdict = a ? verdictText(a) : { word: "Checking…", tone: "wait" };
  const box = field(".s-verdict");
  box.textContent = verdict.word;
  box.className = `s-verdict tone-${verdict.tone}`;
  field(".s-why").textContent = a?.summary || "Looking at the last few days of prices.";
  field(".s-meta").textContent = !a ? "" : `${sureness(a.confidence)} · ${a.source === "rules" ? "backup rules (the AI is off)" : "from your AI"}`;

  const owned = field(".s-owned");
  owned.hidden = !pos;
  if (pos) {
    const pnl = pos.unrealized_pnl;
    owned.textContent = `You own ${money(pos.market_value)} of this. You're ${pnl >= 0 ? "up" : "down"} ${money(Math.abs(pnl))} so far. `
      + (level() >= 2 ? "" : "We'll sell it for you if it moves too far either way (Level 2 teaches how).");
    owned.className = `s-owned ${pnl >= 0 ? "up" : "down"}`;
  }

  const exits = field(".s-exits");
  exits.hidden = !pos || level() < 2;
  if (pos && level() >= 2) {
    const sl = pos.stop_loss ? `falls to ${money(pos.stop_loss)} (stop-loss)` : null;
    const tp = pos.take_profit ? `rises to ${money(pos.take_profit)} (take-profit)` : null;
    field(".s-exits-text").textContent = sl || tp
      ? `It sells automatically if the price ${[sl, tp].filter(Boolean).join(" or ")}. You paid ${money(pos.avg_price)}.`
      : "No automatic exits are set on this coin. Consider adding a stop-loss.";
  } else {
    field(".s-exits-form").hidden = true;
  }

  // Buying is always possible while there's room: this is practice, and the suggestion is only a guess.
  const canBuy = !!a && !pos && !state.account.halted
    && state.account.positions.length < state.risk.max_open_positions;
  const suggested = a?.action === "buy";
  const plan = field(".s-plan");
  plan.hidden = !canBuy;
  const button = field(".s-act");
  button.hidden = !canBuy;
  if (canBuy) {
    const p = buyPlan();
    plan.textContent = (suggested ? "" : "The suggestion is to wait, but this is practice, so you can still buy to learn. ")
      + `If you buy: we'd put in ${money(p.spend)}. We'll sell for you if it falls ${p.lossPct}% `
      + `(you'd lose about ${money(p.loss)}) or rises ${p.gainPct}% (you'd make about ${money(p.gain)}), before small fees.`;
    button.textContent = suggested ? `Buy ${money(p.spend)} of ${name}` : `Buy anyway: ${money(p.spend)} of ${name}`;
    button.className = `btn btn-block s-act ${suggested ? "btn-buy" : "btn-ghost"}`;
  }

  const sellButton = field(".s-sell");
  sellButton.hidden = !pos;
  if (pos) {
    sellButton.textContent = `Sell my ${name}`;
    sellButton.className = `btn btn-block s-sell ${a?.action === "sell" ? "btn-sell" : "btn-ghost"}`;
  }
}

function render() {
  if (!state) return;
  const { account } = state;
  $("s-balance").textContent = money(account.equity);
  const change = account.equity - account.starting_cash;
  const result = $("s-result");
  const untouched = Math.abs(change) < 0.005 && !account.positions.length;
  result.textContent = untouched ? "You haven't made any trades yet."
    : `You're ${change >= 0 ? "up" : "down"} ${money(Math.abs(change))} since you started.`;
  result.className = `s-result ${untouched ? "" : change >= 0 ? "up" : "down"}`;
  $("s-next").textContent = nextStep();

  const notice = $("s-notice");
  notice.hidden = !account.halted;
  if (account.halted) notice.textContent = "We've paused buying for today because you lost a lot in one day. This protects you, and it starts again tomorrow.";

  const cards = $("s-cards");
  if (cards.children.length !== state.symbols.length) {
    cards.replaceChildren(...state.symbols.map(card));
  }
  [...cards.children].forEach(renderCard);
  if (level() >= 2) renderSafety();
}

// ---------------------------------------------------------------- safety net (level 2)
const SAFETY_KEYS = ["stop_loss_pct", "take_profit_pct", "max_position_pct"];
let safetyDirty = false;

function renderSafety() {
  const form = $("s-safety-form");
  if (!safetyDirty) {
    for (const key of SAFETY_KEYS) form[key].value = +(state.risk[key] * 100).toFixed(2);
  }
  const p = buyPlan();
  $("s-safety-note").textContent = `With these settings, each buy puts in up to ${money(p.spend)}. If the stop-loss is hit `
    + `you'd lose about ${money(p.loss)}; if the take-profit is hit you'd make about ${money(p.gain)} (before fees). `
    + `That's risking $1 to make $${(p.gain / Math.max(p.loss, 1e-9)).toFixed(1)}.`;
}

async function saveSafety(e) {
  e.preventDefault();
  const form = e.target;
  const body = Object.fromEntries(SAFETY_KEYS.map((k) => [k, Number(form[k].value) / 100]));
  try {
    await api("/api/risk", "PUT", body);
    safetyDirty = false;
    $("s-safety-note").dataset.saved = "1";
    learnRefresh();
  } catch (err) {
    $("s-safety-note").textContent = `Couldn't save: ${err.message}`;
    return;
  }
  await refreshState();
}

// ---------------------------------------------------------------- recent trades + explanations
const explanations = {};
let trades = [];

function tradeLine(t) {
  const name = { BTCUSDT: "Bitcoin", ETHUSDT: "Ethereum", SOLUSDT: "Solana" }[t.symbol] || t.symbol.replace(/USDT$/, "");
  const when = new Date(t.ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const how = { manual: "by you", stop_loss: "by your stop-loss", take_profit: "by your take-profit",
    trailing_stop: "by your trailing stop", signal: "by auto-trading" }[t.reason] || "";
  if (t.side === "buy") return { text: `Bought ${money(t.qty * t.price)} of ${name} ${how}`, when, tone: "" };
  const pnl = t.pnl ?? 0;
  return { text: `Sold ${name} ${how}: ${pnl >= 0 ? "made" : "lost"} ${money(Math.abs(pnl))}`, when, tone: pnl >= 0 ? "up" : "down" };
}

function renderTrades() {
  const list = $("s-trade-list");
  $("s-trades-empty").hidden = trades.length > 0;
  list.replaceChildren(...trades.map((t) => {
    const li = document.createElement("li");
    const line = tradeLine(t);
    li.className = "s-trade";
    li.innerHTML = `<div class="s-trade-row"><span class="s-trade-text"></span><span class="s-trade-when"></span>
      <button type="button" class="btn btn-sm">Explain</button></div><div class="s-explain" hidden></div>`;
    li.querySelector(".s-trade-text").textContent = line.text;
    if (line.tone) li.querySelector(".s-trade-text").classList.add(line.tone);
    li.querySelector(".s-trade-when").textContent = line.when;
    const btn = li.querySelector("button");
    const box = li.querySelector(".s-explain");
    const show = (x) => {
      box.hidden = false;
      box.innerHTML = "<b></b><p class='e-what'></p><p class='e-why'></p><p class='e-lesson'><span>Lesson:</span> </p><p class='e-src'></p>";
      box.querySelector("b").textContent = x.headline;
      box.querySelector(".e-what").textContent = x.what;
      box.querySelector(".e-why").textContent = x.why;
      box.querySelector(".e-lesson").append(x.lesson);
      box.querySelector(".e-src").textContent = x.source === "rules" ? "Explained by the app's rules" : "Explained by your AI";
      btn.textContent = "Hide";
    };
    if (explanations[t.id]?.open) show(explanations[t.id].data);
    btn.addEventListener("click", async () => {
      if (!box.hidden) {
        box.hidden = true;
        btn.textContent = "Explain";
        explanations[t.id].open = false;
        return;
      }
      if (explanations[t.id]) {
        explanations[t.id].open = true;
        show(explanations[t.id].data);
        return;
      }
      btn.disabled = true;
      btn.textContent = "Thinking…";
      try {
        const data = await api(`/api/trades/${t.id}/explain`, "POST");
        explanations[t.id] = { data, open: true };
        show(data);
        learnRefresh();
      } catch (err) {
        box.hidden = false;
        box.textContent = `Couldn't explain this trade. ${err.message}`;
        btn.textContent = "Explain";
      }
      btn.disabled = false;
    });
    return li;
  }));
}

async function refreshTrades() {
  try {
    const latest = await api("/api/trades?limit=8");
    if (latest.map((t) => t.id).join() !== trades.map((t) => t.id).join()) {
      trades = latest;
      renderTrades();
    }
  } catch { /* shown on next refresh */ }
}

function showMessage(symbol, text) {
  const el = $("s-cards").querySelector(`[data-symbol="${symbol}"] .s-msg`);
  el.textContent = text;
  el.hidden = !text;
}

// ---------------------------------------------------------------- data + actions
async function refreshState() {
  try {
    state = await api("/api/state");
    render();
    refreshTrades();
  } catch {
    $("s-next").textContent = "Can't reach the app right now. Trying again…";
  }
}

async function checkCoins() {
  if (checking || !state) return;
  checking = true;
  $("s-refresh").disabled = true;
  render();
  for (const symbol of state.symbols) {
    try {
      advice[symbol] = await api(`/api/advice/${symbol}`, "POST");
      showMessage(symbol, "");
    } catch (err) {
      showMessage(symbol, `Couldn't check this coin. ${err.message}`);
    }
    await refreshState();
  }
  checking = false;
  $("s-refresh").disabled = false;
  render();
}

async function trade(symbol, request) {
  try {
    await request();
    showMessage(symbol, "");
    learnRefresh();
    if (advice[symbol]) advice[symbol] = { ...advice[symbol], action: "wait" };
  } catch (err) {
    showMessage(symbol, `That didn't work. ${err.message}`);
  }
  await refreshState();
}

const act = (symbol) => trade(symbol, () => fetch("/api/orders", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ symbol, side: "buy" }),
}).then(async (res) => { if (!res.ok) throw new Error((await res.json()).detail); }));

const sell = (symbol) => trade(symbol, () => api(`/api/positions/${symbol}/close`, "POST"));

// ---------------------------------------------------------------- start
$("s-refresh").addEventListener("click", checkCoins);
$("s-safety-form").addEventListener("input", () => { safetyDirty = true; });
$("s-safety-form").addEventListener("submit", saveSafety);
window.addEventListener("learn:level", render);
await refreshState();
setInterval(refreshState, REFRESH_MS);
checkCoins();
