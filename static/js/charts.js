/* Chart.js helpers shared by the Overview and Live Monitoring pages. */
(function () {
  "use strict";

  const EW = window.EquipWatch;
  const MAX_POINTS = 60;
  const COLORS = {
    line: "#2563eb",
    fill: "rgba(37, 99, 235, 0.08)",
    warning: "#d97706",
    critical: "#dc2626",
    healthy: "#16a34a",
    offline: "#9ca3af",
    grid: "#eef0f3",
    text: "#6b7280",
  };

  function timeLabel(iso) {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  /** Line chart for one metric, with dashed warning and critical limit lines. */
  function createMetricChart(canvas) {
    const chart = new Chart(canvas, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          { label: "Value", data: [], borderColor: COLORS.line, backgroundColor: COLORS.fill,
            borderWidth: 2, pointRadius: 0, pointHitRadius: 8, tension: 0.25, fill: true },
          { label: "Warning", data: [], borderColor: COLORS.warning, borderDash: [6, 4],
            borderWidth: 1.5, pointRadius: 0, fill: false },
          { label: "Critical", data: [], borderColor: COLORS.critical, borderDash: [6, 4],
            borderWidth: 1.5, pointRadius: 0, fill: false },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { ticks: { maxTicksLimit: 6, color: COLORS.text, maxRotation: 0 }, grid: { display: false } },
          y: { ticks: { color: COLORS.text }, grid: { color: COLORS.grid } },
        },
        plugins: { legend: { position: "bottom", labels: { boxWidth: 14, boxHeight: 2, color: COLORS.text } } },
      },
    });
    chart.$limits = null;
    chart.$lastTime = 0;
    return chart;
  }

  /** Replace all points. points = [{recorded_at, value}], info = {label, unit, warn, crit}. */
  function setSeries(chart, points, info) {
    const warn = Number(info.warn);
    const crit = Number(info.crit);
    chart.$limits = { warn: warn, crit: crit };
    chart.data.labels = points.map((p) => timeLabel(p.recorded_at));
    chart.data.datasets[0].label = info.label + " (" + info.unit + ")";
    chart.data.datasets[0].data = points.map((p) => Number(p.value));
    chart.data.datasets[1].label = "Warning " + warn;
    chart.data.datasets[1].data = points.map(() => warn);
    chart.data.datasets[2].label = "Critical " + crit;
    chart.data.datasets[2].data = points.map(() => crit);
    chart.$lastTime = points.length ? Date.parse(points[points.length - 1].recorded_at) : 0;
    chart.update();
  }

  /** Append one live point (ignores duplicates and out-of-order points). */
  function pushPoint(chart, recordedAt, value) {
    const time = Date.parse(recordedAt);
    if (!chart.$limits || time <= chart.$lastTime) return;
    chart.$lastTime = time;
    chart.data.labels.push(timeLabel(recordedAt));
    chart.data.datasets[0].data.push(Number(value));
    chart.data.datasets[1].data.push(chart.$limits.warn);
    chart.data.datasets[2].data.push(chart.$limits.crit);
    if (chart.data.labels.length > MAX_POINTS) {
      chart.data.labels.shift();
      chart.data.datasets.forEach((ds) => ds.data.shift());
    }
    chart.update("none");
  }

  /** Load the last 60 readings of one machine/metric from the API into a chart. */
  async function loadSeries(chart, code, metric) {
    const data = await EW.api("/api/v1/equipment/" + encodeURIComponent(code) + "/readings/?limit=" + MAX_POINTS);
    const points = data.readings.map((r) => ({ recorded_at: r.recorded_at, value: r[metric] }));
    setSeries(chart, points, data.thresholds[metric]);
    return data;
  }

  /** Doughnut of machine health: healthy / warning / critical / offline. */
  function createHealthDoughnut(canvas, summary) {
    return new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: ["Healthy", "Warning", "Critical", "Offline"],
        datasets: [{
          data: healthCounts(summary),
          backgroundColor: [COLORS.healthy, COLORS.warning, COLORS.critical, COLORS.offline],
          borderWidth: 2,
          borderColor: "#ffffff",
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "68%",
        animation: { duration: 300 },
        plugins: { legend: { position: "bottom", labels: { boxWidth: 12, color: COLORS.text } } },
      },
    });
  }

  function healthCounts(summary) {
    return [summary.healthy, summary.warning, summary.critical, summary.offline];
  }

  function updateHealthDoughnut(chart, summary) {
    if (!summary || summary.healthy === undefined) return;
    chart.data.datasets[0].data = healthCounts(summary);
    chart.update();
  }

  EW.charts = { createMetricChart, setSeries, pushPoint, loadSeries, createHealthDoughnut, updateHealthDoughnut };
})();
