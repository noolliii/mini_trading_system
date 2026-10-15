import { useCallback, useEffect, useMemo, useState } from 'react'

import LineChart, { SERIES_COLORS } from './LineChart'
import type { ChartSeries } from './LineChart'
import { ApiError, getBacktestDayNight } from './api'
import type { BacktestResult, Me } from './api'

type Props = {
  me: Me
  onSessionLost: () => void
  ticker: string
}

const RANGES = [
  { days: 90, label: '3개월' },
  { days: 365, label: '1년' },
  { days: 1095, label: '3년' },
]

function pct(v: number): string {
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

/**
 * 백테스트 — 한 종목의 낮(장중) vs 밤(종가 배팅) vs 단순보유 누적수익률.
 *
 * R_day = 종가/시가 - 1, R_night = 시가/전일종가 - 1. DB 에 적재된 일봉만
 * 보고, 계산은 서버(backtest.py)의 순수 함수가 합니다 — 조회라 누구나 봅니다.
 *
 * 종목은 「시세」탭 맨 위에서 고른 걸 그대로 받습니다 — 여기서 따로 고르지 않습니다.
 */
export default function Backtest({ me, onSessionLost, ticker }: Props) {
  const [days, setDays] = useState(365)
  const [data, setData] = useState<BacktestResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handle = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.code === 'session_expired') {
        onSessionLost()
        return
      }
      setError(e instanceof ApiError ? e.message : (e as Error).message)
    },
    [onSessionLost],
  )

  const load = useCallback(async () => {
    try {
      setData(await getBacktestDayNight(ticker, days))
      setError(null)
    } catch (e) {
      handle(e)
    }
  }, [ticker, days, handle])

  useEffect(() => {
    void load()
  }, [load])

  const series: ChartSeries[] = useMemo(() => {
    if (!data || data.points.length === 0) return []
    return [
      { key: 'night', name: '밤(종가 배팅)', points: data.points.map((p) => ({ t: p.time, v: p.cum_night })) },
      { key: 'day', name: '낮(장중)', points: data.points.map((p) => ({ t: p.time, v: p.cum_day })) },
      { key: 'hold', name: '단순보유', points: data.points.map((p) => ({ t: p.time, v: p.cum_buy_hold })) },
    ]
  }, [data])

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>백테스트 · 낮 vs 밤</h2>
        <span className="hint">
          R_day = 종가/시가 - 1 · R_night = 시가/전일종가 - 1 · 적재된 일봉만 봅니다
        </span>
      </div>

      <div className="filters">
        <label className="field">
          <span>기간</span>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {RANGES.map((r) => (
              <option key={r.days} value={r.days}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
        <div className="spacer" />
      </div>

      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}

      {data && data.points.length > 0 && (
        <div className="tiles">
          <div className="tile">
            <div className="tile-label">
              <span className="swatch" style={{ background: SERIES_COLORS[0] }} />
              밤 누적수익률
            </div>
            <div className="tile-value">{pct(data.summary.final_night)}</div>
            <div className="tile-time">승률 {(data.summary.win_rate_night * 100).toFixed(1)}%</div>
          </div>
          <div className="tile">
            <div className="tile-label">
              <span className="swatch" style={{ background: SERIES_COLORS[1] }} />
              낮 누적수익률
            </div>
            <div className="tile-value">{pct(data.summary.final_day)}</div>
            <div className="tile-time">승률 {(data.summary.win_rate_day * 100).toFixed(1)}%</div>
          </div>
          <div className="tile">
            <div className="tile-label">
              <span className="swatch" style={{ background: SERIES_COLORS[2] }} />
              단순보유
            </div>
            <div className="tile-value">{pct(data.summary.final_buy_hold)}</div>
            <div className="tile-time">{data.name} 전체 보유</div>
          </div>
        </div>
      )}

      {!data || data.points.length === 0 ? (
        <div className="state">
          적재된 일봉이 없습니다.
          {me.permissions.includes('market:write')
            ? ' 「시세」 탭에서 먼저 수집하세요.'
            : ' admin 이 수집해야 보입니다.'}
        </div>
      ) : (
        <div className="chart-wrap">
          <LineChart series={series} unit="%" />
        </div>
      )}
    </section>
  )
}
