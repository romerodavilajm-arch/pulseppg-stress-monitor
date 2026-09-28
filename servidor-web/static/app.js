// Página mínima de la Fase 2: recibe samples por Socket.IO y dibuja la onda IR.

const RATE_HZ = 50;
const WINDOW_SEC = 5;
const MAX_POINTS = RATE_HZ * WINDOW_SEC;

const socket = io();
const $ = (id) => document.getElementById(id);

let piConnected = false;
let capturing = false;
let count = 0;
let points = [];   // valores IR de la ventana visible
let dirty = false;

function updateButtons() {
  $("start").disabled = !piConnected || capturing;
  $("abort").disabled = !piConnected || !capturing;
}

function showMessage(text) {
  $("mensaje").textContent = text || "";
}

socket.on("connect", () => showMessage(""));
socket.on("disconnect", () => {
  showMessage("Sin conexión con el servidor, reintentando...");
  piConnected = false;
  capturing = false;
  $("pi").textContent = "desconocida";
  $("pi").className = "off";
  updateButtons();
});

socket.on("pi", ({ connected }) => {
  piConnected = connected;
  $("pi").textContent = connected ? "conectada" : "desconectada";
  $("pi").className = connected ? "on" : "off";
  if (!connected) capturing = false;
  updateButtons();
});

socket.on("sample", ({ ir }) => {
  count += 1;
  points.push(ir);
  if (points.length > MAX_POINTS) points.shift();
  dirty = true;
});

socket.on("countdown", ({ value }) => {
  const m = Math.floor(value / 60);
  const s = String(value % 60).padStart(2, "0");
  $("countdown").textContent = `${m}:${s}`;
});

socket.on("status", ({ value }) => {
  $("status").textContent = value;
  capturing = value === "capturing" || value === "paused" || value === "restarting";
  updateButtons();
});

socket.on("aborted", () => showMessage("Prueba cancelada."));
socket.on("error", ({ message }) => showMessage(message));

$("start").addEventListener("click", () => {
  showMessage("");
  points = [];
  count = 0;
  $("count").textContent = "0";
  dirty = true;
  socket.emit("start", {});
});

$("abort").addEventListener("click", () => socket.emit("abort", {}));

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
