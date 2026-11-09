/* Live CCTV console: refreshes the latest frame once a second and draws the table zones over it. */
(function () {
  "use strict";

  const page = document.getElementById("cctvConsole");
  if (!page) return;
  const RS = window.RestaurantSpy;
  const img = document.getElementById("cctvFrame");
  const overlay = document.getElementById("cctvOverlay");
  const labels = document.getElementById("cctvLabels");
  const message = document.getElementById("cctvMessage");
  const messageText = document.getElementById("cctvMessageText");
  const statusEl = document.getElementById("cctvStatus");
  const lastFrameEl = document.getElementById("cctvLastFrame");
  const detectorEl = document.getElementById("cctvDetector");
  const liveBadge = document.getElementById("cctvLiveBadge");
  const pauseButton = document.getElementById("cctvPause");
  const stage = document.getElementById("cctvStage");
  const COLORS = {
    READY: "#16a34a", OCCUPIED: "#2563eb", RESERVED: "#9333ea", VACANT_NEEDS_CLEARING: "#ea580c",
    CLEANING_REQUIRED: "#dc2626", CLEANING_IN_PROGRESS: "#0891b2", BLOCKED: "#6b7280", INACTIVE: "#6b7280",
  };
  const SHORT = {
    READY: "FREE", OCCUPIED: "OCCUPIED", RESERVED: "RESERVED", VACANT_NEEDS_CLEARING: "NEEDS CLEARING",
    CLEANING_REQUIRED: "CLEANING REQUIRED", CLEANING_IN_PROGRESS: "CLEANING", BLOCKED: "BLOCKED", INACTIVE: "INACTIVE",
  };
  let paused = false;
  let lastFrameAt = null;

  function setStatus(state) {
    statusEl.textContent = state.status_label;
    statusEl.className = "cctv-status st-" + state.status.toLowerCase();
    if (liveBadge) liveBadge.classList.toggle("off", state.status !== "ONLINE");
    lastFrameAt = state.last_frame_at;
    if (state.detector) {
      detectorEl.innerHTML = "Detector: " + escapeHtml(state.detector) + " · dishes " +
        (state.detects_dishes ? "detectable" : "<em>not detectable</em>");
    }
    const noFrame = !state.has_frame;
    message.classList.toggle("d-none", !noFrame);
    img.classList.toggle("d-none", noFrame);
    if (noFrame) {
      messageText.textContent = state.status === "OFFLINE"
        ? "Camera disconnected" + (state.last_error ? ": " + state.last_error : "") + ". Tables keep their last known state."
        : "Waiting for the first frame from the video worker.";
    }
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function draw(state) {
    overlay.innerHTML = "";
    labels.innerHTML = "";
    state.zones.forEach(function (zone) {
      const color = COLORS[zone.status] || "#9ca3af";
      const polygon = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      polygon.setAttribute("points", zone.points.map((p) => (p[0] * 1000) + "," + (p[1] * 1000)).join(" "));
      polygon.setAttribute("fill", color);
      polygon.setAttribute("fill-opacity", "0.12");
      polygon.setAttribute("stroke", color);
      polygon.setAttribute("stroke-width", "4");
      polygon.setAttribute("vector-effect", "non-scaling-stroke");
      overlay.appendChild(polygon);
      const xs = zone.points.map((p) => p[0]), ys = zone.points.map((p) => p[1]);
      const label = document.createElement("div");
      label.className = "cctv-label";
      label.style.left = (Math.min.apply(null, xs) * 100) + "%";
      label.style.top = (Math.min.apply(null, ys) * 100) + "%";
      label.style.borderColor = color;
      let text = zone.table + " — " + (SHORT[zone.status] || zone.status_label || "?");
      const extra = [];
      if (zone.people !== null && zone.people !== undefined) extra.push(zone.people + " 👤");
      if (zone.dishes) extra.push(zone.dishes + " 🍽");
      label.innerHTML = "<strong>" + escapeHtml(text) + "</strong>" + (extra.length ? "<span>" + extra.join(" · ") + "</span>" : "");
      labels.appendChild(label);
    });
  }

  function tickClock() {
    if (!lastFrameAt) { lastFrameEl.textContent = "never"; return; }
    const seconds = Math.max(0, Math.round((Date.now() - new Date(lastFrameAt).getTime()) / 1000));
    lastFrameEl.textContent = seconds < 2 ? "just now" : seconds + " s ago";
  }

  async function refresh() {
    if (paused) return;
    try {
      const state = await RS.api(page.dataset.stateUrl);
      setStatus(state);
      draw(state);
      if (state.has_frame) img.src = page.dataset.frameUrl + "?t=" + Date.now();
    } catch (err) {
      statusEl.textContent = "Server unreachable";
      statusEl.className = "cctv-status st-offline";
    }
  }

  pauseButton.addEventListener("click", function () {
    paused = !paused;
    pauseButton.setAttribute("aria-pressed", String(paused));
    pauseButton.innerHTML = paused
      ? '<i class="bi bi-play-fill" aria-hidden="true"></i> Resume'
      : '<i class="bi bi-pause-fill" aria-hidden="true"></i> Pause';
    if (!paused) refresh();
  });
  document.getElementById("cctvFullscreen").addEventListener("click", function () {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (stage.requestFullscreen) stage.requestFullscreen();
  });

  refresh();
  setInterval(refresh, 1000);
  setInterval(tickClock, 1000);
})();
