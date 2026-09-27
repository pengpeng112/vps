// API 封装：Bearer JWT；401 一律回登录页；错误统一 {code,message,request_id}
let token = localStorage.getItem('token') || ''

export function setToken(t: string) {
  token = t
  localStorage.setItem('token', t)
}
export function clearToken() {
  token = ''
  localStorage.removeItem('token')
}

export class ApiError extends Error {
  status: number
  code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function request<T = any>(method: string, path: string, body?: unknown, extraHeaders?: Record<string, string>): Promise<T> {
  const headers: Record<string, string> = { ...(extraHeaders || {}) }
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const resp = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  // 认证流程（login/totp/setup）的 401 必须留在表单上，不能清会话跳走
  if (resp.status === 401 && !path.startsWith('/api/v1/auth/')) {
    clearToken()
    location.href = '/login'
    throw new ApiError(401, 'unauthorized', '未登录或会话失效')
  }
  const text = await resp.text()
  let data: any = {}
  try {
    data = text ? JSON.parse(text) : {}
  } catch {
    data = { raw: text }
  }
  if (!resp.ok) {
    throw new ApiError(resp.status, data.code || 'error', data.message || `HTTP ${resp.status}`)
  }
  return data as T
}

export const api = {
  get: <T = any>(p: string) => request<T>('GET', p),
  post: <T = any>(p: string, b?: unknown, h?: Record<string, string>) => request<T>('POST', p, b, h),
  patch: <T = any>(p: string, b?: unknown, h?: Record<string, string>) => request<T>('PATCH', p, b, h),
  delete: <T = any>(p: string) => request<T>('DELETE', p),
}

// ---- 格式化 ----
export function fmtBytes(s: unknown): string {
  if (s === null || s === undefined || s === '') return '—'
  const n = typeof s === 'string' ? parseInt(s, 10) : Number(s)
  if (!Number.isFinite(n) || n < 0) return '—'
  if (n === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = n
  while (v >= 1000 && i < units.length - 1) {
    v /= 1000
    i++
  }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`
}

export function fmtTime(t?: string | null): string {
  if (!t) return '—'
  const d = new Date(t)
  if (isNaN(d.getTime())) return String(t)
  return d.toLocaleString('zh-CN', { hour12: false })
}

export function fmtDaysLeft(n?: number | null, expires?: string | null): string {
  if (!expires) return '永久'
  if (n == null) return '—'
  if (n < 0) return `已过期 ${-n} 天`
  if (n === 0) return '今天到期'
  return `剩余 ${n} 天`
}

export function toLocalInput(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}
