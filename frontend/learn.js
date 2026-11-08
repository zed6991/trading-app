// Learning path: navigation, level gating, course home, lesson + quiz player, challenges, glossary and tappable terms.
const $ = (id) => document.getElementById(id);
const POLL_MS = 8000;
const VIEW_KEY = "view";
const VIEWS = ["learn", "practice", "dashboard", "glossary"];
// Where each challenge is done, so "Continue" can take the learner straight there.
const CHALLENGE_VIEW = {
  manual_buy: "practice", manual_sell: "practice", win: "practice", risk: "practice", brackets: "practice",
  protect_exit: "practice", explain: "practice", chart: "dashboard", ticket: "dashboard", indicator: "dashboard",
  analyze: "dashboard", backtest: "dashboard", backtest_strategy: "dashboard", beat_hold: "dashboard",
  auto_on: "dashboard", auto_trade: "dashboard",
};
const CHALLENGE_EVENT = {
  "first-buy": "manual_buy", "first-sell": "manual_sell", "first-win": "win", "set-safety-net": "risk",
  "edit-stop": "brackets", "auto-exit": "protect_exit", "view-charts": "chart", "use-ticket": "ticket",
  "indicator-on": "indicator", "ask-ai": "analyze", "run-backtest": "backtest", "compare-strategies": "backtest_strategy",
  "beat-hold": "beat_hold", "auto-on": "auto_on", "explain-trade": "explain", "auto-trade": "auto_trade",
};

let course = null; // GET /api/learn
let glossary = []; // GET /api/learn/glossary
const termIndex = new Map(); // lowercase alias -> slug
let view = "learn";
let lesson = null; // { data, step, quizIndex, feedback }
let pendingLevelUp = null;

// ---------------------------------------------------------------- helpers
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

async function api(path, method = "GET", body) {
  const res = await fetch(path, body === undefined ? { method }
    : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Something went wrong");
  return data;
}

function storageGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* preference only */ }
}

/** Lesson markup -> HTML. [[slug]] / [[slug|text]] become tappable terms; blank lines split paragraphs. */
function rich(text) {
  return String(text || "").split(/\n\n+/).map((para) => {
    const html = esc(para).replace(/\[\[([a-z0-9-]+)(?:\|([^\]]+))?\]\]/g, (_, slug, shown) => {
      const g = glossary.find((t) => t.slug === slug);
      const name = g ? (/[A-Z].*[A-Z]/.test(g.term) ? g.term : g.term.toLowerCase()) : slug; // "Price" -> "price", keep "RSI"
      return `<span class="term" data-term="${slug}" tabindex="0" role="button">${shown || esc(name)}</span>`;
    });
    return `<p>${html.replace(/\n/g, "<br>")}</p>`;
  }).join("");
}

const levelOf = (n) => course?.levels.find((l) => l.n === n);
const levelNow = () => course?.level || 1;

// ---------------------------------------------------------------- views + gating
function setView(next) {
  if (!VIEWS.includes(next)) next = "learn";
  if (next === "dashboard" && levelNow() < 3) next = "learn";
  view = next;
  const body = document.body;
  VIEWS.forEach((v) => body.classList.toggle(`view-${v}`, v === next));
  body.classList.toggle("simple-mode", next !== "dashboard");
  document.querySelectorAll("#main-nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === next));
  storageSet(VIEW_KEY, next);
  window.scrollTo(0, 0);
  if (next === "dashboard") window.dispatchEvent(new Event("resize")); // charts measure themselves on resize
}

function applyLevel(n) {
  const body = document.body;
  const before = Number(body.dataset.level) || 0;
  body.dataset.level = String(n);
  for (let i = 1; i <= 6; i += 1) body.classList.toggle(`unlock-${i}`, i <= n);
  $("level-pill").textContent = course?.finished ? "Graduate" : `Level ${n}`;
  if (before !== n) window.dispatchEvent(new Event("learn:level"));
  if (view === "dashboard" && n < 3) setView("learn");
}

// ---------------------------------------------------------------- course data
async function loadCourse({ quiet = true } = {}) {
  try {
    const prev = course;
    course = await api("/api/learn");
    applyLevel(course.level);
    if (prev && !prev.unlock_all && !course.unlock_all) {
      if (course.level > prev.level) showLevelUp(course.level);
      else if (course.finished && !prev.finished) showLevelUp(null);
    }
    if (!lesson) renderHome();
  } catch (err) {
    if (!quiet) $("l-title").textContent = `Can't reach the app right now. ${err.message}`;
  }
}

function nextStep() {
  const lvl = levelOf(levelNow());
  if (!lvl || course.finished) return null;
  const lessonTodo = lvl.lessons.find((l) => !l.done);
  if (lessonTodo) return { kind: "lesson", item: lessonTodo };
  const ch = lvl.challenges.find((c) => !c.done && !c.bonus);
  return ch ? { kind: "challenge", item: ch } : null;
}

// ---------------------------------------------------------------- course home
function statusBadge(lvl) {
  if (lvl.status === "done") return `<span class="l-badge done" aria-label="Done">✓</span>`;
  if (lvl.status === "locked") {
    return `<span class="l-badge locked" aria-label="Locked"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3" fill="none"/></svg></span>`;
  }
  return `<span class="l-badge current">${lvl.n}</span>`;
}

function levelBody(lvl) {
  const lessons = lvl.lessons.map((l) => `
    <li><button type="button" class="l-lesson ${l.done ? "done" : ""}" data-lesson="${esc(l.id)}">
      <span class="l-check">${l.done ? "✓" : ""}</span>
      <span class="l-lesson-title">${esc(l.title)}</span>
      <span class="l-lesson-meta">${l.done ? "Review" : `${l.minutes} min`}</span>
    </button></li>`).join("");
  const challenges = lvl.challenges.map((c) => `
    <li class="l-challenge ${c.done ? "done" : ""}">
      <span class="l-check">${c.done ? "✓" : ""}</span>
      <div>
        <p class="l-ch-title">${esc(c.title)}${c.bonus ? ' <span class="l-tag">Bonus</span>' : ""}${c.times > 1 && !c.done ? ` <span class="l-tag">${c.progress}/${c.times}</span>` : ""}</p>
        ${c.done ? "" : `<p class="l-ch-how">${esc(c.how)}</p>`}
      </div>
      ${c.done || lvl.status !== "current" ? "" : `<button type="button" class="btn btn-sm" data-go="${esc(c.id)}">Go</button>`}
    </li>`).join("");
  return `
    <h3 class="l-sub">Lessons</h3><ul class="l-lessons">${lessons}</ul>
    <h3 class="l-sub">Practice challenges</h3>
    <p class="l-sub-note">Do these in the app with your practice money. They tick off automatically.</p>
    <ul class="l-challenges">${challenges}</ul>
    <p class="l-unlocks"><b>This level unlocks:</b> ${lvl.unlocks.map(esc).join(" · ")}</p>`;
}

function renderHome() {
  if (!course) return;
  $("learn-home").hidden = false;
  $("lesson").hidden = true;
  const cur = levelOf(levelNow());
  $("l-kicker").textContent = course.finished ? "Course complete" : `Level ${cur.n} of ${course.max_level}`;
  $("l-title").textContent = course.finished ? "You've finished the course. Keep practising." : cur.title;
  $("l-progress-fill").style.width = `${course.percent}%`;
  $("l-progress-text").textContent = course.unlock_all
    ? "Everything is unlocked. The lessons are still here whenever you want them."
    : `${course.percent}% of the course done`;

  const step = nextStep();
  const btn = $("l-continue");
  btn.hidden = !step;
  if (step) {
    btn.textContent = step.kind === "lesson" ? `Continue: ${step.item.title} →` : `Next challenge: ${step.item.title} →`;
    btn.onclick = () => (step.kind === "lesson" ? openLesson(step.item.id) : goChallenge(step.item.id));
  }

  $("l-levels").innerHTML = course.levels.map((lvl) => {
    const head = `<div class="l-level-head">${statusBadge(lvl)}<div><h2>Level ${lvl.n}: ${esc(lvl.title)}</h2><p>${esc(lvl.summary)}</p></div></div>`;
    if (lvl.status === "locked") {
      return `<li class="l-level locked">${head}<p class="l-unlocks"><b>Unlocks:</b> ${lvl.unlocks.map(esc).join(" · ")}</p></li>`;
    }
    const open = lvl.status === "current" ? " open" : "";
    return `<li class="l-level ${lvl.status}"><details${open}><summary>${head}</summary>${levelBody(lvl)}</details></li>`;
  }).join("");

  $("l-foot").innerHTML = course.unlock_all
    ? `<button type="button" class="link-btn" data-act="guided">Go back to the guided path (re-lock levels I haven't finished)</button>`
    : `<button type="button" class="link-btn" data-act="unlock">Already know how to trade? Unlock everything</button>`;
  $("l-foot").innerHTML += ` · <button type="button" class="link-btn" data-act="restart">Restart the course</button>`;
}

function goChallenge(id) {
  const target = CHALLENGE_VIEW[CHALLENGE_EVENT[id]] || "practice";
  setView(target);
}

async function footAction(btn) {
  const act = btn.dataset.act;
  if (!btn.dataset.armed) {
    btn.dataset.armed = "1";
    btn.dataset.label = btn.textContent;
    btn.textContent = act === "restart" ? "Click again to clear all course progress" : "Click again to confirm";
    setTimeout(() => { if (btn.isConnected) { btn.textContent = btn.dataset.label; delete btn.dataset.armed; } }, 4000);
    return;
  }
  if (act === "unlock") course = await api("/api/learn/unlock-all", "POST", { on: true });
  else if (act === "guided") course = await api("/api/learn/unlock-all", "POST", { on: false });
  else course = await api("/api/learn/reset", "POST");
  applyLevel(course.level);
  renderHome();
}

// ---------------------------------------------------------------- lesson player
async function openLesson(id) {
  try {
    const data = await api(`/api/learn/lessons/${encodeURIComponent(id)}`);
    if (data.locked) return;
    lesson = { data, step: 0, quizIndex: 0, feedback: null, wrong: new Set(), leveledUp: false };
    setView("learn");
    renderLesson();
  } catch (err) {
    $("l-progress-text").textContent = `Couldn't open that lesson. ${err.message}`;
  }
}

function closeLesson() {
  lesson = null;
  renderHome();
  window.scrollTo(0, 0);
  if (pendingLevelUp !== null) {
    const n = pendingLevelUp;
    pendingLevelUp = null;
    showLevelUp(n);
  }
}

function renderVisual(name) {
  if (name === "live-price") return `<div class="l-visual l-live"><span>Bitcoin right now</span><b data-live="BTCUSDT">…</b></div>`;
  if (name === "account") return `<div class="l-visual l-live"><span>Your practice account</span><b data-live="equity">…</b><small data-live="cash"></small></div>`;
  if (name === "candle") {
    return `<figure class="l-visual"><svg viewBox="0 0 320 180" role="img" aria-label="Two candlesticks: green with open at the bottom and close at the top, red with open at the top and close at the bottom. Wicks show the high and low.">
      <line x1="90" y1="20" x2="90" y2="160" class="wick"/><rect x="70" y="55" width="40" height="70" class="body-up"/>
      <line x1="230" y1="20" x2="230" y2="160" class="wick"/><rect x="210" y="55" width="40" height="70" class="body-down"/>
      <g class="lbl"><text x="120" y="24">High</text><text x="120" y="60">Close</text><text x="120" y="128">Open</text><text x="120" y="162">Low</text>
      <text x="164" y="60" text-anchor="end">Open</text><text x="164" y="128" text-anchor="end">Close</text></g>
      <text x="90" y="178" class="cap" text-anchor="middle">Went up</text><text x="230" y="178" class="cap" text-anchor="middle">Went down</text></svg></figure>`;
  }
  if (name === "bracket") {
    return `<figure class="l-visual"><svg viewBox="0 0 320 170" role="img" aria-label="A price line starting at the buy price, with a take-profit line 4% above and a stop-loss line 2% below.">
      <line x1="20" y1="30" x2="300" y2="30" class="tp"/><text x="24" y="22" class="lbl tp-t">Take-profit +4%: sell and bank the gain</text>
      <line x1="20" y1="100" x2="300" y2="100" class="entry"/><text x="24" y="92" class="lbl">You buy here</text>
      <line x1="20" y1="135" x2="300" y2="135" class="sl"/><text x="24" y="155" class="lbl sl-t">Stop-loss −2%: sell and keep the loss small</text>
      <polyline points="20,100 60,92 95,108 130,80 165,88 200,62 235,70 270,40 296,30" class="path"/></svg></figure>`;
  }
  if (name === "trend") {
    return `<figure class="l-visual"><svg viewBox="0 0 320 160" role="img" aria-label="A zig-zag line climbing, with each peak and each dip higher than the one before.">
      <polyline points="20,140 70,90 100,115 150,65 180,90 230,40 260,62 300,20" class="path"/>
      <g class="dots"><circle cx="100" cy="115" r="4"/><circle cx="180" cy="90" r="4"/><circle cx="260" cy="62" r="4"/></g>
      <text x="190" y="120" class="lbl">Higher lows</text><text x="40" y="40" class="lbl">Higher highs</text>
      <g class="dots hi"><circle cx="70" cy="90" r="4"/><circle cx="150" cy="65" r="4"/><circle cx="230" cy="40" r="4"/></g></svg></figure>`;
  }
  return "";
}

async function fillLive(root) {
  const nodes = root.querySelectorAll("[data-live]");
  if (!nodes.length) return;
  try {
    const s = await api("/api/state");
    const fmt = (n) => `$${Number(n).toLocaleString("en-US", { maximumFractionDigits: n < 100 ? 2 : 0 })}`;
    nodes.forEach((n) => {
      const k = n.dataset.live;
      if (k === "equity") n.textContent = fmt(s.account.equity);
      else if (k === "cash") n.textContent = `${fmt(s.account.cash)} of it is cash, ready to spend`;
      else n.textContent = s.prices[k] ? fmt(s.prices[k]) : "Price not loaded yet";
    });
  } catch { /* decorative */ }
}

function renderLesson() {
  const { data, step } = lesson;
  const cards = data.cards.length;
  const qs = data.quiz.length;
  const total = cards + qs + 1;
  const dots = Array.from({ length: total }, (_, i) => `<span class="dot${i < step ? " past" : i === step ? " now" : ""}"></span>`).join("");
  const top = `<div class="lesson-top"><button type="button" class="link-btn" data-act="close">← Course</button>
    <span class="lesson-crumb">Level ${data.level} · ${esc(data.title)}</span></div><div class="lesson-dots" aria-hidden="true">${dots}</div>`;
  let main = "";
  if (step < cards) {
    const c = data.cards[step];
    main = `<article class="lesson-card"><p class="l-kicker">${step + 1} of ${cards}</p><h1>${esc(c.title)}</h1>
      <div class="lesson-body">${rich(c.body)}</div>${c.visual ? renderVisual(c.visual) : ""}</article>
      <div class="lesson-nav">${step ? '<button type="button" class="btn btn-ghost" data-act="back">Back</button>' : "<span></span>"}
      <button type="button" class="btn btn-primary" data-act="next">${step === cards - 1 ? "Quick check →" : "Next →"}</button></div>`;
  } else if (step < cards + qs) {
    const qi = step - cards;
    const q = data.quiz[qi];
    const fb = lesson.feedback;
    const choices = q.choices.map((choice, i) => {
      let cls = "";
      if (fb?.correct && fb.choice === i) cls = "right";
      else if (lesson.wrong.has(i)) cls = "wrong";
      return `<button type="button" class="quiz-choice ${cls}" data-choice="${i}" ${fb?.correct || lesson.wrong.has(i) ? "disabled" : ""}>${esc(choice)}</button>`;
    }).join("");
    let feedback = "";
    if (fb?.correct) feedback = `<div class="quiz-fb right" role="status"><b>Right.</b> ${rich(fb.why)}</div>`;
    else if (fb) feedback = `<div class="quiz-fb wrong" role="status"><b>Not quite.</b> Have another go.${fb.hint ? ` ${esc(fb.hint)}` : ""}</div>`;
    main = `<article class="lesson-card"><p class="l-kicker">Quick check · ${qi + 1} of ${qs}</p><h1>${esc(q.q)}</h1>
      <div class="quiz-choices">${choices}</div>${feedback}</article>
      <div class="lesson-nav"><button type="button" class="btn btn-ghost" data-act="back">Back</button>
      ${fb?.correct ? `<button type="button" class="btn btn-primary" data-act="next">${qi === qs - 1 ? "Finish →" : "Next question →"}</button>` : "<span></span>"}</div>`;
  } else {
    const lvl = levelOf(data.level);
    const nextLesson = lvl?.lessons.find((l) => !l.done && l.id !== data.id);
    main = `<article class="lesson-card lesson-done"><p class="l-kicker">Lesson complete</p><h1>${esc(data.title)} ✓</h1>
      <div class="takeaway"><b>Remember this:</b> ${rich(data.takeaway)}</div></article>
      <div class="lesson-nav"><button type="button" class="btn btn-ghost" data-act="close">Back to course</button>
      ${nextLesson ? `<button type="button" class="btn btn-primary" data-lesson="${esc(nextLesson.id)}">Next lesson: ${esc(nextLesson.title)} →</button>`
        : '<button type="button" class="btn btn-primary" data-act="close">See your challenges →</button>'}</div>`;
  }
  const box = $("lesson");
  box.innerHTML = top + main;
  $("learn-home").hidden = true;
  box.hidden = false;
  fillLive(box);
  box.querySelector(".lesson-card h1")?.focus?.();
}

async function answer(choice) {
  const qi = lesson.step - lesson.data.cards.length;
  try {
    const res = await api(`/api/learn/lessons/${lesson.data.id}/answer`, "POST", { question: qi, choice });
    lesson.feedback = { ...res, choice };
    if (!res.correct) lesson.wrong.add(choice);
    if (res.leveled_up) pendingLevelUp = res.level;
    renderLesson();
  } catch (err) {
    lesson.feedback = { correct: false, hint: err.message, choice };
    renderLesson();
  }
}

async function lessonAction(act) {
  if (act === "close") {
    await loadCourseSilently();
    closeLesson();
    return;
  }
  if (act === "back") lesson.step = Math.max(0, lesson.step - 1);
  if (act === "next") lesson.step += 1;
  lesson.feedback = null;
  lesson.wrong = new Set();
  const cards = lesson.data.cards.length;
  if (lesson.step === cards + lesson.data.quiz.length) await loadCourseSilently();
  renderLesson();
  window.scrollTo(0, 0);
}

/** Refresh course data without triggering the level-up popup (the lesson player shows it on close). */
async function loadCourseSilently() {
  try {
    course = await api("/api/learn");
    applyLevel(course.level);
  } catch { /* next poll will retry */ }
}

// ---------------------------------------------------------------- level up
function showLevelUp(n) {
  if (lesson) {
    pendingLevelUp = n;
    return;
  }
  const lvl = n ? levelOf(n) : null;
  $("levelup-title").textContent = lvl ? `Level ${n} unlocked: ${lvl.title}` : "You finished the course";
  $("levelup-text").textContent = lvl ? "Nice work. Here's what you can use now:" : "Everything is unlocked. Keep practising, and review your trades often.";
  $("levelup-list").innerHTML = lvl ? lvl.unlocks.map((u) => `<li>${esc(u)}</li>`).join("") : "";
  $("levelup-go").hidden = !lvl;
  $("levelup").hidden = false;
  $("levelup-go").focus();
}

function hideLevelUp() {
  $("levelup").hidden = true;
}

// ---------------------------------------------------------------- glossary + tappable terms
function buildTermIndex() {
  termIndex.clear();
  for (const g of glossary) {
    for (const name of [g.term, g.slug, ...(g.aliases || [])]) termIndex.set(name.toLowerCase(), g.slug);
  }
}

function renderGlossary() {
  const q = $("g-search").value.trim().toLowerCase();
  const nameHit = (g) => [g.term, ...(g.aliases || [])].some((n) => n.toLowerCase().includes(q));
  const list = [...glossary]
    .filter((g) => !q || [g.term, g.short, g.more, ...(g.aliases || [])].join(" ").toLowerCase().includes(q))
    .sort((a, b) => (q ? nameHit(b) - nameHit(a) : 0) || a.term.localeCompare(b.term));
  $("g-list").innerHTML = list.map((g) => `<div class="g-item" id="term-${esc(g.slug)}"><dt>${esc(g.term)}
    <span class="l-tag">Level ${g.level}</span></dt><dd>${esc(g.short)} ${esc(g.more || "")}</dd></div>`).join("")
    || `<p class="s-empty">No matches. Try a shorter word.</p>`;
}

function showTerm(target) {
  const g = glossary.find((t) => t.slug === target.dataset.term);
  if (!g) return;
  const pop = $("term-pop");
  const lvl = levelOf(g.level);
  const when = g.level <= levelNow() ? `Taught in Level ${g.level}${lvl ? `: ${lvl.title}` : ""}`
    : `You'll learn this in Level ${g.level}${lvl ? `: ${lvl.title}` : ""}`;
  pop.innerHTML = `<b>${esc(g.term)}</b><p>${esc(g.short)}</p>${g.more ? `<p class="more">${esc(g.more)}</p>` : ""}<p class="when">${esc(when)}</p>`;
  pop.hidden = false;
  const r = target.getBoundingClientRect();
  const w = Math.min(320, window.innerWidth - 32);
  pop.style.width = `${w}px`;
  const left = Math.max(16, Math.min(r.left, window.innerWidth - w - 16));
  const below = r.bottom + 8 + pop.offsetHeight < window.innerHeight;
  pop.style.left = `${left}px`;
  pop.style.top = `${below ? r.bottom + 8 : Math.max(8, r.top - pop.offsetHeight - 8)}px`;
  pop.dataset.for = g.slug;
}

function hideTerm() {
  $("term-pop").hidden = true;
}

// Dashboard labels are rendered by app.js; mark any that match a glossary term.
const AUTO_SELECTOR = "th, .metric-label, .mcard-label, .switch-label, .chk, .pill, .ai-level > span:first-child, "
  + ".conf > span:first-child, .card-head h3, .field > label, .bt-chart-title";
function autoLink(root = document.body) {
  if (!termIndex.size) return;
  root.querySelectorAll(AUTO_SELECTOR).forEach((node) => {
    if (node.dataset.termChecked || node.closest(".term-pop, .learn, .glossary-view")) return;
    node.dataset.termChecked = "1";
    const text = node.textContent.replace(/\s+/g, " ").trim().toLowerCase();
    const slug = termIndex.get(text) || termIndex.get(text.replace(/\s*%$/, ""));
    if (!slug) return;
    if (node.matches("label, .chk, .switch-label") || node.closest("label, button")) {
      const q = document.createElement("button");
      q.type = "button";
      q.className = "term-q";
      q.dataset.term = slug;
      q.textContent = "?";
      q.setAttribute("aria-label", `What does ${node.textContent.trim()} mean?`);
      (node.closest("button") ? node.closest("button").parentElement : node).append(q);
    } else {
      node.classList.add("term");
      node.dataset.term = slug;
      node.tabIndex = 0;
      node.setAttribute("role", "button");
    }
  });
}

// ---------------------------------------------------------------- client-side challenge events
async function sendEvent(name, value) {
  try {
    await api("/api/learn/events", "POST", value === undefined ? { name } : { name, value });
    await loadCourse();
  } catch { /* progress is best-effort */ }
}

function watchDashboard() {
  document.addEventListener("click", (e) => {
    if (view !== "dashboard") return;
    const tab = e.target.closest(".sym-tab");
    if (tab?.dataset.symbol) sendEvent("chart", tab.dataset.symbol);
    if (e.target.closest("#lower-toggle button")) sendEvent("indicator");
    if (e.target.closest("#ot-submit")) setTimeout(() => sendEvent("ticket"), 600);
  });
  document.addEventListener("change", (e) => {
    if (view === "dashboard" && e.target.matches("#ov-sma20, #ov-sma50, #ov-bb") && e.target.checked) sendEvent("indicator");
  });
  // Trades placed from the dashboard can complete challenges too, so refresh shortly after.
  document.addEventListener("click", (e) => {
    if (e.target.closest("#ot-submit, #ai-run, #bt-run, #tg-auto")) setTimeout(loadCourse, 1500);
  });
}

// ---------------------------------------------------------------- events + start
function bind() {
  $("main-nav").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-view]");
    if (!b) return;
    if (b.dataset.view === "learn" && lesson) closeLesson();
    setView(b.dataset.view);
  });
  $("level-pill").addEventListener("click", () => {
    if (lesson) closeLesson();
    setView("learn");
  });
  $("learn").addEventListener("click", (e) => {
    const lessonBtn = e.target.closest("[data-lesson]");
    if (lessonBtn) { openLesson(lessonBtn.dataset.lesson); return; }
    const go = e.target.closest("[data-go]");
    if (go) { goChallenge(go.dataset.go); return; }
    const choice = e.target.closest("[data-choice]");
    if (choice && !choice.disabled) { answer(Number(choice.dataset.choice)); return; }
    const act = e.target.closest("[data-act]");
    if (!act) return;
    if (act.closest("#l-foot")) footAction(act);
    else lessonAction(act.dataset.act);
  });
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-term]");
    if (t) {
      e.preventDefault();
      if (!$("term-pop").hidden && $("term-pop").dataset.for === t.dataset.term) hideTerm();
      else showTerm(t);
    } else if (!e.target.closest("#term-pop")) {
      hideTerm();
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { hideTerm(); hideLevelUp(); }
    if ((e.key === "Enter" || e.key === " ") && e.target.matches?.(".term[data-term]")) {
      e.preventDefault();
      showTerm(e.target);
    }
  });
  window.addEventListener("scroll", hideTerm, true);
  $("g-search").addEventListener("input", renderGlossary);
  $("levelup-close").addEventListener("click", hideLevelUp);
  $("levelup").addEventListener("click", (e) => { if (e.target === $("levelup")) hideLevelUp(); });
  $("levelup-go").addEventListener("click", () => {
    hideLevelUp();
    const step = nextStep();
    if (step?.kind === "lesson") openLesson(step.item.id);
    else setView("learn");
  });
  window.addEventListener("learn:refresh", () => setTimeout(loadCourse, 300));
  let pending = 0;
  new MutationObserver(() => {
    clearTimeout(pending);
    pending = setTimeout(() => autoLink(), 250);
  }).observe(document.body, { childList: true, subtree: true });
  watchDashboard();
}

async function start() {
  bind();
  try {
    glossary = await api("/api/learn/glossary");
    buildTermIndex();
    renderGlossary();
  } catch { /* terms just won't be tappable */ }
  await loadCourse({ quiet: false });
  setView(storageGet(VIEW_KEY) || "learn");
  autoLink();
  setInterval(() => { if (document.visibilityState === "visible") loadCourse(); }, POLL_MS);
}

start();
