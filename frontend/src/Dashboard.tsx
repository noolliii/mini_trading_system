import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError, getLatest, getMetrics, getSeries } from './api'
import type { LatestReading, SeriesResponse } from './api'
import LineChart, { MAX_SERIES, SERIES_COLORS, formatValue } from './LineChart'

const RANGES = [
  { label: '1시간', hours: 1 },
  { label: '6시간', hours: 6 },
  { label: '24시간', hours: 24 },
  { label: '7일', hours: 24 * 7 },
]

const METRIC_LABELS: Record<string, string> = {
  temperature: '온도',
  humidity: '습도',
  co2: 'CO₂',
}

const REFRESH_MS = 15000

const metricLabel = (m: string) => METRIC_LABELS[m] ?? m

type Props = {
  /** 세션이 끊기면 껍데기(App)에게 알려 로그인 화면으로 돌려보냅니다 */
  onSessionLost: () => void
}

export default function Dashboard({ onSessionLost }: Props) {
  const [metrics, setMetrics] = useState<string[]>([])
  const [metric, setMetric] = useState<string>('')
  const [hours, setHours] = useState(24)
  const [data, setData] = useState<SeriesResponse | null>(null)
  const [latest, setLatest] = useState<LatestReading[]>([])
  const [asTable, setAsTable] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  // 세션 만료는 화면에 에러로 찍지 않고 로그인으로 돌려보냅니다
  const handle = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.code === 'session_expired') {
        onSessionLost()
        return
      }
      setError((e as Error).message)
    },
    [onSessionLost],
  )

  useEffect(() => {
    getMetrics()
      .then((list) => {
        setMetrics(list)
        setMetric((current) => current || list[0] || '')
        if (list.length === 0) setLoading(false)
      })
      .catch((e) => {
        handle(e)
        setLoading(false)
      })
  }, [handle])

  const load = useCallback(async () => {
    if (!metric) return
    try {
      const [s, l] = await Promise.all([getSeries(metric, hours), getLatest(metric)])
      setData(s)
      setLatest(l)
      setError(null)
    } catch (e) {
      handle(e)
    } finally {
      setLoading(false)
    }
  }, [metric, hours, handle])

  useEffect(() => {
    setLoading(true)
    void load()
    const id = setInterval(() => void load(), REFRESH_MS)
    return () => clearInterval(id)
  }, [load])

  const shown = useMemo(() => (data ? data.series.slice(0, MAX_SERIES) : []), [data])
  const folded = data ? data.series.length - shown.length : 0
  const unit = data?.unit ?? null

  // 표 뷰는 라이트 모드에서 3:1 미만인 색의 대비 보완 수단이기도 합니다
  const tableRows = useMemo(() => {
    if (shown.length === 0) return []
    const spine = shown[0].points
    const step = Math.max(1, Math.ceil(spine.length / 200))
    const rows: { t: string; values: (number | undefined)[] }[] = []
    for (let i = 0; i < spine.length; i += step) {
      rows.push({ t: spine[i].t, values: shown.map((s) => s.points[i]?.v) })
    }
    return rows.reverse()
  }, [shown])

  return (
    <section>
      <p className="sub">TimescaleDB에 적재된 센서 측정값을 {REFRESH_MS / 1000}초마다 갱신합니다.</p>

      <div className="filters">
        <label className="field">
          <span>지표</span>
          <select value={metric} onChange={(e) => setMetric(e.target.value)}>
            {metrics.map((m) => (
              <option key={m} value={m}>
                {metricLabel(m)}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>기간</span>
          <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            {RANGES.map((r) => (
              <option key={r.hours} value={r.hours}>
                {r.label}
              </option>
            ))}
          </select>
        </label>

        <span className="spacer" />

        <button aria-pressed={asTable} onClick={() => setAsTable((v) => !v)}>
          {asTable ? '차트로 보기' : '표로 보기'}
        </button>
      </div>

      <div className="tiles">
        {latest.slice(0, MAX_SERIES).map((r, i) => (
          <div className="tile" key={r.device_id}>
            <div className="tile-label">
              <span className="swatch" style={{ background: SERIES_COLORS[i] }} />
              {r.name}
            </div>
            <div className="tile-value">
              {formatValue(r.value)}
              {unit && <span className="tile-unit">{unit}</span>}
            </div>
            <div className="tile-time">
              {new Date(r.time).toLocaleTimeString('ko-KR', {
                hour: '2-digit',
                minute: '2-digit',
                hour12: false,
              })}{' '}
              기준
            </div>
          </div>
        ))}
      </div>

      <div className="card">
        {error ? (
          <div className="state error">
            백엔드에 연결할 수 없습니다 — {error}
            <br />
            <code>docker compose ps</code> 로 backend 컨테이너 상태를 확인하세요.
          </div>
        ) : loading ? (
          <div className="state">불러오는 중…</div>
        ) : shown.length === 0 ? (
          <div className="state">표시할 측정값이 없습니다.</div>
        ) : asTable ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>시각</th>
                  {shown.map((s) => (
                    <th key={s.device_id}>
                      {s.name}
                      {unit ? ` (${unit})` : ''}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tableRows.map((row) => (
                  <tr key={row.t}>
                    <td>{new Date(row.t).toLocaleString('ko-KR', { hour12: false })}</td>
                    {row.values.map((v, i) => (
                      <td key={shown[i].device_id}>{v === undefined ? '—' : formatValue(v)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <>
            <LineChart series={shown} unit={unit} />
            <p className="sub" style={{ margin: '12px 0 0', fontSize: 12 }}>
              {metricLabel(metric)} · {data?.resolution === '1m' ? '1분 평균' : '원본 측정값'}
              {folded > 0 && ` · 장치 ${folded}개는 표시에서 생략됨`}
            </p>
          </>
        )}
      </div>
    </section>
  )
}
