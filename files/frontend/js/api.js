// Единственная точка обращения к API sre-agent.

const TIMEOUT_MS = 10000;

async function request(url, opts = {}) {
  // без таймаута зависший запрос оставит скелетоны навсегда
  const resp = await fetch(url, { ...opts, signal: AbortSignal.timeout(TIMEOUT_MS) });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export const api = {
  getHealth: () => request('/api/health'),

  getFindings: (limit = 100) => request(`/api/findings?limit=${limit}`),
  ackFinding: (id) => request(`/api/findings/${id}/ack`, { method: 'POST' }),

  triggerScan: () => request('/api/trigger/scan', { method: 'POST' }),
  triggerFullScan: (start, end) =>
    request('/api/trigger/full-scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start, end }),
    }),

  getBlogPosts: (limit = 30) => request(`/api/blog?limit=${limit}`),
  getBlogStatus: () => request('/api/blog/status'),
  triggerBlog: () => request('/api/trigger/blog', { method: 'POST' }),
};
