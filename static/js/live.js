/*
 * Real-time synchronisation for every page.
 *
 * Pages mark the parts that must stay current as "live fragments":
 *     <div data-live-fragment="/floor/live/?section=2" data-live-topics="tables reservations">...</div>
 *
 * 1. A WebSocket to /ws/live/ receives small events such as
 *      {"type": "table.changed", "id": "...", "topics": ["tables"]}
 *    and every fragment subscribed to one of those topics is re-fetched from
 *    the server (an ordinary, permission-checked view) and swapped in.
 * 2. Duplicate events (same id) are ignored, and an older response never
 *    overwrites a newer one, so the screen never shows a stale state.
 * 3. If the socket fails, all fragments are polled every 10 s and the top bar
 *    says "Live updates: polling"; the socket is retried with back-off.
 *
 * Testing tips: add ?transport=poll to any URL, or run RestaurantSpy.live.close()
 * in the DevTools console.
 */
(function () {
  "use strict";

  const RS = window.RestaurantSpy;
  const POLL_MS = 10000;
  const REFRESH_WHILE_LIVE_MS = 60000; // time-based states (Reserved, overdue, "5 min ago")
  const indicator = document.getElementById("liveIndicator");
  const forcePolling = new URLSearchParams(location.search).get("transport") === "poll";
  const seenEvents = [];

  const live = {
    state: "idle",
    socket: null,
    pollTimer: null,
    retryTimer: null,
    retryDelay: 2000,
    close() { if (this.socket) this.socket.close(); },
    refreshAll() { refresh(null); },
  };
  RS.live = live;

  // ------------------------------------------------------------------ indicator
  const LABELS = {
    idle: "Idle", connecting: "Connecting…", live: "Live",
    polling: '<span class="d-none d-md-inline">Live updates: </span>polling', offline: "Offline",
  };
  const TITLES = {
    idle: "Live updates: idle", connecting: "Live updates: connecting",
    live: "Live updates: connected (WebSocket)",
    polling: "Live updates: polling every 10 s (WebSocket unavailable)", offline: "Live updates: disconnected",
  };
  function setState(state) {
    live.state = state;
    if (!indicator) return;
    indicator.dataset.state = state;
    indicator.title = TITLES[state];
    const label = indicator.querySelector(".live-label");
    if (label) label.innerHTML = LABELS[state];
  }

  // ------------------------------------------------------------------ fragments
  function fragments() {
    return Array.from(document.querySelectorAll("[data-live-fragment]"));
  }

  function wants(el, topics) {
    if (!topics) return true;
    const mine = (el.dataset.liveTopics || "").split(/\s+/);
    return topics.some((topic) => mine.includes(topic));
  }

  async function reload(el) {
    el._sequence = (el._sequence || 0) + 1;
    const sequence = el._sequence;
    let response;
    try {
      response = await fetch(el.dataset.liveFragment, {
        credentials: "same-origin", redirect: "manual", headers: { "X-Requested-With": "fetch" },
      });
    } catch (err) {
      return; // network down: the next poll tries again
    }
    if (response.type === "opaqueredirect" || response.status === 401 || response.status === 403) {
      location.reload(); // session expired or role changed: let the server decide where to go
      return;
    }
    if (!response.ok || sequence !== el._sequence) return; // failed, or a newer request is under way
    el.innerHTML = await response.text();
    if (el.matches(".notification-menu")) syncBell(el);
    el.dispatchEvent(new CustomEvent("rs:updated", { bubbles: true }));
  }

  const pending = new Set();
  let flushTimer = null;
  function refresh(topics) {
    fragments().filter((el) => wants(el, topics)).forEach((el) => pending.add(el));
    clearTimeout(flushTimer);
    flushTimer = setTimeout(function () {  // coalesce bursts of events into one fetch per fragment
      const batch = Array.from(pending);
      pending.clear();
      batch.forEach(reload);
    }, 250);
  }

  function syncBell(menu) {
    const holder = menu.querySelector("[data-unread]");
    const badge = document.querySelector("[data-notification-count]");
    if (!holder || !badge) return;
    const count = Number(holder.dataset.unread) || 0;
    badge.textContent = count;
    badge.hidden = count === 0;
  }

  // ------------------------------------------------------------------ events
  function handle(message) {
    if (!message || !message.id || seenEvents.includes(message.id)) return; // duplicate
    seenEvents.push(message.id);
    if (seenEvents.length > 200) seenEvents.shift();
    if (message.type === "notification") {
      RS.toast(message.title, message.message, message.kind && message.kind.indexOf("COMPLETED") >= 0 ? "success" : "info");
    }
    refresh(message.topics || null);
  }

  // ------------------------------------------------------------------ polling
  function startPolling() {
    setState("polling");
    if (live.pollTimer) return;
    refresh(null);
    live.pollTimer = setInterval(function () { refresh(null); }, POLL_MS);
  }

  function stopPolling() {
    clearInterval(live.pollTimer);
    live.pollTimer = null;
  }

  // ------------------------------------------------------------------ WebSocket
  function scheduleReconnect() {
    clearTimeout(live.retryTimer);
    live.retryTimer = setTimeout(connect, live.retryDelay);
    live.retryDelay = Math.min(live.retryDelay * 2, 30000);
  }

  function connect() {
    if (forcePolling || !("WebSocket" in window)) { startPolling(); return; }
    if (live.state !== "polling") setState("connecting");
    let socket;
    try {
      socket = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/live/");
    } catch (err) {
      startPolling();
      scheduleReconnect();
      return;
    }
    live.socket = socket;
    socket.onopen = function () {
      live.retryDelay = 2000;
      const wasPolling = live.pollTimer !== null;
      stopPolling();
      setState("live");
      if (wasPolling) refresh(null); // catch up on anything missed while offline
    };
    socket.onmessage = function (event) {
      try { handle(JSON.parse(event.data)); } catch (err) { /* ignore malformed */ }
    };
    socket.onclose = function () {
      live.socket = null;
      startPolling();
      scheduleReconnect();
    };
  }

  setInterval(function () { if (live.state === "live") refresh(null); }, REFRESH_WHILE_LIVE_MS);
  document.addEventListener("DOMContentLoaded", connect);
})();
