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

  // Progressive Web App: the service worker caches static files only (see /sw.js).
  if ("serviceWorker" in navigator && window.isSecureContext) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
  }
})();
