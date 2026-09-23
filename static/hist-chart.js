// Price-history line charts for every <canvas class="hist-chart" data-history=...>.
// Shared by the dashboard cards and the cruise detail pages.
(function () {
  document.querySelectorAll('.hist-chart').forEach(function (canvas) {
    let hist = [];
    try {
      hist = JSON.parse(canvas.dataset.history || '[]');
    } catch (e) {
      return;
    }
    if (!hist.length || typeof Chart === 'undefined') return;

    const labels = hist.map(function (h) {
      if (!h.checked_at) return h.date_short || '';
      const d = new Date(h.checked_at);
      return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    });
    const prices = hist.map(function (h) { return h.price; });
    // Single-point charts need a flat line to render nicely
    const chartLabels = prices.length === 1 ? [labels[0], labels[0]] : labels;
    const chartPrices = prices.length === 1 ? [prices[0], prices[0]] : prices;

    new Chart(canvas, {
      type: 'line',
      data: {
        labels: chartLabels,
        datasets: [{
          label: 'Price',
          data: chartPrices,
          borderColor: '#2ec4b6',
          backgroundColor: 'rgba(46, 196, 182, 0.16)',
          borderWidth: 2.5,
          fill: true,
          tension: 0.35,
          pointRadius: chartPrices.length <= 8 ? 4 : 0,
          pointHoverRadius: 6,
          pointBackgroundColor: '#7fdbda',
          pointBorderColor: '#0a3d5c',
          pointBorderWidth: 1.5,
          shadowOffsetX: 0,
          shadowOffsetY: 0,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: 'rgba(10, 61, 92, 0.92)',
            titleFont: { size: 12 },
            bodyFont: { size: 13, weight: '600' },
            padding: 10,
            displayColors: false,
            callbacks: {
              label: function (ctx) {
                return '$' + Number(ctx.raw).toLocaleString(undefined, {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2
                });
              }
            }
          }
        },
        scales: {
          x: {
            ticks: {
              color: '#5a6d7e',
              maxTicksLimit: 5,
              font: { size: 10, weight: '500' }
            },
            grid: { display: false },
            border: { display: false }
          },
          y: {
            ticks: {
              color: '#5a6d7e',
              font: { size: 10, weight: '500' },
              callback: function (v) { return '$' + v; },
              maxTicksLimit: 4
            },
            grid: { color: 'rgba(15, 76, 117, 0.08)' },
            border: { display: false }
          }
        }
      }
    });
  });
})();
