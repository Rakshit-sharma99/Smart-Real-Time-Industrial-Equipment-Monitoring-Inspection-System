/*
 * QR scanner page (html5-qrcode 2.3.8, Html5Qrcode class for a custom UI).
 *
 * Three ways in: live camera, "scan from image file" (works without HTTPS),
 * or typing a code. Every route ends in the same steps:
 *   decoded text -> parseEquipmentRef() -> lookup API -> open the equipment page.
 */
(function () {
  "use strict";

  const EW = window.EquipWatch;
  const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
  const QR_URL = new RegExp("/e/(" + UUID + ")/?(?:[?#].*)?$", "i");
  const BARE_UUID = new RegExp("^(" + UUID + ")$", "i");
  const CODE = /^[A-Z0-9][A-Z0-9-]{0,19}$/i;

  /** Equipment UUID from a QR payload ("http://host/e/<uuid>/" or a bare UUID), else null. */
  function parseEquipmentRef(text) {
    const value = String(text || "").trim();
    const fromUrl = value.match(QR_URL);
    if (fromUrl) return fromUrl[1].toLowerCase();
    const bare = value.match(BARE_UUID);
    return bare ? bare[1].toLowerCase() : null;
  }

  /** Manual entry also accepts an equipment code such as EQ-001. */
  function parseManualEntry(text) {
    const value = String(text || "").trim();
    return parseEquipmentRef(value) || (CODE.test(value) ? value.toUpperCase() : null);
  }

  EW.scanner = { parseEquipmentRef: parseEquipmentRef, parseManualEntry: parseManualEntry };

  const page = document.getElementById("scannerPage");
  if (!page) return;
  const lookupUrl = page.dataset.lookupUrl;
  const resultBox = document.getElementById("scanResult");
  const view = document.getElementById("scannerView");
  const placeholder = document.getElementById("readerPlaceholder");
  const startButton = document.getElementById("startScan");
  const stopButton = document.getElementById("stopScan");
  const cameraSelect = document.getElementById("cameraSelect");
  let camera = null; // Html5Qrcode instance while the camera runs
  let busy = false; // ignore repeated decodes while a lookup is in flight

  function showResult(kind, text) {
    resultBox.className = "scan-result scan-" + kind;
    resultBox.textContent = text;
  }

  function cameraErrorMessage(error) {
    const text = String((error && (error.name || error.message)) || error);
    if (/NotAllowed|Permission/i.test(text)) {
      return "Camera permission was denied. Allow camera access in the browser, or use 'Scan from image file' / 'Type a code'.";
    }
    if (/NotFound|no camera|Requested device not found|OverconstrainedError/i.test(text)) {
      return "No camera was found on this device. Use 'Scan from image file' or 'Type a code'.";
    }
    if (/NotReadable|Could not start video source|in use/i.test(text)) {
      return "The camera is busy (another app or tab may be using it). Close it and try again, or scan from an image file.";
    }
    if (!window.isSecureContext) {
      return "The camera needs https:// or http://localhost. Use 'Scan from image file' or 'Type a code'.";
    }
    return "The camera could not start (" + text + "). Use 'Scan from image file' or 'Type a code'.";
  }

  async function openEquipment(ref) {
    showResult("info", "Looking up equipment…");
    try {
      const found = await EW.api(lookupUrl + "?q=" + encodeURIComponent(ref));
      if (navigator.vibrate) navigator.vibrate(150);
      view.classList.remove("flash-success");
      void view.offsetWidth;
      view.classList.add("flash-success");
      showResult("success", "Found " + found.code + " · " + found.name + ". Opening…");
      await stopCamera();
      setTimeout(function () { window.location.href = found.url; }, 350);
    } catch (error) {
      busy = false;
      showResult("error", error.status === 404 ? "Unknown equipment. This code is not registered." : "Lookup failed: " + error.message);
    }
  }

  function handleDecoded(text) {
    if (busy) return;
    const ref = parseEquipmentRef(text);
    if (!ref) {
      showResult("error", "Not an equipment QR. Scan a label printed by " + page.dataset.appName + ".");
      return;
    }
    busy = true;
    openEquipment(ref);
  }

  // ---------------------------------------------------------------- camera
  async function loadCameras() {
    try {
      const cameras = await Html5Qrcode.getCameras();
      cameraSelect.innerHTML = "";
      const auto = new Option("Back camera (default)", "");
      cameraSelect.add(auto);
      cameras.forEach(function (cam, index) {
        cameraSelect.add(new Option(cam.label || "Camera " + (index + 1), cam.id));
      });
      cameraSelect.disabled = cameras.length === 0;
      return cameras;
    } catch (error) {
      return [];
    }
  }

  async function startCamera() {
    if (camera) return;
    startButton.disabled = true;
    showResult("info", "Starting camera…");
    camera = new Html5Qrcode("reader", { verbose: false });
    const source = cameraSelect.value ? { deviceId: { exact: cameraSelect.value } } : { facingMode: "environment" };
    try {
      await camera.start(
        source,
        {
          fps: 10,
          qrbox: function (width, height) {
            const side = Math.floor(Math.min(width, height) * 0.7);
            return { width: side, height: side };
          },
        },
        handleDecoded,
        function () {} // "no QR in this frame" - ignore
      );
      placeholder.classList.add("d-none");
      stopButton.disabled = false;
      showResult("info", "Camera on. Hold the QR label inside the square.");
      if (cameraSelect.options.length <= 1) loadCameras();
    } catch (error) {
      camera = null;
      startButton.disabled = false;
      showResult("error", cameraErrorMessage(error));
    }
  }

  async function stopCamera() {
    if (!camera) return;
    try {
      await camera.stop();
      camera.clear();
    } catch (error) {
      /* already stopped */
    }
    camera = null;
    placeholder.classList.remove("d-none");
    startButton.disabled = false;
    stopButton.disabled = true;
  }

  startButton.addEventListener("click", startCamera);
  stopButton.addEventListener("click", function () {
    stopCamera();
    showResult("info", "Camera stopped.");
  });
  window.addEventListener("pagehide", stopCamera);

  if (!window.isSecureContext || !navigator.mediaDevices) {
    document.getElementById("insecureNotice").classList.remove("d-none");
  }
  if (typeof Html5Qrcode === "undefined") {
    startButton.disabled = true;
    showResult("error", "The scanner library did not load (no internet?). Use 'Type a code'.");
    return;
  }

  // ---------------------------------------------------------------- image file
  document.getElementById("scanFile").addEventListener("change", async function (event) {
    const file = event.target.files[0];
    if (!file) return;
    await stopCamera();
    busy = false;
    showResult("info", "Reading the image…");
    const fileScanner = new Html5Qrcode("readerFile", { verbose: false });
    try {
      const text = await fileScanner.scanFile(file, false);
      handleDecoded(text);
    } catch (error) {
      showResult("error", "No QR code found in that image. Try a sharper, closer photo.");
    } finally {
      fileScanner.clear();
      event.target.value = "";
    }
  });

  // ---------------------------------------------------------------- manual entry
  document.getElementById("manualForm").addEventListener("submit", function (event) {
    event.preventDefault();
    const ref = parseManualEntry(document.getElementById("manualCode").value);
    if (!ref) {
      showResult("error", "Type an equipment code like EQ-001 (or paste the link from a QR label).");
      return;
    }
    busy = true;
    openEquipment(ref);
  });
})();
