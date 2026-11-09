/* Zone editor: click the corners of a table on the camera picture, assign a table, save. */
(function () {
  "use strict";

  const editor = document.getElementById("zoneEditor");
  if (!editor) return;
  const RS = window.RestaurantSpy;
  const stage = document.getElementById("zoneStage");
  const overlay = document.getElementById("zoneOverlay");
  const labels = document.getElementById("zoneLabels");
  const list = document.getElementById("zoneList");
  const tableSelect = document.getElementById("zoneTable");
  const finishButton = document.getElementById("zoneFinish");
  const status = document.getElementById("zoneStatus");
  let zones = JSON.parse(JSON.parse(document.getElementById("zones-data").textContent));
  let drawing = [];

  function svgPoint(points) {
    return points.map((p) => (p[0] * 1000) + "," + (p[1] * 1000)).join(" ");
  }

  function render() {
    overlay.innerHTML = "";
    labels.innerHTML = "";
    list.innerHTML = "";
    zones.forEach(function (zone, index) {
      const polygon = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      polygon.setAttribute("points", svgPoint(zone.points));
      polygon.setAttribute("class", "zone-shape");
      overlay.appendChild(polygon);
      const label = document.createElement("button");
      label.type = "button";
      label.className = "cctv-label zone-tag";
      label.textContent = zone.label + " ✕";
      label.title = "Remove this zone";
      label.style.left = (Math.min.apply(null, zone.points.map((p) => p[0])) * 100) + "%";
      label.style.top = (Math.min.apply(null, zone.points.map((p) => p[1])) * 100) + "%";
      label.addEventListener("click", function () { zones.splice(index, 1); render(); });
      labels.appendChild(label);
      const item = document.createElement("li");
      item.className = "list-group-item d-flex justify-content-between align-items-center";
      item.textContent = zone.label + " · " + zone.points.length + " points";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-sm btn-outline-danger";
      remove.textContent = "Remove";
      remove.addEventListener("click", function () { zones.splice(index, 1); render(); });
      item.appendChild(remove);
      list.appendChild(item);
    });
    if (!zones.length) {
      const empty = document.createElement("li");
      empty.className = "list-group-item text-body-secondary small";
      empty.textContent = "No zones yet.";
      list.appendChild(empty);
    }
    if (drawing.length) {
      const path = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
      path.setAttribute("points", svgPoint(drawing));
      path.setAttribute("class", "zone-drawing");
      overlay.appendChild(path);
      drawing.forEach(function (p) {
        const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        dot.setAttribute("cx", p[0] * 1000);
        dot.setAttribute("cy", p[1] * 1000);
        dot.setAttribute("r", 6);
        dot.setAttribute("class", "zone-dot");
        overlay.appendChild(dot);
      });
    }
    finishButton.disabled = drawing.length < 3;
  }

  stage.addEventListener("click", function (event) {
    if (event.target.closest(".zone-tag")) return;
    const rect = stage.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    const y = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
    drawing.push([Math.round(x * 1000) / 1000, Math.round(y * 1000) / 1000]);
    render();
  });

  finishButton.addEventListener("click", function () {
    const option = tableSelect.options[tableSelect.selectedIndex];
    if (!option || drawing.length < 3) return;
    const tableId = Number(option.value);
    zones = zones.filter((z) => z.table !== tableId); // one zone per table
    zones.push({ table: tableId, label: option.dataset.label, points: drawing });
    drawing = [];
    render();
  });
  document.getElementById("zoneUndo").addEventListener("click", function () {
    drawing.pop();
    render();
  });
  document.getElementById("zoneSave").addEventListener("click", async function () {
    status.textContent = "Saving…";
    try {
      const result = await RS.api(editor.dataset.saveUrl, { method: "POST", body: zones.map((z) => ({ table: z.table, points: z.points })) });
      status.textContent = result.saved + " zone(s) saved. The worker uses them from its next sample.";
      status.className = "small mt-2 text-success";
    } catch (err) {
      status.textContent = "Could not save: " + err.message;
      status.className = "small mt-2 text-danger";
    }
  });

  // Keep the picture fresh while drawing, if the worker is running.
  const frame = document.getElementById("zoneFrame");
  if (frame) setInterval(function () { frame.src = editor.dataset.frameUrl + "?t=" + Date.now(); }, 3000);
  render();
})();
