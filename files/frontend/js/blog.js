// Блог: поллинг статуса генерации и typewriter-анимация последнего поста.

(function () {
  const BLOG_STATUS = document.body.dataset.blogStatus;

  if (BLOG_STATUS === 'generating') {
    (function poll() {
      setTimeout(async function () {
        try {
          const r = await fetch('/api/blog/status');
          const st = await r.json();
          if (st.status !== 'generating') { location.reload(); return; }
        } catch (e) {}
        poll();
      }, 1500);
    })();
  }

  document.getElementById('trigger-blog').addEventListener('click', function () {
    fetch('/api/trigger/blog', { method: 'POST' }).then(function () { location.reload(); });
  });

  // ---- печать ответа по буквам, как будто AI рисует текст ----
  const posts = document.querySelectorAll('[data-post]');
  if (!posts.length) return;

  const caret = document.createElement('span');
  caret.className = 'typing-caret';

  function typeTitle(el, text, done) {
    el.textContent = '';
    let i = 0;
    (function step() {
      if (i >= text.length) { done(); return; }
      el.textContent = text.slice(0, ++i);
      el.appendChild(caret);
      const ch = text[i - 1];
      let d = 34 + Math.random() * 26;
      if ('.,!?;:»…—'.indexOf(ch) !== -1) d += 120;
      setTimeout(step, d);
    })();
  }

  function collectTextNodes(root) {
    const nodes = [];
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (w.nextNode()) {
      const n = w.currentNode;
      if (n.textContent.replace(/\s/g, '').length) nodes.push(n);
    }
    return nodes;
  }

  function typeBody(root, done) {
    const nodes = collectTextNodes(root);
    let idx = 0, pos = 0;
    (function step() {
      if (idx >= nodes.length) {
        caret.remove();
        done();
        return;
      }
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
        isBlockEnd = ['P', 'LI', 'H1', 'H2', 'H3', 'H4', 'PRE', 'BLOCKQUOTE'].indexOf(tag) !== -1;
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

    const typer = document.createElement('div');
    typer.className = 'post-type';
    body.parentNode.insertBefore(typer, body);

    const root = document.createElement('div');
    root.className = 'post-body';
    root.innerHTML = fullHtml;
    typer.appendChild(root);

    const titleText = titleEl ? titleEl.textContent.trim() : '';
    if (titleEl) {
      typeTitle(titleEl, titleText, function () {
        typeBody(root, function () {
          typer.remove();
          body.style.display = '';
          body.classList.add('fade-in');
        });
      });
    } else {
      typeBody(root, function () {
        typer.remove();
        body.style.display = '';
        body.classList.add('fade-in');
      });
    }
  }

  animatePost(posts[0]);
})();
