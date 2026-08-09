(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const stages = ["audio", "asr", "diarization", "merge", "export"];
  const state = { settings: null, environment: null, job: null, source: null, started: 0, poller: null };

  const t = (key) => (window.UTTERAN_I18N[state.settings?.language || "ja"] || {})[key] || key;
  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
    if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || `${response.status} ${response.statusText}`); }
    return response.status === 204 ? null : response.json();
  }
  function translate() {
    document.documentElement.lang = state.settings.language;
    document.querySelectorAll("[data-i18n]").forEach((node) => { node.textContent = t(node.dataset.i18n); });
  }
  function applySettings() {
    document.documentElement.dataset.theme = state.settings.theme;
    $("theme-select").value = state.settings.theme;
    $("ui-language").value = state.settings.language;
    $("default-input").value = state.settings.default_input_dir || "";
    $("default-output").value = state.settings.default_output_dir || "";
    if (!$("input-path").value) $("input-path").value = state.settings.default_input_dir || "";
    if (!$("output-dir").value) $("output-dir").value = state.settings.default_output_dir || "";
    $("token-state").textContent = state.settings.token_configured ? t("tokenSet") : t("tokenUnset");
    translate();
  }
  function option(select, value, label) { const node = document.createElement("option"); node.value = value; node.textContent = label; select.appendChild(node); }
  function setOptions(select, values, selected) { select.replaceChildren(); values.forEach((item) => option(select, item.id ?? item, item.label ?? item)); if (selected && [...select.options].some((x) => x.value === selected)) select.value = selected; select.disabled = values.length === 0; }

  async function loadEnvironment(profile) {
    $("form-status").textContent = t("detecting");
    state.environment = await api(`/api/environment${profile ? `?profile=${encodeURIComponent(profile)}` : ""}`);
    const existing = state.environment.profiles.filter((item) => item.exists);
    setOptions($("profile-select"), existing.map((item) => ({ id: item.name, label: item.name })), state.environment.active_profile);
    setOptions($("default-profile"), [{ id: "", label: "—" }, ...existing.map((item) => ({ id: item.name, label: item.name }))], state.settings.default_profile || "");
    const devices = state.environment.devices || {};
    const gpu = [...(devices.ctranslate2?.cuda_devices || []), ...(devices.pytorch?.xpu_devices || [])].filter((x) => x.usable).map((x) => x.name);
    $("hardware-summary").textContent = gpu.join(", ") || `CPU · ${devices.cpu?.logical_cores || "—"} threads`;
    $("profile-summary").textContent = state.environment.active_profile || "—";
    $("model-summary").textContent = String(state.environment.models.length || 0);
    const variants = Object.entries(devices.native?.variants || {}).filter(([, yes]) => yes).map(([name]) => name);
    $("native-summary").textContent = variants.join(", ") || "—";
    const alert = $("environment-alert");
    if (!existing.length) { alert.textContent = t("noProfiles"); alert.classList.remove("hidden"); }
    else if (!(state.environment.options.asr || []).length) { alert.textContent = t("noRuntime"); alert.classList.remove("hidden"); }
    else if (state.environment.errors.length) { alert.textContent = state.environment.errors.join(" · "); alert.classList.remove("hidden"); }
    else alert.classList.add("hidden");
    renderRuntimeOptions();
    $("form-status").textContent = (state.environment.options.asr || []).length ? t("ready") : "";
  }
  function renderRuntimeOptions() {
    const asr = state.environment?.options.asr || [];
    setOptions($("asr-backend"), asr, $("asr-backend").value);
    renderAsrDetail();
    const diar = state.environment?.options.diarization || [];
    setOptions($("diarization-backend"), diar, $("diarization-backend").value);
    renderDiarizationDetail();
    setOptions($("language-select"), (state.environment?.options.languages || []).map((x) => ({ id: x, label: x === "auto" ? t("automatic") : x })), "ja");
    const formats = state.environment?.options.formats || [];
    $("format-options").replaceChildren(...formats.map((name) => { const label = document.createElement("label"); label.className = "format-chip"; const input = document.createElement("input"); input.type = "checkbox"; input.value = name; input.checked = ["srt", "json", "md"].includes(name); const span = document.createElement("span"); span.textContent = name.toUpperCase(); label.append(input, span); return label; }));
    $("start-button").disabled = !asr.length;
  }
  function selectedGroup(groups, select) { return groups.find((item) => item.id === select.value) || groups[0]; }
  function renderAsrDetail() { const group = selectedGroup(state.environment?.options.asr || [], $("asr-backend")); setOptions($("asr-model"), group?.models || []); setOptions($("asr-device"), group?.devices || []); }
  function renderDiarizationDetail() { const group = selectedGroup(state.environment?.options.diarization || [], $("diarization-backend")); setOptions($("diarization-model"), group?.models || []); setOptions($("diarization-device"), group?.devices || []); }
  function renderSpeakerMode() { const mode = $("speaker-mode").value; $("speaker-fixed-field").classList.toggle("hidden", mode !== "fixed"); $("speaker-range-fields").classList.toggle("hidden", mode !== "range"); }
  function renderStageList() { $("stage-list").replaceChildren(...stages.map((stage) => { const li = document.createElement("li"); li.id = `stage-${stage}`; li.textContent = stage; return li; })); }
  function appendLog(text) { if (!text) return; const log = $("log-output"); log.textContent += `${text}\n`; log.scrollTop = log.scrollHeight; }
  function formatTime(seconds) { const value = Math.max(0, Math.round(seconds)); return [Math.floor(value / 3600), Math.floor(value / 60) % 60, value % 60].map((x) => String(x).padStart(2, "0")).join(":"); }
  function splitPatterns(value) { return value.split(/[\r\n,]+/).map((item) => item.trim()).filter(Boolean); }
  function updateClock() { if (!state.started) return; $("elapsed").textContent = formatTime((Date.now() - state.started) / 1000); }
  function handleEvent(event) {
    const data = JSON.parse(event.data); const kind = data.event;
    if (kind === "stage_start") { $(`stage-${data.stage}`)?.classList.add("active"); $("progress-title").textContent = data.stage; }
    if (kind === "progress") { const idx = Math.max(0, stages.indexOf(data.stage)); const ratio = data.ratio ?? 0; const overall = (idx + ratio) / stages.length; $("progress-bar").style.width = `${Math.min(100, overall * 100)}%`; if (overall > .02) { const elapsed = (Date.now() - state.started) / 1000; $("eta").textContent = formatTime(elapsed * (1 - overall) / overall); } if (data.message) appendLog(data.message); }
    if (kind === "stage_done") { const node = $(`stage-${data.stage}`); node?.classList.remove("active"); node?.classList.add("done"); if (node && data.skipped) node.textContent = `${data.stage} · skip`; const idx = stages.indexOf(data.stage); if (idx >= 0) $("progress-bar").style.width = `${((idx + 1) / stages.length) * 100}%`; }
    if (["warning", "error", "file_start", "file_done", "job_resolved"].includes(kind)) appendLog(data.message || data.reason || `${kind}: ${data.input_path || data.job_id || ""}`);
    if (kind === "output_written") appendLog(`${data.format}: ${data.path}`);
    if (kind === "done") finishJob();
  }
  async function finishJob() {
    if (!state.job) return; state.source?.close(); clearInterval(state.poller); const result = await api(`/api/jobs/${state.job.id}`); state.job = result;
    $("cancel-button").disabled = true; $("progress-bar").style.width = result.exit_code === 0 ? "100%" : $("progress-bar").style.width;
    $("result-panel").classList.remove("hidden"); $("result-eyebrow").textContent = result.exit_code === 0 ? "COMPLETE" : result.exit_code === 130 ? "CANCELLED" : "FAILED";
    $("result-title").textContent = result.exit_code === 0 ? t("complete") : result.exit_code === 130 ? t("cancelled") : result.exit_code === 5 ? t("partial") : t("failed");
    const guidance = $("result-guidance"); if (result.guidance) { guidance.textContent = t(`guide_${result.guidance.key}`); guidance.classList.remove("hidden"); } else guidance.classList.add("hidden");
    $("output-list").replaceChildren(...result.outputs.map((path) => { const li = document.createElement("li"); li.textContent = path; return li; }));
    $("open-output").classList.toggle("hidden", !result.outputs.length); result.logs.forEach(appendLog); $("start-button").disabled = false;
  }
  async function startJob(event) {
    event.preventDefault(); const mode = $("speaker-mode").value; const formats = [...$("format-options").querySelectorAll("input:checked")].map((x) => x.value);
    const payload = { input_path: $("input-path").value, output_dir: $("output-dir").value, profile: $("profile-select").value, asr_backend: $("asr-backend").value, asr_model: $("asr-model").value, asr_device: $("asr-device").value, diarization_enabled: $("diarization-enabled").checked, diarization_backend: $("diarization-backend").value, diarization_model: $("diarization-model").value, diarization_device: $("diarization-device").value, num_speakers: mode === "fixed" ? Number($("speaker-count").value) : null, min_speakers: mode === "range" ? Number($("speaker-min").value) : null, max_speakers: mode === "range" ? Number($("speaker-max").value) : null, language: $("language-select").value, formats, resume_mode: $("resume-mode").value, recursive: $("recursive-input").checked, include: splitPatterns($("include-patterns").value), exclude: splitPatterns($("exclude-patterns").value) };
    try { $("start-button").disabled = true; $("result-panel").classList.add("hidden"); $("progress-panel").classList.remove("hidden"); $("log-output").textContent = ""; renderStageList(); state.started = Date.now(); state.job = await api("/api/jobs", { method: "POST", body: JSON.stringify(payload) }); state.source = new EventSource(`/api/jobs/${state.job.id}/events`); ["job_resolved", "file_start", "file_done", "stage_start", "progress", "stage_done", "output_written", "warning", "error", "done"].forEach((name) => state.source.addEventListener(name, handleEvent)); state.poller = setInterval(async () => { updateClock(); const current = await api(`/api/jobs/${state.job.id}`); $("stall-warning").classList.toggle("hidden", !current.stalled); if (["completed", "failed", "cancelled"].includes(current.status)) finishJob(); }, 1000); } catch (error) { $("start-button").disabled = false; appendLog(error.message); $("progress-panel").classList.add("hidden"); alert(error.message); }
  }
  async function saveSettings(event) { event.preventDefault(); const payload = { theme: $("theme-select").value, language: $("ui-language").value, default_profile: $("default-profile").value || null, default_input_dir: $("default-input").value, default_output_dir: $("default-output").value }; state.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) }); applySettings(); $("settings-status").textContent = t("saved"); }
  function bind() {
    document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => { document.querySelectorAll(".nav-item,.view").forEach((node) => node.classList.remove("active")); button.classList.add("active"); $(`view-${button.dataset.view}`).classList.add("active"); }));
    $("refresh-environment").addEventListener("click", () => loadEnvironment($("profile-select").value)); $("profile-select").addEventListener("change", (e) => loadEnvironment(e.target.value)); $("asr-backend").addEventListener("change", renderAsrDetail); $("diarization-backend").addEventListener("change", renderDiarizationDetail); $("speaker-mode").addEventListener("change", renderSpeakerMode); $("diarization-enabled").addEventListener("change", () => $("diarization-fields").classList.toggle("hidden", !$("diarization-enabled").checked)); $("job-form").addEventListener("submit", startJob); $("cancel-button").addEventListener("click", async () => { if (state.job) await api(`/api/jobs/${state.job.id}/cancel`, { method: "POST" }); });
    $("settings-form").addEventListener("submit", saveSettings); $("theme-select").addEventListener("change", (e) => document.documentElement.dataset.theme = e.target.value); $("ui-language").addEventListener("change", (e) => { state.settings.language = e.target.value; translate(); });
    $("save-token").addEventListener("click", async () => { const token = $("token-input").value; if (!token) return; await api("/api/token", { method: "PUT", body: JSON.stringify({ token }) }); $("token-input").value = ""; state.settings.token_configured = true; applySettings(); }); $("clear-token").addEventListener("click", async () => { await api("/api/token", { method: "DELETE" }); state.settings.token_configured = false; applySettings(); });
    $("open-output").addEventListener("click", async () => { const first = state.job?.outputs?.[0]; if (!first) return; const folder = first.replace(/[\\/][^\\/]+$/, ""); await api("/api/open-folder", { method: "POST", body: JSON.stringify({ path: folder }) }); });
    document.addEventListener("dragover", (e) => e.preventDefault()); document.addEventListener("drop", (e) => { e.preventDefault(); const file = e.dataTransfer.files[0]; if (file) $("input-path").value = file.path || file.name; });
  }
  async function boot() { bind(); renderStageList(); state.settings = await api("/api/settings"); applySettings(); await loadEnvironment(state.settings.default_profile); $("server-dot").title = "127.0.0.1"; }
  boot().catch((error) => { $("environment-alert").textContent = error.message; $("environment-alert").classList.remove("hidden"); });
})();
