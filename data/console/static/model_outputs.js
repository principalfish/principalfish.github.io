// Trend charts and bulk-delete controls for the model outputs list page.
// Reads its data from the JSON block with id "outputs-page-data".
(() => {
  const page = JSON.parse(document.getElementById("outputs-page-data").textContent);
  const trendData = page.trend_data;

  // Chart.js draws on a canvas, which the stylesheet cannot reach, so its text
  // and grid colours are read from the page's CSS variables and the charts are
  // redrawn when the colour scheme changes.
  const applyThemeColours = () => {
    const style = getComputedStyle(document.documentElement);
    window.Chart.defaults.color = style.getPropertyValue("--muted").trim() || "#666";
    window.Chart.defaults.borderColor =
      style.getPropertyValue("--row-border").trim() || "rgba(0, 0, 0, 0.1)";
  };

  const chartSpecs = [];
  const charts = [];

  const createLineChart = (canvasId, datasets, yLabel) => {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !window.Chart) return;
    chartSpecs.push({ canvas, datasets, yLabel });
  };

  const drawCharts = () => {
    charts.splice(0).forEach((chart) => chart.destroy());
    if (!window.Chart) return;
    applyThemeColours();
    chartSpecs.forEach(({ canvas, datasets, yLabel }) => {
      charts.push(
        new window.Chart(canvas, {
          type: "line",
          data: {
            labels: trendData.labels,
            datasets,
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            resizeDelay: 200,
            plugins: {
              legend: {
                position: "bottom",
              },
            },
            scales: {
              x: {
                ticks: {
                  maxTicksLimit: 12,
                },
              },
              y: {
                title: {
                  display: true,
                  text: yLabel,
                },
              },
            },
          },
        }),
      );
    });
  };

  createLineChart("seats-trend-chart", trendData.seats_datasets, page.seats_label);
  createLineChart("vote-totals-trend-chart", trendData.vote_pct_datasets, "Vote %");
  drawCharts();
  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", drawCharts);

  const selectAll = document.getElementById("select-all-models");
  const checkboxes = Array.from(document.querySelectorAll(".model-select"));
  const bulkDeleteBtn = document.getElementById("bulk-delete-btn");

  const syncButtonState = () => {
    if (!bulkDeleteBtn) return;
    bulkDeleteBtn.disabled = !checkboxes.some((box) => box.checked);
  };

  if (selectAll) {
    selectAll.addEventListener("change", () => {
      checkboxes.forEach((box) => {
        box.checked = selectAll.checked;
      });
      syncButtonState();
    });
  }

  checkboxes.forEach((box) => {
    box.addEventListener("change", () => {
      if (selectAll) {
        selectAll.checked = checkboxes.length > 0 && checkboxes.every((item) => item.checked);
      }
      syncButtonState();
    });
  });

  const bulkDeleteForm = document.getElementById("bulk-delete-form");
  if (bulkDeleteForm && bulkDeleteBtn) {
    bulkDeleteForm.addEventListener("submit", (event) => {
      // The form's inline onsubmit confirm runs first; a declined confirm must
      // not strip the ids or disable the button.
      if (event.defaultPrevented) return;
      const selected = checkboxes.filter((box) => box.checked).map((box) => box.value);
      if (selected.length === 0) {
        event.preventDefault();
        return;
      }

      bulkDeleteForm.querySelectorAll('input[name="election_ids"]').forEach((node) => node.remove());
      selected.forEach((id) => {
        const hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = "election_ids";
        hidden.value = id;
        bulkDeleteForm.appendChild(hidden);
      });

      bulkDeleteBtn.disabled = true;
      bulkDeleteBtn.textContent = "Deleting...";
    });
  }

  syncButtonState();
})();
