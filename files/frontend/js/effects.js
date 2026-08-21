// Тосты и состояния загрузки кнопок.

let toastContainer;

function ensureContainer() {
  if (!toastContainer) {
    toastContainer = document.createElement('div');
    toastContainer.className = 'toast-container';
    document.body.appendChild(toastContainer);
  }
  return toastContainer;
}

export function toast(message, type = 'info', timeout = 4000) {
  const box = document.createElement('div');
  box.className = 'toast ' + type;
  box.textContent = message;
  ensureContainer().appendChild(box);
  setTimeout(() => {
    box.classList.add('leaving');
    setTimeout(() => box.remove(), 350);
  }, timeout);
}

export function setButtonLoading(btn, loading) {
  btn.disabled = loading;
  btn.classList.toggle('is-loading', loading);
}
