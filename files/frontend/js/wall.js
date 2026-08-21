// Стена аномалий: пресеты диапазона, запуск сканов, ack находок.

const fsStart = document.getElementById('fs-start');
const fsEnd = document.getElementById('fs-end');
const fsStatus = document.getElementById('fs-status');

function toLocalInput(dt) {
  const pad = n => String(n).padStart(2, '0');
  return dt.getFullYear() + '-' + pad(dt.getMonth() + 1) + '-' + pad(dt.getDate()) +
    'T' + pad(dt.getHours()) + ':' + pad(dt.getMinutes());
}

function setPreset(hours) {
  const end = new Date();
  const start = new Date(end.getTime() - hours * 3600 * 1000);
  fsStart.value = toLocalInput(start);
  fsEnd.value = toLocalInput(end);
}

function toIso(value) {
  return value ? new Date(value).toISOString().replace(/\.\d+Z$/, 'Z') : null;
}

setPreset(24);

document.querySelectorAll('.preset').forEach(btn => {
  btn.addEventListener('click', () => setPreset(parseInt(btn.dataset.hours, 10)));
});

document.getElementById('trigger-scan').addEventListener('click', async () => {
  await fetch('/api/trigger/scan', { method: 'POST' });
  location.reload();
});

document.getElementById('trigger-full-scan').addEventListener('click', async () => {
  const start = toIso(fsStart.value);
  const end = toIso(fsEnd.value);
  if (!start || !end || start >= end) {
    fsStatus.textContent = 'Укажите корректный диапазон времени.';
    fsStatus.classList.add('error');
    return;
  }
  fsStatus.textContent = 'Запущено...';
  fsStatus.classList.remove('error');
  const resp = await fetch('/api/trigger/full-scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ start, end }),
  });
  const data = await resp.json();
  fsStatus.textContent = data.message || (resp.ok ? 'Запущено' : 'Ошибка');
  if (!resp.ok) fsStatus.classList.add('error');
});

document.querySelectorAll('.ack').forEach(btn => {
  btn.addEventListener('click', async () => {
    await fetch('/api/findings/' + btn.dataset.id + '/ack', { method: 'POST' });
    btn.closest('article').classList.add('acknowledged');
    btn.disabled = true;
  });
});
