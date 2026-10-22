const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

export type Stock = { ticker: string; name: string; market: string }
export type Candle = {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}
export type CandleSeries = { ticker: string; name: string; candles: Candle[] }
export type CollectResult = {
  ticker: string
  mode: string
  fetched: number
  inserted: number
  first: string | null
  last: string | null
}
export type TradingDay = { date: string; is_open: boolean }
export type CalendarCollectResult = { year: number; fetched: number; inserted: number }

// ── 백테스트 ───────────────────────────────────────────
export type BacktestPoint = { time: string; cum_day: number; cum_night: number; cum_buy_hold: number }
export type BacktestSummary = {
  final_day: number
  final_night: number
  final_buy_hold: number
  win_rate_day: number
  win_rate_night: number
}
export type BacktestResult = { ticker: string; name: string; points: BacktestPoint[]; summary: BacktestSummary }

export type UniverseImportResult = { total: number; inserted: number }
export type UniverseTickerSummary = {
  ticker: string
  name: string
  final_day: number
  final_night: number
  final_buy_hold: number
  win_rate_day: number
  win_rate_night: number
  candle_count: number
}
export type UniverseResult = { tickers: UniverseTickerSummary[]; points: BacktestPoint[] }

export type Top5Strategy =
  | 'momentum'
  | 'mean_reversion'
  | 'volume_surge'
  | 'rolling_edge'
  | 'factor_combo'
  | 'vol_filtered_edge'
  | 'vol_surge_trend'
  | 'vol_surge_edge'
export type Top5Point = { time: string; cum_portfolio: number; cum_universe: number }
export type Top5Summary = {
  final_portfolio: number
  final_universe: number
  win_rate: number
  trading_nights: number
  mdd: number
  sharpe: number
  calmar: number
  cagr: number
  // 유니버스(동일가중 전체 종목) 벤치마크 쪽 지표 — 전략과 무관하게 같은 기간이면 항상 같습니다
  universe_mdd: number
  universe_sharpe: number
  universe_calmar: number
  universe_cagr: number
}
export type Top5Pick = { time: string; tickers: string[] }
export type Top5Result = {
  strategy: Top5Strategy
  points: Top5Point[]
  summary: Top5Summary
  picks: Top5Pick[]
}
export type Top5LeaderboardRow = { strategy: Top5Strategy; k: number; summary: Top5Summary }
export type Top5Leaderboard = { rows: Top5LeaderboardRow[] }

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
  expires_at: string
  expires_in: number
  token_preview: string
  /** true 면 서버가 캐시한 토큰을 재사용했고, KIS 로 발급 요청이 나가지 않았습니다 */
  cached: boolean
}

export type UserUpdate = {
  username?: string
  role?: string
  new_password?: string
}

export type MeUpdate = {
  current_password: string
  username?: string
  new_password?: string
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

const json = (body: unknown, method = 'POST'): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

// ── 로그인 ─────────────────────────────────────────────
export const login = (username: string, password: string) =>
  request<LoginResult>('/api/login', json({ username, password }))
export const logout = () => request<void>('/api/logout', { method: 'POST' })
// 가입과 동시에 로그인됩니다 — 서버가 세션 쿠키를 바로 심습니다
export const signup = (username: string, password: string) =>
  request<LoginResult>('/api/signup', json({ username, password }))
export const getMe = () => request<Me>('/api/me')

// ── 내 정보 ────────────────────────────────────────────
export const updateMe = (body: MeUpdate) => request<Me>('/api/me', json(body, 'PATCH'))

// ── 사용자 (admin) ─────────────────────────────────────
export const getUsers = () => request<User[]>('/api/users')
export const createUser = (username: string, password: string, role: string) =>
  request<User>('/api/users', json({ username, password, role }))
export const updateUser = (id: number, body: UserUpdate) =>
  request<User>(`/api/users/${id}`, json(body, 'PATCH'))
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
// force 를 켜면 캐시를 무시하고 새로 발급받습니다
export const issueKisToken = (mode: 'paper' | 'live', force = false) =>
  request<TokenIssue>(`/api/kis/token?mode=${mode}&force=${force}`, { method: 'POST' })

// ── 종목 · 일봉 ────────────────────────────────────────
export const getStocks = (q?: string) =>
  request<Stock[]>(`/api/stocks${q ? `?q=${encodeURIComponent(q)}` : ''}`)
// 종목을 하나씩 직접 더합니다 — admin 만. kospi100.csv 를 다시 고치지 않아도 됩니다
export const createStock = (body: { ticker: string; name: string; market: string }) =>
  request<Stock>('/api/stocks', json(body))
export const getCandles = (ticker: string, days: number) =>
  request<CandleSeries>(`/api/candles?ticker=${ticker}&days=${days}`)
// 수집은 admin 만. KIS 로 나가는 호출이라 서버가 다시 막습니다
export const collectCandles = (ticker: string, mode: 'paper' | 'live', days: number) =>
  request<CollectResult>(`/api/stocks/${ticker}/collect?mode=${mode}&days=${days}`, {
    method: 'POST',
  })
// 마지막 적재일 다음 거래일부터만 받습니다 — 전체를 다시 받지 않습니다
export const collectIncremental = (ticker: string, mode: 'paper' | 'live') =>
  request<CollectResult>(`/api/stocks/${ticker}/collect-incremental?mode=${mode}`, {
    method: 'POST',
  })
// 거래일 캘린더. 조회(DB 만 봄)는 누구나, 수집(KIS 실전 도메인)은 admin 만
export const getTradingDays = (year: number) =>
  request<TradingDay[]>(`/api/trading-days?year=${year}`)
export const collectTradingDays = (year: number) =>
  request<CalendarCollectResult>(`/api/trading-days/collect?year=${year}`, { method: 'POST' })

// ── 백테스트 — 낮(장중) vs 밤(종가 배팅) ────────────────
export const getBacktestDayNight = (ticker: string, days: number) =>
  request<BacktestResult>(`/api/backtest/day-night?ticker=${ticker}&days=${days}`)

// 번들에 들어 있는 코스피 100 종목을 stocks 테이블에 더합니다 — admin 만
export const importUniverse = () =>
  request<UniverseImportResult>('/api/stocks/import-universe', { method: 'POST' })
// tickers 를 비우면 전체 종목, 주면 그 종목들만 골라 평균을 냅니다
export const getBacktestUniverse = (days: number, tickers?: string[]) =>
  request<UniverseResult>(
    `/api/backtest/universe?days=${days}` +
      (tickers && tickers.length > 0 ? `&tickers=${tickers.join(',')}` : ''),
  )

// start/end 를 주면 days 대신 그 기간으로 조회합니다(기간 지정 기능)
export const getBacktestTop5 = (
  strategy: Top5Strategy,
  days: number,
  k: number,
  cost: number,
  range?: { start?: string; end?: string },
) =>
  request<Top5Result>(
    `/api/backtest/top5?strategy=${strategy}&days=${days}&k=${k}&cost=${cost}` +
      (range?.start ? `&start=${range.start}` : '') +
      (range?.end ? `&end=${range.end}` : ''),
  )

export const getBacktestTop5Leaderboard = (
  days: number,
  cost: number,
  range?: { start?: string; end?: string },
) =>
  request<Top5Leaderboard>(
    `/api/backtest/top5-leaderboard?days=${days}&cost=${cost}` +
      (range?.start ? `&start=${range.start}` : '') +
      (range?.end ? `&end=${range.end}` : ''),
  )
