import { useCallback, useEffect, useMemo, useState } from 'react'

import LineChart, { SERIES_COLORS } from './LineChart'
import type { ChartSeries } from './LineChart'
import { ApiError, getBacktestTop5, getBacktestTop5Leaderboard, getStocks } from './api'
import type { Me, Stock, Top5Result, Top5Strategy, Top5Summary } from './api'

type Props = {
  me: Me
  onSessionLost: () => void
}

const STRATEGIES: { value: Top5Strategy; label: string; hint: string }[] = [
  { value: 'momentum', label: '모멘텀 (고가마감)', hint: 'Score = (종가-저가) / (고가-저가)' },
  { value: 'mean_reversion', label: '역추세 (낙폭과대)', hint: 'Score = -(종가/시가 - 1)' },
  { value: 'volume_surge', label: '거래량폭발', hint: 'Score = (거래량/20일평균) · I(양봉)' },
  { value: 'rolling_edge', label: '롤링엣지 (야간샤프)', hint: 'Score = 20일 밤수익률 평균/표준편차' },
  {
    value: 'factor_combo',
    label: '팩터콤보 (정배열+수급+야간샤프)',
    hint: '필터: 20일선 위 · 양봉 · 윗꼬리 30%↓ 만 통과 · Score = (거래량/20일평균) × 20일 야간샤프',
  },
  {
    value: 'vol_filtered_edge',
    label: '저변동 트렌드엣지 (안정형)',
    hint: '필터: 20일선 위 · 20일 밤변동성 2%↓ 만 통과 · Score = 20일 밤수익률 평균/표준편차 (MDD↓ · 샤프↑ 지향)',
  },
  {
    value: 'vol_surge_trend',
    label: '필터드 거래량폭발 (추세+저변동)',
    hint: '필터: 20일선 위 · 20일 밤변동성 2%↓ 만 통과 · Score = (거래량/20일평균) · I(양봉)',
  },
  {
    value: 'vol_surge_edge',
    label: '거래량폭발×야간샤프',
    hint: '필터 없음 · Score = (거래량/20일평균)·I(양봉) × 20일 야간샤프',
  },
]

const RANGES = [
  { days: 90, label: '3개월' },
  { days: 365, label: '1년' },
  { days: 1095, label: '3년' },
  { days: 1825, label: '5년' },
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
type LeaderboardRow = { value: Top5Strategy; label: string; hint: string; k: number; summary: Top5Summary }

export default function Top5({ me, onSessionLost }: Props) {
  const [strategy, setStrategy] = useState<Top5Strategy>('momentum')
  const [days, setDays] = useState(365)
  const [k, setK] = useState(5)
  const [costPct, setCostPct] = useState(0.1) // 화면은 %, 서버는 소수(0.001)
  // 채우면 기간(days) 대신 이 구간으로 조회합니다 — 비우면 기존처럼 기간 선택을 씁니다
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [data, setData] = useState<Top5Result | null>(null)
  const [leaderboard, setLeaderboard] = useState<LeaderboardRow[] | null>(null)
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
      setData(
        await getBacktestTop5(strategy, days, k, costPct / 100, {
          start: startDate || undefined,
          end: endDate || undefined,
        }),
      )
      setError(null)
    } catch (e) {
      handle(e)
    }
  }, [strategy, days, k, costPct, startDate, endDate, handle])

  useEffect(() => {
    void load()
  }, [load])

  // 8대 전략을 같은 조건(기간 · 비용)으로 한 번에 비교하되, 종목 수(K)는 전략마다
  // 1~20 중 샤프지수가 가장 좋았던 값을 서버가 직접 찾아 돌려줍니다 — K 를 하나로
  // 고정하면 전략마다 유불리가 갈리기 때문입니다(블랙박스 금지 — 어떤 K 를 썼는지도 같이 보여줍니다).
  const loadLeaderboard = useCallback(async () => {
    try {
      const { rows } = await getBacktestTop5Leaderboard(days, costPct / 100, {
        start: startDate || undefined,
        end: endDate || undefined,
      })
      const byStrategy = new Map(rows.map((r) => [r.strategy, r]))
      setLeaderboard(
        STRATEGIES.flatMap((s) => {
          const row = byStrategy.get(s.value)
          return row ? [{ ...s, k: row.k, summary: row.summary }] : []
        }),
      )
    } catch (e) {
      handle(e)
    }
  }, [days, costPct, startDate, endDate, handle])

  useEffect(() => {
    void loadLeaderboard()
  }, [loadLeaderboard])

  const rankedLeaderboard = useMemo(
    () => (leaderboard ? [...leaderboard].sort((a, b) => b.summary.calmar - a.summary.calmar) : []),
    [leaderboard],
  )
  const benchmark = leaderboard?.[0]?.summary

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
        <label className="field">
          <span>시작일 (비우면 기간 선택 사용)</span>
          <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </label>
        <label className="field">
          <span>종료일 (비우면 지금까지)</span>
          <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
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
            <div className="tile">
              <div className="tile-label">최대낙폭(MDD)</div>
              <div className="tile-value">{pct(data.summary.mdd)}</div>
              <div className="tile-time">고점 대비 최대 하락폭</div>
            </div>
            <div className="tile">
              <div className="tile-label">샤프지수</div>
              <div className="tile-value">{data.summary.sharpe.toFixed(2)}</div>
              <div className="tile-time">연율화, 흔들림 대비 수익</div>
            </div>
            <div className="tile">
              <div className="tile-label">칼마지수</div>
              <div className="tile-value">{data.summary.calmar.toFixed(2)}</div>
              <div className="tile-time">누적수익률 ÷ |MDD|</div>
            </div>
            <div className="tile">
              <div className="tile-label">CAGR</div>
              <div className="tile-value">{pct(data.summary.cagr)}</div>
              <div className="tile-time">연율화 복리수익률</div>
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

      {rankedLeaderboard.length > 0 && (
        <>
          <div className="panel-head" style={{ marginTop: 28 }}>
            <h3 style={{ margin: 0 }}>전략 비교 리더보드</h3>
            <span className="hint">위와 같은 기간 · 비용으로 8대 전략을 한 번에 비교 · 종목 수(K)는 전략마다 샤프지수가 가장 좋은 값(1~20) · 칼마지수 순</span>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>전략 이름</th>
                  <th>종목 선택 기준</th>
                  <th>종목 수</th>
                  <th>누적수익률</th>
                  <th>CAGR</th>
                  <th>MDD</th>
                  <th>샤프</th>
                  <th>칼마</th>
                </tr>
              </thead>
              <tbody>
                {benchmark && (
                  <tr>
                    <td>KOSPI 100 동일가중</td>
                    <td>전체 종목 종배 (벤치마크)</td>
                    <td>전체</td>
                    <td className="mono">{pct(benchmark.final_universe)}</td>
                    <td className="mono">{pct(benchmark.universe_cagr)}</td>
                    <td className="mono">{pct(benchmark.universe_mdd)}</td>
                    <td className="mono">{benchmark.universe_sharpe.toFixed(2)}</td>
                    <td className="mono">{benchmark.universe_calmar.toFixed(2)}</td>
                  </tr>
                )}
                {rankedLeaderboard.map((row) => (
                  <tr key={row.value} style={row.value === strategy ? { fontWeight: 600 } : undefined}>
                    <td>{row.label}</td>
                    <td>{row.hint}</td>
                    <td>{row.k}</td>
                    <td className="mono">{pct(row.summary.final_portfolio)}</td>
                    <td className="mono">{pct(row.summary.cagr)}</td>
                    <td className="mono">{pct(row.summary.mdd)}</td>
                    <td className="mono">{row.summary.sharpe.toFixed(2)}</td>
                    <td className="mono">{row.summary.calmar.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}
