/*
 * Table QR scanner (html5-qrcode 2.3.8, Html5Qrcode class for a custom UI).
 * Camera, a photo of the code (works without HTTPS) or the typed table number.
 * Every route: decoded text -> table reference -> lookup API -> open the inspection.
 */
(function () {
  "use strict";

  const RS = window.RestaurantSpy;
  const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
  const QR_URL = new RegExp("/t/(" + UUID + ")/?(?:[?#].*)?$", "i");
  const BARE_UUID = new RegExp("^(" + UUID + ")$", "i");

  /** Table UUID from a QR payload ("https://host/t/<uuid>/" or a bare UUID), else null. */
  function parseTableRef(text) {
    const value = String(text || "").trim();
    const fromUrl = value.match(QR_URL);
    if (fromUrl) return fromUrl[1].toLowerCase();
    const bare = value.match(BARE_UUID);
    return bare ? bare[1].toLowerCase() : null;
  }

  /** Typed input: "12", "T12" or a QR link/UUID. */
  function parseManualEntry(text) {
    const value = String(text || "").trim();
    return parseTableRef(value) || (/^[Tt]?\d{1,4}$/.test(value) ? value.replace(/^[Tt]/, "") : null);
  }

  RS.scanner = { parseTableRef: parseTableRef, parseManualEntry: parseManualEntry };

  const page = document.getElementById("scannerPage");
  if (!page) return;
  const resultBox = document.getElementById("scanResult");
  const view = document.getElementById("scannerView");
  const placeholder = document.getElementById("readerPlaceholder");
  const startButton = document.getElementById("startScan");
  const stopButton = document.getElementById("stopScan");
  const cameraSelect = document.getElementById("cameraSelect");
  let camera = null;
  let busy = false;

  function show(kind, text) {
    resultBox.className = "scan-result scan-" + kind;
    resultBox.textContent = text;
  }

  function cameraError(error) {
    const text = String((error && (error.name || error.message)) || error);
    if (/NotAllowed|Permission/i.test(text)) return "Camera permission was denied. Allow it in the browser, or use a photo / the table number.";
    if (/NotFound|no camera|Requested device not found|Overconstrained/i.test(text)) return "No camera found. Use a photo or type the table number.";
    if (/NotReadable|Could not start video source|in use/i.test(text)) return "The camera is busy in another app or tab. Close it and try again, or use a photo.";
    if (!window.isSecureContext) return "The camera needs https:// or http://localhost. Use a photo or type the table number.";
    return "The camera could not start (" + text + "). Use a photo or type the table number.";
  }

  async function openTable(ref) {
    show("info", "Looking up the table…");
    try {
      const found = await RS.api(page.dataset.lookupUrl + "?q=" + encodeURIComponent(ref));
      if (navigator.vibrate) navigator.vibrate(150);
      view.classList.remove("flash-success");
      void view.offsetWidth;
      view.classList.add("flash-success");
      show("success", "Table " + found.table + " found. Opening…");
      await stopCamera();
      setTimeout(function () { window.location.href = found.inspect_url || found.url; }, 300);
    } catch (error) {
      busy = false;
      show("error", error.status === 404 ? "Unknown table. This code is not a table of your restaurant." : "Lookup failed: " + error.message);
    }
  }

  function handleDecoded(text) {
    if (busy) return;
    const ref = parseTableRef(text);
    if (!ref) { show("error", "Not a RestaurantSpy table code."); return; }
    busy = true;
    openTable(ref);
  }

  async function loadCameras() {
    try {
      const cameras = await Html5Qrcode.getCameras();
      cameraSelect.innerHTML = "";
      cameraSelect.add(new Option("Back camera (default)", ""));
      cameras.forEach((cam, i) => cameraSelect.add(new Option(cam.label || "Camera " + (i + 1), cam.id)));
      cameraSelect.disabled = cameras.length === 0;
    } catch (err) { /* permission not granted yet */ }
  }

  async function startCamera() {
    if (camera) return;
    startButton.disabled = true;
    show("info", "Starting camera…");
    camera = new Html5Qrcode("reader", { verbose: false });
    const source = cameraSelect.value ? { deviceId: { exact: cameraSelect.value } } : { facingMode: "environment" };
    try {
      await camera.start(source, {
        fps: 10,
        qrbox: (w, h) => { const side = Math.floor(Math.min(w, h) * 0.7); return { width: side, height: side }; },
      }, handleDecoded, function () {});
      placeholder.classList.add("d-none");
      stopButton.disabled = false;
      show("info", "Camera on. Hold the table's QR code inside the square.");
      if (cameraSelect.options.length <= 1) loadCameras();
    } catch (error) {
      camera = null;
      startButton.disabled = false;
      show("error", cameraError(error));
    }
  }

  async function stopCamera() {
    if (!camera) return;
    try { await camera.stop(); camera.clear(); } catch (err) { /* already stopped */ }
    camera = null;
    placeholder.classList.remove("d-none");
    startButton.disabled = false;
    stopButton.disabled = true;
  }

  startButton.addEventListener("click", startCamera);
  stopButton.addEventListener("click", function () { stopCamera(); show("info", "Camera stopped."); });
  window.addEventListener("pagehide", stopCamera);

  if (!window.isSecureContext || !navigator.mediaDevices) document.getElementById("insecureNotice").classList.remove("d-none");
  if (typeof Html5Qrcode === "undefined") {
    startButton.disabled = true;
    show("error", "The scanner library did not load (no internet?). Type the table number instead.");
  } else {
    document.getElementById("scanFile").addEventListener("change", async function (event) {
      const file = event.target.files[0];
      if (!file) return;
      await stopCamera();
      busy = false;
      show("info", "Reading the photo…");
      const fileScanner = new Html5Qrcode("readerFile", { verbose: false });
      try {
        handleDecoded(await fileScanner.scanFile(file, false));
      } catch (error) {
        show("error", "No QR code found in that photo. Try a sharper, closer photo.");
      } finally {
        fileScanner.clear();
        event.target.value = "";
      }
    });
  }

  document.getElementById("manualForm").addEventListener("submit", function (event) {
    event.preventDefault();
    const ref = parseManualEntry(document.getElementById("manualCode").value);
    if (!ref) { show("error", "Type a table number such as 12 or T12."); return; }
    busy = true;
    openTable(ref);
  });
})();
