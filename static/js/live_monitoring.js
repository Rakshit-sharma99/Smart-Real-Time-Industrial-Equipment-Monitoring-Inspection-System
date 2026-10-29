/* Live Monitoring page: pick a machine card and a metric; the chart shows its last 60 readings. */
(function () {
  "use strict";

  const EW = window.EquipWatch;
  const page = document.getElementById("liveMonitoring");
  if (!page) return;

  const chart = EW.charts.createMetricChart(document.getElementById("liveChart"));
  const title = document.getElementById("liveChartTitle");
  let selected = page.dataset.selected;
  let metric = page.dataset.metric;

  function syncUi() {
    document.querySelectorAll(".live-card").forEach(function (card) {
      const isSelected = card.dataset.code === selected;
      card.classList.toggle("selected", isSelected);
      card.setAttribute("aria-pressed", isSelected ? "true" : "false");
    });
    document.querySelectorAll("[data-metric-choice]").forEach(function (button) {
      const active = button.dataset.metricChoice === metric;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    const url = new URL(location.href);
    url.searchParams.set("eq", selected);
    url.searchParams.set("metric", metric);
    history.replaceState(null, "", url);
  }

  async function loadChart() {
    if (!selected) return;
    const data = await EW.charts.loadSeries(chart, selected, metric);
    title.textContent = data.equipment.code + " · " + data.equipment.name + " — " + data.thresholds[metric].label;
  }

  function reload() {
    syncUi();
    loadChart().catch((err) => EW.toast("Could not load the chart: " + err.message, "warning"));
  }

  document.querySelectorAll(".live-card").forEach(function (card) {
    card.addEventListener("click", function (event) {
      if (event.target.closest("a")) return; // the code link opens the equipment page
      selected = card.dataset.code;
      reload();
    });
    card.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selected = card.dataset.code;
        reload();
      }
    });
  });

  document.querySelectorAll("[data-metric-choice]").forEach(function (button) {
    button.addEventListener("click", function () {
      metric = button.dataset.metricChoice;
      reload();
    });
  });

  EW.live.onMessage(function (message) {
    if (message.type === "reading" && message.equipment.code === selected) {
      EW.charts.pushPoint(chart, message.recorded_at, message.values[metric]);
    }
  });
  EW.live.onPoll(loadChart);

  reload();
})();
