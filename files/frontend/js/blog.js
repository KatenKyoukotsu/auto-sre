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
  if (pollActive) return;
  pollActive = true;
  setTimeout(async () => {
    try {
      const st = await api.getBlogStatus();
      if (st.status !== 'generating') { pollActive = false; onDone(st); return; }
    } catch (e) {}
    pollActive = false;
    pollWhileGenerating(onDone);
  }, 1500);
}

// ---- появление нового поста: блоки выезжают по очереди ----

const SEEN_KEY = 'auto-sre.seen-posts';
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
let pollActive = false;

function getSeen() {
  try { return new Set(JSON.parse(sessionStorage.getItem(SEEN_KEY) || '[]')); }
  catch { return new Set(); }
}

function markSeen(ids) {
  const seen = getSeen();
  ids.forEach(id => seen.add(id));
  try { sessionStorage.setItem(SEEN_KEY, JSON.stringify([...seen])); } catch {}
}

function revealPost(post) {
  // один экземпляр текста в DOM; блоки проявляются каскадом, вся анимация ~2с
  const header = post.querySelector('header');
  const blocks = post.querySelectorAll('.post-body > *');
  [header, ...blocks].forEach((node, i) => {
    node.style.setProperty('--d', Math.min(i * 90, 2400) + 'ms');
  });
  post.classList.add('reveal');
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

  if (animate) revealPost(post);
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

let firstLoadDone = false;

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

  // typewriter — только для постов, появившихся пока страница открыта;
  // при нескольких новых за раз печатаем только самый свежий, остальные въезжают
  const seen = getSeen();
  let typedOnce = false;
  posts.forEach((p, i) => {
    const liveNew = firstLoadDone && !seen.has(p.id);
    const animate = liveNew && !typedOnce && !reducedMotion.matches;
    if (animate) typedOnce = true;
    contentBox.appendChild(renderPost(p, animate));
    if (liveNew && !animate) {
      const postEl = contentBox.lastChild;
      postEl.classList.add('card-enter');
      postEl.style.setProperty('--i', i);
    }
  });
  markSeen(posts.map(p => p.id));
  firstLoadDone = true;
}

renderSkeletons(2);

loadPosts().catch(err => {
  contentBox.textContent = '';
  contentBox.appendChild(el('div', 'empty error-line', 'Не удалось загрузить блог: ' + err.message));
});
