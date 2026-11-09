// Code Lab (Levels 8-9): Python exercises with automatic checking, a scratchpad, your own strategy, and an AI pair-programmer.
import { $, api, esc, num, pct, tone, card, legend, lineChart, learnRefresh, storageGet, storageSet, term, aiSource,
  armed, onView, unlocked, COLORS } from "./ui.js";

const SCRATCH = 'import qp\n\nspy = qp.prices("SPY")\nprint(spy.tail())\nqp.chart(spy["close"], "SPY close")\n';
let info = null; // {exercises, strategy_template, sandboxed, symbols}
let current = null; // {kind: "exercise"|"scratch"|"strategy", id?}
let last = null; // last run result (for the AI)
let busy = false;

const key = () => `code:${current.kind}:${current.id || ""}`;
const editor = () => $("code-editor");
const exercise = () => info?.exercises.find((e) => e.id === current?.id);

function starterFor(c) {
  if (c.kind === "exercise") return info.exercises.find((e) => e.id === c.id)?.starter || "";
  if (c.kind === "strategy") return info.strategy_template;
  return SCRATCH;
}

function renderList() {
  const items = (lvl) => info.exercises.filter((e) => e.level === lvl).map((e) => `
    <li><button type="button" class="code-item ${current?.id === e.id ? "active" : ""}" data-ex="${e.id}">
      <span class="l-check">${e.done ? "✓" : ""}</span><span>${esc(e.title)}</span></button></li>`).join("");
  $("code-list").innerHTML = `
    <h3>Python basics <small>Level 8</small></h3><ul>${items(8)}</ul>
    <div data-unlock="9"><h3>Strategies <small>Level 9</small></h3><ul>${items(9)}
      <li><button type="button" class="code-item ${current?.kind === "strategy" ? "active" : ""}" data-kind="strategy"><span class="l-check">★</span><span>My strategy</span></button></li></ul></div>
    <h3>Free play</h3><ul><li><button type="button" class="code-item ${current?.kind === "scratch" ? "active" : ""}" data-kind="scratch"><span class="l-check">✎</span><span>Scratchpad</span></button></li></ul>
    <p class="code-safe">${info.sandboxed ? "Your code runs in a sandbox: no internet, no access to your files." : "Code runs in a separate process with a time limit."}</p>`;
}

function open(c) {
  if (current && editor()) storageSet(key(), editor().value);
  current = c;
  storageSet("code:current", JSON.stringify(c));
  const saved = storageGet(key());
  editor().value = saved ?? starterFor(c);
  last = null;
  const ex = exercise();
  $("code-title").textContent = ex ? ex.title : c.kind === "strategy" ? "My strategy" : "Scratchpad";
  $("code-task").innerHTML = ex ? `<p>${esc(ex.task)}</p>${ex.done ? '<p class="up">✓ Passed. You can keep experimenting.</p>' : ""}`
    : c.kind === "strategy"
      ? `<p>Write <code>positions(df)</code>: return 1 (hold) or 0 (cash) for every day, using only past data. It becomes a
         ${term("signal-function", "signal function")}. Then test it on an asset: the app checks for
         ${term("look-ahead-bias", "look-ahead")}, splits ${term("in-sample", "in-sample")} / ${term("out-of-sample", "out-of-sample")},
         includes costs and runs the luck test.</p>`
      : `<p>Try anything. <code>qp.prices(symbol, years)</code>, <code>qp.chart(...)</code>, <code>qp.table(...)</code> and
         <code>qp.symbols()</code> are available, plus pandas, numpy and scikit-learn.</p>`;
  $("code-check").hidden = !ex;
  $("code-fix").hidden = !!ex;
  $("code-strategy").hidden = c.kind !== "strategy";
  $("code-out").innerHTML = "";
  $("code-ai").innerHTML = "";
  renderList();
  if (c.kind === "strategy") renderSaved();
}

// ---------------------------------------------------------------- output
function renderOutputs(r) {
  const parts = [];
  if (r.stdout) parts.push(`<pre class="code-stdout">${esc(r.stdout)}</pre>`);
  if (r.error) parts.push(`<pre class="code-err">${esc(r.error)}</pre>`);
  (r.outputs || []).forEach((o, i) => {
    if (o.type === "chart") parts.push(`<div class="lab-box"><h3>${esc(o.title || "Chart")}</h3>${legend(o.series.map((s, j) => ({ label: s.name, color: COLORS[j % COLORS.length] })))}<div class="lab-chart" id="code-chart-${i}"></div></div>`);
    if (o.type === "table") {
      parts.push(`<div class="lab-box"><h3>${esc(o.title || "Table")}</h3><div class="heat-wrap"><table class="lab-table"><tr>${o.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>
        ${o.rows.map((row) => `<tr>${row.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</table></div></div>`);
    }
  });
  if (r.check) parts.unshift(`<p class="lab-verdict ${r.check.pass ? "good" : "bad"}">${r.check.pass ? "✓ " : ""}${esc(r.check.msg)}</p>`);
  if (!parts.length) parts.push(`<p class="muted">No output. Use print(...) or qp.chart(...) to see results.</p>`);
  parts.push(`<p class="code-meta">${r.duration_ms} ms</p>`);
  $("code-out").innerHTML = parts.join("");
  (r.outputs || []).forEach((o, i) => {
    if (o.type === "chart") lineChart(`code-chart-${i}`, o.series.map((s, j) => ({ color: COLORS[j % COLORS.length], data: s.points.map(([t, v]) => ({ time: t, value: v })) })));
  });
}

async function run(check = false) {
  if (busy) return;
  busy = true;
  storageSet(key(), editor().value);
  $("code-out").innerHTML = `<p class="lab-loading">${check ? "Running and checking…" : "Running…"}</p>`;
  try {
    const r = check ? await api("/api/code/check", "POST", { exercise_id: current.id, code: editor().value })
      : await api("/api/code/run", "POST", { code: editor().value });
    last = r;
    renderOutputs(r);
    if (check && r.passed) {
      const ex = exercise();
      if (ex) ex.done = true;
      renderList();
    }
    learnRefresh();
  } catch (err) {
    $("code-out").innerHTML = `<p class="lab-msg">${esc(err.message)}</p>`;
  }
  busy = false;
}

// ---------------------------------------------------------------- AI pair-programmer
async function assist(mode) {
  const question = $("code-q").value.trim();
  if (mode === "ask" && !question) { $("code-q").focus(); return; }
  $("code-ai").innerHTML = `<p class="lab-loading">Thinking…</p>`;
  try {
    const r = await api("/api/code/assist", "POST", {
      mode, question, code: editor().value, exercise_id: current.kind === "exercise" ? current.id : null,
      output: last?.stdout || "", error: last?.error || (last?.check && !last.check.pass ? last.check.msg : ""),
    });
    $("code-ai").innerHTML = `<div class="code-ai-box"><p>${esc(r.explanation).replace(/\n/g, "<br>")}</p>
      ${r.hints.length ? `<ul>${r.hints.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>` : ""}
      ${r.code ? `<pre class="code-stdout">${esc(r.code)}</pre><button type="button" class="btn btn-sm" id="code-use">Use this code</button>
        <span class="muted"> Read it first: AI code can be wrong.</span>` : ""}
      ${r.mode === "hint" && mode === "fix" ? '<p class="muted">In exercises the pair-programmer gives hints, not answers.</p>' : ""}
      <p class="code-meta">${aiSource(r.source, r.cost_usd)}</p></div>`;
    $("code-use")?.addEventListener("click", () => { editor().value = r.code; storageSet(key(), r.code); });
    learnRefresh();
  } catch (err) {
    $("code-ai").innerHTML = `<p class="lab-msg">${esc(err.message)}</p>`;
  }
}

// ---------------------------------------------------------------- strategy mode
async function renderSaved() {
  try {
    const saved = await api("/api/code/strategies");
    const names = Object.keys(saved);
    $("code-saved").innerHTML = names.length ? names.map((n) => `<li><button type="button" class="link-btn" data-load="${esc(n)}">${esc(n)}</button>
      <button type="button" class="btn btn-xs btn-ghost" data-del="${esc(n)}">Delete</button></li>`).join("")
      : '<li class="muted">No saved strategies yet.</li>';
    $("code-saved").onclick = async (e) => {
      const l = e.target.closest("[data-load]");
      const d = e.target.closest("[data-del]");
      if (l) { editor().value = saved[l.dataset.load].code; $("code-sname").value = l.dataset.load; }
      if (d && armed(d, "Sure?")) { await api(`/api/code/strategies/${encodeURIComponent(d.dataset.del)}`, "DELETE"); renderSaved(); }
    };
  } catch { /* shown on next open */ }
}

async function testStrategy() {
  if (busy) return;
  busy = true;
  storageSet(key(), editor().value);
  const symbol = $("code-sym").value;
  $("code-out").innerHTML = `<p class="lab-loading">Running your strategy on ${esc(symbol)}, checking for look-ahead, backtesting and luck-testing…</p>`;
  try {
    const r = await api("/api/code/strategy/evaluate", "POST", { code: editor().value, symbol });
    if (!r.ok) {
      last = r.run;
      renderOutputs(r.run);
    } else {
      last = { stdout: r.run.stdout, error: null };
      const ins = r.in_sample.metrics;
      const outs = r.out_sample.metrics;
      const peek = r.lookahead.length
        ? `<p class="lab-verdict bad"><b>Look-ahead detected.</b> When future prices were removed or changed, your positions on
           ${r.lookahead.map(esc).join(", ")} changed, so the code uses information it couldn't have had. Results below are not
           trustworthy. Look for <code>shift(-1)</code> or anything that reads ahead.</p>`
        : `<p class="lab-verdict good">✓ Look-ahead check passed: removing or changing future prices didn't change any past position.</p>`;
      const luck = r.luck.error ? `<p class="muted">${esc(r.luck.error)}</p>`
        : `<p class="lab-verdict ${r.luck.beat_pct >= 95 ? "good" : r.luck.beat_pct < 75 ? "bad" : ""}">Luck test: ${esc(r.luck.verdict)}</p>`;
      const m = (x, title) => `<div class="lab-box"><h3>${title}</h3><div class="lab-cards">
        ${card("Return", pct(x.total_return_pct), `buy & hold ${pct(x.buy_hold_return_pct)}`, x.total_return_pct > x.buy_hold_return_pct ? "good" : "bad")}
        ${card("Sharpe", num(x.sharpe))}${card("Max drawdown", `-${num(x.max_drawdown_pct, 1)}%`)}${card("Trades", String(x.num_trades), `win rate ${num(x.win_rate_pct, 0)}%`)}</div></div>`;
      const curve = r.out_sample.equity_curve;
      const sub = r.candles.filter((c) => c.time >= curve[0].time);
      const hold = sub.map((c) => ({ time: c.time, value: (c.close / sub[0].open) * 10000 }));
      $("code-out").innerHTML = `${peek}
        <p class="muted">In the market ${r.time_in_market_pct}% of the time. Costs included. All money in each trade, no stop-loss.</p>
        <div class="two-col">${m(ins, `In-sample (${new Date(r.in_sample.start * 1000).getFullYear()} to ${new Date(r.in_sample.end * 1000).getFullYear()})`)}
        ${m(outs, "Out-of-sample: the honest number")}</div>${luck}
        <div class="lab-box"><h3>Out-of-sample: $10,000 over time</h3>${legend([{ label: "Your strategy", color: COLORS[0] }, { label: "Buy & hold", color: COLORS[1] }])}
        <div class="lab-chart" id="code-chart-eq"></div></div>
        ${r.run.stdout ? `<pre class="code-stdout">${esc(r.run.stdout)}</pre>` : ""}`;
      lineChart("code-chart-eq", [{ data: curve, color: COLORS[0] }, { data: hold, color: COLORS[1], dashed: true }]);
    }
    learnRefresh();
  } catch (err) {
    $("code-out").innerHTML = `<p class="lab-msg">${esc(err.message)}</p>`;
  }
  busy = false;
}

async function saveStrategy() {
  const name = $("code-sname").value.trim();
  if (!name) { $("code-sname").focus(); return; }
  try {
    await api("/api/code/strategies", "POST", { name, code: editor().value, symbol: $("code-sym").value });
    $("code-smsg").textContent = `Saved as "${name}". The agent can use it in Level 13.`;
    renderSaved();
    learnRefresh();
  } catch (err) { $("code-smsg").textContent = err.message; }
}

// ---------------------------------------------------------------- start
function bind() {
  $("code-list").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.ex) open({ kind: "exercise", id: b.dataset.ex });
    else if (b.dataset.kind) open({ kind: b.dataset.kind });
  });
  $("code-run").addEventListener("click", () => run(false));
  $("code-check").addEventListener("click", () => run(true));
  $("code-reset").addEventListener("click", (e) => { if (armed(e.currentTarget, "Reset to the starter code?")) editor().value = starterFor(current); });
  document.querySelectorAll("[data-assist]").forEach((b) => b.addEventListener("click", () => assist(b.dataset.assist)));
  $("code-q").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); assist("ask"); } });
  $("code-test").addEventListener("click", testStrategy);
  $("code-save").addEventListener("click", saveStrategy);
  editor().addEventListener("keydown", (e) => {
    if (e.key === "Tab") {
      e.preventDefault();
      const el = e.target;
      const s = el.selectionStart;
      el.setRangeText("    ", s, el.selectionEnd, "end");
    } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      if (current.kind === "strategy") testStrategy(); else run(current.kind === "exercise");
    }
  });
  editor().addEventListener("input", () => storageSet(key(), editor().value));
}

async function init() {
  if (info) return;
  try {
    const [data, state] = await Promise.all([api("/api/code/exercises"), api("/api/state")]);
    info = data;
    $("code-sym").innerHTML = (state.assets || []).map((a) => `<option value="${a.symbol}">${esc(a.name)}</option>`).join("");
    let saved = null;
    try { saved = JSON.parse(storageGet("code:current") || "null"); } catch { saved = null; }
    const firstTodo = info.exercises.find((e) => !e.done && unlocked(e.level));
    open(saved && (saved.kind !== "exercise" || info.exercises.some((e) => e.id === saved.id)) ? saved
      : firstTodo ? { kind: "exercise", id: firstTodo.id } : { kind: "scratch" });
  } catch (err) {
    $("code-task").innerHTML = `<p class="lab-msg">${esc(err.message)}</p>`;
  }
}

bind();
onView("code", init);
window.addEventListener("code:open", (e) => { init().then(() => open(e.detail)); });
