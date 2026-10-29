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

  EW.live.onMessage(function (message) {
    if (message.summary) EW.charts.updateHealthDoughnut(doughnut, message.summary);
    if (trend && message.type === "reading" && message.equipment.code === selected) {
      EW.charts.pushPoint(trend, message.recorded_at, message.values.temperature);
    }
  });
  EW.live.onPoll(loadTrend);

  loadTrend().catch(() => {});
})();
