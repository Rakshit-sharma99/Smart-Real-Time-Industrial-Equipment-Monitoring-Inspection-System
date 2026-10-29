/* Overview page: health doughnut + temperature trend. KPIs and the status table update via live.js. */
(function () {
  "use strict";

  const EW = window.EquipWatch;
  const initial = JSON.parse(document.getElementById("dashboard-data").textContent);

  const doughnut = EW.charts.createHealthDoughnut(document.getElementById("healthChart"), initial.summary);

  const select = document.getElementById("trendMachine");
  const trendCanvas = document.getElementById("trendChart");
  const trend = trendCanvas ? EW.charts.createMetricChart(trendCanvas) : null;
  let selected = select ? select.value : null;

  async function loadTrend() {
    if (!trend || !selected) return;
    await EW.charts.loadSeries(trend, selected, "temperature");
  }

  if (select) {
    select.addEventListener("change", function () {
      selected = select.value;
      loadTrend().catch((err) => EW.toast("Could not load the trend: " + err.message, "warning"));
    });
  }

  // New alerts go to the top of the "Recent alerts" list (built with DOM calls, no HTML strings).
  const recentList = document.getElementById("recentAlerts");
  function badge(status) {
    const wrap = document.createElement("span");
    const el = document.createElement("span");
    el.innerHTML = '<i class="bi" aria-hidden="true"></i><span class="status-text"></span>';
    wrap.append(el);
    EW.setBadge(el, status);
    return wrap;
  }
  function prependAlert(message) {
    if (!recentList) return;
    const a = message.alert;
    const li = document.createElement("li");
    li.className = "list-group-item alert-row";
    li.dataset.alertId = a.id;
    const left = document.createElement("div");
    left.className = "min-w-0";
    const link = document.createElement("a");
    link.className = "fw-semibold";
    link.href = "/alerts/" + a.id + "/";
    link.textContent = "AL-" + String(a.id).padStart(5, "0");
    const what = document.createElement("span");
    what.textContent = " " + message.equipment.code + " · " + a.metric.charAt(0).toUpperCase() + a.metric.slice(1);
    const when = document.createElement("div");
    when.className = "small text-body-secondary";
    when.textContent = "just now";
    left.append(link, what, when);
    const right = document.createElement("div");
    right.className = "text-end d-flex flex-column align-items-end gap-1";
    const severity = badge(a.severity);
    severity.className = "js-alert-severity";
    const status = badge(a.status);
    status.className = "js-alert-status";
    right.append(severity, status);
    const row = document.createElement("div");
    row.className = "d-flex justify-content-between gap-2";
    row.append(left, right);
    li.append(row);
    recentList.prepend(li);
    while (recentList.children.length > 6) recentList.lastElementChild.remove();
  }

  EW.live.onMessage(function (message) {
    if (message.type === "alert" && message.event === "created") prependAlert(message);
    if (message.summary) EW.charts.updateHealthDoughnut(doughnut, message.summary);
    if (trend && message.type === "reading" && message.equipment.code === selected) {
      EW.charts.pushPoint(trend, message.recorded_at, message.values.temperature);
    }
  });
  EW.live.onPoll(loadTrend);

  loadTrend().catch(() => {});
})();
