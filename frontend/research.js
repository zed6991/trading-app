// Research (Level 12): SEC filings read by an LLM with every number checked, news sentiment, the "AI remembers" test,
// and earnings event studies.
import { $, api, esc, num, pct, tone, card, learnRefresh, term, aiSource, onView } from "./ui.js";

let assets = [];
let ticker = "AAPL";
let aiOn = false;

const companies = () => assets.filter((a) => a.kind === "stock");

function sentimentBar(s) {
  const pos = ((s + 1) / 2) * 100;
  return `<div class="senti"><span class="senti-track"><i style="left:${pos}%"></i></span>
    <span class="mono ${tone(s)}">${s > 0 ? "+" : ""}${num(s)}</span></div>`;
}

const TRUST = {
  high: ["good", "High trust: every quoted number was found word-for-word in the filing."],
  check: ["", "Check by hand: some quotes or numbers couldn't be found in the filing."],
  low: ["bad", "Low trust: none of the quoted numbers could be verified."],
};

function render() {
  const route = $("rs-route");
  api("/api/ai/settings").then((s) => {
    aiOn = s.enabled;
    $("rs-mem-go").textContent = aiOn ? "Test its memory" : "Show a recorded example";
    route.innerHTML = !s.enabled
      ? "AI is off, so filings are read by a rule-based reader: it copies the sentences that report key figures and scores tone by counting positive and negative finance words."
      : s.cloud_enabled && s.cloud_tasks.includes("research") && !s.cap_reached
      ? `Research uses the cloud model <b>${esc(s.cloud_model)}</b> (spent $${num(s.spend_usd, 4)} of your $${num(s.monthly_cap_usd)} monthly cap).`
      : `Research uses your local AI (${esc(s.local_model)}), which is free but slower. You can switch on a cloud model in Settings.`;
  }).catch(() => {});
  $("rs-ticker").innerHTML = companies().map((a) => `<option value="${a.symbol}" ${a.symbol === ticker ? "selected" : ""}>${esc(a.name)} (${a.symbol})</option>`).join("");
  $("rs-news-sym").innerHTML = assets.map((a) => `<option value="${a.symbol}" ${a.symbol === ticker ? "selected" : ""}>${esc(a.name)}</option>`).join("");
  loadFilings();
}

async function loadFilings() {
  ticker = $("rs-ticker").value || ticker;
  $("rs-filings").innerHTML = `<p class="lab-loading">Loading filings from SEC EDGAR…</p>`;
  try {
    const f = await api(`/api/research/filings/${ticker}`);
    $("rs-filings").innerHTML = `<table class="lab-table"><tr><th>Filed</th><th>Document</th><th></th></tr>
      ${f.filings.slice(0, 12).map((x) => `<tr><td>${x.date}</td><td>${esc(x.label)}</td>
        <td><button type="button" class="btn btn-sm" data-acc="${x.accession}">Analyse</button></td></tr>`).join("")}</table>`;
    const lastEarn = f.filings.find((x) => x.earnings);
    if (lastEarn) {
      const d = new Date(lastEarn.date);
      d.setFullYear(d.getFullYear() - 2);
      $("rs-mem-date").value = d.toISOString().slice(0, 10);
    }
  } catch (err) {
    $("rs-filings").innerHTML = `<p class="lab-msg">${esc(err.message)}</p>`;
  }
}

async function analyse(accession) {
  const out = $("rs-result");
  const anon = $("rs-anon").checked;
  out.innerHTML = `<p class="lab-loading">The AI is reading the filing${anon ? " (with names and dates hidden)" : ""}. This takes 10-60 seconds…</p>`;
  out.scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const r = await api("/api/research/filing", "POST", { ticker, accession, anonymous: anon });
    const [cls, msg] = r.source === "rules"
      ? ["", r.checked ? "Every quote is exact because it was copied from the filing. Check that each figure is the line item its label says."
        : "The rule-based reader found no sentences with headline figures in this part of the filing. Try the earnings release instead."]
      : TRUST[r.trust];
    out.innerHTML = `<div class="lab-box">
      <h3>${esc(r.company)}: ${esc(r.filing.label)} (${r.filing.date}) ${r.anonymous ? '<span class="l-tag">anonymised</span>' : ""}</h3>
      <p class="rs-summary">${esc(r.summary)}</p>
      <div class="lab-cards">
        ${card(term("sentiment", "Sentiment"), sentimentBar(r.sentiment), "−1 very negative … +1 very positive")}
        ${card("Numbers verified", `${r.verified} / ${r.checked}`, "quotes found word-for-word in the filing", cls)}
      </div>
      ${r.source === "rules" ? `<p class="muted">Rule-based reader: every quote below is copied word-for-word from the filing, so it always verifies. The <i>labels</i> come from pattern matching and can be wrong (a figure may belong to a different line item), so read each quote. Tone: ${r.word_counts ? `${r.word_counts.positive} positive vs ${r.word_counts.negative} negative finance words` : "word counts"}. Getting the labels right is exactly what an LLM adds.</p>` : ""}
      <p class="lab-verdict ${cls}">${esc(msg)}${r.unsupported_summary_numbers.length ? ` The summary mentions ${r.unsupported_summary_numbers.map(esc).join(", ")}, which doesn't appear in the text as written (it may be rounded or converted, or made up): a possible ${term("hallucination", "hallucination")}.` : ""}</p>
      <table class="lab-table rs-nums"><tr><th>Key number</th><th>Value</th><th>Quote from the filing</th><th>Check</th></tr>
        ${r.key_numbers.map((n) => `<tr class="${n.verified ? "" : "hl"}"><td>${esc(n.label)}</td><td class="mono">${esc(n.value)}</td>
          <td class="rs-quote">“${esc(n.quote)}”</td><td>${n.verified ? '<span class="up">✓ found</span>' : `<span class="down">⚠ ${esc(n.problem)}</span>`}</td></tr>`).join("")}
      </table>
      ${r.guidance ? `<p><b>Guidance:</b> ${esc(r.guidance)}</p>` : ""}
      ${r.risks.length ? `<p><b>Risks mentioned:</b></p><ul>${r.risks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      <p class="code-meta">Read ${r.chars_read.toLocaleString()} of ${r.full_chars.toLocaleString()} characters ·
        <a href="${esc(r.url)}" target="_blank" rel="noopener">Open on SEC EDGAR</a> · ${aiSource(r.source, r.cost_usd)}${r.cached ? " · saved result" : ""}</p>
    </div>`;
    learnRefresh();
  } catch (err) {
    out.innerHTML = `<p class="lab-msg">${esc(err.message)}</p>`;
  }
}

async function news() {
  const sym = $("rs-news-sym").value;
  const out = $("rs-news");
  out.innerHTML = `<p class="lab-loading">Scoring the latest headlines…</p>`;
  try {
    const r = await api(`/api/research/news/${sym}`);
    if (!r.items.length) { out.innerHTML = `<p class="muted">No recent headlines found.</p>`; return; }
    out.innerHTML = `<p>Average score: ${sentimentBar(r.average)}</p>
      <table class="lab-table"><tr><th>Headline</th><th>Score</th><th>Why</th></tr>
      ${r.items.map((i) => `<tr><td><a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.title)}</a>
        <br><small class="muted">${esc(i.publisher)} · ${new Date(i.time * 1000).toLocaleDateString()}</small></td>
        <td class="mono ${tone(i.score)}">${i.score > 0 ? "+" : ""}${num(i.score, 1)}</td><td>${esc(i.why)}</td></tr>`).join("")}</table>
      <p class="code-meta">${aiSource(r.source, r.cost_usd)}. Headlines are scored on their wording only. Markets usually price news in within minutes.</p>`;
    learnRefresh();
  } catch (err) { out.innerHTML = `<p class="lab-msg">${esc(err.message)}</p>`; }
}

async function memory() {
  const out = $("rs-mem");
  out.innerHTML = `<p class="lab-loading">Asking the AI, with no documents…</p>`;
  try {
    const r = await api("/api/research/memory", "POST", { ticker, date: $("rs-mem-date").value });
    if (r.example) {
      out.innerHTML = `<p class="lab-verdict bad"><b>Recorded example.</b> On ${esc(r.recorded)} a local model (${esc(r.model)}) was asked, with no documents,
        what happened to ${esc(r.ticker)} after its earnings around ${esc(r.date)}. It answered as if it remembered: any backtest of that model's
        scores over that period would leak the future (${term("training-data-leakage", "training-data leakage")}). (Its details aren't even reliable: it's recalling, not reading.)</p>
        <p><i>“${esc(r.answer)}”</i></p><p class="code-meta">Turn AI on in Settings to run this test live on any company and date.</p>`;
      learnRefresh();
      return;
    }
    out.innerHTML = `<p class="lab-verdict ${r.knows ? "bad" : "good"}">${r.knows
      ? `<b>It remembers.</b> The AI says it knows what happened after ${r.date}. Scoring filings from that period with this model would leak the future into your backtest (${term("training-data-leakage", "training-data leakage")}).`
      : "<b>It says it doesn't know.</b> Models can still be influenced by things they half-remember, so anonymising is still wise."}</p>
      <p><i>“${esc(r.answer)}”</i></p><p class="code-meta">${aiSource(r.source, r.cost_usd)}</p>`;
    learnRefresh();
  } catch (err) { out.innerHTML = `<p class="lab-msg">${esc(err.message)}</p>`; }
}

async function study() {
  const out = $("rs-study");
  const count = Number($("rs-count").value);
  const anon = $("rs-study-anon").checked;
  out.innerHTML = `<p class="lab-loading">Reading ${count} earnings releases${anon ? " (anonymised)" : ""} and lining them up with prices. Allow a minute or two with the local AI…</p>`;
  try {
    const r = await api("/api/research/study", "POST", { ticker, count, horizon: Number($("rs-horizon").value), anonymous: anon });
    out.innerHTML = `<p class="lab-verdict">${esc(r.verdict)}</p>
      <div class="lab-cards">${card("Correlation", r.correlation === null ? "–" : num(r.correlation), `score vs next ${r.horizon} days' move`)}
        ${card("Same direction", r.same_direction_pct === null ? "–" : `${num(r.same_direction_pct, 0)}%`, "score sign matched the move")}
        ${card("Events", String(r.events), r.anonymous ? "anonymised" : "names and dates shown")}</div>
      <table class="lab-table"><tr><th>Release</th><th>Tone score</th><th>Next ${r.horizon} days</th><th>Trust</th><th>Summary</th></tr>
      ${r.rows.map((x) => `<tr><td>${x.date}</td><td class="mono ${tone(x.sentiment)}">${x.sentiment > 0 ? "+" : ""}${num(x.sentiment)}</td>
        <td class="mono ${tone(x.move_pct)}">${x.move_pct === null ? "–" : pct(x.move_pct)}</td><td>${esc(x.trust)}</td>
        <td><small>${esc(x.summary.slice(0, 180))}${x.summary.length > 180 ? "…" : ""}</small></td></tr>`).join("")}</table>
      <p class="code-meta">${r.cost_usd ? `Cloud cost $${num(r.cost_usd, 4)}` : "Free (local AI or saved results)"}</p>`;
    learnRefresh();
  } catch (err) { out.innerHTML = `<p class="lab-msg">${esc(err.message)}</p>`; }
}

async function init() {
  if (!assets.length) {
    try { assets = (await api("/api/state")).assets || []; } catch { return; }
  }
  render();
}

$("rs-ticker").addEventListener("change", loadFilings);
$("rs-filings").addEventListener("click", (e) => { const b = e.target.closest("[data-acc]"); if (b) analyse(b.dataset.acc); });
$("rs-news-go").addEventListener("click", news);
$("rs-mem-go").addEventListener("click", memory);
$("rs-study-go").addEventListener("click", study);
onView("research", init);
