// Markdown -> безопасный DOM. Контент блога генерирует LLM, поэтому
// прогон через DOMPurify обязателен.

export function renderMarkdown(target, text) {
  let html;
  try {
    html = window.marked.parse(text || '');
  } catch (e) {
    html = null;
  }
  if (html === null || html === undefined) {
    target.textContent = text || '';
    return;
  }
  target.innerHTML = window.DOMPurify.sanitize(html);
}
