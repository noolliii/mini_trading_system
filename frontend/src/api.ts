const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

export type Device = { device_id: string; name: string; location: string | null }
export type Point = { t: string; v: number }
export type Series = { device_id: string; name: string; points: Point[] }
export type SeriesResponse = {
  metric: string
  unit: string | null
  resolution: 'raw' | '1m'
  series: Series[]
}
export type LatestReading = {
  device_id: string
  name: string
  metric: string
  value: number
  time: string
}

export type Me = { id: number; username: string; role: string; permissions: string[] }
export type LoginResult = { username: string; role: string; expires_at: string }
export type User = {
  id: number
  username: string
  role: string
  created_at: string
  account_count: number
}
export type Account = {
  account_no: string
  user_id: number
  username: string
  broker: string
  mode: string
  created_at: string
}
export type TokenIssue = {
  mode: string
  host: string
  issued_at: string
  expires_in: number
  token_preview: string
}

/**
 * 서버가 나눠 보낸 실패를 그대로 들고 다닙니다.
 * invalid_credentials · session_expired · forbidden 셋을 화면에서 구분하기 위함입니다.
 */
export class ApiError extends Error {
  status: number
  code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    // 세션 쿠키를 같이 보냅니다. 토큰을 자바스크립트가 들고 있지 않습니다
    credentials: 'include',
    ...init,
  })

  if (!res.ok) {
    let code = 'http_error'
    let message = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      const detail = body?.detail
      if (detail && typeof detail === 'object') {
        code = detail.code ?? code
        message = detail.message ?? message
      } else if (typeof detail === 'string') {
        message = detail
      }
    } catch {
      // 본문이 JSON 이 아니면 상태 줄을 그대로 씁니다
    }
    throw new ApiError(res.status, code, message)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

// ── 로그인 ─────────────────────────────────────────────
export const login = (username: string, password: string) =>
  request<LoginResult>('/api/login', json({ username, password }))
export const logout = () => request<void>('/api/logout', { method: 'POST' })
export const getMe = () => request<Me>('/api/me')

// ── 사용자 (admin) ─────────────────────────────────────
export const getUsers = () => request<User[]>('/api/users')
export const createUser = (username: string, password: string, role: string) =>
  request<User>('/api/users', json({ username, password, role }))
export const deleteUser = (id: number) =>
  request<void>(`/api/users/${id}`, { method: 'DELETE' })

// ── 계좌 ───────────────────────────────────────────────
export const getAccounts = () => request<Account[]>('/api/accounts')
export const createAccount = (body: {
  account_no: string
  broker: string
  mode: string
  user_id?: number
}) => request<Account>('/api/accounts', json(body))
export const deleteAccount = (accountNo: string) =>
  request<void>(`/api/accounts/${encodeURIComponent(accountNo)}`, { method: 'DELETE' })

// ── KIS 토큰 (admin) ───────────────────────────────────
export const issueKisToken = (mode: 'paper' | 'live') =>
  request<TokenIssue>(`/api/kis/token?mode=${mode}`, { method: 'POST' })

// ── 센서 ───────────────────────────────────────────────
export const getMetrics = () => request<string[]>('/api/metrics')
export const getDevices = () => request<Device[]>('/api/devices')
export const getLatest = (metric: string) =>
  request<LatestReading[]>(`/api/latest?metric=${encodeURIComponent(metric)}`)
export const getSeries = (metric: string, hours: number) =>
  request<SeriesResponse>(`/api/series?metric=${encodeURIComponent(metric)}&hours=${hours}`)
