import { useCallback, useEffect, useMemo, useState } from 'react'

import LineChart, { SERIES_COLORS } from './LineChart'
import type { ChartSeries } from './LineChart'
import { ApiError, collectCandles, getCandles, getStocks } from './api'
import type { CandleSeries, Me, Stock } from './api'

type Props = {
  me: Me
  onSessionLost: () => void
}

const RANGES = [
  { days: 30, label: '1개월' },
  { days: 90, label: '3개월' },
  { days: 365, label: '1년' },
]

function won(v: number): string {
  return v.toLocaleString('ko-KR', { maximumFractionDigits: 0 })
}

/**
 * 시세 화면.
 *
 * 차트는 DB 에 적재된 일봉만 그립니다 — 화면이 KIS 로 직접 나가지 않습니다.
 * 수집은 admin 만 할 수 있고, 버튼을 숨기는 것과 별개로 서버가 다시 막습니다.
 */
export default function Market({ me, onSessionLost }: Props) {
  const [stocks, setStocks] = useState<Stock[]>([])
  const [ticker, setTicker] = useState('005930')
  const [days, setDays] = useState(365)
  const [data, setData] = useState<CandleSeries | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const canCollect = me.permissions.includes('market:write')

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
    getStocks()
      .then(setStocks)
      .catch(handle)
  }, [handle])

  const load = useCallback(async () => {
    try {
      setData(await getCandles(ticker, days))
      setError(null)
    } catch (e) {
      handle(e)
    }
  }, [ticker, days, handle])

  useEffect(() => {
    void load()
  }, [load])

  const collect = async () => {
    setBusy(true)
    setNotice(null)
    try {
      // 실습은 모의 도메인으로 받습니다
      const r = await collectCandles(ticker, 'paper', days)
      setNotice(
        r.fetched === 0
          ? 'KIS 가 이 구간에 일봉을 주지 않았습니다.'
          : `${r.fetched}건 받아 ${r.inserted}건 새로 넣었습니다 (나머지는 이미 있던 날짜).`,
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
    if (!data || data.candles.length === 0) return []
    return [
      {
        key: data.ticker,
        name: `${data.name} 종가`,
        points: data.candles.map((c) => ({ t: c.time, v: c.close })),
      },
    ]
  }, [data])

  const summary = useMemo(() => {
    if (!data || data.candles.length === 0) return null
    const first = data.candles[0]
    const last = data.candles[data.candles.length - 1]
    const change = ((last.close - first.close) / first.close) * 100
    return { first, last, change }
  }, [data])

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>일봉</h2>
        <span className="hint">
          DB 에 적재된 일봉만 그립니다 · 수집은 {canCollect ? 'admin 인 내가' : 'admin 만'} 합니다
        </span>
      </div>

      <div className="filters">
        <label className="field">
          <span>종목</span>
          <select value={ticker} onChange={(e) => setTicker(e.target.value)}>
            {stocks.map((s) => (
              <option key={s.ticker} value={s.ticker}>
                {s.name} ({s.ticker}) · {s.market}
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
        <div className="spacer" />
        {canCollect && (
          <button className="primary" onClick={() => void collect()} disabled={busy}>
            {busy ? 'KIS 에서 받는 중…' : 'KIS 에서 일봉 수집'}
          </button>
        )}
      </div>

      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
      {notice && <div className="banner">{notice}</div>}

      {summary && (
        <div className="tiles">
          <div className="tile">
            <div className="tile-label">
              <span className="swatch" style={{ background: SERIES_COLORS[0] }} />
              최근 종가
            </div>
            <div className="tile-value">
              {won(summary.last.close)}
              <span className="tile-unit">원</span>
            </div>
            <div className="tile-time">
              {new Date(summary.last.time).toLocaleDateString('ko-KR')}
            </div>
          </div>
          <div className="tile">
            <div className="tile-label">기간 등락</div>
            <div className="tile-value">
              {summary.change >= 0 ? '+' : ''}
              {summary.change.toFixed(2)}
              <span className="tile-unit">%</span>
            </div>
            <div className="tile-time">
              {new Date(summary.first.time).toLocaleDateString('ko-KR')} 종가 대비
            </div>
          </div>
          <div className="tile">
            <div className="tile-label">거래량</div>
            <div className="tile-value">
              {won(summary.last.volume)}
              <span className="tile-unit">주</span>
            </div>
            <div className="tile-time">최근 거래일</div>
          </div>
          <div className="tile">
            <div className="tile-label">적재된 일봉</div>
            <div className="tile-value">
              {data?.candles.length ?? 0}
              <span className="tile-unit">건</span>
            </div>
            <div className="tile-time">최근 {days}일 구간</div>
          </div>
        </div>
      )}

      {series.length === 0 ? (
        <div className="state">
          적재된 일봉이 없습니다.
          {canCollect
            ? ' 위의 「KIS 에서 일봉 수집」을 누르세요.'
            : ' admin 이 수집해야 보입니다.'}
        </div>
      ) : (
        <div className="chart-wrap">
          <LineChart series={series} unit="원" />
        </div>
      )}
    </section>
  )
}
