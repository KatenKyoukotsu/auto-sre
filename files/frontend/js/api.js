// Единственная точка обращения к API sre-agent.

async function json(resp) {
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export const api = {
  getHealth: () => fetch('/api/health').then(json),

  getFindings: (limit = 100) => fetch(`/api/findings?limit=${limit}`).then(json),
  ackFinding: (id) => fetch(`/api/findings/${id}/ack`, { method: 'POST' }).then(json),

  triggerScan: () => fetch('/api/trigger/scan', { method: 'POST' }).then(json),
  triggerFullScan: (start, end) =>
    fetch('/api/trigger/full-scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start, end }),
    }).then(json),

  getBlogPosts: (limit = 30) => fetch(`/api/blog?limit=${limit}`).then(json),
  getBlogStatus: () => fetch('/api/blog/status').then(json),
  triggerBlog: () => fetch('/api/trigger/blog', { method: 'POST' }).then(json),
};
