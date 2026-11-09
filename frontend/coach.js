// AI coach panel: chat in context, weekly review and "what next". Opens from the floating "Ask coach" button.
const $ = (id) => document.getElementById(id);
const HISTORY_KEY = "coach-history";
const MAX_SAVED = 30;

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

let glossary = [];
let termRe = null;
const termSlug = new Map(); // lowercase name -> slug
let history = []; // [{role, content, lesson?, follow_ups?, review?}]
let busy = false;

async function api(path, method = "GET", body) {
  const res = await fetch(path, body === undefined ? { method }
    : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Something went wrong");
  return data;
}

function load() {
  try { history = JSON.parse(sessionStorage.getItem(HISTORY_KEY) || "[]"); } catch { history = []; }
  if (!Array.isArray(history)) history = [];
}
function save() {
  try { sessionStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(-MAX_SAVED))); } catch { /* optional */ }
}

// ---------------------------------------------------------------- tappable terms in coach text
async function loadGlossary() {
  try {
    glossary = await api("/api/learn/glossary");
  } catch { return; }
  const names = [];
  for (const g of glossary) {
    for (const n of [g.term, ...(g.aliases || [])]) {
      // Short or very common words ("value", "size") would underline half of every answer.
      if (n.length < 4 || ["value", "size", "price", "cash", "holding", "paper", "mark"].includes(n.toLowerCase())) continue;
      termSlug.set(n.toLowerCase(), g.slug);
      names.push(n);
    }
  }
  names.sort((a, b) => b.length - a.length);
  const pattern = names.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  if (pattern) termRe = new RegExp(`(?<![\\w-])(${pattern})(?![\\w-])`, "gi");
}

/** Plain text -> HTML paragraphs, linking the first mention of each glossary term the learner has reached. */
function rich(text) {
  const seen = new Set();
  return String(text || "").split(/\n\s*\n+/).map((para) => {
    let html = "";
    let last = 0;
    if (termRe) {
      termRe.lastIndex = 0;
      for (const m of para.matchAll(termRe)) {
        const slug = termSlug.get(m[0].toLowerCase());
        const g = glossary.find((t) => t.slug === slug);
        if (!g || seen.has(slug)) continue;
        seen.add(slug);
        html += esc(para.slice(last, m.index))
          + `<span class="term" data-term="${esc(slug)}" tabindex="0" role="button">${esc(m[0])}</span>`;
        last = m.index + m[0].length;
      }
    }
    html += esc(para.slice(last));
    return `<p>${html.replace(/\n/g, "<br>")}</p>`;
  }).join("");
}

// ---------------------------------------------------------------- rendering
function currentView() {
  const m = document.body.className.match(/\bview-(\w+)/);
  return m ? m[1] : "learn";
}
function currentSymbol() {
  if (currentView() !== "dashboard") return null;
  return document.querySelector(".sym-tab.active")?.dataset.symbol || null;
}

function lessonButton(lesson) {
  if (!lesson || lesson.locked) return "";
  return `<button type="button" class="btn btn-sm c-lesson" data-open-lesson="${esc(lesson.id)}">
    Open lesson: ${esc(lesson.title)}</button>`;
}

function reviewHtml(r) {
  const list = (items, cls) => items.length ? `<ul class="c-list ${cls}">${items.map((x) => `<li>${rich(x)}</li>`).join("")}</ul>` : "";
  const s = r.stats;
  const tiles = s.closed ? `
    <div class="c-tiles">
      <div><span>Closed trades</span><b class="mono">${s.closed}</b></div>
      <div><span>Won / lost</span><b class="mono">${s.wins} / ${s.losses}</b></div>
      <div><span>Result after fees</span><b class="mono ${s.net_pnl > 0 ? "up" : s.net_pnl < 0 ? "down" : ""}">${s.net_pnl < 0 ? "-" : ""}$${Math.abs(s.net_pnl).toLocaleString(undefined, { maximumFractionDigits: 2 })}</b></div>
      <div><span>Fees on these trades</span><b class="mono">$${s.closed_fees.toLocaleString(undefined, { maximumFractionDigits: 2 })}</b></div>
    </div>` : "";
  const ref = r.next?.revisit || (r.next?.primary?.kind === "lesson" ? { id: r.next.primary.id, title: r.next.primary.title } : null);
  return `<div class="c-review">
    <p class="c-kicker">Your last ${r.days} days</p>
    <h3>${esc(r.headline)}</h3>
    ${tiles}
    ${r.went_well.length ? `<p class="c-sub good">Going well</p>${list(r.went_well, "good")}` : ""}
    ${r.work_on.length ? `<p class="c-sub work">Work on</p>${list(r.work_on, "work")}` : ""}
    <p class="c-sub">Next step</p>${rich(r.next_step)}
    ${lessonButton(ref)}
  </div>`;
}

function render() {
  const log = $("coach-log");
  const intro = `<div class="c-msg bot"><p>Hi! I'm your coach. Ask me anything about trading or this app, and I'll
    explain it using what you've learned so far. I can see your practice account, so you can ask about your own trades.</p></div>`;
  log.innerHTML = intro + history.map((m) => {
    if (m.role === "user") return `<div class="c-msg me">${esc(m.content).replace(/\n/g, "<br>")}</div>`;
    if (m.review) return `<div class="c-msg bot wide">${reviewHtml(m.review)}</div>`;
    return `<div class="c-msg bot">${rich(m.content)}${lessonButton(m.lesson)}</div>`;
  }).join("") + (busy ? `<div class="c-msg bot typing" aria-label="Coach is thinking"><i></i><i></i><i></i></div>` : "");
  const lastCoach = [...history].reverse().find((m) => m.role === "assistant");
  const follow = lastCoach?.follow_ups?.length ? lastCoach.follow_ups : null;
  $("coach-chips").innerHTML = (follow || ["What should I learn next?", "Explain what I'm looking at", "What are fees?"])
    .map((q) => `<button type="button" class="c-chip" data-ask="${esc(q)}">${esc(q)}</button>`).join("")
    + `<button type="button" class="c-chip c-chip-review" data-review="1">Review my week</button>`;
  $("coach-send").disabled = busy;
  $("coach-input").disabled = busy;
  log.scrollTop = log.scrollHeight;
}

// ---------------------------------------------------------------- actions
function finish(entry) {
  busy = false;
  history.push(entry);
  save();
  render();
  window.dispatchEvent(new Event("learn:refresh")); // asking/reviewing can tick off a challenge
  $("coach-input").focus();
}

async function ask(text) {
  const message = text.trim();
  if (!message || busy) return;
  const prior = history.filter((m) => !m.review).slice(-8).map((m) => ({ role: m.role, content: m.content }));
  history.push({ role: "user", content: message });
  busy = true;
  render();
  try {
    const res = await api("/api/coach/ask", "POST",
      { message, history: prior, view: currentView(), symbol: currentSymbol() });
    finish({ role: "assistant", content: res.answer, lesson: res.lesson, follow_ups: res.follow_ups, source: res.source });
  } catch (err) {
    finish({ role: "assistant", content: `Sorry, I couldn't answer just now (${err.message}). Try again in a moment.` });
  }
}

async function review() {
  if (busy) return;
  history.push({ role: "user", content: "Review my week" });
  busy = true;
  render();
  try {
    const r = await api("/api/coach/review?days=7");
    finish({ role: "assistant", content: r.headline, review: r });
  } catch (err) {
    finish({ role: "assistant", content: `Sorry, I couldn't build your review (${err.message}).` });
  }
}

function open() {
  $("coach").hidden = false;
  document.body.classList.add("coach-open");
  $("coach-fab").setAttribute("aria-expanded", "true");
  render();
  if (window.matchMedia("(min-width: 601px)").matches) $("coach-input").focus();
}
function close() {
  $("coach").hidden = true;
  document.body.classList.remove("coach-open");
  $("coach-fab").setAttribute("aria-expanded", "false");
}

function bind() {
  $("coach-fab").addEventListener("click", () => ($("coach").hidden ? open() : close()));
  $("coach-close").addEventListener("click", close);
  $("coach-clear").addEventListener("click", () => { history = []; save(); render(); });
  $("coach-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("coach-input");
    const text = input.value;
    input.value = "";
    input.style.height = "";
    ask(text);
  });
  $("coach-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("coach-form").requestSubmit();
    }
  });
  $("coach-input").addEventListener("input", (e) => {
    e.target.style.height = "";
    e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`;
  });
  $("coach").addEventListener("click", (e) => {
    const chip = e.target.closest("[data-ask]");
    if (chip) { ask(chip.dataset.ask); return; }
    if (e.target.closest("[data-review]")) { review(); return; }
    const lessonBtn = e.target.closest("[data-open-lesson]");
    if (lessonBtn) {
      window.dispatchEvent(new CustomEvent("learn:open-lesson", { detail: lessonBtn.dataset.openLesson }));
      if (window.matchMedia("(max-width: 600px)").matches) close();
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("coach").hidden && !e.defaultPrevented && $("term-pop").hidden) close();
  });
  window.addEventListener("learn:open-coach", (e) => {
    open();
    if (e.detail === "review") review();
  });
}

load();
bind();
loadGlossary().then(() => { if (!$("coach").hidden) render(); });
