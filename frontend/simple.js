// Practice: the beginner trading screen. Stocks/ETFs and crypto in two lists; open one to trade it.
const $ = (id) => document.getElementById(id);
const REFRESH_MS = 5000;
const level = () => Number(document.body.dataset.level) || 1;
const learnRefresh = () => window.dispatchEvent(new Event("learn:refresh"));

const advice = {}; // symbol -> latest answer from /api/advice
const open = new Set(); // expanded rows
const forms = {}; // symbol -> { type, sizing, limit, risk } (what the learner typed)
let state = null;

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

function money(n) {
  const v = Math.abs(n);
  const text = v.toLocaleString("en-US", { minimumFractionDigits: v < 100 ? 2 : 0, maximumFractionDigits: v < 100 ? 2 : 0 });
  return `${n < -0.005 ? "-" : ""}$${text}`;
}
const pct = (x) => `${+(x * 100).toFixed(2)}%`;

async function api(path, method = "GET", body) {
  const res = await fetch(path, body === undefined ? { method }
    : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Something went wrong");
  return data;
}

// ---------------------------------------------------------------- facts
const assetOf = (s) => state?.assets?.find((a) => a.symbol === s) || { symbol: s, name: s, kind: "crypto", fee_rate: 0.001 };
const positionOf = (s) => state?.account.positions.find((p) => p.symbol === s);
const isCrypto = (s) => assetOf(s).kind === "crypto";
const usOpen = () => !!state?.status?.us_market?.open;
const tradable = (s) => isCrypto(s) || usOpen();
const ordersFor = (s) => (state?.orders || []).filter((o) => o.symbol === s);

function formOf(symbol) {
  if (!forms[symbol]) {
    const eq = state?.account.equity || 100000;
    forms[symbol] = { type: "market", sizing: "auto", limit: "", risk: String(Math.round(eq * 0.01)) };
  }
  return forms[symbol];
}

/** What a buy would spend and risk with the current form. */
function buyPlan(symbol) {
  const { account, risk } = state;
  const f = formOf(symbol);
  const cap = account.equity * risk.max_position_pct;
  let spend = Math.min(account.cash, cap);
  let over = false;
  if (f.sizing === "risk" && risk.stop_loss_pct > 0) {
    const want = Number(f.risk) / risk.stop_loss_pct;
    over = want > cap * 1.0001;
    spend = want;
  }
  return { spend, cap, over, loss: spend * risk.stop_loss_pct, gain: spend * risk.take_profit_pct };
}

function costNote(symbol) {
  return isCrypto(symbol) ? "a 0.1% fee each way" : "no commission, just the small spread";
}

// ---------------------------------------------------------------- rows
function row(symbol) {
  const a = assetOf(symbol);
  const el = document.createElement("article");
  el.className = "s-row";
  el.dataset.symbol = symbol;
  el.innerHTML = `
    <button type="button" class="s-row-head" aria-expanded="false">
      <span class="s-row-name"><b>${esc(a.name)}</b><small>${esc(symbol.replace(/USDT$/, ""))} · ${esc(a.sector || a.kind)}</small></span>
      <span class="s-row-own"></span>
      <span class="s-row-price mono"></span>
      <span class="s-row-chev" aria-hidden="true">›</span>
    </button>
    <div class="s-detail" hidden>
      <p class="s-owned"></p>
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
      <div class="s-advice">
        <button type="button" class="btn btn-sm s-advice-btn">Get a suggestion</button>
        <div class="s-advice-out" hidden><span class="s-verdict"></span><p class="s-why"></p><p class="s-meta"></p></div>
      </div>
      <form class="s-order" autocomplete="off">
        <div class="seg seg-sm s-type" role="group" aria-label="Order type">
          <button type="button" data-type="market">Market order</button>
          <button type="button" data-type="limit">Limit order</button>
        </div>
        <label class="s-limit" hidden>Limit price <span class="input-prefix"><span>$</span><input type="number" name="limit" step="any" min="0"></span></label>
        <div class="seg seg-sm s-sizing" role="group" aria-label="How much to buy" data-unlock="2">
          <button type="button" data-sizing="auto">Auto size</button>
          <button type="button" data-sizing="risk">Size by risk</button>
        </div>
        <label class="s-risk" hidden>Dollars I'd accept losing if the stop-loss is hit
          <span class="input-prefix"><span>$</span><input type="number" name="risk" step="1" min="1"></span></label>
        <p class="s-plan"></p>
        <div class="s-actions">
          <button type="submit" class="btn s-buy" data-side="buy"></button>
          <button type="button" class="btn btn-ghost s-sell" data-side="sell" hidden></button>
        </div>
      </form>
      <ul class="s-waiting"></ul>
      <p class="s-msg" hidden></p>
    </div>`;

  const q = (sel) => el.querySelector(sel);
  q(".s-row-head").addEventListener("click", () => {
    if (open.has(symbol)) open.delete(symbol); else open.add(symbol);
    renderRow(el);
  });
  q(".s-advice-btn").addEventListener("click", () => getAdvice(symbol, el));
  q(".s-type").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-type]");
    if (!b) return;
    const f = formOf(symbol);
    f.type = b.dataset.type;
    if (f.type === "limit" && !f.limit) {
      const px = state.prices[symbol];
      if (px) f.limit = String(+(px * (positionOf(symbol) ? 1.02 : 0.99)).toPrecision(6));
    }
    renderRow(el);
  });
  q(".s-sizing").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-sizing]");
    if (!b) return;
    formOf(symbol).sizing = b.dataset.sizing;
    renderRow(el);
  });
  const order = q(".s-order");
  order.addEventListener("input", () => {
    const f = formOf(symbol);
    f.limit = order.limit.value;
    f.risk = order.risk.value;
    renderRow(el, { keepInputs: true });
  });
  order.addEventListener("submit", (e) => { e.preventDefault(); submit(symbol, "buy", el); });
  q(".s-sell").addEventListener("click", () => submit(symbol, "sell", el));
  q(".s-waiting").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-cancel]");
    if (b) act(symbol, el, () => api(`/api/orders/${b.dataset.cancel}`, "DELETE"));
  });

  const exits = q(".s-exits-form");
  q(".s-exits-edit").addEventListener("click", () => {
    const pos = positionOf(symbol);
    exits.stop_loss.value = pos?.stop_loss ? +pos.stop_loss.toPrecision(6) : "";
    exits.take_profit.value = pos?.take_profit ? +pos.take_profit.toPrecision(6) : "";
    exits.hidden = false;
    exits.stop_loss.focus();
  });
  q(".s-exits-cancel").addEventListener("click", () => { exits.hidden = true; });
  exits.addEventListener("submit", (e) => {
    e.preventDefault();
    const num = (v) => (v === "" ? null : Number(v));
    act(symbol, el, () => api(`/api/positions/${symbol}`, "PUT",
      { stop_loss: num(exits.stop_loss.value), take_profit: num(exits.take_profit.value) }));
    exits.hidden = true;
  });
  return el;
}

function renderRow(el, { keepInputs = false } = {}) {
  const symbol = el.dataset.symbol;
  const q = (sel) => el.querySelector(sel);
  const a = assetOf(symbol);
  const pos = positionOf(symbol);
  const price = state.prices[symbol];
  const isOpen = open.has(symbol);
  const f = formOf(symbol);
  const lvl = level();

  q(".s-row-price").textContent = price ? money(price) : "…";
  const own = q(".s-row-own");
  const waiting = ordersFor(symbol);
  if (pos) {
    own.textContent = `You own ${money(pos.market_value)} (${pos.unrealized_pnl >= 0 ? "+" : "-"}${money(Math.abs(pos.unrealized_pnl))})`;
    own.className = `s-row-own ${pos.unrealized_pnl >= 0 ? "up" : "down"}`;
  } else {
    own.textContent = waiting.length ? `${waiting.length} order waiting` : "";
    own.className = "s-row-own";
  }
  q(".s-row-head").setAttribute("aria-expanded", isOpen ? "true" : "false");
  el.classList.toggle("open", isOpen);
  q(".s-detail").hidden = !isOpen;
  if (!isOpen) return;

  // What you own and its exits
  const owned = q(".s-owned");
  owned.hidden = !pos;
  if (pos) {
    const unit = isCrypto(symbol) ? a.name : `shares of ${a.name}`;
    owned.textContent = `You own ${+pos.qty.toPrecision(6)} ${unit}, worth ${money(pos.market_value)}. You paid ${money(pos.avg_price)} `
      + `each and you're ${pos.unrealized_pnl >= 0 ? "up" : "down"} ${money(Math.abs(pos.unrealized_pnl))} so far.`;
    owned.className = `s-owned ${pos.unrealized_pnl >= 0 ? "up" : "down"}`;
  }
  const exits = q(".s-exits");
  exits.hidden = !pos || lvl < 2;
  if (pos && lvl >= 2) {
    const sl = pos.stop_loss ? `falls to ${money(pos.stop_loss)} (stop-loss)` : null;
    const tp = pos.take_profit ? `rises to ${money(pos.take_profit)} (take-profit)` : null;
    q(".s-exits-text").textContent = sl || tp ? `It sells automatically if the price ${[sl, tp].filter(Boolean).join(" or ")}.`
      : "No automatic exits are set. Consider adding a stop-loss.";
  }

  // Order form
  q(".s-type").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.type === f.type));
  q(".s-sizing").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.sizing === f.sizing));
  const sizing = lvl >= 2 ? f.sizing : "auto";
  q(".s-limit").hidden = f.type !== "limit";
  q(".s-risk").hidden = sizing !== "risk";
  const form = q(".s-order");
  if (!keepInputs) {
    form.limit.value = f.limit;
    form.risk.value = f.risk;
  }

  const plan = buyPlan(symbol);
  const canBuy = !pos && !state.account.halted && state.account.positions.length < state.risk.max_open_positions;
  const buy = q(".s-buy");
  buy.hidden = !!pos;
  buy.disabled = !canBuy || (sizing === "risk" && plan.over) || (f.type === "limit" && !(Number(f.limit) > 0));
  const limitText = f.type === "limit" && Number(f.limit) > 0 ? ` at ${money(Number(f.limit))} or lower` : "";
  buy.textContent = f.type === "limit" ? `Place limit buy${limitText}` : `Buy ${money(plan.spend)} of ${a.name}`;
  buy.className = `btn s-buy ${f.type === "limit" ? "btn-primary" : "btn-buy"}`;

  const sell = q(".s-sell");
  sell.hidden = !pos;
  if (pos) {
    const lp = Number(f.limit);
    sell.textContent = f.type === "limit" ? `Place limit sell${lp > 0 ? ` at ${money(lp)} or higher` : ""}` : `Sell all my ${a.name}`;
    sell.disabled = f.type === "limit" && !(lp > 0);
    sell.className = `btn s-sell ${f.type === "limit" ? "btn-primary" : "btn-sell"}`;
  }

  let planText = "";
  if (!pos) {
    if (!canBuy && state.account.positions.length >= state.risk.max_open_positions) {
      planText = `You already hold ${state.risk.max_open_positions} things, your limit for open positions. Sell one first, or raise the limit in your safety net.`;
    } else if (sizing === "risk" && plan.over) {
      planText = `To lose only ${money(Number(f.risk))} with a ${pct(state.risk.stop_loss_pct)} stop-loss you'd buy ${money(plan.spend)}, `
        + `more than your per-trade cap of ${money(plan.cap)}. Risk less, or widen the stop-loss in your safety net.`;
    } else {
      planText = `${sizing === "risk" ? `Size = ${money(Number(f.risk))} ÷ ${pct(state.risk.stop_loss_pct)} stop = ${money(plan.spend)}. ` : `We'd put in ${money(plan.spend)}. `}`
        + (state.risk.stop_loss_pct > 0 ? `It sells for you if it falls ${pct(state.risk.stop_loss_pct)} (you'd lose about ${money(plan.loss)})` : "No stop-loss is set")
        + (state.risk.take_profit_pct > 0 ? ` or rises ${pct(state.risk.take_profit_pct)} (you'd make about ${money(plan.gain)})` : "")
        + `. Costs: ${costNote(symbol)}.`;
    }
  }
  if (!tradable(symbol)) {
    planText += f.type === "limit"
      ? " The US market is closed, so a limit order waits for the next open."
      : " The US market is closed: practice market orders fill at the last price, but a real broker would wait for the open.";
  }
  q(".s-plan").textContent = planText.trim();

  // Waiting limit orders
  q(".s-waiting").innerHTML = waiting.map((o) => `<li><span>Limit ${o.side} at ${money(o.limit_price)} (placed when the price was `
    + `${money(o.price_when_placed)})</span><button type="button" class="btn btn-sm btn-ghost" data-cancel="${o.id}">Cancel</button></li>`).join("");

  // Suggestion
  const adv = advice[symbol];
  const out = q(".s-advice-out");
  out.hidden = !adv || adv.loading;
  q(".s-advice-btn").textContent = adv?.loading ? "Thinking…" : adv ? "Check again" : "Get a suggestion";
  q(".s-advice-btn").disabled = !!adv?.loading;
  if (adv && !adv.loading) {
    const word = adv.action === "buy" ? "Suggestion: buy" : adv.action === "sell" ? "Suggestion: sell"
      : pos ? "Suggestion: keep it" : "Suggestion: wait";
    const v = q(".s-verdict");
    v.textContent = word;
    v.className = `s-verdict tone-${adv.action === "buy" ? "up" : adv.action === "sell" ? "down" : "wait"}`;
    q(".s-why").textContent = adv.summary || "";
    const sure = adv.confidence >= 0.75 ? "Quite sure" : adv.confidence >= 0.5 ? "Fairly sure" : "Not very sure";
    q(".s-meta").textContent = `${sure} · ${adv.source === "rules" ? "backup rules (the AI is off)" : "from your AI"} · an educated guess, not a prediction`;
  }
}

function renderLists() {
  const groups = { stocks: $("s-list-stocks"), crypto: $("s-list-crypto") };
  for (const [key, box] of Object.entries(groups)) {
    const symbols = state.symbols.filter((s) => (key === "crypto") === isCrypto(s));
    if ([...box.children].map((c) => c.dataset.symbol).join() !== symbols.join()) {
      box.replaceChildren(...symbols.map(row));
    }
    [...box.children].forEach((el) => {
      if (el.contains(document.activeElement) && document.activeElement.matches("input")) renderRow(el, { keepInputs: true });
      else renderRow(el);
    });
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
  const us = state.status?.us_market;
  $("s-next").textContent = us ? `${us.label}. Crypto trades 24/7.` : "";
  $("s-next").className = `s-next ${us?.open ? "up" : ""}`;

  const notice = $("s-notice");
  notice.hidden = !account.halted;
  if (account.halted) notice.textContent = "Buying is paused for today because you hit your daily loss limit. This protects you, and it starts again tomorrow.";
  renderLists();
  if (level() >= 2) renderSafety();
}

// ---------------------------------------------------------------- safety net (level 2)
const SAFETY_KEYS = ["stop_loss_pct", "take_profit_pct", "max_position_pct"];
let safetyDirty = false;

function renderSafety() {
  const form = $("s-safety-form");
  if (!safetyDirty) {
    for (const key of SAFETY_KEYS) form[key].value = +(state.risk[key] * 100).toFixed(2);
    form.max_open_positions.value = state.risk.max_open_positions;
  }
  const { account, risk } = state;
  const spend = Math.min(account.cash, account.equity * risk.max_position_pct);
  const loss = spend * risk.stop_loss_pct;
  const gain = spend * risk.take_profit_pct;
  $("s-safety-note").textContent = `With these settings an auto-sized buy puts in up to ${money(spend)}. If the stop-loss is hit `
    + `you'd lose about ${money(loss)} (${(loss / account.equity * 100).toFixed(2)}% of your account); if the take-profit is hit `
    + `you'd make about ${money(gain)}. That's risking $1 to make $${(gain / Math.max(loss, 1e-9)).toFixed(1)}.`;
}

async function saveSafety(e) {
  e.preventDefault();
  const form = e.target;
  const body = Object.fromEntries(SAFETY_KEYS.map((k) => [k, Number(form[k].value) / 100]));
  body.max_open_positions = Number(form.max_open_positions.value);
  try {
    await api("/api/risk", "PUT", body);
    safetyDirty = false;
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
  const name = assetOf(t.symbol).name;
  const when = new Date(t.ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const how = { manual: "by you", limit: "by your limit order", stop_loss: "by your stop-loss", take_profit: "by your take-profit",
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

// ---------------------------------------------------------------- actions
function showMessage(el, text, tone = "") {
  const m = el.querySelector(".s-msg");
  m.textContent = text;
  m.className = `s-msg ${tone}`;
  m.hidden = !text;
}

async function act(symbol, el, request) {
  try {
    const res = await request();
    showMessage(el, res?.note || "", "info");
    learnRefresh();
  } catch (err) {
    showMessage(el, `That didn't work. ${err.message}`, "down");
  }
  await refreshState();
}

function submit(symbol, side, el) {
  const f = formOf(symbol);
  const body = { symbol, side, type: f.type };
  if (f.type === "limit") body.limit_price = Number(f.limit);
  if (side === "buy" && level() >= 2 && f.sizing === "risk") body.risk_amount = Number(f.risk);
  if (side === "sell" && f.type === "market") {
    return act(symbol, el, () => api(`/api/positions/${symbol}/close`, "POST"));
  }
  return act(symbol, el, async () => {
    const res = await api("/api/orders", "POST", body);
    if (res.status === "waiting") f.type = "market";
    if (advice[symbol]) advice[symbol] = { ...advice[symbol], action: "wait" };
    return res;
  });
}

async function getAdvice(symbol, el) {
  advice[symbol] = { ...(advice[symbol] || {}), loading: true };
  renderRow(el);
  try {
    advice[symbol] = await api(`/api/advice/${symbol}`, "POST");
    showMessage(el, "");
  } catch (err) {
    delete advice[symbol];
    showMessage(el, `Couldn't get a suggestion. ${err.message}`, "down");
  }
  renderRow(el);
}

async function refreshState() {
  try {
    state = await api("/api/state");
    render();
    refreshTrades();
  } catch {
    $("s-next").textContent = "Can't reach the app right now. Trying again…";
  }
}

// ---------------------------------------------------------------- start
$("s-safety-form").addEventListener("input", () => { safetyDirty = true; });
$("s-safety-form").addEventListener("submit", saveSafety);
window.addEventListener("learn:level", render);
await refreshState();
setInterval(() => { if (document.visibilityState === "visible") refreshState(); }, REFRESH_MS);
