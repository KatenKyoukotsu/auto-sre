// Стена аномалий: статус продакшена, список находок, запуск сканов.

import { api } from './api.js';
import { formatDate, formatConfidence } from './format.js';

const statusBox = document.getElementById('status');
const findingsBox = document.getElementById('findings');

const fsStart = document.getElementById('fs-start');
const fsEnd = document.getElementById('fs-end');
const fsStatus = document.getElementById('fs-status');

// ---- статус-бар ----

function renderStatus(health) {
  statusBox.textContent = '';
  const dot = document.createElement('span');
  dot.className = 'status-dot';

  const info = document.createElement('div');
  const strong = document.createElement('strong');
  strong.textContent = 'Состояние продакшена';

  const meta = document.createElement('div');
  meta.className = 'status-meta';
  meta.append(`Модель: ${health.model} · Последний скан: ${health.last_scan || 'ещё не выполнялся'}`);

  if (health.last_error) {
    const err = document.createElement('div');
    err.className = 'error-line';
    err.textContent = `Ошибка скана: ${health.last_error}`;
    meta.appendChild(err);
  }

  info.append(strong, meta);
  statusBox.append(dot, info);
}

// ---- карточки находок ----

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null && text !== '') node.textContent = text;
  return node;
}

function labeled(label, value) {
  const p = el('p');
  p.append(el('strong', null, label + ' '), value);
  return p;
}

function renderFinding(f) {
  const card = el('article', `card severity-${f.severity}` + (f.acknowledged ? ' acknowledged' : ''));

  const head = el('div', 'card-head');
  head.append(
    el('span', 'badge sev', f.severity),
    el('h2', null, f.title),
    el('time', null, formatDate(f.created_at)),
  );
  card.appendChild(head);

  if (f.service) {
    const service = el('div', 'service', 'Сервис: ');
    service.appendChild(el('code', null, f.service));
    card.appendChild(service);
  }

  card.appendChild(el('p', 'summary', f.summary));
  if (f.possible_cause) card.appendChild(labeled('Вероятная причина:', f.possible_cause));
  if (f.recommended_action) card.appendChild(labeled('Рекомендуемое действие:', f.recommended_action));

  const confidence = formatConfidence(f.confidence);
  if (confidence) card.appendChild(el('div', 'meta', `Доверие модели: ${confidence}`));

  const actions = el('div', 'card-actions');
  const ackBtn = el('button', 'btn small ack', 'Подтвердить');
  ackBtn.dataset.id = f.id;
  if (f.acknowledged) ackBtn.disabled = true;
  actions.appendChild(ackBtn);
  card.appendChild(actions);

  return card;
}

function renderFindings(findings) {
  findingsBox.textContent = '';
  if (!findings.length) {
    findingsBox.appendChild(
      el('div', 'empty', 'Аномалий не обнаружено. Прод в порядке (или ещё не сканировался).'),
    );
    return;
  }
  for (const f of findings) findingsBox.appendChild(renderFinding(f));
}

// ack через делегирование — карточки перерисовываются динамически
findingsBox.addEventListener('click', async (e) => {
  const btn = e.target.closest('.ack');
  if (!btn || btn.disabled) return;
  await api.ackFinding(btn.dataset.id);
  btn.closest('article').classList.add('acknowledged');
  btn.disabled = true;
});

// ---- полный скан ----

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
  await api.triggerScan();
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
  try {
    const data = await api.triggerFullScan(start, end);
    fsStatus.textContent = data.message || 'Запущено';
  } catch (err) {
    fsStatus.textContent = 'Ошибка';
    fsStatus.classList.add('error');
  }
});

// ---- начальная загрузка ----

Promise.all([api.getHealth(), api.getFindings(100)])
  .then(([health, findings]) => {
    renderStatus(health);
    renderFindings(findings);
  })
  .catch(err => {
    findingsBox.textContent = '';
    findingsBox.appendChild(el('div', 'empty error-line', 'Не удалось загрузить данные: ' + err.message));
  });
