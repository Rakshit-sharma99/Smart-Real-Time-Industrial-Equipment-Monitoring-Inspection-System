/* EquipWatch shared front-end helpers (no build step, plain JavaScript). */
(function () {
  "use strict";

  const EquipWatch = (window.EquipWatch = window.EquipWatch || {});

  /** CSRF token for fetch() POSTs, read from <meta name="csrf-token">. */
  EquipWatch.csrfToken = function () {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  };

  /** fetch() wrapper for our JSON API. Throws an Error with the API's message on failure. */
  EquipWatch.api = async function (url, options) {
    const opts = Object.assign({ method: "GET", headers: {} }, options || {});
    opts.headers = Object.assign({ Accept: "application/json" }, opts.headers);
    opts.credentials = "same-origin";
    if (opts.body && typeof opts.body !== "string") {
      opts.body = JSON.stringify(opts.body);
      opts.headers["Content-Type"] = "application/json";
    }
    if (opts.method !== "GET") {
      opts.headers["X-CSRFToken"] = EquipWatch.csrfToken();
    }
    const response = await fetch(url, opts);
    let data = null;
    try {
      data = await response.json();
    } catch (e) {
      data = null;
    }
    if (!response.ok) {
      const message = data && data.error ? data.error : "Request failed (" + response.status + ")";
      const error = new Error(message);
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  };

  /** Small Bootstrap toast in the bottom-right corner. tone: healthy|warning|critical|info */
  EquipWatch.toast = function (message, tone) {
    const stack = document.getElementById("toastStack");
    if (!stack || !window.bootstrap) return;
    const el = document.createElement("div");
    el.className = "toast ew-toast tone-" + (tone || "info");
    el.setAttribute("role", "status");
    const body = document.createElement("div");
    body.className = "d-flex";
    const text = document.createElement("div");
    text.className = "toast-body";
    text.textContent = message;
    const close = document.createElement("button");
    close.type = "button";
    close.className = "btn-close me-2 m-auto";
    close.setAttribute("data-bs-dismiss", "toast");
    close.setAttribute("aria-label", "Close");
    body.append(text, close);
    el.append(body);
    stack.append(el);
    const toast = new bootstrap.Toast(el, { delay: 5000 });
    el.addEventListener("hidden.bs.toast", () => el.remove());
    toast.show();
  };

  // Status -> tone, mirrors core/templatetags/ui.py for badges updated live.
  const TONES = {
    HEALTHY: "healthy", WARNING: "warning", CRITICAL: "critical", OFFLINE: "offline", INACTIVE: "offline",
    ACTIVE: "critical", ACKNOWLEDGED: "warning", RESOLVED: "healthy",
  };
  const ICONS = {
    healthy: "bi-check-circle-fill", warning: "bi-exclamation-triangle-fill", critical: "bi-x-octagon-fill",
    offline: "bi-dash-circle-fill", info: "bi-arrow-repeat", neutral: "bi-circle",
  };
  EquipWatch.toneFor = (status) => TONES[status] || "neutral";

  /** Update a .status-badge element in place. */
  EquipWatch.setBadge = function (badge, status, label) {
    if (!badge) return;
    const tone = EquipWatch.toneFor(status);
    badge.className = "status-badge tone-" + tone;
    badge.dataset.status = status;
    const icon = badge.querySelector(".bi");
    if (icon) icon.className = "bi " + ICONS[tone];
    const text = badge.querySelector(".status-text");
    if (text) text.textContent = label || status.charAt(0) + status.slice(1).toLowerCase().replace(/_/g, " ");
  };

  /** "12 s ago" style text. */
  EquipWatch.timeAgo = function (isoString) {
    if (!isoString) return "never";
    const seconds = Math.max(0, Math.round((Date.now() - new Date(isoString).getTime()) / 1000));
    if (seconds < 60) return seconds + " s ago";
    if (seconds < 3600) return Math.round(seconds / 60) + " min ago";
    if (seconds < 86400) return Math.round(seconds / 3600) + " h ago";
    return Math.round(seconds / 86400) + " d ago";
  };

  // ---------------------------------------------------------------------------
  // Confirm modal: <form data-confirm="Question?" data-confirm-button="Delete">
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
      if (form.requestSubmit) {
        form.requestSubmit(submitter);
      } else {
        form.submit();
      }
    };
    modal.show();
  });

  // Prevent double submits of forms marked data-once (e.g. "Submit inspection").
  document.addEventListener("submit", function (event) {
    const form = event.target;
    if (event.defaultPrevented || !form.hasAttribute || !form.hasAttribute("data-once")) return;
    if (form.dataset.submitting === "1") {
      event.preventDefault();
      return;
    }
    form.dataset.submitting = "1";
  });
})();
