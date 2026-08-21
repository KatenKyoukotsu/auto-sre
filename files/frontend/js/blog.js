// Блог: рендер постов из API, поллинг статуса генерации, typewriter.

import { api } from './api.js';
import { formatDate } from './format.js';
import { renderMarkdown } from './markdown.js';
import { toast, setButtonLoading } from './effects.js';

const contentBox = document.getElementById('blog-content');

// ---- утилиты DOM ----

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null && text !== '') node.textContent = text;
  return node;
}

// ---- панель «AI пишет» ----

function renderPending(error) {
  const panel = el('section', 'ai-pending');
  const head = el('div', 'ai-pending-head');
  head.append(el('span', 'ai-spinner'), el('strong', null, 'Auto SRE пишет ответ…'));

  const body = el('div', 'ai-pending-body');
  body.append(
    el('span', 'ai-ghost', 'Собираю инциденты за сутки, ищу закономерности, формулирую дайджест…'),
    el('div', 'ai-dots'),
  );
  body.querySelector('.ai-dots').append(el('span'), el('span'), el('span'));

  panel.append(head, body);
  if (error) panel.appendChild(el('div', 'error-line', 'Ошибка: ' + error));
  return panel;
}

function pollWhileGenerating(onDone) {
  setTimeout(async () => {
    try {
      const st = await api.getBlogStatus();
      if (st.status !== 'generating') { onDone(st); return; }
    } catch (e) {}
    pollWhileGenerating(onDone);
  }, 1500);
}

// ---- typewriter (пока проигрывается для последнего поста при каждой загрузке;
// в фазе 5 ограничим «живыми» новыми постами) ----

const caret = document.createElement('span');
caret.className = 'typing-caret';

function typeTitle(elm, text, done) {
  elm.textContent = '';
  let i = 0;
  (function step() {
    if (i >= text.length) { done(); return; }
    elm.textContent = text.slice(0, ++i);
    elm.appendChild(caret);
    let d = 34 + Math.random() * 26;
    if ('.,!?;:»…—'.indexOf(text[i - 1]) !== -1) d += 120;
    setTimeout(step, d);
  })();
}

function collectTextNodes(root) {
  const nodes = [];
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) {
    if (w.currentNode.textContent.replace(/\s/g, '').length) nodes.push(w.currentNode);
  }
  return nodes;
}

function typeBody(root, done) {
  const nodes = collectTextNodes(root);
  let idx = 0, pos = 0;
  (function step() {
    if (idx >= nodes.length) { caret.remove(); done(); return; }
    const node = nodes[idx];
    const full = node.textContent;
    node.textContent = full.slice(0, pos + 1);
    node.parentNode.insertBefore(caret, node.nextSibling);
    pos++;
    let isBlockEnd = false;
    if (pos >= full.length) {
      idx++;
      pos = 0;
      const tag = node.parentNode ? node.parentNode.tagName : '';
      isBlockEnd = ['P', 'LI', 'H1', 'H2', 'H3', 'H4', 'PRE', 'BLOCKQUOTE'].includes(tag);
    }
    let d = 24 + Math.random() * 22;
    const ch = node.textContent[pos - 1] || '';
    if ('.!?…'.indexOf(ch) !== -1) d += 180;
    if (isBlockEnd) d += 220;
    setTimeout(step, d);
  })();
}

function animatePost(post) {
  const titleEl = post.querySelector('.post-title');
  const body = post.querySelector('.post-body');
  if (!body) return;
  const fullHtml = body.innerHTML;
  body.style.display = 'none';

  const typer = el('div', 'post-type');
  const root = el('div', 'post-body');
  root.innerHTML = fullHtml;
  typer.appendChild(root);
  body.parentNode.insertBefore(typer, body);

  const finish = () => {
    typer.remove();
    body.style.display = '';
    body.classList.add('fade-in');
  };
  const typeText = () => typeBody(root, finish);

  if (titleEl) typeTitle(titleEl, titleEl.textContent.trim(), typeText);
  else typeText();
}

// ---- рендер постов ----

function renderPost(p, animate) {
  const post = el('article', 'post');
  post.dataset.id = p.id;

  const header = el('header');
  header.append(el('h2', 'post-title', p.title), el('time', null, formatDate(p.created_at)));
  post.appendChild(header);

  const body = el('div', 'post-body');
  renderMarkdown(body, p.content);
  post.appendChild(body);

  if (animate) animatePost(post);
  return post;
}

// ---- скелетоны ----

function renderSkeletons(count) {
  contentBox.textContent = '';
  for (let i = 0; i < count; i++) {
    const card = el('div', 'skeleton-card');
    card.append(
      el('div', 'skeleton-line w-60'),
      el('div', 'skeleton-line w-90'),
      el('div', 'skeleton-line w-90'),
      el('div', 'skeleton-line w-30'),
    );
    contentBox.appendChild(card);
  }
}

// ---- кнопка генерации ----

document.getElementById('trigger-blog').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  setButtonLoading(btn, true);
  try {
    await api.triggerBlog();
    toast('Генерация поста запущена');
    contentBox.textContent = '';
    contentBox.appendChild(renderPending());
    pollWhileGenerating(onGenerationDone);
  } catch (err) {
    toast('Не удалось запустить генерацию: ' + err.message, 'error');
  } finally {
    setButtonLoading(btn, false);
  }
});

function onGenerationDone(status) {
  if (status && status.error) toast('Ошибка генерации поста: ' + status.error, 'error', 8000);
  else toast('Новый пост опубликован');
  loadPosts();
}

// ---- загрузка ----

async function loadPosts() {
  const [status, posts] = await Promise.all([api.getBlogStatus(), api.getBlogPosts(30)]);
  contentBox.textContent = '';

  if (status.status === 'generating') {
    contentBox.appendChild(renderPending(status.error));
    pollWhileGenerating(onGenerationDone);
  }

  if (!posts.length) {
    if (status.status !== 'generating') {
      contentBox.appendChild(el('div', 'empty', 'Постов пока нет. Первый дайджест появится после сканa.'));
    }
    return;
  }

  posts.forEach((p, i) => contentBox.appendChild(renderPost(p, i === 0)));
}

renderSkeletons(2);

loadPosts().catch(err => {
  contentBox.textContent = '';
  contentBox.appendChild(el('div', 'empty error-line', 'Не удалось загрузить блог: ' + err.message));
});
