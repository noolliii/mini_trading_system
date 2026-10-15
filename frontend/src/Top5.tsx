import { useCallback, useEffect, useMemo, useState } from 'react'

import LineChart, { SERIES_COLORS } from './LineChart'
import type { ChartSeries } from './LineChart'
import { ApiError, getBacktestTop5, getStocks } from './api'
import type { Me, Stock, Top5Result, Top5Strategy } from './api'

type Props = {
  me: Me
  onSessionLost: () => void
}

const STRATEGIES: { value: Top5Strategy; label: string; hint: string }[] = [
  { value: 'momentum', label: '모멘텀 (고가마감)', hint: 'Score = (종가-저가) / (고가-저가)' },
  { value: 'mean_reversion', label: '역추세 (낙폭과대)', hint: 'Score = -(종가/시가 - 1)' },
  { value: 'volume_surge', label: '거래량폭발', hint: 'Score = (거래량/20일평균) · I(양봉)' },
  { value: 'rolling_edge', label: '롤링엣지 (야간샤프)', hint: 'Score = 20일 밤수익률 평균/표준편차' },
]

const RANGES = [
  { days: 90, label: '3개월' },
  { days: 365, label: '1년' },
]

function pct(v: number): string {
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

/**
 * Top-K 전략 — 매일 유니버스에서 점수 상위 K종목을 골라 종가 배팅.
 *
 * 점수는 그날 자신의 데이터(모멘텀 · 역추세 · 거래량폭발) 또는 그 전까지의
 * 과거 밤수익률(롤링엣지)만 씁니다 — 서버(selection.py)가 미래의 값을
 * 들여다보지 않게 막아 둔 걸 그대로 보여줄 뿐입니다.
 */
export default function Top5({ me, onSessionLost }: Props) {
  const [strategy, setStrategy] = useState<Top5Strategy>('momentum')
  const [days, setDays] = useState(365)
  const [k, setK] = useState(5)
  const [costPct, setCostPct] = useState(0.1) // 화면은 %, 서버는 소수(0.001)
  const [data, setData] = useState<Top5Result | null>(null)
  const [stocks, setStocks] = useState<Stock[]>([])
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

  useEffect(() => {
    getStocks().then(setStocks).catch(handle)
  }, [handle])

  const names = useMemo(() => new Map(stocks.map((s) => [s.ticker, s.name])), [stocks])

  const load = useCallback(async () => {
    try {
      setData(await getBacktestTop5(strategy, days, k, costPct / 100))
      setError(null)
    } catch (e) {
      handle(e)
    }
  }, [strategy, days, k, costPct, handle])

  useEffect(() => {
    void load()
  }, [load])

  const series: ChartSeries[] = useMemo(() => {
    if (!data || data.points.length === 0) return []
    return [
      { key: 'portfolio', name: `Top-${k} 전략`, points: data.points.map((p) => ({ t: p.time, v: p.cum_portfolio })) },
      { key: 'universe', name: '유니버스 평균', points: data.points.map((p) => ({ t: p.time, v: p.cum_universe })) },
    ]
  }, [data, k])

  const latestPicks = data ? data.picks.slice(-5).reverse() : []
  const chosen = STRATEGIES.find((s) => s.value === strategy)!

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Top-K 전략</h2>
        <span className="hint">점수는 당일 15:20까지 알 수 있는 정보만 씁니다 · 미래참조 없음</span>
      </div>

      <div className="filters">
        <label className="field">
          <span>전략</span>
          <select value={strategy} onChange={(e) => setStrategy(e.target.value as Top5Strategy)}>
            {STRATEGIES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
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
        <label className="field">
          <span>K (몇 종목)</span>
          <input
            type="number"
            min={1}
            max={20}
            value={k}
            onChange={(e) => setK(Math.min(20, Math.max(1, Number(e.target.value) || 1)))}
            style={{ width: 70 }}
          />
        </label>
        <label className="field">
          <span>왕복비용(%)</span>
          <input
            type="number"
            min={0}
            max={5}
            step={0.01}
            value={costPct}
            onChange={(e) => setCostPct(Math.min(5, Math.max(0, Number(e.target.value) || 0)))}
            style={{ width: 80 }}
          />
        </label>
        <div className="spacer" />
      </div>

      <div className="hint" style={{ marginBottom: 14 }}>
        {chosen.hint}
      </div>

      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}

      {data && data.points.length > 0 ? (
        <>
          <div className="tiles">
            <div className="tile">
              <div className="tile-label">
                <span className="swatch" style={{ background: SERIES_COLORS[0] }} />
                {chosen.label} 누적
              </div>
              <div className="tile-value">{pct(data.summary.final_portfolio)}</div>
              <div className="tile-time">승률 {(data.summary.win_rate * 100).toFixed(1)}%</div>
            </div>
            <div className="tile">
              <div className="tile-label">
                <span className="swatch" style={{ background: SERIES_COLORS[1] }} />
                유니버스 평균
              </div>
              <div className="tile-value">{pct(data.summary.final_universe)}</div>
              <div className="tile-time">전략과 같은 기간</div>
            </div>
            <div className="tile">
              <div className="tile-label">초과수익</div>
              <div className="tile-value">
                {pct(data.summary.final_portfolio - data.summary.final_universe)}
              </div>
              <div className="tile-time">거래 {data.summary.trading_nights}일 밤</div>
            </div>
          </div>

          <div className="chart-wrap" style={{ marginBottom: 20 }}>
            <LineChart series={series} unit="%" />
          </div>

          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>종배 대상일</th>
                  <th>고른 종목</th>
                </tr>
              </thead>
              <tbody>
                {latestPicks.length === 0 && (
                  <tr>
                    <td colSpan={2} className="empty">
                      선택 기록이 없습니다.
                    </td>
                  </tr>
                )}
                {latestPicks.map((p) => (
                  <tr key={p.time}>
                    <td className="mono">{new Date(p.time).toLocaleDateString('ko-KR')}</td>
                    <td>
                      {p.tickers.length === 0
                        ? '(이 날은 조건을 만족한 종목이 없었습니다)'
                        : p.tickers.map((t) => names.get(t) ?? t).join(', ')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div className="state">
          적재된 일봉이 모자랍니다. 「유니버스」탭에서 코스피 100을 먼저 채워 주세요.
        </div>
      )}
    </section>
  )
}
