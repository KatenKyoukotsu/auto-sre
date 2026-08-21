// Стена аномалий: статус продакшена, живой список находок, запуск сканов.

import { api } from './api.js';
import { formatDate, formatConfidence } from './format.js';
import { toast, setButtonLoading } from './effects.js';

const POLL_INTERVAL_MS = 15000;

const statusBox = document.getElementById('status');
const findingsBox = document.getElementById('findings');

const fsStart = document.getElementById('fs-start');
const fsEnd = document.getElementById('fs-end');
const fsStatus = document.getElementById('fs-status');

// id -> { acknowledged } уже отрисованных находок
const rendered = new Map();

// ---- утилиты DOM ----

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

// ---- скелетоны ----

function renderSkeletons(box, count) {
  box.textContent = '';
  for (let i = 0; i < count; i++) {
    const card = el('div', 'skeleton-card');
    card.append(
      el('div', 'skeleton-line w-30'),
      el('div', 'skeleton-line w-90'),
      el('div', 'skeleton-line w-60'),
    );
    box.appendChild(card);
  }
}

// ---- статус-бар ----

const LLM_LABELS = {
  ok: 'доступна',
  failing: 'сбои',
  open: 'недоступна',
};

function llmLabel(llm) {
  if (llm.reachable === false) return 'недоступна';
  return LLM_LABELS[llm.state] || llm.state || 'неизвестно';
}

function renderStatus(health) {
  statusBox.textContent = '';
  const dot = el('span', 'status-dot');

  const llm = health.llm || {};
  const degraded = llm.reachable === false || (llm.state && llm.state !== 'ok') || health.last_error;
  if (degraded) dot.classList.add('warn');

  const info = el('div');
  info.appendChild(el('strong', null, 'Состояние продакшена'));

  const meta = el('div', 'status-meta');
  meta.append(
    `Модель: ${llm.model || health.model} — ${llmLabel(llm)}`,
    ` · Последний скан: ${health.last_scan ? formatDate(health.last_scan) : 'ещё не выполнялся'}`,
  );
  if (llm.last_ok) {
    meta.append(` · LLM отвечала: ${formatDate(llm.last_ok)}`);
  }
  if (llm.last_error) {
    meta.appendChild(el('div', 'error-line', `LLM: ${llm.last_error}`));
  }
  if (health.last_error) {
    meta.appendChild(el('div', 'error-line', `Скан: ${health.last_error}`));
  }

  info.appendChild(meta);
  statusBox.append(dot, info);
}

// ---- карточки находок ----

function renderFinding(f, isNew) {
  const card = el('article', `card severity-${f.severity}` + (f.acknowledged ? ' acknowledged' : '') + (isNew ? ' card-enter' : ''));

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

function appendFindings(findings, markNew) {
  // скелетоны — только для момента первой загрузки
  findingsBox.querySelectorAll('.skeleton-card').forEach(n => n.remove());
  const emptyBox = findingsBox.querySelector('.empty');
  if (!findings.length) {
    if (!rendered.size && !emptyBox) {
      findingsBox.appendChild(
        el('div', 'empty', 'Аномалий не обнаружено. Прод в порядке (или ещё не сканировался).'),
      );
    }
    return;
  }
  if (emptyBox) emptyBox.remove();

  // API отдаёт находки по убыванию времени; новые вставляем сверху в исходном порядке
  const newCards = [];
  for (const f of findings) {
    const known = rendered.get(f.id);
    if (known) {
      if (!known.acknowledged && f.acknowledged) {
        known.acknowledged = true;
        const card = findingsBox.querySelector(`article[data-id="${f.id}"]`);
        if (card) {
          card.classList.add('acknowledged');
          const btn = card.querySelector('.ack');
          if (btn) btn.disabled = true;
        }
      }
      continue;
    }
    rendered.set(f.id, { acknowledged: !!f.acknowledged });
    const card = renderFinding(f, markNew);
    card.dataset.id = f.id;
    newCards.push(card);
  }

  const anchor = findingsBox.firstChild;
  newCards.forEach((card, i) => {
    card.style.setProperty('--i', Math.min(i, 8));
    findingsBox.insertBefore(card, anchor);
  });
}

// ack через делегирование — карточки перерисовываются динамически
findingsBox.addEventListener('click', async (e) => {
  const btn = e.target.closest('.ack');
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  try {
    await api.ackFinding(btn.dataset.id);
    btn.closest('article').classList.add('acknowledged');
  } catch (err) {
    toast('Не удалось подтвердить находку: ' + err.message, 'error');
    btn.disabled = false;
  }
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

document.getElementById('trigger-scan').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  setButtonLoading(btn, true);
  try {
    await api.triggerScan();
    toast('Скан аномалий запущен');
  } catch (err) {
    toast('Не удалось запустить скан: ' + err.message, 'error');
  } finally {
    setButtonLoading(btn, false);
  }
});

document.getElementById('trigger-full-scan').addEventListener('click', async (e) => {
  const start = toIso(fsStart.value);
  const end = toIso(fsEnd.value);
  if (!start || !end || start >= end) {
    fsStatus.textContent = 'Укажите корректный диапазон времени.';
    fsStatus.classList.add('error');
    return;
  }
  const btn = e.currentTarget;
  setButtonLoading(btn, true);
  fsStatus.textContent = 'Запущено...';
  fsStatus.classList.remove('error');
  try {
    const data = await api.triggerFullScan(start, end);
    fsStatus.textContent = data.message || 'Запущено';
    toast('Полное сканирование запущено');
  } catch (err) {
    fsStatus.textContent = 'Ошибка';
    fsStatus.classList.add('error');
  } finally {
    setButtonLoading(btn, false);
  }
});

// ---- загрузка и живой поллинг ----

renderSkeletons(findingsBox, 3);

let firstLoad = true;
let failures = 0;

function setPollError(message) {
  let box = findingsBox.querySelector('.poll-error');
  if (!message) {
    if (box) box.remove();
    return;
  }
  if (!box) {
    box = el('div', 'poll-error');
    findingsBox.prepend(box);
  }
  box.textContent = message;
}

async function pollTick() {
  try {
    const [health, findings] = await Promise.all([api.getHealth(), api.getFindings(100)]);
    failures = 0;
    setPollError(null);
    renderStatus(health);
    appendFindings(findings, !firstLoad);
    firstLoad = false;
  } catch (err) {
    // тишина хуже честного баннера: показываем сразу, снимаем при первом успехе
    failures += 1;
    if (failures >= 1) setPollError('Нет связи с сервером: ' + err.message);
  }
}

setInterval(() => pollTick(), POLL_INTERVAL_MS);
pollTick();
