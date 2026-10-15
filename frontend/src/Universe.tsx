import { useCallback, useEffect, useMemo, useState } from 'react'

import LineChart, { SERIES_COLORS } from './LineChart'
import type { ChartSeries } from './LineChart'
import { ApiError, getBacktestUniverse, importUniverse } from './api'
import type { Me, UniverseResult } from './api'

type Props = {
  me: Me
  onSessionLost: () => void
}

const RANGES = [
  { days: 90, label: '3개월' },
  { days: 365, label: '1년' },
]

function pct(v: number): string {
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

/**
 * 유니버스 — 등록된 전체 종목의 동일가중 낮 vs 밤 vs 단순보유 비교.
 *
 * "가져오기"는 번들에 들어 있는 kospi100.csv 를 stocks 테이블에 더할 뿐이고,
 * 실제 일봉은 「시세」탭의 "증분 수집"으로 받습니다. 종목을 한 개씩 더하는
 * 칸도 「시세」탭에 있습니다 — 여기서는 늘 등록된 종목 전체로 계산합니다.
 */
export default function Universe({ me, onSessionLost }: Props) {
  const [days, setDays] = useState(365)
  const [data, setData] = useState<UniverseResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const canWrite = me.permissions.includes('market:write')

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
      setData(await getBacktestUniverse(days))
      setError(null)
    } catch (e) {
      handle(e)
    }
  }, [days, handle])

  useEffect(() => {
    void load()
  }, [load])

  const doImport = async () => {
    setBusy(true)
    setNotice(null)
    try {
      const r = await importUniverse()
      setNotice(
        `코스피 100 중 ${r.inserted}종목을 새로 더했습니다 (나머지 ${r.total - r.inserted}종목은 이미 있었습니다). ` +
          '일봉은 「시세」탭의 "증분 수집"으로 받으세요.',
      )
      setError(null)
      await load()
    } catch (e) {
      handle(e)
    } finally {
      setBusy(false)
    }
  }

  const series: ChartSeries[] = useMemo(() => {
    if (!data || data.points.length === 0) return []
    return [
      { key: 'night', name: '밤(동일가중)', points: data.points.map((p) => ({ t: p.time, v: p.cum_night })) },
      { key: 'day', name: '낮(동일가중)', points: data.points.map((p) => ({ t: p.time, v: p.cum_day })) },
      { key: 'hold', name: '단순보유(동일가중)', points: data.points.map((p) => ({ t: p.time, v: p.cum_buy_hold })) },
    ]
  }, [data])

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>유니버스</h2>
        <span className="hint">동일가중(1/N) 평균 · 등록된 종목 전체로 계산합니다</span>
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
        {canWrite && (
          <button onClick={() => void doImport()} disabled={busy}>
            {busy ? '가져오는 중…' : '코스피 100 종목 가져오기'}
          </button>
        )}
      </div>

      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
      {notice && <div className="banner">{notice}</div>}

      {data && data.points.length > 0 ? (
        <>
          <div className="tiles">
            <div className="tile">
              <div className="tile-label">
                <span className="swatch" style={{ background: SERIES_COLORS[0] }} />
                밤 평균
              </div>
              <div className="tile-value">{pct(data.points[data.points.length - 1].cum_night)}</div>
              <div className="tile-time">종목 {data.tickers.length}개</div>
            </div>
            <div className="tile">
              <div className="tile-label">
                <span className="swatch" style={{ background: SERIES_COLORS[1] }} />
                낮 평균
              </div>
              <div className="tile-value">{pct(data.points[data.points.length - 1].cum_day)}</div>
            </div>
            <div className="tile">
              <div className="tile-label">
                <span className="swatch" style={{ background: SERIES_COLORS[2] }} />
                단순보유 평균
              </div>
              <div className="tile-value">{pct(data.points[data.points.length - 1].cum_buy_hold)}</div>
            </div>
          </div>

          <div className="chart-wrap" style={{ marginBottom: 20 }}>
            <LineChart series={series} unit="%" />
          </div>

          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>종목</th>
                  <th>낮</th>
                  <th>밤</th>
                  <th>단순보유</th>
                  <th>승률(낮)</th>
                  <th>승률(밤)</th>
                  <th>일봉 수</th>
                </tr>
              </thead>
              <tbody>
                {data.tickers.map((t) => (
                  <tr key={t.ticker}>
                    <td>
                      {t.name} <span className="mono">{t.ticker}</span>
                    </td>
                    <td className="mono">{pct(t.final_day)}</td>
                    <td className="mono">{pct(t.final_night)}</td>
                    <td className="mono">{pct(t.final_buy_hold)}</td>
                    <td className="mono">{(t.win_rate_day * 100).toFixed(0)}%</td>
                    <td className="mono">{(t.win_rate_night * 100).toFixed(0)}%</td>
                    <td className="mono">{t.candle_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div className="state">
          적재된 일봉이 없습니다. 「시세」탭에서 수집하세요.
        </div>
      )}
    </section>
  )
}
