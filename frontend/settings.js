// Settings: AI models (local first, optional OpenRouter cloud with a monthly cap).
import { $, api, esc, num, onView } from "./ui.js";

const TASK_LABELS = { code: "Code Lab pair-programmer", research: "Research (filings and news)", agent: "Agent checks and capstone write-up",
  coach: "Coach chat" };

async function load() {
  const box = $("set-ai");
  try {
    const s = await api("/api/ai/settings");
    const known = s.models.some((m) => m.id === s.cloud_model);
    box.innerHTML = `
      <p class="${s.local_available ? "up" : "down"}">Local AI: ${esc(s.local_model)} ${s.local_available ? "is running (free, private)." : "isn't reachable. Start Ollama to use it."}</p>
      <label class="switch-row"><input type="checkbox" id="set-cloud" ${s.cloud_enabled ? "checked" : ""} ${s.key_present ? "" : "disabled"}>
        Use a cloud model (OpenRouter) for the tasks ticked below</label>
      ${s.key_present ? "" : '<p class="muted">No OpenRouter key found. Set OPENROUTER_API_KEY or put the key in ~/.config/openrouter/api_key, then restart.</p>'}
      <div class="lab-form">
        <label>Cloud model <select id="set-model">${s.models.map((m) => `<option value="${esc(m.id)}" ${m.id === s.cloud_model ? "selected" : ""}>${esc(m.label)}</option>`).join("")}
          <option value="__custom" ${known ? "" : "selected"}>Other OpenRouter model…</option></select></label>
        <label id="set-custom-wrap" ${known ? "hidden" : ""}>Model id <input id="set-custom" value="${known ? "" : esc(s.cloud_model)}" placeholder="provider/model"></label>
        <label>Monthly cap <span class="input-prefix"><span>$</span><input type="number" id="set-cap" min="0" max="100" step="0.5" value="${num(s.monthly_cap_usd, 2)}"></span></label>
      </div>
      <div class="chips">${s.tasks.map((t) => `<label class="chip ${s.cloud_tasks.includes(t) ? "on" : ""}"><input type="checkbox" data-task="${t}" ${s.cloud_tasks.includes(t) ? "checked" : ""}>${esc(TASK_LABELS[t] || t)}</label>`).join("")}</div>
      <p>Spent this month (${esc(s.month)}): <b>$${num(s.spend_usd, 4)}</b> over ${s.spend_calls} calls${s.cap_reached ? ' · <span class="down">cap reached: using the local AI until next month</span>' : ""}.</p>
      <button type="button" class="btn btn-primary" id="set-save">Save</button> <span class="muted" id="set-msg"></span>
      <p class="muted">Anything not ticked, or anything after the cap is reached, uses your local AI. If the cloud model fails, the app falls back to the local one too. Your key never leaves this computer except to OpenRouter.</p>`;
    $("set-model").addEventListener("change", () => { $("set-custom-wrap").hidden = $("set-model").value !== "__custom"; });
    box.querySelectorAll(".chip input").forEach((i) => i.addEventListener("change", () => i.closest(".chip").classList.toggle("on", i.checked)));
    $("set-save").addEventListener("click", save);
  } catch (err) {
    box.innerHTML = `<p class="lab-msg">${esc(err.message)}</p>`;
  }
}

async function save() {
  const model = $("set-model").value === "__custom" ? $("set-custom").value.trim() : $("set-model").value;
  try {
    await api("/api/ai/settings", "PUT", {
      cloud_enabled: $("set-cloud").checked, cloud_model: model, monthly_cap_usd: Number($("set-cap").value),
      cloud_tasks: [...document.querySelectorAll("#set-ai [data-task]:checked")].map((i) => i.dataset.task),
    });
    await load();
    $("set-msg").textContent = "Saved.";
  } catch (err) { $("set-msg").textContent = err.message; }
}

onView("settings", load);
