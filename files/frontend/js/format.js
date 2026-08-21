// Форматирование данных для отображения.

const dateTimeFmt = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit', month: '2-digit', year: 'numeric',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
});

export function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d) ? String(iso) : dateTimeFmt.format(d);
}

export function formatConfidence(value) {
  if (value === null || value === undefined) return null;
  return Math.round(value * 100) + '%';
}
