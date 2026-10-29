/*
 * Live updates for every page.
 *
 * 1. Opens a WebSocket to /ws/dashboard/ and receives "reading" and "alert" messages.
 * 2. If the socket fails or closes, polls the JSON API every 10 s instead and
 *    shows "Live updates: polling" in the top bar, while retrying the socket.
 * 3. Updates any element on the page that opts in with data attributes:
 *      .js-live-status[data-code]                      status badge (+ data-last-reading)
 *      [data-live-value][data-code][data-metric]       metric value (+ data-warn/data-crit for colour)
 *      [data-live-time][data-code]                     "12 s ago" text
 *      [data-kpi="healthy"]                            KPI number from the summary counts
 * Page scripts can listen with EquipWatch.live.onMessage(fn) and add their own
 * polling with EquipWatch.live.onPoll(fn).
 *
 * Testing tips: add ?transport=poll to any URL to skip WebSockets, or run
 * EquipWatch.live.close() in the DevTools console to drop the socket.
 */
(function () {
  "use strict";

  const EW = window.EquipWatch;
  const POLL_MS = 10000;
  const SUMMARY_REFRESH_MS = 30000;
  const OFFLINE_AFTER_MS = (Number(document.body.dataset.offlineAfter) || 30) * 1000;
  const indicator = document.getElementById("liveIndicator");
  const forcePolling = new URLSearchParams(location.search).get("transport") === "poll";

  const live = {
    state: "idle",
    socket: null,
    listeners: [],
    pollers: [],
    pollTimer: null,
    retryTimer: null,
    retryDelay: 2000,
    onMessage(fn) { this.listeners.push(fn); },
    onPoll(fn) { this.pollers.push(fn); },
    close() { if (this.socket) this.socket.close(); },
  };
  EW.live = live;

  // ---------------------------------------------------------------- indicator
  const LABELS = {
    idle: "Idle",
    connecting: "Connecting…",
    live: "Live",
    polling: '<span class="d-none d-md-inline">Live updates: </span>polling',
    offline: "Offline",
  };
  const TITLES = {
    idle: "Live updates: idle",
    connecting: "Live updates: connecting",
    live: "Live updates: connected (WebSocket)",
    polling: "Live updates: polling every 10 s (WebSocket unavailable)",
    offline: "Live updates: disconnected",
  };
  function setState(state) {
    live.state = state;
    if (!indicator) return;
    indicator.dataset.state = state;
    indicator.title = TITLES[state];
    const label = indicator.querySelector(".live-label");
    if (label) label.innerHTML = LABELS[state];
  }

  // ---------------------------------------------------------------- DOM bindings
  function byCode(selector, code) {
    return document.querySelectorAll(selector + '[data-code="' + CSS.escape(code) + '"]');
  }

  function toneFor(value, warn, crit) {
    if (value === null || value === undefined || isNaN(value)) return "offline";
    if (value >= crit) return "critical";
    if (value >= warn) return "warning";
    return "healthy";
  }

  function setTone(el, tone) {
    el.classList.remove("tone-healthy", "tone-warning", "tone-critical", "tone-offline");
    el.classList.add("tone-" + tone);
  }

  function applyStatus(code, status, lastReadingIso) {
    byCode(".js-live-status", code).forEach(function (holder) {
      if (lastReadingIso) holder.dataset.lastReading = lastReadingIso;
      EW.setBadge(holder.querySelector(".status-badge"), status);
    });
    if (lastReadingIso) {
      byCode("[data-live-time]", code).forEach(function (el) {
        el.dataset.time = lastReadingIso;
        el.textContent = EW.timeAgo(lastReadingIso);
      });
    }
  }

  function applyValues(code, values) {
    if (!values) return;
    byCode("[data-live-value]", code).forEach(function (el) {
      const raw = values[el.dataset.metric];
      if (raw === undefined) return;
      el.textContent = raw;
      if (el.dataset.warn) {
        const tone = toneFor(Number(raw), Number(el.dataset.warn), Number(el.dataset.crit));
        setTone(el.closest("[data-tone-target]") || el, tone);
      }
    });
  }

  function applySummary(summary) {
    if (!summary) return;
    document.querySelectorAll("[data-kpi]").forEach(function (el) {
      const value = summary[el.dataset.kpi];
      if (value === undefined || String(value) === el.textContent.trim()) return;
      el.textContent = value;
      if (el.hasAttribute("data-hide-zero")) el.hidden = Number(value) === 0;
      el.classList.remove("flash");
      void el.offsetWidth; // restart the CSS animation
      el.classList.add("flash");
    });
  }

  function applyAlert(message) {
    const a = message.alert;
    document.querySelectorAll('[data-alert-id="' + a.id + '"]').forEach(function (row) {
      EW.setBadge(row.querySelector(".js-alert-severity .status-badge"), a.severity);
      EW.setBadge(row.querySelector(".js-alert-status .status-badge"), a.status);
    });
    const notice = document.getElementById("newAlertsNotice");
    if (notice && message.event === "created") notice.hidden = false;
  }

  function dispatch(message) {
    live.listeners.forEach(function (fn) {
      try { fn(message); } catch (err) { console.error(err); }
    });
  }

  function handleMessage(message) {
    if (message.type === "reading") {
      applyStatus(message.equipment.code, message.equipment.status, message.recorded_at);
      applyValues(message.equipment.code, message.values);
    }
    if (message.type === "alert") {
      applyAlert(message);
      showAlertToast(message);
    }
    applySummary(message.summary);
    dispatch(message);
  }

  function showAlertToast(message) {
    const a = message.alert;
    const where = message.equipment.code + " (" + message.equipment.name + ")";
    const metric = a.metric.charAt(0).toUpperCase() + a.metric.slice(1);
    if (message.event === "created" || message.event === "escalated") {
      const verb = message.event === "escalated" ? "escalated to " : "";
      EW.toast(metric + " alert " + verb + a.severity + " on " + where + ". Simulated data.",
        a.severity === "CRITICAL" ? "critical" : "warning");
    } else if (message.event === "resolved") {
      EW.toast(metric + " alert resolved on " + where + ".", "healthy");
    }
  }

  // Machines whose last reading is too old turn OFFLINE without a server message.
  function markStaleMachines() {
    const now = Date.now();
    document.querySelectorAll(".js-live-status[data-last-reading]").forEach(function (holder) {
      const badge = holder.querySelector(".status-badge");
      if (!badge || badge.dataset.status === "INACTIVE" || badge.dataset.status === "OFFLINE") return;
      const last = Date.parse(holder.dataset.lastReading);
      if (!isNaN(last) && now - last > OFFLINE_AFTER_MS) EW.setBadge(badge, "OFFLINE");
    });
    document.querySelectorAll("[data-live-time][data-time]").forEach(function (el) {
      el.textContent = EW.timeAgo(el.dataset.time);
    });
  }
  setInterval(markStaleMachines, 5000);

  // ---------------------------------------------------------------- polling
  const pageHasLiveData = !!document.querySelector("[data-kpi], .js-live-status, [data-live-value]");

  async function refreshSummary() {
    const summary = await EW.api("/api/v1/dashboard/summary/");
    applySummary(summary);
    dispatch({ type: "summary", summary: summary });
  }

  async function pollBuiltIn() {
    if (document.querySelector(".js-live-status, [data-live-value]")) {
      const data = await EW.api("/api/v1/equipment/status/");
      data.results.forEach(function (item) {
        applyStatus(item.code, item.status, item.last_reading_at);
        applyValues(item.code, item.values);
      });
    }
    if (document.querySelector("[data-kpi]")) {
      await refreshSummary();
    }
  }

  async function runPollers() {
    const all = [pollBuiltIn].concat(live.pollers);
    for (const fn of all) {
      try { await fn(); } catch (err) { console.warn("Polling failed:", err.message); }
    }
  }

  function startPolling() {
    const hasWork = pageHasLiveData || live.pollers.length > 0;
    setState(hasWork ? "polling" : "offline");
    if (!hasWork || live.pollTimer) return;
    runPollers();
    live.pollTimer = setInterval(runPollers, POLL_MS);
  }

  function stopPolling() {
    clearInterval(live.pollTimer);
    live.pollTimer = null;
  }

  // ---------------------------------------------------------------- WebSocket
  function scheduleReconnect() {
    clearTimeout(live.retryTimer);
    live.retryTimer = setTimeout(connect, live.retryDelay);
    live.retryDelay = Math.min(live.retryDelay * 2, 30000);
  }

  function connect() {
    if (forcePolling || !("WebSocket" in window)) {
      startPolling();
      return;
    }
    if (live.state !== "polling") setState("connecting");
    const scheme = location.protocol === "https:" ? "wss://" : "ws://";
    let socket;
    try {
      socket = new WebSocket(scheme + location.host + "/ws/dashboard/");
    } catch (err) {
      startPolling();
      scheduleReconnect();
      return;
    }
    live.socket = socket;
    socket.onopen = function () {
      live.retryDelay = 2000;
      stopPolling();
      setState("live");
    };
    socket.onmessage = function (event) {
      let message;
      try { message = JSON.parse(event.data); } catch (err) { return; }
      handleMessage(message);
    };
    socket.onclose = function () {
      live.socket = null;
      startPolling();
      scheduleReconnect();
    };
  }

  // Keep KPI counts honest (e.g. machines going OFFLINE) even when the socket is quiet.
  setInterval(function () {
    if (live.state === "live" && document.querySelector("[data-kpi]")) {
      refreshSummary().catch(function () {});
    }
  }, SUMMARY_REFRESH_MS);

  // Page scripts load after this file; connect once they have registered their hooks.
  document.addEventListener("DOMContentLoaded", connect);
})();
