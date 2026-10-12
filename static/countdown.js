// Live countdown for <div id="countdown" data-departure="ISO date"> with
// [data-unit=days|hours|mins|secs] children. Same behaviour as the Tahiti
// dashboard's countdown.
(function () {
  const root = document.getElementById('countdown');
  if (!root) return;
  const target = new Date(root.dataset.departure).getTime();
  const els = {};
  ['days', 'hours', 'mins', 'secs'].forEach(function (u) {
    els[u] = root.querySelector('[data-unit="' + u + '"]');
  });

  function tick() {
    let diff = Math.max(0, target - Date.now());
    const days = Math.floor(diff / 86400000);
    diff -= days * 86400000;
    const hours = Math.floor(diff / 3600000);
    diff -= hours * 3600000;
    const mins = Math.floor(diff / 60000);
    diff -= mins * 60000;
    const secs = Math.floor(diff / 1000);
    els.days.textContent = days;
    els.hours.textContent = String(hours).padStart(2, '0');
    els.mins.textContent = String(mins).padStart(2, '0');
    els.secs.textContent = String(secs).padStart(2, '0');
  }
  tick();
  setInterval(tick, 1000);
})();
