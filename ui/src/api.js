const req = async (baseUrl, method, path, body = null, params = {}) => {
  const fullUrl = new URL(baseUrl)
  fullUrl.pathname = path
  for (const [k, v] of Object.entries(params)) {
    if (v != null && v !== '') fullUrl.searchParams.set(k, String(v))
  }
  const opts = { method }
  if (body) {
    opts.headers = { 'Content-Type': 'application/json' }
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(fullUrl.toString(), opts)
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status}: ${text}`)
  }
  if (res.status === 204) return null
  return res.json()
}

export const api = {
  health: (host) =>
    req(host, 'GET', '/health'),

  index: (host, dbPath, limit = 500) =>
    req(host, 'GET', '/index', null, { db_path: dbPath, limit }),

  get: (host, id, dbPath) =>
    req(host, 'GET', `/documents/${id}`, null, { db_path: dbPath }),

  create: (host, data) =>
    req(host, 'POST', '/documents', data),

  update: (host, id, data) =>
    req(host, 'PATCH', `/documents/${id}`, data),

  delete: (host, id, dbPath) =>
    req(host, 'DELETE', `/documents/${id}`, null, { db_path: dbPath }),

  search: (host, query, opts = {}) =>
    req(host, 'POST', '/search', { query, limit: 20, ...opts }),
}
