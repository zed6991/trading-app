// Simple view: the default, beginner-friendly screen. One card per coin, each ending in one clear action.
const $ = (id) => document.getElementById(id);
const REFRESH_MS = 5000;
const STORE_KEY = "view";

const advice = {}; // symbol -> latest answer from /api/advice
let state = null;
let checking = false;

function money(n) {
  const v = Math.abs(n);
  const text = v.toLocaleString("en-US", { maximumFractionDigits: v < 100 ? 2 : 0 });
  return `${n < -0.005 ? "-" : ""}$${text}`;
}

async function api(path, method = "GET") {
  const res = await fetch(path, { method });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || "Something went wrong");
  return body;
}

// ---------------------------------------------------------------- view switch
function readView() {
  try { return localStorage.getItem(STORE_KEY); } catch { return null; }
}

function setView(view) {
  const simple = view !== "advanced";
  document.body.classList.toggle("simple-mode", simple);
  $("view-toggle").textContent = simple ? "Advanced" : "Simple view";
  try { localStorage.setItem(STORE_KEY, simple ? "simple" : "advanced"); } catch { /* preference only */ }
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
  if (!acts.length) return "Nothing to do right now. Waiting is a fine choice.";
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
    <p class="s-plan" hidden></p>
    <button type="button" class="btn btn-block s-act" hidden></button>
    <button type="button" class="btn btn-block btn-ghost s-sell" hidden></button>
    <p class="s-msg" hidden></p>`;
  el.querySelector(".s-act").addEventListener("click", () => act(symbol));
  el.querySelector(".s-sell").addEventListener("click", () => sell(symbol));
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
      + "We'll sell it for you if it moves too far either way.";
    owned.className = `s-owned ${pnl >= 0 ? "up" : "down"}`;
  }

  const canBuy = a?.action === "buy" && !pos && !state.account.halted
    && state.account.positions.length < state.risk.max_open_positions;
  const plan = field(".s-plan");
  plan.hidden = !canBuy;
  const button = field(".s-act");
  button.hidden = !canBuy;
  if (canBuy) {
    const p = buyPlan();
    plan.textContent = `If you buy: we'd put in ${money(p.spend)}. We'll sell for you if it falls ${p.lossPct}% `
      + `(you'd lose about ${money(p.loss)}) or rises ${p.gainPct}% (you'd make about ${money(p.gain)}), before small fees.`;
    button.textContent = `Buy ${money(p.spend)} of ${name}`;
    button.className = "btn btn-block s-act btn-buy";
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
$("view-toggle").addEventListener("click", () => {
  setView(document.body.classList.contains("simple-mode") ? "advanced" : "simple");
});
$("s-refresh").addEventListener("click", checkCoins);
setView(readView());
await refreshState();
setInterval(refreshState, REFRESH_MS);
checkCoins();
