// Pantalla única (Fase 8): bienvenida → captura → analizando → resultados.
//
// La captura llega por Socket.IO (sample, countdown, status). Cuando la Pi
// termina, el servidor avisa `analyzing {session_id}` y la página consulta
// /api/estado/<id> cada 2 s hasta 'ready' o 'error' (sección 6, fase 7).

const RATE_HZ = 50;
const WINDOW_SEC = 5;
const MAX_POINTS = RATE_HZ * WINDOW_SEC;
const BPM_SEC = 10;                 // segundos de señal para el pulso en vivo
const POLL_MS = 2000;
// Si tras esto el watcher sigue sin conocer la sesión, algo se perdió en el
// camino (el simulador sin WATCHER_URL, por ejemplo).
const NOT_FOUND_LIMIT_MS = 60000;

const socket = io();
const $ = (id) => document.getElementById(id);

const SCREENS = ["bienvenida", "captura", "analisis", "resultados"];
const CAPTURING = ["capturing", "paused", "restarting"];
const QUALITY = { capturing: "buena", paused: "dedo retirado", restarting: "reiniciando" };
const STATUS_TEXT = {
  uploaded: "Archivo recibido, esperando el análisis...",
  processing: "Calculando HRV y clasificando ventanas...",
};

let screen = "bienvenida";
let piConnected = false;
let count = 0;
let points = [];        // IR de la ventana visible
let recent = [];        // IR de los últimos BPM_SEC segundos
let dirty = false;
let pollTimer = null;
let analysisId = null;

// --- Utilidades -------------------------------------------------------------

function show(name) {
  screen = name;
  for (const s of SCREENS) $(`pantalla-${s}`).hidden = s !== name;
  updateButtons();
  if (name === "bienvenida") loadSessions();
}

function updateButtons() {
  $("start").disabled = !piConnected || screen !== "bienvenida";
  $("abort").disabled = !piConnected || screen !== "captura";
}

function showMessage(text) {
  $("mensaje").textContent = text || "";
}

function fmt(value, digits = 0) {
  return value === null || value === undefined ? "--" : Number(value).toFixed(digits);
}

function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleString("es") : "--";
}

function cell(row, text) {
  const td = document.createElement("td");
  td.textContent = text;
  row.appendChild(td);
  return td;
}

function resetCapture() {
  points = [];
  recent = [];
  count = 0;
  $("count").textContent = "0";
  $("countdown").textContent = "--:--";
  $("bpm-vivo").textContent = "--";
  $("calidad").textContent = "--";
  dirty = true;
}

// --- Socket.IO --------------------------------------------------------------

socket.on("connect", () => showMessage(""));
socket.on("disconnect", () => {
  showMessage("Sin conexión con el servidor, reintentando...");
  piConnected = false;
  $("pi").textContent = "desconocida";
  $("pi").className = "off";
  updateButtons();
});

socket.on("pi", ({ connected }) => {
  piConnected = connected;
  $("pi").textContent = connected ? "conectada" : "desconectada";
  $("pi").className = connected ? "on" : "off";
  updateButtons();
});

socket.on("sample", ({ ir }) => {
  if (screen !== "captura") return;
  count += 1;
  points.push(ir);
  if (points.length > MAX_POINTS) points.shift();
  recent.push(ir);
  if (recent.length > RATE_HZ * BPM_SEC) recent.shift();
  dirty = true;
});

socket.on("countdown", ({ value }) => {
  const m = Math.floor(value / 60);
  const s = String(value % 60).padStart(2, "0");
  $("countdown").textContent = `${m}:${s}`;
});

socket.on("status", ({ value }) => {
  if (CAPTURING.includes(value)) {
    if (screen !== "captura") {
      resetCapture();
      showMessage("");
      show("captura");
    }
    $("calidad").textContent = QUALITY[value];
  } else if (value === "uploading") {
    stopPolling();
    show("analisis");
    $("analisis-titulo").textContent = "Enviando la captura...";
    $("analisis-detalle").textContent = "";
  } else if (value === "upload_failed") {
    show("bienvenida");
    showMessage("No se pudo subir la sesión al watcher; se reintentará antes de la próxima.");
  }
});

socket.on("analyzing", ({ session_id }) => startPolling(session_id));

socket.on("aborted", () => {
  show("bienvenida");
  showMessage("Prueba cancelada.");
});

socket.on("new", () => {
  stopPolling();
  showMessage("");
  show("bienvenida");
});

socket.on("error", ({ message }) => showMessage(message));

$("start").addEventListener("click", () => {
  showMessage("");
  socket.emit("start", {});
});
$("abort").addEventListener("click", () => socket.emit("abort", {}));
$("nueva").addEventListener("click", () => socket.emit("new", {}));

// --- Análisis: polling de /api/estado ----------------------------------------

function stopPolling() {
  clearTimeout(pollTimer);
  pollTimer = null;
  analysisId = null;
}

function startPolling(sessionId) {
  stopPolling();
  analysisId = sessionId;
  show("analisis");
  $("analisis-titulo").textContent = "Analizando...";
  $("analisis-detalle").textContent = "";
  const since = Date.now();

  const tick = async () => {
    if (analysisId !== sessionId) return;
    let data = null;
    let status = null;
    try {
      const resp = await fetch(`/api/estado/${encodeURIComponent(sessionId)}`);
      status = resp.status;
      data = await resp.json();
    } catch (_) {
      // Polling tolerante a errores: se reintenta en el siguiente tick.
    }
    if (analysisId !== sessionId) return;

    if (data && (data.status === "ready" || data.status === "error")) {
      stopPolling();
      loadResults(sessionId);
      return;
    }
    if (status === 404 && Date.now() - since > NOT_FOUND_LIMIT_MS) {
      $("analisis-detalle").textContent =
        "El watcher todavía no tiene esta sesión. Revisa `docker compose logs watcher`.";
    } else if (data && data.status) {
      $("analisis-detalle").textContent = STATUS_TEXT[data.status] || "";
    } else if (status !== 404) {
      $("analisis-detalle").textContent = "Sin respuesta del servidor, reintentando...";
    }
    pollTimer = setTimeout(tick, POLL_MS);
  };
  tick();
}

// --- Resultados ---------------------------------------------------------------

const LEVEL_TEXT = { bajo: "Bajo", moderado: "Moderado", alto: "Alto" };

async function loadResults(sessionId) {
  let data;
  try {
    const resp = await fetch(`/api/resultados/${encodeURIComponent(sessionId)}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    data = await resp.json();
  } catch (err) {
    showMessage(`No se pudieron leer los resultados (${err.message}).`);
    show("bienvenida");
    return;
  }
  renderResults(data);
  showMessage("");
  show("resultados");
}

// Fase 9: el watcher marca las capturas canceladas con status_detail "abort: ...".
function isAborted(status, detail) {
  return status === "error" && (detail || "").startsWith("abort");
}

function renderResults({ session, metrics, windows, summary, events }) {
  const ok = session.status === "ready";
  $("resultado-ok").hidden = !ok;
  $("resultado-error").hidden = ok;
  $("r-error-titulo").textContent = isAborted(session.status, session.status_detail)
    ? "Prueba cancelada"
    : "La sesión no se pudo analizar";
  $("r-error").textContent = ok ? "" : session.status_detail || `Estado: ${session.status}`;

  const m = metrics || {};
  $("r-bpm").textContent = fmt(m.bpm);
  $("r-sdnn").textContent = m.sdnn == null ? "--" : `${fmt(m.sdnn)} ms`;
  $("r-rmssd").textContent = m.rmssd == null ? "--" : `${fmt(m.rmssd)} ms`;
  $("r-nivel").textContent = LEVEL_TEXT[summary.level] || "--";
  $("r-nivel").className = `valor nivel-${summary.level || "ninguno"}`;

  renderTimeline(windows, session.duration_sec);

  const dl = $("r-sesion");
  dl.replaceChildren();
  const facts = [
    ["Sesión", session.session_id],
    ["Dispositivo", session.device_id || "--"],
    ["Inicio", fmtTime(session.start_time)],
    ["Duración", session.duration_sec == null ? "--" : `${fmt(session.duration_sec)} s`],
    ["Calidad", session.quality || "--"],
    ["Estado", session.status],
    ["Latidos detectados", summary.peaks],
    ["pNN50", m.pnn50 == null ? "--" : `${fmt(m.pnn50, 1)} %`],
    ["Ventanas con estrés", `${summary.estres} de ${summary.windows}`],
    ["SHA-256 del crudo", session.checksum || "--"],
  ];
  for (const [k, v] of facts) {
    const dt = document.createElement("dt");
    dt.textContent = k;
    const dd = document.createElement("dd");
    dd.textContent = v;
    dl.append(dt, dd);
  }

  const wb = $("r-ventanas");
  wb.replaceChildren();
  for (const w of windows) {
    const tr = document.createElement("tr");
    cell(tr, fmt(w.t_start, 1));
    cell(tr, fmt(w.t_end, 1));
    cell(tr, w.level);
    cell(tr, fmt(w.score, 3));
    wb.appendChild(tr);
  }

  const eb = $("r-eventos");
  eb.replaceChildren();
  for (const e of events) {
    const tr = document.createElement("tr");
    cell(tr, new Date(e.changed_at).toLocaleTimeString("es"));
    cell(tr, e.from_status || "--");
    cell(tr, e.to_status);
    cell(tr, e.detail || "");
    eb.appendChild(tr);
  }
}

function renderTimeline(windows, duration) {
  const bar = $("timeline");
  const axis = $("timeline-eje");
  bar.replaceChildren();
  axis.replaceChildren();
  if (!windows.length) {
    bar.textContent = "Sin ventanas clasificadas.";
    return;
  }
  const end = Math.max(duration || 0, windows[windows.length - 1].t_end);
  for (const w of windows) {
    const seg = document.createElement("div");
    seg.className = `ventana ${w.level}`;
    seg.style.left = `${(w.t_start / end) * 100}%`;
    seg.style.width = `${((w.t_end - w.t_start) / end) * 100}%`;
    const fill = document.createElement("div");
    fill.className = "relleno";
    fill.style.height = `${Math.max(4, (w.score ?? 0) * 100)}%`;
    seg.appendChild(fill);
    seg.title = `${fmt(w.t_start)}–${fmt(w.t_end)} s · ${w.level} · p=${fmt(w.score, 2)}`;
    bar.appendChild(seg);
  }
  // Marcas cada minuto.
  for (let t = 0; t <= end; t += 60) {
    const tick = document.createElement("span");
    tick.style.left = `${(t / end) * 100}%`;
    tick.textContent = `${t / 60}:00`;
    axis.appendChild(tick);
  }
}

// --- Bienvenida: últimas sesiones ------------------------------------------

async function loadSessions() {
  let rows = [];
  try {
    const resp = await fetch("/api/sesiones");
    if (resp.ok) rows = await resp.json();
  } catch (_) {
    // Sin base de datos la lista queda vacía; la captura sigue funcionando.
  }
  const body = $("sesiones");
  body.replaceChildren();
  $("sin-sesiones").hidden = rows.length > 0;
  for (const r of rows) {
    const tr = document.createElement("tr");
    cell(tr, fmtTime(r.start_time));
    cell(tr, isAborted(r.status, r.status_detail) ? "cancelada" : r.status);
    cell(tr, fmt(r.bpm));
    const td = cell(tr, "");
    if (r.status === "ready" || r.status === "error") {
      const btn = document.createElement("button");
      btn.textContent = "Ver";
      btn.addEventListener("click", () => loadResults(r.session_id));
      td.appendChild(btn);
    }
    body.appendChild(tr);
  }
}

// --- Pulso en vivo ------------------------------------------------------------

// Estimación simple para la pantalla: máximos locales por encima de la media
// de la ventana, separados al menos 0.33 s (180 BPM). El valor que cuenta es
// el de Spark en los resultados.
function liveBpm() {
  if (recent.length < RATE_HZ * 4) return null;
  const mean = recent.reduce((a, b) => a + b, 0) / recent.length;
  const max = Math.max(...recent);
  const threshold = mean + (max - mean) * 0.4;
  const minGap = Math.round(RATE_HZ / 3);
  const peaks = [];
  for (let i = 1; i < recent.length - 1; i++) {
    const v = recent[i];
    if (v > threshold && v >= recent[i - 1] && v > recent[i + 1]) {
      if (peaks.length && i - peaks[peaks.length - 1] < minGap) {
        if (v > recent[peaks[peaks.length - 1]]) peaks[peaks.length - 1] = i;
      } else {
        peaks.push(i);
      }
    }
  }
  if (peaks.length < 2) return null;
  const beats = (peaks[peaks.length - 1] - peaks[0]) / RATE_HZ;
  return (60 * (peaks.length - 1)) / beats;
}

setInterval(() => {
  if (screen !== "captura") return;
  const bpm = liveBpm();
  $("bpm-vivo").textContent = bpm ? Math.round(bpm) : "--";
}, 1000);

// --- Dibujo ---------------------------------------------------------------

const canvas = $("onda");
const ctx = canvas.getContext("2d");

function draw() {
  if (dirty) {
    dirty = false;
    $("count").textContent = String(count);

    const { width, height } = canvas;
    ctx.clearRect(0, 0, width, height);
    if (points.length > 1) {
      // Autoescala sobre la ventana visible, con 10 % de margen.
      let min = Math.min(...points);
      let max = Math.max(...points);
      const pad = (max - min) * 0.1 || 1;
      min -= pad;
      max += pad;

      ctx.beginPath();
      ctx.strokeStyle = "#c01048";
      ctx.lineWidth = 2;
      points.forEach((v, i) => {
        const x = (i / (MAX_POINTS - 1)) * width;
        const y = height - ((v - min) / (max - min)) * height;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }
  }
  requestAnimationFrame(draw);
}
requestAnimationFrame(draw);

show("bienvenida");
