/* RestaurantSpy shared front-end helpers (no build step, plain JavaScript). */
(function () {
  "use strict";

  const RS = (window.RestaurantSpy = window.RestaurantSpy || {});

  /** CSRF token for fetch() POSTs, read from <meta name="csrf-token">. */
  RS.csrfToken = function () {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  };

  /** fetch() wrapper for our JSON API. Throws an Error carrying the API's message on failure. */
  RS.api = async function (url, options) {
    const opts = Object.assign({ method: "GET", headers: {} }, options || {});
    opts.headers = Object.assign({ Accept: "application/json" }, opts.headers);
    opts.credentials = "same-origin";
    if (opts.body && typeof opts.body !== "string") {
      opts.body = JSON.stringify(opts.body);
      opts.headers["Content-Type"] = "application/json";
    }
    if (opts.method !== "GET") opts.headers["X-CSRFToken"] = RS.csrfToken();
    const response = await fetch(url, opts);
    let data = null;
    try { data = await response.json(); } catch (e) { data = null; }
    if (!response.ok) {
      const error = new Error(data && data.error ? data.error : "Request failed (" + response.status + ")");
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  };

  /** Small toast in the bottom-right corner. tone: success | danger | info */
  RS.toast = function (title, message, tone) {
    const stack = document.getElementById("toastStack");
    if (!stack || !window.bootstrap) return;
    const el = document.createElement("div");
    el.className = "toast rs-toast tone-" + (tone || "info");
    el.setAttribute("role", "status");
    const row = document.createElement("div");
    row.className = "d-flex";
    const body = document.createElement("div");
    body.className = "toast-body";
    const strong = document.createElement("strong");
    strong.textContent = title;
    body.append(strong);
    if (message) {
      const small = document.createElement("div");
      small.className = "small text-body-secondary";
      small.textContent = message;
      body.append(small);
    }
    const close = document.createElement("button");
    close.type = "button";
    close.className = "btn-close me-2 m-auto";
    close.setAttribute("data-bs-dismiss", "toast");
    close.setAttribute("aria-label", "Close");
    row.append(body, close);
    el.append(row);
    stack.append(el);
    const toast = new bootstrap.Toast(el, { delay: 6000 });
    el.addEventListener("hidden.bs.toast", () => el.remove());
    toast.show();
  };

  // ---------------------------------------------------------------------------
  // Confirm modal: <form data-confirm="Question?" data-confirm-button="Cancel booking">
  // The data-confirm* attributes may also sit on one submit button only.
  // ---------------------------------------------------------------------------
  document.addEventListener("submit", function (event) {
    const form = event.target;
    const submitter = event.submitter || null;
    const source = submitter && submitter.dataset.confirm ? submitter : form;
    if (!source.dataset || !source.dataset.confirm || form.dataset.confirmed === "1") return;
    const modalEl = document.getElementById("confirmModal");
    if (!modalEl || !window.bootstrap) return; // fall back to a plain submit
    event.preventDefault();
    document.getElementById("confirmModalTitle").textContent = source.dataset.confirmTitle || "Please confirm";
    document.getElementById("confirmModalText").textContent = source.dataset.confirm;
    const ok = document.getElementById("confirmModalOk");
    ok.textContent = source.dataset.confirmButton || "Confirm";
    ok.className = "btn " + (source.dataset.confirmTone === "primary" ? "btn-primary" : "btn-danger");
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    ok.onclick = function () {
      form.dataset.confirmed = "1";
      modal.hide();
      if (form.requestSubmit) form.requestSubmit(submitter); else form.submit();
    };
    modal.show();
  });

  // Prevent double submits of forms marked data-once (e.g. "Confirm booking").
  document.addEventListener("submit", function (event) {
    const form = event.target;
    if (event.defaultPrevented || !form.hasAttribute || !form.hasAttribute("data-once")) return;
    if (form.dataset.submitting === "1") { event.preventDefault(); return; }
    form.dataset.submitting = "1";
  });

  /** 42 -> "42 min", 125 -> "2 h 5 min", 3000 -> "2 days". Mirrors format_minutes in core/templatetags/ui.py. */
  RS.formatMinutes = function (minutes) {
    minutes = Math.max(0, Math.floor(minutes));
    if (minutes < 60) return minutes + " min";
    if (minutes < 1440) {
      const h = Math.floor(minutes / 60), m = minutes % 60;
      return m ? h + " h " + m + " min" : h + " h";
    }
    const days = Math.floor(minutes / 1440);
    return days + (days === 1 ? " day" : " days");
  };

  // Seated / dirty timers on table cards keep counting between live refreshes.
  function tickTimers() {
    const now = Date.now();
    document.querySelectorAll("[data-timer]").forEach(function (el) {
      const minutes = (now - new Date(el.dataset.since).getTime()) / 60000;
      const sinceConfirmed = (now - new Date(el.dataset.confirmed || el.dataset.since).getTime()) / 60000;
      const limit = Number(el.dataset.limit), stale = Number(el.dataset.stale);
      const level = sinceConfirmed >= stale ? "stale" : minutes >= limit ? "late" : minutes >= limit * 0.75 ? "warn" : "ok";
      el.className = el.className.replace(/\blevel-\w+/, "level-" + level);
      const card = el.closest(".table-tile");
      if (card) card.classList.toggle("over-limit", level === "late" || level === "stale");
      const text = el.querySelector("[data-timer-text]");
      if (text) {
        const length = RS.formatMinutes(minutes);
        text.textContent = el.hasAttribute("data-timer-prefix") ? (el.dataset.kind === "seated" ? "Seated · " : "Dirty · ") + length : length;
      }
    });
  }
  setInterval(tickTimers, 30000);

  // Progressive Web App: the service worker caches static files only (see /sw.js).
  if ("serviceWorker" in navigator && window.isSecureContext) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
  }
})();
