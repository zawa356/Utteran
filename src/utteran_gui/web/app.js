(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const stages = ["audio", "asr", "diarization", "merge", "export"];
  const outputFormats = ["srt", "vtt", "json", "txt", "md"];
  const ROW_HEIGHT = 108;
  const OVERSCAN = 8;
  const state = {
    settings: null,
    environment: null,
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
      throw new Error(body.detail || `${response.status} ${response.statusText}`);
    }
    return response.status === 204 ? null : response.json();
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
    $("token-state").textContent = state.settings.token_configured ? t("tokenSet") : t("tokenUnset");
    translate();
    renderHistory();
    if (state.detail?.result) {
      renderViewerMetadata(state.detail.result);
      renderStatistics(state.detail.result);
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

  async function loadEnvironment(profile) {
    $("form-status").textContent = t("detecting");
    state.environment = await api(
      `/api/environment${profile ? `?profile=${encodeURIComponent(profile)}` : ""}`,
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
    $("model-summary").textContent = String(state.environment.models.length || 0);
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
    } else {
      alert.classList.add("hidden");
    }
    renderRuntimeOptions();
    $("form-status").textContent = (state.environment.options.asr || []).length ? t("ready") : "";
  }

  function renderRuntimeOptions() {
    const asr = state.environment?.options.asr || [];
    setOptions($("asr-backend"), asr, $("asr-backend").value);
    renderAsrDetail();
    const diarization = state.environment?.options.diarization || [];
    setOptions($("diarization-backend"), diarization, $("diarization-backend").value);
    renderDiarizationDetail();
    setOptions(
      $("language-select"),
      (state.environment?.options.languages || []).map((item) => ({
        id: item,
        label: item === "auto" ? t("automatic") : item,
      })),
      "ja",
    );
    const formats = state.environment?.options.formats || [];
    $("format-options").replaceChildren(
      ...formats.map((name) => formatChip(name, ["srt", "json", "md"].includes(name))),
    );
    $("start-button").disabled = !asr.length;
  }

  function selectedGroup(groups, select) {
    return groups.find((item) => item.id === select.value) || groups[0];
  }

  function renderAsrDetail() {
    const group = selectedGroup(state.environment?.options.asr || [], $("asr-backend"));
    setOptions($("asr-model"), group?.models || []);
    setOptions($("asr-device"), group?.devices || []);
  }

  function renderDiarizationDetail() {
    const group = selectedGroup(
      state.environment?.options.diarization || [],
      $("diarization-backend"),
    );
    setOptions($("diarization-model"), group?.models || []);
    setOptions($("diarization-device"), group?.devices || []);
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
    if (kind === "done") finishJob();
  }

  async function finishJob() {
    if (!state.job) return;
    state.source?.close();
    clearInterval(state.poller);
    const result = await api(`/api/jobs/${state.job.id}`);
    state.job = result;
    $("cancel-button").disabled = true;
    if (result.exit_code === 0) $("progress-bar").style.width = "100%";
    $("result-panel").classList.remove("hidden");
    $("result-eyebrow").textContent =
      result.exit_code === 0 ? "COMPLETE" : result.exit_code === 130 ? "CANCELLED" : "FAILED";
    $("result-title").textContent =
      result.exit_code === 0
        ? t("complete")
        : result.exit_code === 130
          ? t("cancelled")
          : result.exit_code === 5
            ? t("partial")
            : t("failed");
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
    $("open-result").classList.toggle("hidden", result.exit_code !== 0 || !resolved?.job_id);
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
      $("result-panel").classList.add("hidden");
      $("progress-panel").classList.remove("hidden");
      $("log-output").textContent = "";
      renderStageList();
      state.started = Date.now();
      state.job = await api("/api/jobs", { method: "POST", body: JSON.stringify(payload) });
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

  async function saveSettings(event) {
    event.preventDefault();
    const payload = {
      theme: $("theme-select").value,
      language: $("ui-language").value,
      default_profile: $("default-profile").value || null,
      default_input_dir: $("default-input").value,
      default_output_dir: $("default-output").value,
    };
    state.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
    applySettings();
    $("settings-status").textContent = t("saved");
  }

  function bind() {
    document.querySelectorAll(".nav-item").forEach((button) =>
      button.addEventListener("click", async () => {
        if (state.detail) closeViewer();
        showView(button.dataset.view);
        if (button.dataset.view === "history") await loadHistory();
      }),
    );
    $("refresh-environment").addEventListener("click", () => loadEnvironment($("profile-select").value));
    $("profile-select").addEventListener("change", (event) => loadEnvironment(event.target.value));
    $("asr-backend").addEventListener("change", renderAsrDetail);
    $("diarization-backend").addEventListener("change", renderDiarizationDetail);
    $("speaker-mode").addEventListener("change", renderSpeakerMode);
    $("diarization-enabled").addEventListener("change", () =>
      $("diarization-fields").classList.toggle("hidden", !$("diarization-enabled").checked),
    );
    $("job-form").addEventListener("submit", startJob);
    $("cancel-button").addEventListener("click", async () => {
      if (state.job) await api(`/api/jobs/${state.job.id}/cancel`, { method: "POST" });
    });
    $("settings-form").addEventListener("submit", saveSettings);
    $("theme-select").addEventListener("change", (event) => {
      document.documentElement.dataset.theme = event.target.value;
    });
    $("ui-language").addEventListener("change", (event) => {
      state.settings.language = event.target.value;
      applySettings();
    });
    $("save-token").addEventListener("click", async () => {
      const token = $("token-input").value;
      if (!token) return;
      await api("/api/token", { method: "PUT", body: JSON.stringify({ token }) });
      $("token-input").value = "";
      state.settings.token_configured = true;
      applySettings();
    });
    $("clear-token").addEventListener("click", async () => {
      await api("/api/token", { method: "DELETE" });
      state.settings.token_configured = false;
      applySettings();
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
    document.addEventListener("dragover", (event) => event.preventDefault());
    document.addEventListener("drop", (event) => {
      event.preventDefault();
      const file = event.dataTransfer.files[0];
      if (file) $("input-path").value = file.path || file.name;
    });
  }

  async function boot() {
    bind();
    renderStageList();
    state.settings = await api("/api/settings");
    applySettings();
    await loadEnvironment(state.settings.default_profile);
    $("server-dot").title = "127.0.0.1";
  }

  boot().catch((error) => {
    $("environment-alert").textContent = error.message;
    $("environment-alert").classList.remove("hidden");
  });
})();
