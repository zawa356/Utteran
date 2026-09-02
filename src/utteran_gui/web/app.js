(() => {
  "use strict";

  const pendingFrontendErrors = [];

  function flushFrontendErrors() {
    const reporter = window.pywebview?.api?.report_frontend_error;
    if (!reporter) return;
    pendingFrontendErrors.splice(0).forEach((payload) => {
      Promise.resolve(reporter(payload)).catch(() => {});
    });
  }

  function queueFrontendError(payload) {
    if (pendingFrontendErrors.length >= 20) pendingFrontendErrors.shift();
    pendingFrontendErrors.push(payload);
    flushFrontendErrors();
  }

  window.addEventListener("error", (event) => {
    queueFrontendError({
      kind: "error",
      message: event.message || String(event.error || "Unknown frontend error"),
      source: event.filename || "",
      line: event.lineno || 0,
      column: event.colno || 0,
    });
  });
  window.addEventListener("unhandledrejection", (event) => {
    queueFrontendError({
      kind: "unhandledrejection",
      message: event.reason?.message || String(event.reason || "Unknown rejected promise"),
    });
  });
  window.addEventListener("pywebviewready", flushFrontendErrors);

  const $ = (id) => document.getElementById(id);
  const stages = ["audio", "asr", "diarization", "merge", "export"];
  const outputFormats = ["srt", "vtt", "json", "txt", "md"];
  const ROW_HEIGHT = 108;
  const OVERSCAN = 8;
  const state = {
    settings: null,
    environment: null,
    modelJob: null,
    job: null,
    source: null,
    started: 0,
    poller: null,
    history: [],
    detail: null,
    viewer: {
      filtered: [],
      matches: [],
      currentMatch: -1,
      composing: false,
      searchTimer: null,
      renderFrame: null,
    },
  };

  const t = (key) =>
    (window.UTTERAN_I18N[state.settings?.language || "ja"] || {})[key] || key;

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(apiErrorMessage(body.detail, `${response.status} ${response.statusText}`));
    }
    return response.status === 204 ? null : response.json();
  }

  function apiErrorMessage(detail, fallback) {
    if (typeof detail === "string" && detail) return detail;
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => (typeof item?.msg === "string" ? item.msg : ""))
        .filter(Boolean);
      if (messages.length) return messages.join(" / ");
    }
    if (detail && typeof detail.message === "string") return detail.message;
    return fallback;
  }

  function translate() {
    document.documentElement.lang = state.settings.language;
    document.querySelectorAll("[data-i18n]").forEach((node) => {
      node.textContent = t(node.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
      node.placeholder = t(node.dataset.i18nPlaceholder);
    });
  }

  function applySettings() {
    document.documentElement.dataset.theme = state.settings.theme;
    $("theme-select").value = state.settings.theme;
    $("ui-language").value = state.settings.language;
    $("default-input").value = state.settings.default_input_dir || "";
    $("default-output").value = state.settings.default_output_dir || "";
    if (!$("input-path").value) $("input-path").value = state.settings.default_input_dir || "";
    if (!$("output-dir").value) $("output-dir").value = state.settings.default_output_dir || "";
    $("log-path").textContent = state.settings.log_dir || "—";
    $("raw-log-warning").classList.toggle("hidden", !state.settings.raw_subprocess_logs);
    renderTokenState();
    translate();
    renderHistory();
    if (state.detail?.result) {
      renderViewerMetadata(state.detail.result);
      renderStatistics(state.detail.result);
    }
  }

  function tokenStateText() {
    if (state.settings.token_store_available === false) return t("tokenStoreUnavailable");
    return state.settings.token_configured ? t("tokenSet") : t("tokenUnset");
  }

  function renderTokenState() {
    const message = tokenStateText();
    $("token-state").textContent = message;
    $("wizard-token-state").textContent = message;
  }

  async function saveToken(inputId) {
    const input = $(inputId);
    const token = input.value;
    if (!token) return false;
    try {
      const status = await api("/api/token", { method: "PUT", body: JSON.stringify({ token }) });
      input.value = "";
      state.settings.token_configured = status.configured;
      state.settings.token_store_available = status.available;
      renderTokenState();
      return true;
    } catch (error) {
      state.settings.token_configured = false;
      state.settings.token_store_available = false;
      renderTokenState();
      throw new Error(`${t("tokenSaveFailed")} ${error.message}`);
    }
  }

  function option(select, value, label) {
    const node = document.createElement("option");
    node.value = value;
    node.textContent = label;
    select.appendChild(node);
  }

  function setOptions(select, values, selected) {
    select.replaceChildren();
    values.forEach((item) => option(select, item.id ?? item, item.label ?? item));
    if (selected && [...select.options].some((item) => item.value === selected)) {
      select.value = selected;
    }
    select.disabled = values.length === 0;
  }

  function showView(name) {
    document.querySelectorAll(".view").forEach((node) => node.classList.remove("active"));
    $(`view-${name}`).classList.add("active");
    document.querySelectorAll(".nav-item").forEach((node) => {
      node.classList.toggle("active", node.dataset.view === (name === "viewer" ? "history" : name));
    });
  }

  function closeViewer() {
    clearTimeout(state.viewer.searchTimer);
    state.detail = null;
    state.viewer.filtered = [];
    state.viewer.matches = [];
    state.viewer.currentMatch = -1;
    $("search-text").value = "";
    $("transcript-rows").replaceChildren();
    $("transcript-spacer").style.height = "0px";
  }

  async function loadEnvironment(profile, refresh = false) {
    $("form-status").textContent = t("detecting");
    const query = new URLSearchParams();
    if (profile) query.set("profile", profile);
    if (refresh) query.set("refresh", "true");
    state.environment = await api(
      `/api/environment${query.size ? `?${query}` : ""}`,
    );
    const existing = state.environment.profiles.filter((item) => item.exists);
    setOptions(
      $("profile-select"),
      existing.map((item) => ({ id: item.name, label: item.name })),
      state.environment.active_profile,
    );
    setOptions(
      $("history-profile"),
      existing.map((item) => ({ id: item.name, label: item.name })),
      state.environment.active_profile,
    );
    setOptions(
      $("default-profile"),
      [{ id: "", label: "—" }, ...existing.map((item) => ({ id: item.name, label: item.name }))],
      state.settings.default_profile || "",
    );
    const devices = state.environment.devices || {};
    const gpu = [
      ...(devices.ctranslate2?.cuda_devices || []),
      ...(devices.pytorch?.xpu_devices || []),
    ]
      .filter((item) => item.usable)
      .map((item) => item.name);
    $("hardware-summary").textContent =
      gpu.join(", ") || `CPU · ${devices.cpu?.logical_cores || "—"} threads`;
    $("profile-summary").textContent = state.environment.active_profile || "—";
    $("model-summary").textContent = String(
      state.environment.models.filter((item) => item.installed).length,
    );
    const variants = Object.entries(devices.native?.variants || {})
      .filter(([, available]) => available)
      .map(([name]) => name);
    $("native-summary").textContent = variants.join(", ") || "—";
    const alert = $("environment-alert");
    if (!existing.length) {
      alert.textContent = t("noProfiles");
      alert.classList.remove("hidden");
    } else if (!(state.environment.options.asr || []).length) {
      alert.textContent = t("noRuntime");
      alert.classList.remove("hidden");
    } else if (state.environment.errors.length) {
      alert.textContent = state.environment.errors.join(" · ");
      alert.classList.remove("hidden");
    } else if ((state.environment.options.guidance || []).length) {
      alert.textContent = state.environment.options.guidance.join(" · ");
      alert.classList.remove("hidden");
    } else {
      alert.classList.add("hidden");
    }
    renderRuntimeOptions();
    $("form-status").textContent = (state.environment.options.asr || []).length ? t("ready") : "";
  }

  function renderRuntimeOptions() {
    const defaults = state.environment?.options.defaults || {};
    setOptions(
      $("language-select"),
      (state.environment?.options.languages || []).map((item) => ({
        id: item,
        label: item === "auto" ? t("automatic") : item,
      })),
      $("language-select").value || "ja",
    );
    const blockedGenai = genaiIsBlocked();
    const allAsr = state.environment?.options.asr || [];
    const asr = allAsr.filter((item) => !(blockedGenai && item.id === "openvino-genai"));
    setOptions($("asr-backend"), asr, $("asr-backend").value || defaults.asr_backend);
    renderAsrDetail(defaults.asr_model, defaults.asr_device);
    const diarization = state.environment?.options.diarization || [];
    setOptions(
      $("diarization-backend"),
      diarization,
      $("diarization-backend").value || defaults.diarization_backend,
    );
    renderDiarizationDetail($("diarization-device").value || defaults.diarization_device);
    renderConfigurationNotice();
    const formats = state.environment?.options.formats || [];
    $("format-options").replaceChildren(
      ...formats.map((name) => formatChip(name, ["srt", "json", "md"].includes(name))),
    );
    $("start-button").disabled = !asr.length;
  }

  function selectedGroup(groups, select) {
    return groups.find((item) => item.id === select.value) || groups[0];
  }

  function renderAsrDetail(selectedModel = "", selectedDevice = "") {
    const group = selectedGroup(state.environment?.options.asr || [], $("asr-backend"));
    setOptions($("asr-model"), group?.models || [], selectedModel);
    const devices = (group?.devices || []).filter(
      (item) => $("show-discouraged-configurations").checked || item.recommended !== false,
    );
    setOptions($("asr-device"), devices, selectedDevice || $("asr-device").value);
    const defaults = state.environment?.options.defaults || {};
    if ($("asr-backend").value !== defaults.asr_backend && defaults.asr_backend) {
      $("form-status").textContent = `auto: ${defaults.asr_backend} / ${defaults.asr_device}`;
    }
    renderConfigurationNotice();
  }

  function genaiIsBlocked() {
    return $("diarization-enabled").checked &&
      ["auto", "ja", "zh", "th", "lo", "my", "yue"].includes($("language-select").value);
  }

  function renderConfigurationNotice() {
    const notice = $("configuration-notice");
    const allAsr = state.environment?.options.asr || [];
    if (genaiIsBlocked() && allAsr.some((item) => item.id === "openvino-genai")) {
      notice.textContent = t("genaiDiarizationUnavailable");
      notice.classList.remove("hidden");
      return;
    }
    const group = selectedGroup(allAsr, $("asr-backend"));
    const device = (group?.devices || []).find((item) => item.id === $("asr-device").value);
    if (device?.recommendation_reason) {
      notice.textContent = device.recommendation_reason;
      notice.classList.remove("hidden");
    } else {
      notice.classList.add("hidden");
    }
  }

  function renderDiarizationDetail(selectedDevice = "") {
    const group = selectedGroup(
      state.environment?.options.diarization || [],
      $("diarization-backend"),
    );
    setOptions($("diarization-model"), group?.models || []);
    setOptions($("diarization-device"), group?.devices || [], selectedDevice);
  }

  function renderSpeakerMode() {
    const mode = $("speaker-mode").value;
    $("speaker-fixed-field").classList.toggle("hidden", mode !== "fixed");
    $("speaker-range-fields").classList.toggle("hidden", mode !== "range");
  }

  function renderStageList() {
    $("stage-list").replaceChildren(
      ...stages.map((stage) => {
        const item = document.createElement("li");
        item.id = `stage-${stage}`;
        item.textContent = stage;
        return item;
      }),
    );
  }

  function appendLog(text) {
    if (!text) return;
    const log = $("log-output");
    log.textContent += `${text}\n`;
    log.scrollTop = log.scrollHeight;
  }

  function formatTime(seconds) {
    const value = Math.max(0, Math.round(Number(seconds) || 0));
    return [Math.floor(value / 3600), Math.floor(value / 60) % 60, value % 60]
      .map((item) => String(item).padStart(2, "0"))
      .join(":");
  }

  function formatDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString(state.settings.language);
  }

  function formatBytes(bytes) {
    let value = Number(bytes) || 0;
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    return `${value.toFixed(index ? 1 : 0)} ${units[index]}`;
  }

  function splitPatterns(value) {
    return value
      .split(/[\r\n,]+/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function updateClock() {
    if (state.started) $("elapsed").textContent = formatTime((Date.now() - state.started) / 1000);
  }

  const wizardState = {
    hardware: null,
    profile: null,
    modelRef: "faster-whisper:large-v3-turbo",
    diarizationModelRef: "pyannote:pyannote/speaker-diarization-community-1",
    wantDiarization: true,
    completedStages: new Set(),
    resumeExecution: false,
    running: false,
    job: null,
    source: null,
    poller: null,
    started: 0,
    retry: null,
  };

  function showWizardStep(name) {
    document.querySelectorAll(".wizard-step").forEach((node) => node.classList.add("hidden"));
    $(`wizard-step-${name}`).classList.remove("hidden");
  }

  async function openWizard() {
    showView("wizard");
    try {
      const status = await api("/api/wizard/status");
      wizardState.profile = status.profile;
      wizardState.modelRef = status.model_ref || wizardState.modelRef;
      wizardState.wantDiarization = status.diarization_enabled !== false;
      wizardState.completedStages = new Set(status.completed_stages || []);
      wizardState.resumeExecution = wizardState.completedStages.has("venv");
      if (status.profile) await ensureWizardHardware();
      const diarizationChoice = document.querySelector(
        `input[name="wizard-diarization"][value="${wizardState.wantDiarization ? "yes" : "no"}"]`,
      );
      if (diarizationChoice) diarizationChoice.checked = true;
      if (status.step === "execution") {
        wizardRunUnattended();
        return;
      }
      if (status.step === "token") {
        await showWizardToken(wizardState.resumeExecution ? status.token_error : null);
        return;
      }
      if (status.step === "profile") {
        await ensureWizardHardware();
        renderWizardRecommendation();
      }
      if (status.step === "confirm") {
        renderWizardConfirmation();
      }
      if (status.step === "model") renderWizardModels();
      showWizardStep(status.step || "welcome");
    } catch (error) {
      window.alert(error.message);
      showWizardStep("welcome");
    }
  }

  function leaveWizardForLater() {
    showView("workspace");
  }

  async function wizardBegin() {
    try {
      await api("/api/wizard/start", { method: "POST" });
      wizardState.hardware = await api("/api/wizard/hardware");
    } catch (error) {
      window.alert(error.message);
      return;
    }
    renderWizardRecommendation();
    showWizardStep("profile");
  }

  async function ensureWizardHardware() {
    if (!wizardState.hardware) wizardState.hardware = await api("/api/wizard/hardware");
  }

  async function saveWizardState(step, extra = {}) {
    return api("/api/wizard/state", {
      method: "PUT",
      body: JSON.stringify({
        step,
        profile: wizardState.profile,
        diarization_enabled: wizardState.wantDiarization,
        model_ref: wizardState.modelRef,
        ...extra,
      }),
    });
  }

  function renderWizardRecommendation() {
    const recommendation = wizardState.hardware.recommendation;
    wizardState.profile = recommendation.recommended;
    $("wizard-reasons").replaceChildren(
      ...recommendation.reasons.map((text) => {
        const item = document.createElement("li");
        item.textContent = text;
        return item;
      }),
    );
    $("wizard-detection-note").classList.toggle("hidden", recommendation.detection_confident);
    $("wizard-profile-options").replaceChildren(
      ...recommendation.alternatives.map((alternative) =>
        wizardProfileCard(alternative, alternative.profile === recommendation.recommended),
      ),
    );
  }

  const WIZARD_PROFILE_LABEL_KEYS = {
    cpu: "wizardProfileCpu",
    cuda: "wizardProfileCuda",
    intel: "wizardProfileIntel",
    vulkan: "wizardProfileVulkan",
  };

  function wizardProfileLabel(profile) {
    const key = WIZARD_PROFILE_LABEL_KEYS[profile];
    return key ? t(key) : profile;
  }

  function wizardProfileCard(alternative, isRecommended) {
    const label = document.createElement("label");
    label.className = "wizard-profile-card";
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "wizard-profile";
    input.value = alternative.profile;
    input.checked = isRecommended;
    input.addEventListener("change", () => {
      wizardState.profile = alternative.profile;
    });
    const title = document.createElement("strong");
    title.textContent =
      wizardProfileLabel(alternative.profile) + (isRecommended ? ` · ${t("wizardRecommended")}` : "");
    const asr = document.createElement("p");
    asr.textContent = `${t("wizardAsrAccel")}: ${alternative.asr_accelerated ? t("wizardYes") : t("wizardNo")}`;
    const diarization = document.createElement("p");
    diarization.className = "wizard-diarization-line";
    diarization.textContent = `${t("wizardDiarizationAccel")}: ${
      alternative.diarization_accelerated ? t("wizardYes") : t("wizardNo")
    }`;
    const size = document.createElement("p");
    size.className = "muted";
    size.textContent = `${t("wizardDiskUsage")}: ${formatBytes(alternative.approx_disk_bytes)}`;
    label.append(input, title, asr, diarization, size);
    if (alternative.caveat) {
      const caveat = document.createElement("p");
      caveat.className = "muted";
      caveat.textContent = alternative.caveat;
      label.appendChild(caveat);
    }
    alternative.extra_setup.forEach((note) => {
      const extra = document.createElement("p");
      extra.className = "muted";
      extra.textContent = note;
      label.appendChild(extra);
    });
    return label;
  }

  function wizardShowError(message, retry) {
    $("wizard-error-message").textContent = message;
    wizardState.retry = retry;
    showWizardStep("error");
  }

  function runWizardJob(kind, extra = {}) {
    return new Promise((resolve, reject) => {
      $("wizard-job-stage").textContent = "—";
      $("wizard-job-stalled").classList.add("hidden");
      wizardState.started = Date.now();
      const payload = { kind, profile: wizardState.profile, ...extra };
      api("/api/wizard/jobs", { method: "POST", body: JSON.stringify(payload) })
        .then((job) => {
          wizardState.job = job;
          wizardState.source = new EventSource(`/api/wizard/jobs/${job.id}/events`);
          const onLine = (event) => {
            const data = JSON.parse(event.data);
            if (data.stage) $("wizard-job-stage").textContent = data.stage;
            if (data.event === "progress" && Number.isFinite(data.ratio)) {
              $("wizard-progress-bar").style.width = `${Math.min(100, data.ratio * 100)}%`;
              const detail = progressDetail(data);
              if (detail) $("wizard-job-stage").textContent = detail;
            }
            if (data.message) {
              $("wizard-job-log").textContent += `${data.message}\n`;
              $("wizard-job-log").scrollTop = $("wizard-job-log").scrollHeight;
            }
          };
          ["stage_start", "progress", "log", "error"].forEach((name) =>
            wizardState.source.addEventListener(name, onLine),
          );
          wizardState.poller = setInterval(async () => {
            $("wizard-elapsed").textContent = formatTime((Date.now() - wizardState.started) / 1000);
            const current = await api(`/api/wizard/jobs/${job.id}`);
            $("wizard-job-stalled").classList.toggle("hidden", !current.stalled);
            if (["completed", "failed", "cancelled"].includes(current.status)) {
              clearInterval(wizardState.poller);
              wizardState.source.close();
              if (current.status === "completed") {
                resolve(current);
              } else {
                const message = current.guidance
                  ? t(`guide_${current.guidance.key}`)
                  : t("wizardStepFailed");
                const diagnostic = (current.logs || []).slice(-12).join("\n");
                const error = new Error(diagnostic ? `${message}\n\n${diagnostic}` : message);
                error.guidanceKey = current.guidance?.key || "";
                error.wizardKind = kind;
                reject(error);
              }
            }
          }, 1000);
        })
        .catch(reject);
    });
  }

  async function wizardProfileNext() {
    await saveWizardState("diarization");
    const selected = document.querySelector('input[name="wizard-diarization"][value="yes"]');
    selected.checked = true;
    showWizardStep("diarization");
  }

  async function wizardDiarizationNext() {
    wizardState.wantDiarization =
      document.querySelector('input[name="wizard-diarization"]:checked')?.value !== "no";
    if (wizardState.wantDiarization) {
      await saveWizardState("token");
      await showWizardToken();
    } else {
      await saveWizardState("model");
      renderWizardModels();
      showWizardStep("model");
    }
  }

  async function showWizardToken(errorCode = null) {
    const tokenStatus = await api("/api/token");
    state.settings.token_configured = tokenStatus.configured;
    state.settings.token_store_available = tokenStatus.available;
    renderTokenState();
    $("wizard-token-preflight").classList.add("hidden");
    if (errorCode) showTokenPreflightError(tokenErrorText(errorCode));
    showWizardStep("token");
  }

  function showTokenPreflightError(message) {
    $("wizard-token-preflight").textContent = message;
    $("wizard-token-preflight").classList.remove("hidden");
  }

  function tokenErrorText(code) {
    const keys = {
      token_missing: "wizardTokenMissing",
      token_invalid: "wizardTokenInvalid",
      agreement_required: "wizardTokenAgreementRequired",
      network_error: "wizardTokenNetworkError",
    };
    return t(keys[code] || "wizardTokenProfileFailed");
  }

  async function wizardTokenNext() {
    try {
      const tokenStatus = await api("/api/token");
      if (!tokenStatus.configured && tokenStatus.available) {
        showTokenPreflightError(t("wizardTokenMissing"));
        return;
      }
      $("wizard-token-preflight").classList.add("hidden");
      if (wizardState.resumeExecution) {
        await saveWizardState("execution");
        wizardRunUnattended();
      } else {
        await saveWizardState("model");
        renderWizardModels();
        showWizardStep("model");
      }
    } catch (error) {
      showTokenPreflightError(error.message);
    }
  }

  async function wizardModelChoiceNext() {
    wizardState.modelRef = $("wizard-model-select").value;
    await saveWizardState("confirm");
    renderWizardConfirmation();
    showWizardStep("confirm");
  }

  function renderWizardModels() {
    const nativeProfile = ["intel", "vulkan"].includes(wizardState.profile);
    const choices = nativeProfile
      ? [
          { id: "whisper-cpp:large-v3-turbo-q5_0", label: "Whisper.cpp large-v3-turbo q5 (推奨・高速)" },
          { id: "whisper-cpp:large-v3-turbo", label: "Whisper.cpp large-v3-turbo (高精度)" },
          { id: "whisper-cpp:large-v3-q5_0", label: "Whisper.cpp large-v3 q5" },
        ]
      : [
          { id: "faster-whisper:large-v3-turbo", label: "large-v3-turbo (推奨)" },
          { id: "faster-whisper:small", label: "small (軽量・バランス)" },
          { id: "faster-whisper:base", label: "base (軽量・高速)" },
          { id: "faster-whisper:tiny", label: "tiny (最小・試用向け)" },
          { id: "faster-whisper:medium", label: "medium (精度重視)" },
          { id: "faster-whisper:large-v3", label: "large-v3 (高精度・大容量)" },
          { id: "faster-whisper:kotoba-whisper-v2.0", label: "Kotoba-Whisper v2.0 (日本語)" },
        ];
    const selected = choices.some((item) => item.id === wizardState.modelRef)
      ? wizardState.modelRef
      : choices[0].id;
    setOptions($("wizard-model-select"), choices, selected);
    wizardState.modelRef = selected;
    $("wizard-model-note").textContent = nativeProfile
      ? "このGPU構成に対応するGGMLモデルを取得します。"
      : "この構成に対応するCTranslate2モデルを取得します。";
  }

  function renderWizardConfirmation() {
    $("wizard-confirm-profile").textContent = wizardProfileLabel(wizardState.profile);
    $("wizard-confirm-diarization").textContent = wizardState.wantDiarization
      ? t("wizardYes")
      : t("wizardNo");
    const alternative = wizardState.hardware?.recommendation?.alternatives?.find(
      (item) => item.profile === wizardState.profile,
    );
    const profileBytes = Number(alternative?.approx_disk_bytes || 0);
    const modelBytes = 1.6 * 1024 ** 3 + (wizardState.wantDiarization ? 0.6 * 1024 ** 3 : 0);
    $("wizard-confirm-download").textContent = `≈ ${formatBytes(profileBytes + modelBytes)}`;
  }

  async function wizardConfirmStart() {
    await saveWizardState("execution");
    wizardRunUnattended();
  }

  async function wizardRunUnattended() {
    if (wizardState.running) return;
    if (!wizardState.profile) {
      await saveWizardState("profile");
      await ensureWizardHardware();
      renderWizardRecommendation();
      showWizardStep("profile");
      return;
    }
    wizardState.running = true;
    showWizardStep("progress");
    $("wizard-job-log").textContent = "";
    try {
      const status = await api("/api/wizard/status");
      wizardState.completedStages = new Set(status.completed_stages || []);
      if (!wizardState.completedStages.has("venv")) {
        $("wizard-progress-title").textContent = t("wizardStepVenv");
        await runWizardJob("venv_build");
        wizardState.completedStages.add("venv");
      }
      if (wizardState.wantDiarization && !wizardState.completedStages.has("preflight")) {
        $("wizard-progress-title").textContent = t("wizardStepPreflight");
        let result;
        try {
          result = await api("/api/wizard/token-preflight", {
            method: "POST",
            body: JSON.stringify({
              profile: wizardState.profile,
              check_model: wizardState.diarizationModelRef,
            }),
          });
        } catch (_error) {
          wizardState.resumeExecution = true;
          await showWizardToken("network_error");
          return;
        }
        if (result.access !== "available") {
          wizardState.resumeExecution = true;
          await showWizardToken(result.access);
          return;
        }
        wizardState.completedStages.add("preflight");
      }
      if (!wizardState.completedStages.has("vad_model")) {
        $("wizard-progress-title").textContent = t("wizardStepVadModel");
        await runWizardJob("model_download", {
          model_ref: "whisper-cpp-vad:silero-v6.2.0",
        });
        wizardState.completedStages.add("vad_model");
      }
      if (wizardState.wantDiarization && !wizardState.completedStages.has("diarization_model")) {
        $("wizard-progress-title").textContent = t("wizardStepDiarizationModel");
        await runWizardJob("model_download", { model_ref: wizardState.diarizationModelRef });
        wizardState.completedStages.add("diarization_model");
      }
      if (!wizardState.completedStages.has("asr_model")) {
        $("wizard-progress-title").textContent = t("wizardStepModel");
        await runWizardJob("model_download", { model_ref: wizardState.modelRef });
        wizardState.completedStages.add("asr_model");
      }
      if (!wizardState.completedStages.has("smoke")) {
        $("wizard-progress-title").textContent = t("wizardStepSmoke");
        await runWizardJob("smoke_test", {
          model_ref: wizardState.modelRef,
          diarization_enabled: wizardState.wantDiarization,
        });
      }
      showWizardStep("done");
    } catch (error) {
      if (
        wizardState.wantDiarization &&
        ["model_download", "smoke_test"].includes(error.wizardKind) &&
        ["token", "license"].includes(error.guidanceKey)
      ) {
        wizardState.resumeExecution = true;
        await saveWizardState("token", {
          token_error: error.guidanceKey === "license" ? "agreement_required" : "token_invalid",
        });
        await showWizardToken(
          error.guidanceKey === "license" ? "agreement_required" : "token_invalid",
        );
      } else {
        wizardShowError(error.message, wizardRunUnattended);
      }
    } finally {
      wizardState.running = false;
    }
  }

  async function wizardSaveToken() {
    try {
      await saveToken("wizard-token-input");
      $("wizard-token-input").value = "";
      await saveWizardState("token", { token_error: null });
      showTokenPreflightError(t("wizardTokenSavedCheckProfile"));
    } catch (error) {
      showTokenPreflightError(error.message);
    }
  }

  async function wizardFinishSetup() {
    await api("/api/wizard/complete", { method: "POST" });
    showView("workspace");
    await loadEnvironment(wizardState.profile);
  }

  async function openWizardFromSettings() {
    const status = await api("/api/wizard/status");
    if (status.completed_at) {
      await api("/api/wizard/start", { method: "POST" });
      showView("wizard");
      showWizardStep("welcome");
      return;
    }
    await openWizard();
  }

  function bindWizard() {
    $("wizard-begin").addEventListener("click", wizardBegin);
    $("wizard-skip-welcome").addEventListener("click", leaveWizardForLater);
    $("wizard-skip-profile").addEventListener("click", leaveWizardForLater);
    $("wizard-skip-model").addEventListener("click", leaveWizardForLater);
    $("wizard-profile-next").addEventListener("click", wizardProfileNext);
    $("wizard-diarization-next").addEventListener("click", wizardDiarizationNext);
    $("wizard-model-next").addEventListener("click", wizardModelChoiceNext);
    $("wizard-save-token").addEventListener("click", wizardSaveToken);
    $("wizard-token-next").addEventListener("click", wizardTokenNext);
    $("wizard-token-skip").addEventListener("click", () => {
      wizardState.wantDiarization = false;
      wizardState.resumeExecution
        ? saveWizardState("execution").then(wizardRunUnattended)
        : saveWizardState("model").then(() => showWizardStep("model"));
    });
    $("wizard-confirm-back").addEventListener("click", () => showWizardStep("model"));
    $("wizard-confirm-start").addEventListener("click", wizardConfirmStart);
    $("wizard-cancel").addEventListener("click", async () => {
      if (wizardState.job) await api(`/api/wizard/jobs/${wizardState.job.id}/cancel`, { method: "POST" });
    });
    $("wizard-error-later").addEventListener("click", leaveWizardForLater);
    $("wizard-error-retry").addEventListener("click", () => wizardState.retry?.());
    $("wizard-finish").addEventListener("click", wizardFinishSetup);
    $("relaunch-wizard").addEventListener("click", openWizardFromSettings);
  }

  function handleEvent(event) {
    const data = JSON.parse(event.data);
    const kind = data.event;
    if (kind === "stage_start") {
      $(`stage-${data.stage}`)?.classList.add("active");
      $("progress-title").textContent = data.stage;
    }
    if (kind === "progress") {
      const index = Math.max(0, stages.indexOf(data.stage));
      const ratio = data.ratio ?? 0;
      const overall = (index + ratio) / stages.length;
      $("progress-bar").style.width = `${Math.min(100, overall * 100)}%`;
      if (overall > 0.02) {
        const elapsed = (Date.now() - state.started) / 1000;
        $("eta").textContent = formatTime((elapsed * (1 - overall)) / overall);
      }
      if (data.message) appendLog(data.message);
    }
    if (kind === "stage_done") {
      const node = $(`stage-${data.stage}`);
      node?.classList.remove("active");
      node?.classList.add("done");
      if (node && data.skipped) node.textContent = `${data.stage} · skip`;
      const index = stages.indexOf(data.stage);
      if (index >= 0) $("progress-bar").style.width = `${((index + 1) / stages.length) * 100}%`;
    }
    if (["warning", "error", "file_start", "file_done", "job_resolved"].includes(kind)) {
      appendLog(data.message || data.reason || `${kind}: ${data.input_path || data.job_id || ""}`);
    }
    if (kind === "output_written") appendLog(`${data.format}: ${data.path}`);
    // The CLI writes the done JSONL event before the OS process has exited.
    // finishJob therefore verifies the retained server status and leaves the
    // event stream/poller alive while it is still running.
    if (kind === "done") finishJob();
  }

  const JOB_STATUS_DEFINITIONS = Object.freeze({
    starting: Object.freeze({ terminal: false, outcome: "running" }),
    running: Object.freeze({ terminal: false, outcome: "running" }),
    completed: Object.freeze({ terminal: true, outcome: "success" }),
    failed: Object.freeze({ terminal: true, outcome: "failure" }),
    cancelled: Object.freeze({ terminal: true, outcome: "cancelled" }),
  });

  function jobStatusDefinition(status) {
    const definition = JOB_STATUS_DEFINITIONS[status];
    if (definition) return definition;
    queueFrontendError({
      kind: "unknown_job_status",
      message: `Unknown job status: ${String(status)}`,
    });
    return Object.freeze({ terminal: false, outcome: "unknown" });
  }

  async function finishJob() {
    if (!state.job) return;
    const result = await api(`/api/jobs/${state.job.id}`);
    const definition = jobStatusDefinition(result.status);
    if (!definition.terminal) return;
    state.job = result;
    state.source?.close();
    clearInterval(state.poller);
    $("cancel-button").disabled = true;
    if (definition.outcome === "success") $("progress-bar").style.width = "100%";
    $("result-panel").classList.remove("hidden");
    $("result-eyebrow").textContent =
      definition.outcome === "success"
        ? "COMPLETE"
        : definition.outcome === "cancelled"
          ? "CANCELLED"
          : definition.outcome === "failure"
            ? "FAILED"
            : "UNKNOWN";
    $("result-title").textContent =
      definition.outcome === "success"
        ? t("complete")
        : definition.outcome === "cancelled"
          ? t("cancelled")
        : result.exit_code === 5
          ? t("partial")
          : definition.outcome === "failure"
            ? t("failed")
            : t("unknown");
    const guidance = $("result-guidance");
    if (result.guidance) {
      guidance.textContent = t(`guide_${result.guidance.key}`);
      guidance.classList.remove("hidden");
    } else {
      guidance.classList.add("hidden");
    }
    const summary = [...result.events].reverse().find((item) => item.event === "run_summary");
    const summaryNode = $("result-summary");
    if (summary) {
      const executed = summary.executed_stages?.join(", ") || t("none");
      const reused = summary.reused_stages?.join(", ") || t("none");
      summaryNode.textContent =
        `ASR: ${summary.asr_backend} / ${summary.asr_model} / ${summary.asr_device} · ` +
        `${t("executedStages")}: ${executed} · ${t("reusedStages")}: ${reused}`;
      summaryNode.classList.remove("hidden");
    } else {
      summaryNode.classList.add("hidden");
    }
    $("output-list").replaceChildren(
      ...result.outputs.map((path) => {
        const item = document.createElement("li");
        item.textContent = path;
        return item;
      }),
    );
    const resolved = [...result.events].reverse().find((item) => item.event === "job_resolved");
    $("open-result").dataset.jobId = resolved?.job_id || "";
    $("open-result").classList.toggle(
      "hidden",
      definition.outcome !== "success" || !resolved?.job_id,
    );
    $("open-output").classList.toggle("hidden", !result.outputs.length);
    result.logs.forEach(appendLog);
    $("start-button").disabled = false;
  }

  async function startJob(event) {
    event.preventDefault();
    const mode = $("speaker-mode").value;
    const formats = [...$("format-options").querySelectorAll("input:checked")].map(
      (item) => item.value,
    );
    const payload = {
      input_path: $("input-path").value,
      output_dir: $("output-dir").value,
      profile: $("profile-select").value,
      asr_backend: $("asr-backend").value,
      asr_model: $("asr-model").value,
      asr_device: $("asr-device").value,
      diarization_enabled: $("diarization-enabled").checked,
      diarization_backend: $("diarization-backend").value,
      diarization_model: $("diarization-model").value,
      diarization_device: $("diarization-device").value,
      num_speakers: mode === "fixed" ? Number($("speaker-count").value) : null,
      min_speakers: mode === "range" ? Number($("speaker-min").value) : null,
      max_speakers: mode === "range" ? Number($("speaker-max").value) : null,
      language: $("language-select").value,
      formats,
      resume_mode: $("resume-mode").value,
      recursive: $("recursive-input").checked,
      include: splitPatterns($("include-patterns").value),
      exclude: splitPatterns($("exclude-patterns").value),
    };
    try {
      $("start-button").disabled = true;
      state.source?.close();
      clearInterval(state.poller);
      $("result-panel").classList.add("hidden");
      $("progress-panel").classList.remove("hidden");
      $("log-output").textContent = "";
      renderStageList();
      state.started = Date.now();
      state.job = await api("/api/jobs", { method: "POST", body: JSON.stringify(payload) });
      $("start-button").disabled = false;
      state.source = new EventSource(`/api/jobs/${state.job.id}/events`);
      [
        "job_resolved",
        "file_start",
        "file_done",
        "stage_start",
        "progress",
        "stage_done",
        "output_written",
        "run_summary",
        "warning",
        "error",
        "done",
      ].forEach((name) => state.source.addEventListener(name, handleEvent));
      state.poller = setInterval(async () => {
        updateClock();
        const current = await api(`/api/jobs/${state.job.id}`);
        $("stall-warning").classList.toggle("hidden", !current.stalled);
        if (["completed", "failed", "cancelled"].includes(current.status)) finishJob();
      }, 1000);
    } catch (error) {
      $("start-button").disabled = false;
      appendLog(error.message);
      $("progress-panel").classList.add("hidden");
      window.alert(error.message);
    }
  }

  async function loadHistory() {
    const profile = $("history-profile").value || $("profile-select").value;
    if (!profile) return;
    const alert = $("history-alert");
    alert.classList.add("hidden");
    try {
      const payload = await api(`/api/history?profile=${encodeURIComponent(profile)}`);
      state.history = Array.isArray(payload.jobs) ? payload.jobs : [];
      renderHistory();
    } catch (error) {
      alert.textContent = error.message;
      alert.classList.remove("hidden");
    }
  }

  function renderHistory() {
    if (!$("history-body")) return;
    const filter = $("history-status").value;
    const sort = $("history-sort").value;
    const jobs = state.history.filter((item) => filter === "all" || item.status === filter);
    jobs.sort((left, right) => {
      if (sort === "updated-asc") return String(left.updated_at).localeCompare(String(right.updated_at));
      if (sort === "name-asc") return String(left.input_name).localeCompare(String(right.input_name));
      if (sort === "size-desc") return Number(right.size_bytes) - Number(left.size_bytes);
      return String(right.updated_at).localeCompare(String(left.updated_at));
    });
    $("history-body").replaceChildren(...jobs.map(historyRow));
    $("history-empty").classList.toggle("hidden", jobs.length !== 0);
  }

  function historyRow(job) {
    const row = document.createElement("tr");
    if (job.result_error) row.title = job.result_error;
    const asr = job.processing?.asr;
    const values = [
      job.input_name,
      formatDate(job.updated_at),
      statusLabel(job.status),
      asr ? `${asr.model || "—"} · ${asr.device || "—"}` : "—",
      job.speaker_count ?? "—",
      formatBytes(job.size_bytes),
    ];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      if (index === 0) cell.className = "history-name";
      if (index === 2) cell.className = `status-${job.status}`;
      row.appendChild(cell);
    });
    const actions = document.createElement("td");
    actions.className = "table-actions";
    const open = actionButton(t("open"), () => openViewer(job.job_id));
    actions.appendChild(open);
    if (job.output_paths?.length) {
      actions.appendChild(actionButton(t("openFolder"), () => openFolder(folderOf(job.output_paths[0]))));
    }
    actions.appendChild(actionButton(t("delete"), () => deleteHistoryJob(job), "danger"));
    row.appendChild(actions);
    return row;
  }

  function actionButton(label, handler, style = "ghost") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `button ${style} small`;
    button.textContent = label;
    button.addEventListener("click", handler);
    return button;
  }

  function statusLabel(status) {
    return {
      done: t("completedStatus"),
      failed: t("failedStatus"),
      corrupt: t("corruptStatus"),
    }[status] || status;
  }

  async function openViewer(jobId) {
    const profile = $("history-profile").value || $("profile-select").value;
    if (!profile || !jobId) return;
    showView("viewer");
    $("viewer-error").classList.add("hidden");
    $("viewer-title").textContent = "…";
    try {
      state.detail = await api(
        `/api/history/${encodeURIComponent(jobId)}?profile=${encodeURIComponent(profile)}`,
      );
      renderViewer();
    } catch (error) {
      $("viewer-error").textContent = error.message;
      $("viewer-error").classList.remove("hidden");
    }
  }

  function renderViewer() {
    const detail = state.detail;
    const result = detail?.result;
    $("viewer-title").textContent = detail?.job?.input_name || "—";
    $("viewer-subtitle").textContent = `${detail?.job?.job_id || ""} · ${formatDate(detail?.job?.updated_at)}`;
    if (!result || result.schema_version !== 1) {
      $("viewer-error").textContent = detail?.result_error || t("noResult");
      $("viewer-error").classList.remove("hidden");
      $("viewer-meta").replaceChildren();
      $("viewer-stats").replaceChildren();
      $("transcript-rows").replaceChildren();
      $("regenerate-form").classList.add("hidden");
      return;
    }
    $("regenerate-form").classList.remove("hidden");
    renderViewerMetadata(result);
    renderStatistics(result);
    renderSpeakerFilters(result.speakers || []);
    renderRegeneration(detail, result);
    $("search-text").value = "";
    $("time-from").value = "";
    $("time-to").value = "";
    $("transcript-viewport").scrollTop = 0;
    applyFiltersAndSearch();
  }

  function renderViewerMetadata(result) {
    const asr = result.processing?.asr || {};
    const diarization = result.processing?.diarization;
    const items = [
      [t("asrProcessing"), `${asr.backend || "—"} · ${asr.model || "—"}`, asr.device || "—"],
      [
        t("diarProcessing"),
        diarization ? `${diarization.backend || "—"} · ${diarization.model || "—"}` : t("noDiarization"),
        diarization?.device || "—",
      ],
      [t("generatedAt"), formatDate(result.processing?.created_at), ""],
      [
        t("duration"),
        formatTime(result.input?.duration),
        `${result.segments.length} ${t("segmentCount")} · ${(result.speakers || []).length} ${t("speakers")}`,
      ],
    ];
    $("viewer-meta").replaceChildren(
      ...items.map(([label, value, note]) => {
        const card = document.createElement("article");
        const caption = document.createElement("span");
        const strong = document.createElement("strong");
        const small = document.createElement("small");
        caption.textContent = label;
        strong.textContent = value;
        small.textContent = note;
        card.append(caption, strong, small);
        return card;
      }),
    );
  }

  function renderStatistics(result) {
    const totals = new Map();
    let totalDuration = 0;
    result.segments.forEach((segment) => {
      const speaker = segment.speaker || "UNKNOWN";
      const duration = Math.max(0, Number(segment.end) - Number(segment.start));
      totals.set(speaker, (totals.get(speaker) || 0) + duration);
      totalDuration += duration;
    });
    const names = new Map((result.speakers || []).map((item) => [item.id, item.name]));
    const speakers = result.speakers || [];
    const cards = [...totals.entries()].map(([speaker, duration]) => {
      const card = document.createElement("article");
      card.className = "stat-card";
      card.style.setProperty(
        "--speaker-color",
        `var(--speaker-${speakerColorIndex(speaker === "UNKNOWN" ? null : speaker, speakers) % 8})`,
      );
      const name = document.createElement("strong");
      const value = document.createElement("span");
      const track = document.createElement("i");
      const fill = document.createElement("b");
      const ratio = totalDuration ? duration / totalDuration : 0;
      name.textContent = names.get(speaker) || speaker;
      value.textContent = `${formatTime(duration)} · ${(ratio * 100).toFixed(1)}%`;
      fill.style.width = `${ratio * 100}%`;
      track.appendChild(fill);
      card.append(name, value, track);
      return card;
    });
    const average = document.createElement("article");
    average.className = "stat-card summary";
    const averageLabel = document.createElement("strong");
    const averageValue = document.createElement("span");
    averageLabel.textContent = t("averageTurn");
    averageValue.textContent = formatTime(totalDuration / Math.max(1, result.segments.length));
    average.append(averageLabel, averageValue);
    $("viewer-stats").replaceChildren(...cards, average);
  }

  function renderSpeakerFilters(speakers) {
    $("speaker-filters").replaceChildren(
      ...speakers.map((speaker, index) => {
        const label = document.createElement("label");
        label.style.setProperty("--speaker-color", `var(--speaker-${index % 8})`);
        const input = document.createElement("input");
        const span = document.createElement("span");
        input.type = "checkbox";
        input.value = speaker.id;
        input.checked = true;
        input.addEventListener("change", applyFiltersAndSearch);
        span.textContent = speaker.name;
        label.append(input, span);
        return label;
      }),
    );
  }

  function applyFiltersAndSearch() {
    const result = state.detail?.result;
    if (!result) return;
    const selected = new Set(
      [...$("speaker-filters").querySelectorAll("input:checked")].map((item) => item.value),
    );
    const from = $("time-from").value === "" ? 0 : Number($("time-from").value) * 60;
    const to = $("time-to").value === "" ? Number.POSITIVE_INFINITY : Number($("time-to").value) * 60;
    state.viewer.filtered = result.segments
      .map((segment, originalIndex) => ({ segment, originalIndex }))
      .filter(({ segment }) => {
        const speakerVisible = !segment.speaker || selected.has(segment.speaker);
        return speakerVisible && Number(segment.end) >= from && Number(segment.start) <= to;
      });
    rebuildMatches();
    $("visible-count").textContent = String(state.viewer.filtered.length);
    $("transcript-spacer").style.height = `${state.viewer.filtered.length * ROW_HEIGHT}px`;
    renderVirtualRows();
  }

  function rebuildMatches() {
    const query = $("search-text").value;
    const foldedQuery = query.toLocaleLowerCase();
    const matches = [];
    if (foldedQuery) {
      state.viewer.filtered.forEach(({ segment, originalIndex }, filteredIndex) => {
        const folded = String(segment.text).toLocaleLowerCase();
        let start = 0;
        while (start <= folded.length - foldedQuery.length) {
          const index = folded.indexOf(foldedQuery, start);
          if (index < 0) break;
          matches.push({ originalIndex, filteredIndex, start: index });
          start = index + Math.max(1, foldedQuery.length);
        }
      });
    }
    state.viewer.matches = matches;
    state.viewer.currentMatch = matches.length ? 0 : -1;
    $("match-count").textContent = `${matches.length} ${t("matches")}`;
    $("match-prev").disabled = !matches.length;
    $("match-next").disabled = !matches.length;
  }

  function scheduleSearch() {
    if (state.viewer.composing) return;
    clearTimeout(state.viewer.searchTimer);
    state.viewer.searchTimer = setTimeout(() => {
      rebuildMatches();
      renderVirtualRows();
    }, 180);
  }

  function moveMatch(delta) {
    if (!state.viewer.matches.length) return;
    state.viewer.currentMatch =
      (state.viewer.currentMatch + delta + state.viewer.matches.length) % state.viewer.matches.length;
    const match = state.viewer.matches[state.viewer.currentMatch];
    $("transcript-viewport").scrollTop = Math.max(0, match.filteredIndex * ROW_HEIGHT - ROW_HEIGHT);
    renderVirtualRows();
  }

  function renderVirtualRows() {
    cancelAnimationFrame(state.viewer.renderFrame);
    state.viewer.renderFrame = requestAnimationFrame(() => {
      const viewport = $("transcript-viewport");
      const count = state.viewer.filtered.length;
      const visible = Math.ceil(viewport.clientHeight / ROW_HEIGHT);
      const start = Math.max(0, Math.floor(viewport.scrollTop / ROW_HEIGHT) - OVERSCAN);
      const end = Math.min(count, start + visible + OVERSCAN * 2);
      const nodes = [];
      for (let index = start; index < end; index += 1) nodes.push(transcriptRow(index));
      $("transcript-rows").replaceChildren(...nodes);
    });
  }

  function transcriptRow(filteredIndex) {
    const { segment, originalIndex } = state.viewer.filtered[filteredIndex];
    const speakers = state.detail.result.speakers || [];
    const speakerIndex = speakerColorIndex(segment.speaker, speakers);
    const row = document.createElement("article");
    row.className = "transcript-row";
    row.style.top = `${filteredIndex * ROW_HEIGHT}px`;
    row.style.setProperty("--speaker-color", `var(--speaker-${speakerIndex % 8})`);
    const time = document.createElement("time");
    const speaker = document.createElement("strong");
    const text = document.createElement("p");
    time.textContent = `${formatTime(segment.start)} – ${formatTime(segment.end)}`;
    speaker.textContent = segment.speaker_display || segment.speaker || "—";
    appendHighlightedText(text, String(segment.text), originalIndex);
    row.append(time, speaker, text);
    return row;
  }

  function speakerColorIndex(speaker, speakers) {
    const index = speakers.findIndex((item) => item.id === speaker);
    return index >= 0 ? index : speakers.length;
  }

  function appendHighlightedText(container, text, originalIndex) {
    const query = $("search-text").value;
    const foldedQuery = query.toLocaleLowerCase();
    if (!foldedQuery) {
      container.textContent = text;
      return;
    }
    const folded = text.toLocaleLowerCase();
    let cursor = 0;
    while (cursor < text.length) {
      const index = folded.indexOf(foldedQuery, cursor);
      if (index < 0) {
        container.appendChild(document.createTextNode(text.slice(cursor)));
        break;
      }
      if (index > cursor) container.appendChild(document.createTextNode(text.slice(cursor, index)));
      const mark = document.createElement("mark");
      mark.textContent = text.slice(index, index + query.length);
      const active = state.viewer.matches[state.viewer.currentMatch];
      if (active?.originalIndex === originalIndex && active.start === index) mark.className = "current";
      container.appendChild(mark);
      cursor = index + Math.max(1, query.length);
    }
  }

  function renderRegeneration(detail, result) {
    $("regenerate-output").value = detail.job.output_dir || state.settings.default_output_dir || "";
    const selected = detail.job.formats?.length ? detail.job.formats : ["srt", "json", "md"];
    $("regenerate-formats").replaceChildren(
      ...outputFormats.map((name) => formatChip(name, selected.includes(name))),
    );
    $("speaker-label-fields").replaceChildren(
      ...(result.speakers || []).map((speaker, index) => {
        const label = document.createElement("label");
        label.style.setProperty("--speaker-color", `var(--speaker-${index % 8})`);
        const span = document.createElement("span");
        const input = document.createElement("input");
        span.textContent = speaker.id;
        input.value = speaker.name === speaker.id ? "" : speaker.name;
        input.maxLength = 100;
        input.autocomplete = "off";
        input.dataset.speaker = speaker.id;
        input.placeholder = speaker.id;
        label.append(span, input);
        return label;
      }),
    );
    $("regenerated-outputs").replaceChildren();
    $("regenerate-status").textContent = "";
  }

  function formatChip(name, checked) {
    const label = document.createElement("label");
    label.className = "format-chip";
    const input = document.createElement("input");
    const span = document.createElement("span");
    input.type = "checkbox";
    input.value = name;
    input.checked = checked;
    span.textContent = name.toUpperCase();
    label.append(input, span);
    return label;
  }

  async function regenerate(event) {
    event.preventDefault();
    if (!state.detail) return;
    const formats = [...$("regenerate-formats").querySelectorAll("input:checked")].map(
      (item) => item.value,
    );
    if (!formats.length) return;
    const labels = {};
    $("speaker-label-fields").querySelectorAll("input").forEach((input) => {
      labels[input.dataset.speaker] = input.value.trim();
    });
    const payload = {
      profile: $("history-profile").value || $("profile-select").value,
      output_dir: $("regenerate-output").value,
      formats,
      speaker_labels: labels,
    };
    $("regenerate-button").disabled = true;
    $("regenerate-status").textContent = t("exporting");
    try {
      const response = await api(
        `/api/history/${encodeURIComponent(state.detail.job.job_id)}/regenerate`,
        { method: "POST", body: JSON.stringify(payload) },
      );
      $("regenerate-status").textContent = t("exported");
      $("regenerated-outputs").replaceChildren(
        ...response.outputs.map((path) => {
          const item = document.createElement("li");
          item.textContent = path;
          return item;
        }),
      );
      state.detail.job.output_paths = response.outputs;
      state.detail.job.output_dir = response.output_dir;
      state.detail.job.formats = formats;
      await loadHistory();
    } catch (error) {
      $("regenerate-status").textContent = error.message;
    } finally {
      $("regenerate-button").disabled = false;
    }
  }

  async function deleteHistoryJob(job) {
    const message = t("deleteConfirm")
      .replace("{name}", job.input_name)
      .replace("{size}", formatBytes(job.size_bytes));
    if (!window.confirm(message)) return;
    try {
      const profile = $("history-profile").value || $("profile-select").value;
      await api(
        `/api/history/${encodeURIComponent(job.job_id)}?profile=${encodeURIComponent(profile)}`,
        { method: "DELETE" },
      );
      if (state.detail?.job?.job_id === job.job_id) {
        closeViewer();
        showView("history");
      }
      await loadHistory();
    } catch (error) {
      const alert = state.detail ? $("viewer-error") : $("history-alert");
      alert.textContent = error.message;
      alert.classList.remove("hidden");
    }
  }

  function folderOf(path) {
    return String(path).replace(/[\\/][^\\/]+$/, "");
  }

  async function openFolder(path) {
    if (!path) return;
    await api("/api/open-folder", { method: "POST", body: JSON.stringify({ path }) });
  }

  async function choosePath(kind, targetId) {
    try {
      const bridge = window.pywebview?.api;
      if (!bridge) throw new Error(t("dialogUnavailable"));
      const path = await bridge.choose_path(kind);
      if (path) $(targetId).value = path;
    } catch (error) {
      window.alert(error.message || String(error));
    }
  }

  async function loadModels() {
    const profile = $("profile-select").value || state.environment?.active_profile;
    if (profile) await loadEnvironment(profile);
    renderModels();
  }

  function renderModels() {
    const environment = state.environment || {};
    $("models-profile").textContent = environment.active_profile || "—";
    $("models-path").textContent = environment.model_path || "—";
    const showAll = $("show-all-models").checked;
    const rows = (environment.models || []).filter(
      (item) => showAll || (item.recommended !== false && !item.english_only),
    );
    $("model-catalog").replaceChildren(...rows.map(modelCard));
    const irBySize = new Map(
      (environment.openvino_models || []).map((item) => [item.model_size, item]),
    );
    const ggmlSizes = [...new Set(rows.filter((item) => item.backend === "whisper-cpp").map((item) => item.model_size).filter(Boolean))];
    $("openvino-list").replaceChildren(
      ...ggmlSizes.map((size) => {
        const row = document.createElement("div");
        row.className = "model-card-actions";
        const status = irBySize.get(size);
        const label = document.createElement("span");
        label.textContent = `${size}: ${status?.installed ? t("installed") : t("notInstalled")}`;
        const model = rows.find((item) => item.backend === "whisper-cpp" && item.model_size === size);
        const button = actionButton(status?.installed ? t("delete") : t("generate"), () => {
          if (!model) return;
          const action = status?.installed ? "remove_openvino" : "prepare_openvino";
          const warning = status?.installed
            ? t("removeIrConfirm")
            : t("prepareIrConfirm").replace("{size}", size);
          if (window.confirm(warning)) runModelAction(action, model.key, `${size} OpenVINO IR`);
        }, status?.installed ? "danger" : "secondary");
        row.append(label, button);
        return row;
      }),
    );
  }

  function modelCard(model) {
    const card = document.createElement("article");
    card.className = "panel model-card";
    const head = document.createElement("div");
    head.className = "model-card-head";
    const title = document.createElement("strong");
    title.textContent = model.display_name || model.model_id;
    const stateLabel = document.createElement("small");
    stateLabel.textContent = model.installed ? t("installed") : t("notInstalled");
    head.append(title, stateLabel);
    const description = document.createElement("p");
    description.textContent = model.description || "";
    const reason = document.createElement("p");
    reason.className = "model-reason";
    reason.textContent = `${model.execution_label || ""} · ${model.recommendation_reason || ""}`;
    const metadata = document.createElement("small");
    const quantization = model.quantization ? ` · ${model.quantization}` : "";
    metadata.textContent = `${model.backend} · ${model.format || "—"}${quantization} · ${formatBytes(model.installed ? model.size_bytes : model.approximate_size_bytes)}`;
    const actions = document.createElement("div");
    actions.className = "model-card-actions";
    if (model.installed) {
      actions.append(
        actionButton(t("verify"), () => runModelAction("verify", model.key, model.display_name), "secondary"),
        actionButton(t("delete"), () => {
          if (window.confirm(t("deleteModelConfirm").replace("{name}", model.display_name).replace("{size}", formatBytes(model.size_bytes)))) {
            runModelAction("remove", model.key, model.display_name);
          }
        }, "danger"),
      );
    } else {
      actions.append(actionButton(t("download"), () => {
        const message = t("downloadModelConfirm").replace("{name}", model.display_name).replace("{size}", formatBytes(model.approximate_size_bytes));
        if (window.confirm(message)) runModelAction("download", model.key, model.display_name);
      }, "primary"));
    }
    card.append(head, description, reason, metadata, actions);
    return card;
  }

  async function runModelAction(action, modelRef, label) {
    const profile = state.environment?.active_profile;
    if (!profile) return;
    $("model-progress").classList.remove("hidden");
    $("model-progress-title").textContent = label;
    $("model-log").textContent = "";
    $("model-progress-bar").style.width = "0%";
    $("model-progress-bytes").textContent = "—";
    $("model-progress-eta").textContent = "—";
    try {
      const job = await api("/api/models/actions", {
        method: "POST",
        body: JSON.stringify({ profile, action, model_ref: modelRef }),
      });
      state.modelJob = job;
      let cursor = 0;
      while (true) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        const current = await api(`/api/wizard/jobs/${job.id}`);
        const progress = [...(current.events || [])].reverse().find(
          (item) => item.event === "progress",
        );
        if (progress) updateModelProgress(progress);
        const logs = current.logs || [];
        if (logs.length > cursor) {
          $("model-log").textContent += `${logs.slice(cursor).join("\n")}\n`;
          cursor = logs.length;
        }
        if (["completed", "failed", "cancelled"].includes(current.status)) {
          state.modelJob = null;
          if (current.status !== "completed") {
            const key = current.guidance?.key;
            $("models-alert").textContent = key ? t(`guide_${key}`) : t(current.status === "cancelled" ? "cancelled" : "failed");
            $("models-alert").classList.remove("hidden");
          }
          await loadModels();
          break;
        }
      }
    } catch (error) {
      $("models-alert").textContent = error.message;
      $("models-alert").classList.remove("hidden");
    } finally {
      $("model-progress").classList.add("hidden");
    }
  }

  function progressDetail(progress) {
    if (!Number.isFinite(progress.downloaded_bytes) || !Number.isFinite(progress.total_bytes)) {
      return "";
    }
    const rate = Number(progress.rate_bytes_per_second || 0);
    return `${formatBytes(progress.downloaded_bytes)} / ${formatBytes(progress.total_bytes)}` +
      (rate > 0 ? ` · ${formatBytes(rate)}/s` : "");
  }

  function updateModelProgress(progress) {
    const ratio = Number(progress.ratio || 0);
    $("model-progress-bar").style.width = `${Math.min(100, ratio * 100)}%`;
    $("model-progress-bytes").textContent = progressDetail(progress) || "—";
    $("model-progress-eta").textContent = Number.isFinite(progress.eta_seconds)
      ? formatTime(progress.eta_seconds)
      : "—";
  }

  async function loadQueue() {
    const items = await api("/api/queue");
    const visible = items.slice(-20).reverse();
    $("queue-empty").classList.toggle("hidden", visible.length !== 0);
    $("queue-list").replaceChildren(...visible.map(queueCard));
    const active = items.some((item) => ["waiting", "running"].includes(item.status));
    $("queued-settings-note").classList.toggle("hidden", !active);
    if (active) $("start-button").textContent = state.settings?.language === "en"
      ? "Add to queue"
      : "キューに追加";
    else $("start-button").textContent = t("start");
  }

  function queueCard(item) {
    const card = document.createElement("article");
    card.className = "panel queue-card";
    const text = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = item.label || item.kind;
    const detail = document.createElement("small");
    detail.textContent = `${item.kind} · ${item.status}` +
      (item.position ? ` · #${item.position}` : "");
    text.append(title, detail);
    card.append(text);
    if (["waiting", "running"].includes(item.status)) {
      card.append(actionButton(t("cancel"), async () => {
        await api(`/api/queue/${item.id}/cancel`, { method: "POST" });
        await loadQueue();
      }, "danger"));
    }
    return card;
  }

  async function saveSettings(event) {
    event.preventDefault();
    const payload = {
      theme: $("theme-select").value,
      language: $("ui-language").value,
      default_profile: $("default-profile").value || null,
      default_input_dir: $("default-input").value,
      default_output_dir: $("default-output").value,
    };
    state.settings = await api("/api/settings", { method: "PATCH", body: JSON.stringify(payload) });
    applySettings();
    $("settings-status").textContent = t("saved");
  }

  function bind() {
    document.querySelectorAll(".nav-item").forEach((button) =>
      button.addEventListener("click", async () => {
        if (state.detail) closeViewer();
        showView(button.dataset.view);
        if (button.dataset.view === "history") await loadHistory();
        if (button.dataset.view === "models") await loadModels();
        if (button.dataset.view === "queue") await loadQueue();
      }),
    );
    $("refresh-environment").addEventListener("click", () =>
      loadEnvironment($("profile-select").value, true),
    );
    $("profile-select").addEventListener("change", (event) => loadEnvironment(event.target.value));
    $("asr-backend").addEventListener("change", () => renderAsrDetail());
    $("asr-device").addEventListener("change", renderConfigurationNotice);
    $("language-select").addEventListener("change", renderRuntimeOptions);
    $("show-discouraged-configurations").addEventListener("change", () => renderAsrDetail());
    $("diarization-backend").addEventListener("change", renderDiarizationDetail);
    $("speaker-mode").addEventListener("change", renderSpeakerMode);
    $("diarization-enabled").addEventListener("change", () => {
      $("diarization-fields").classList.toggle("hidden", !$("diarization-enabled").checked);
      renderRuntimeOptions();
    });
    $("job-form").addEventListener("submit", startJob);
    $("pick-input-file").addEventListener("click", () => choosePath("input_file", "input-path"));
    $("pick-input-folder").addEventListener("click", () => choosePath("input_folder", "input-path"));
    $("pick-output-folder").addEventListener("click", () => choosePath("output_folder", "output-dir"));
    $("refresh-models").addEventListener("click", loadModels);
    $("refresh-queue").addEventListener("click", loadQueue);
    $("show-all-models").addEventListener("change", renderModels);
    $("cancel-model-action").addEventListener("click", async () => {
      if (state.modelJob) {
        await api(`/api/wizard/jobs/${state.modelJob.id}/cancel`, { method: "POST" });
      }
    });
    $("cancel-button").addEventListener("click", async () => {
      if (state.job) await api(`/api/jobs/${state.job.id}/cancel`, { method: "POST" });
    });
    $("settings-form").addEventListener("submit", saveSettings);
    $("open-logs").addEventListener("click", () => openFolder(state.settings.log_dir));
    $("theme-select").addEventListener("change", (event) => {
      document.documentElement.dataset.theme = event.target.value;
    });
    $("ui-language").addEventListener("change", (event) => {
      state.settings.language = event.target.value;
      translate();
      renderHistory();
      if (state.detail?.result) {
        renderViewerMetadata(state.detail.result);
        renderStatistics(state.detail.result);
      }
    });
    $("save-token").addEventListener("click", async () => {
      try {
        await saveToken("token-input");
        $("settings-status").textContent = t("saved");
      } catch (error) {
        $("settings-status").textContent = error.message;
      }
    });
    $("clear-token").addEventListener("click", async () => {
      await api("/api/token", { method: "DELETE" });
      state.settings.token_configured = false;
      state.settings.token_store_available = true;
      renderTokenState();
    });
    $("open-output").addEventListener("click", async () => {
      const first = state.job?.outputs?.[0];
      if (first) await openFolder(folderOf(first));
    });
    $("open-result").addEventListener("click", async () => {
      showView("history");
      await loadHistory();
      await openViewer($("open-result").dataset.jobId);
    });
    $("refresh-history").addEventListener("click", loadHistory);
    $("history-profile").addEventListener("change", loadHistory);
    $("history-status").addEventListener("change", renderHistory);
    $("history-sort").addEventListener("change", renderHistory);
    $("viewer-back").addEventListener("click", () => {
      closeViewer();
      showView("history");
    });
    $("viewer-open-folder").addEventListener("click", () => openFolder(state.detail?.job?.output_dir));
    $("viewer-delete").addEventListener("click", () => {
      const job = state.history.find((item) => item.job_id === state.detail?.job?.job_id);
      if (job) deleteHistoryJob(job);
    });
    $("search-text").addEventListener("compositionstart", () => {
      state.viewer.composing = true;
      clearTimeout(state.viewer.searchTimer);
    });
    $("search-text").addEventListener("compositionend", () => {
      state.viewer.composing = false;
      scheduleSearch();
    });
    $("search-text").addEventListener("input", scheduleSearch);
    $("match-prev").addEventListener("click", () => moveMatch(-1));
    $("match-next").addEventListener("click", () => moveMatch(1));
    $("time-from").addEventListener("input", applyFiltersAndSearch);
    $("time-to").addEventListener("input", applyFiltersAndSearch);
    $("clear-filters").addEventListener("click", () => {
      $("time-from").value = "";
      $("time-to").value = "";
      $("speaker-filters").querySelectorAll("input").forEach((input) => {
        input.checked = true;
      });
      applyFiltersAndSearch();
    });
    $("transcript-viewport").addEventListener("scroll", renderVirtualRows, { passive: true });
    $("regenerate-form").addEventListener("submit", regenerate);
    bindWizard();
  }

  async function boot() {
    bind();
    renderStageList();
    const version = await api("/api/version");
    $("app-version").textContent = `v${version.version}`;
    state.settings = await api("/api/settings");
    applySettings();
    $("server-dot").title = "127.0.0.1";
    // Device detection runs an uncached probe pass through the active
    // profile's CLI, which can legitimately take well over a minute (up to
    // ~95s measured on a machine without an NVIDIA GPU - see
    // environment.py's `_DEVICES_PROBE_TIMEOUT_SECONDS`). It must not block
    // the rest of startup (wizard resume check, job queue) the way it did
    // before Phase 5l - the window would sit on "detecting..." with no
    // further progress. `loadEnvironment` already reports its own error
    // state through `#environment-alert`, so let it run in the background.
    loadEnvironment(state.settings.default_profile).catch((error) => {
      $("environment-alert").textContent = error.message;
      $("environment-alert").classList.remove("hidden");
    });
    const wizardStatus = await api("/api/wizard/status");
    await loadQueue();
    setInterval(() => loadQueue().catch(() => {}), 1000);
    if (wizardStatus.first_run) await openWizard();
  }

  boot().catch((error) => {
    $("environment-alert").textContent = error.message;
    $("environment-alert").classList.remove("hidden");
  });
})();
