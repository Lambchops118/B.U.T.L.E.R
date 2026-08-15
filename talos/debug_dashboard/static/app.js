"use strict";

const state = {
  snapshot: null,
  receivedAt: null,
  paused: false,
  fetching: false,
  timer: null,
  hostHistory: { cpu: [], memory: [], disk: [] },
  gpuHistory: {},
  extensionIds: new Set(),
  openDetails: new Set(),
};

const byId = (id) => document.getElementById(id);
const filterInput = byId("global-filter");

function text(value, fallback = "--") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function number(value, digits = 1) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(digits).replace(/\.0$/, "") : "--";
}

function bytes(value) {
  let amount = Number(value);
  if (!Number.isFinite(amount)) return "--";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
  return `${amount.toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`;
}

function shortTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit", fractionalSecondDigits: 3 });
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

function cell(value, className = "") {
  const td = document.createElement("td");
  td.textContent = text(value);
  if (className) td.className = className;
  return td;
}

function detailsCell(label, payload, key) {
  const td = document.createElement("td");
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  const pre = document.createElement("pre");
  summary.textContent = label;
  pre.textContent = JSON.stringify(payload, null, 2);
  details.dataset.detailKey = key;
  details.open = state.openDetails.has(key);
  details.addEventListener("toggle", () => {
    if (details.open) state.openDetails.add(key);
    else state.openDetails.delete(key);
  });
  details.append(summary, pre);
  td.appendChild(details);
  return td;
}

function renderTable(target, headers, rows, emptyText = "No records available.") {
  clear(target);
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  headers.forEach((header) => {
    const th = document.createElement("th");
    th.textContent = header;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  target.appendChild(thead);
  const tbody = document.createElement("tbody");
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = headers.length;
    td.className = "empty";
    td.textContent = emptyText;
    tr.appendChild(td);
    tbody.appendChild(tr);
  } else {
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      row.forEach((item) => tr.appendChild(item instanceof Node ? item : cell(item)));
      tbody.appendChild(tr);
    });
  }
  target.appendChild(tbody);
}

function badge(status) {
  const span = document.createElement("span");
  const normalized = String(status || "unknown").toLowerCase().replaceAll(" ", "_");
  span.className = `badge ${normalized}`;
  span.textContent = String(status || "unknown").toUpperCase();
  return span;
}

function matchesFilter(...values) {
  const query = filterInput.value.trim().toLowerCase();
  if (!query) return true;
  return values.some((value) => JSON.stringify(value ?? "").toLowerCase().includes(query));
}

function eventKey(prefix, payload, index = 0) {
  return [
    prefix,
    payload._source_file || "",
    payload.timestamp || payload.ts_callback_started || payload.created_at || payload.run_started_at || "",
    payload.request_id || payload.session_id || payload.id || "",
    payload.event || payload.role || "",
    index,
  ].join("|");
}

function eventDuration(event) {
  const preferred = ["text_stream_total_ms", "agent_stream_total_ms", "llm_request_ms", "tool_execution_ms", "prompt_assembly_ms", "awareness_snapshot_ms"];
  for (const key of preferred) if (event[key] !== null && event[key] !== undefined) return `${number(event[key])} ms`;
  return "--";
}

function renderInteractions(snapshot) {
  const io = snapshot.interaction_io;
  const benchmarks = io.voice_benchmarks;
  const conversation = io.conversation;
  const pipeline = io.pipeline;
  const summary = byId("interaction-summary");
  summary.textContent = `voice CSV: ${benchmarks.status} (${benchmarks.rows.length} rows, newest ${shortTime(benchmarks.newest_timestamp)}) | conversation DB: ${conversation.status} (${conversation.messages.length} messages) | pipeline: ${pipeline.status} (${pipeline.events.length} events)`;
  summary.className = `status-strip ${benchmarks.status === "degraded" || conversation.status === "degraded" ? "degraded" : ""}`;

  const voiceRows = benchmarks.rows
    .filter((row) => matchesFilter(row.session_id, row.transcript, row.command, row.response_preview))
    .map((row, index) => [
      cell(shortTime(row.ts_callback_started || row.ts_command_start || row.run_started_at), "nowrap"),
      cell(row.session_id, "nowrap"),
      cell(row.transcript, "wrap"),
      cell(row.command, "wrap"),
      cell(row.response_preview, "wrap"),
      cell(row.total_end_of_speech_to_first_audio_ms === null ? "--" : `${number(row.total_end_of_speech_to_first_audio_ms)} ms`, "nowrap"),
      detailsCell("all fields", row, eventKey("voice", row, index)),
    ]);
  renderTable(byId("voice-table"), ["TIME", "SESSION / REQUEST", "HEARD / RAW TRANSCRIPT", "COMMAND SENT", "OUTPUT PREVIEW", "E2E → AUDIO", "DETAIL"], voiceRows, "No voice benchmark CSV rows yet. Rows appear after completed voice attempts.");

  const conversationRows = conversation.messages
    .filter((message) => matchesFilter(message.session_id, message.role, message.content))
    .map((message, index) => [
      cell(shortTime(message.created_at), "nowrap"),
      cell(message.session_id, "nowrap"),
      cell(message.role.toUpperCase(), "nowrap"),
      cell(message.content, "wrap"),
      detailsCell("metadata", message.metadata, eventKey("conversation", message, index)),
    ]);
  renderTable(byId("conversation-table"), ["TIME", "SESSION", "ROLE", "FULL STORED TEXT", "META"], conversationRows, "No persisted user/assistant messages found.");

  const promptReady = pipeline.events.find((event) => event.event === "prompt_ready");
  const prompt = byId("prompt-inspector");
  clear(prompt);
  if (promptReady) {
    const list = document.createElement("dl");
    list.className = "metric-list";
    const entries = [
      ["request", promptReady.request_id], ["time", promptReady.timestamp],
      ["backend", `${text(promptReady.backend)} / ${text(promptReady.model)}`],
      ["messages", promptReady.message_count], ["tools exposed", promptReady.tool_count],
      ["estimated tokens", promptReady.prompt_tokens_estimated], ["prompt bytes", promptReady.prompt_bytes],
      ["assembly latency", `${number(promptReady.prompt_assembly_ms)} ms`],
    ];
    entries.forEach(([key, value]) => { const dt = document.createElement("dt"); const dd = document.createElement("dd"); dt.textContent = key; dd.textContent = text(value); list.append(dt, dd); });
    prompt.appendChild(list);
  }
  const notice = document.createElement("div");
  notice.className = "notice";
  notice.textContent = `${io.prompt_capture.reason} ${io.prompt_capture.integration_note}`;
  prompt.appendChild(notice);

  const toolEvents = pipeline.events
    .filter((event) => event.event === "tools_completed" && matchesFilter(event.request_id, event.route, event))
    .map((event, index) => [
      cell(shortTime(event.timestamp), "nowrap"), cell(event.request_id, "nowrap"), cell(event.round),
      cell(event.tool_call_count), cell(event.tool_execution_ms === undefined ? "--" : `${number(event.tool_execution_ms)} ms`),
      cell(event.route), detailsCell("raw", event, eventKey("tool", event, index)),
    ]);
  renderTable(byId("tool-table"), ["TIME", "REQUEST", "ROUND", "CALLS", "DURATION", "ROUTE", "DETAIL"], toolEvents, "No tool completion telemetry in the bounded event window. Tool names and arguments are not persisted.");

  const pipelineRows = pipeline.events
    .filter((event) => matchesFilter(event.request_id, event.component, event.event, event))
    .map((event, index) => [cell(shortTime(event.timestamp), "nowrap"), cell(event.request_id, "nowrap"), cell(event.component), cell(event.event), cell(eventDuration(event), "nowrap"), detailsCell("fields", event, eventKey("pipeline", event, index))]);
  renderTable(byId("pipeline-table"), ["TIME", "REQUEST", "COMPONENT", "EVENT", "DURATION", "DETAIL"], pipelineRows);
}

function appendHistory(name, value) {
  if (!Number.isFinite(Number(value))) return;
  state.hostHistory[name].push({ timestamp: new Date().toISOString(), value: Number(value) });
  state.hostHistory[name] = state.hostHistory[name].slice(-180);
}

function kpi(label, value, note = "") {
  const node = document.createElement("div");
  node.className = "kpi";
  const labelNode = document.createElement("div"); labelNode.className = "kpi-label"; labelNode.textContent = label;
  const valueNode = document.createElement("div"); valueNode.className = "kpi-value"; valueNode.textContent = value;
  const noteNode = document.createElement("div"); noteNode.className = "kpi-note"; noteNode.textContent = note;
  node.append(labelNode, valueNode, noteNode);
  return node;
}

function renderHealth(snapshot) {
  const health = snapshot.system_health;
  const host = health.host || {};
  const cpu = host.cpu || {};
  const memory = host.memory || {};
  const disk = host.disk || {};
  const gpu = host.gpu || {};
  appendHistory("cpu", cpu.utilization_percent);
  appendHistory("memory", memory.used_percent);
  appendHistory("disk", disk.used_percent);
  const gpus = gpu.gpus || [];
  gpus.forEach((gpu) => {
    const key = String(gpu.index);
    state.gpuHistory[key] ||= [];
    if (Number.isFinite(Number(gpu.utilization_percent))) {
      state.gpuHistory[key].push({ timestamp: snapshot.generated_at, value: Number(gpu.utilization_percent) });
      state.gpuHistory[key] = state.gpuHistory[key].slice(-180);
    }
  });
  const kpis = byId("host-kpis"); clear(kpis);
  kpis.append(
    kpi("METRICS SOURCE", String(host.status || "unknown").toUpperCase(), host.status === "available" ? text(host.source) : text(host.reason)),
    kpi("CPU UTILIZATION", `${number(cpu.utilization_percent)}%`, `${text(cpu.logical_count)} logical CPUs`),
    kpi("MEMORY USED", `${number(memory.used_percent)}%`, `${bytes(memory.available_bytes)} free / ${bytes(memory.total_bytes)}`),
    kpi("REMOTE DISK USED", `${number(disk.used_percent)}%`, `${bytes(disk.free_bytes)} free`),
    kpi("GPU COUNT", host.status === "available" ? String(gpus.length) : "--", gpu.status || host.status),
  );
  const serviceRows = health.services.map((service) => [badge(service.status), cell(service.name), cell(`${number(service.latency_ms)} ms`, "nowrap"), cell(service.detail, "wrap")]);
  renderTable(byId("service-table"), ["STATUS", "COMPONENT", "PROBE", "DETAIL"], serviceRows);
  const gpuRows = gpus.map((gpu) => [cell(gpu.index), cell(gpu.name), cell(`${number(gpu.utilization_percent)}%`), cell(`${number(gpu.memory_used_mib)} / ${number(gpu.memory_total_mib)} MiB`), cell(`${number(gpu.temperature_c)} °C`), cell(`${number(gpu.power_w)} W`)]);
  renderTable(byId("gpu-table"), ["INDEX", "GPU", "UTIL", "VRAM", "TEMP", "POWER"], gpuRows, gpu.reason || host.reason || "No remote NVIDIA GPU data available.");
  renderCharts(byId("host-charts"), [
    { name: "CPU UTILIZATION", unit: "%", values: state.hostHistory.cpu },
    { name: "MEMORY USED", unit: "%", values: state.hostHistory.memory },
    { name: "DISK USED", unit: "%", values: state.hostHistory.disk },
    ...gpus.map((gpu) => ({ name: `GPU ${gpu.index} UTILIZATION`, unit: "%", values: state.gpuHistory[String(gpu.index)] || [] })),
  ]);
}

function prettyMetric(name) { return String(name).replaceAll("_", " ").toUpperCase(); }

function latestPoint(values) { return values && values.length ? values[values.length - 1] : null; }

function renderAudio(snapshot) {
  const audio = snapshot.live_audio;
  const summary = byId("audio-summary");
  summary.textContent = `${audio.status.toUpperCase()} | source: ${audio.source} | ${audio.sample_semantics}`;
  summary.className = `status-strip ${audio.status === "available" ? "" : "degraded"}`;
  const kpis = byId("audio-kpis"); clear(kpis);
  Object.entries(audio.configured_thresholds || {}).forEach(([name, value]) => kpis.appendChild(kpi(prettyMetric(name), number(value, 0), "configured/default RMS")));
  Object.entries(audio.series || {}).slice(0, 6).forEach(([name, values]) => {
    const point = latestPoint(values);
    kpis.appendChild(kpi(prettyMetric(name), point ? number(point.value, 2) : "--", point ? shortTime(point.timestamp) : "no samples"));
  });
  const chartSeries = Object.entries(audio.series || {}).map(([name, values]) => ({ name: prettyMetric(name), unit: "", values }));
  renderCharts(byId("audio-charts"), chartSeries, "No audio metrics have been emitted yet. The current sources update after an utterance or barge-in snapshot, not per audio frame.");
  const latest = audio.latest_barge_in || {};
  const counterRows = Object.entries(latest.counters || {}).map(([name, value]) => [cell(prettyMetric(name)), cell(value)]);
  renderTable(byId("audio-counter-table"), ["COUNTER", "VALUE"], counterRows, "No barge-in counter snapshot available.");
  const measurementRows = Object.entries(latest.measurements || {}).map(([name, value]) => [cell(prettyMetric(name)), cell(value)]);
  renderTable(byId("audio-measurement-table"), ["MEASUREMENT", "VALUE"], measurementRows, "No barge-in measurement snapshot available.");
}

function renderCharts(target, seriesList, emptyText = "No samples yet.") {
  clear(target);
  if (!seriesList.length) { const empty = document.createElement("div"); empty.className = "empty"; empty.textContent = emptyText; target.appendChild(empty); return; }
  seriesList.forEach((series) => {
    const card = document.createElement("div"); card.className = "chart-card";
    const heading = document.createElement("div"); heading.className = "chart-heading";
    const title = document.createElement("span"); title.textContent = series.name;
    const latest = document.createElement("span"); const point = latestPoint(series.values); latest.textContent = point ? `${number(point.value, 2)}${series.unit}` : "--";
    const canvas = document.createElement("canvas"); canvas.width = 480; canvas.height = 180;
    const meta = document.createElement("div"); meta.className = "chart-meta"; meta.textContent = `${series.values.length} sample(s), browser-retained max 180`;
    heading.append(title, latest); card.append(heading, canvas, meta); target.appendChild(card);
    drawChart(canvas, series.values);
  });
}

function drawChart(canvas, values) {
  const ctx = canvas.getContext("2d"); const width = canvas.width; const height = canvas.height;
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, width, height);
  ctx.strokeStyle = "#173817"; ctx.lineWidth = 1;
  for (let x = 0; x <= width; x += width / 10) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke(); }
  for (let y = 0; y <= height; y += height / 6) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke(); }
  const numeric = values.map((item) => Number(item.value)).filter(Number.isFinite);
  if (!numeric.length) return;
  let min = Math.min(...numeric); let max = Math.max(...numeric);
  if (min === max) { min -= Math.max(1, Math.abs(min) * .1); max += Math.max(1, Math.abs(max) * .1); }
  const padding = (max - min) * .08; min -= padding; max += padding;
  ctx.strokeStyle = "#00ff00"; ctx.lineWidth = 2; ctx.beginPath();
  numeric.forEach((value, index) => { const x = numeric.length === 1 ? width / 2 : index / (numeric.length - 1) * width; const y = height - ((value - min) / (max - min)) * height; if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
  ctx.stroke();
  ctx.fillStyle = "#00ff00"; ctx.font = "18px Courier New"; ctx.fillText(max.toFixed(1), 5, 18); ctx.fillText(min.toFixed(1), 5, height - 5);
}

function renderRaw(snapshot) {
  const events = snapshot.interaction_io.pipeline.events.filter((event) => matchesFilter(event.request_id, event.component, event.event, event));
  const rows = events.map((event, index) => [cell(shortTime(event.timestamp), "nowrap"), cell(event.request_id, "nowrap"), cell(event.component), cell(event.event), detailsCell("JSON", event, eventKey("raw", event, index))]);
  renderTable(byId("raw-table"), ["TIME", "REQUEST", "COMPONENT", "EVENT", "PAYLOAD"], rows);
}

function renderExtensions(snapshot) {
  (snapshot.extensions || []).forEach((extension, index) => {
    const id = String(extension.id || `extension-${index}`).replace(/[^a-z0-9_-]/gi, "-");
    if (state.extensionIds.has(id)) {
      const existing = byId(`extension-data-${id}`);
      if (existing) existing.textContent = JSON.stringify(extension.data ?? extension, null, 2);
      return;
    }
    state.extensionIds.add(id);
    const tab = document.createElement("button"); tab.type = "button"; tab.role = "tab"; tab.id = `tab-${id}`; tab.dataset.tab = id; tab.setAttribute("aria-controls", `panel-${id}`); tab.setAttribute("aria-selected", "false"); tab.textContent = extension.title || id;
    const panel = document.createElement("section"); panel.id = `panel-${id}`; panel.className = "tab-panel"; panel.role = "tabpanel"; panel.setAttribute("aria-labelledby", tab.id); panel.hidden = true;
    const windowNode = document.createElement("section"); windowNode.className = "window"; const heading = document.createElement("h2"); heading.textContent = extension.title || id; const body = document.createElement("div"); body.className = "window-body"; const pre = document.createElement("pre"); pre.id = `extension-data-${id}`; pre.textContent = JSON.stringify(extension.data ?? extension, null, 2); body.appendChild(pre); windowNode.append(heading, body); panel.appendChild(windowNode);
    byId("tabs").appendChild(tab); byId("extension-panels").appendChild(panel); bindTab(tab);
  });
}

function render(snapshot) {
  state.snapshot = snapshot;
  byId("schema-version").textContent = text(snapshot.schema_version);
  byId("snapshot-time").textContent = new Date(snapshot.generated_at).toLocaleString();
  byId("footer-source").textContent = `pipeline files: ${snapshot.interaction_io.pipeline.files_scanned}; benchmark files: ${snapshot.interaction_io.voice_benchmarks.files_scanned}; generated ${snapshot.generated_at}`;
  renderInteractions(snapshot); renderHealth(snapshot); renderAudio(snapshot); renderRaw(snapshot); renderExtensions(snapshot);
}

async function fetchSnapshot() {
  if (state.fetching || state.paused) return;
  state.fetching = true;
  try {
    const response = await fetch("/api/snapshot?event_limit=250&interaction_limit=80", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    state.receivedAt = Date.now(); render(payload.snapshot);
    byId("connection-light").className = "light healthy"; byId("connection-text").textContent = "LIVE / POLLING";
  } catch (error) {
    byId("connection-light").className = "light failed"; byId("connection-text").textContent = `ERROR: ${error.message}`;
  } finally { state.fetching = false; schedule(); }
}

function schedule() {
  clearTimeout(state.timer);
  if (!state.paused) state.timer = setTimeout(fetchSnapshot, Number(byId("refresh-rate").value));
}

function bindTab(tab) {
  tab.addEventListener("click", () => {
    document.querySelectorAll('[role="tab"]').forEach((item) => item.setAttribute("aria-selected", String(item === tab)));
    document.querySelectorAll('[role="tabpanel"]').forEach((panel) => { panel.hidden = panel.id !== tab.getAttribute("aria-controls"); });
    history.replaceState(null, "", `#${tab.dataset.tab}`);
  });
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const tabs = Array.from(document.querySelectorAll('[role="tab"]')); const current = tabs.indexOf(tab); const offset = event.key === "ArrowRight" ? 1 : -1; const next = tabs[(current + offset + tabs.length) % tabs.length]; next.focus(); next.click();
  });
}

document.querySelectorAll('[role="tab"]').forEach(bindTab);
byId("refresh-now").addEventListener("click", () => { state.paused = false; byId("pause-toggle").textContent = "Pause"; fetchSnapshot(); });
byId("pause-toggle").addEventListener("click", () => { state.paused = !state.paused; byId("pause-toggle").textContent = state.paused ? "Resume" : "Pause"; byId("connection-text").textContent = state.paused ? "PAUSED" : "LIVE / POLLING"; if (state.paused) clearTimeout(state.timer); else fetchSnapshot(); });
byId("refresh-rate").addEventListener("change", schedule);
filterInput.addEventListener("input", () => { if (state.snapshot) { renderInteractions(state.snapshot); renderRaw(state.snapshot); } });
setInterval(() => { byId("snapshot-age").textContent = state.receivedAt ? `${((Date.now() - state.receivedAt) / 1000).toFixed(1)} s` : "--"; }, 250);

const requestedTab = location.hash.slice(1);
if (requestedTab) document.querySelector(`[data-tab="${CSS.escape(requestedTab)}"]`)?.click();
fetchSnapshot();
