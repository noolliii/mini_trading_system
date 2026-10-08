import { useLayoutEffect, useMemo, useRef, useState } from 'react'

/** 차트가 받는 모양. 무엇을 그리든 (키 · 이름 · 점들) 셋이면 됩니다. */
export type ChartPoint = { t: string; v: number }
export type ChartSeries = { key: string; name: string; points: ChartPoint[] }

/**
 * 범주형 팔레트 고정 슬롯. 순서 자체가 색약 안전성을 담보하므로 돌려쓰지 않습니다.
 * 4개 이상이면 상위 3개만 그리고 나머지는 접습니다.
 */
export const SERIES_COLORS = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)']
export const MAX_SERIES = SERIES_COLORS.length

const HEIGHT = 320
const M = { top: 12, right: 78, bottom: 28, left: 52 }

type Props = {
  series: ChartSeries[]
  unit: string | null
}

type HoverRow = { name: string; color: string; v: number; t: number }
type Hover = { px: number; t: number; rows: HoverRow[] }

function niceTicks(min: number, max: number, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { lo: 0, hi: 1, ticks: [0, 1] }
  if (min === max) {
    min -= 1
    max += 1
  }
  const raw = (max - min) / count
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  const norm = raw / mag
  const step = (norm >= 7.5 ? 10 : norm >= 3.5 ? 5 : norm >= 1.5 ? 2 : 1) * mag
  const lo = Math.floor(min / step) * step
  const hi = Math.ceil(max / step) * step
  const ticks: number[] = []
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Number(v.toFixed(10)))
  return { lo, hi, ticks }
}

/** t에 가장 가까운 점의 인덱스를 이진 탐색으로 찾습니다. */
function nearest(ts: number[], t: number): number {
  let lo = 0
  let hi = ts.length - 1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (ts[mid] < t) lo = mid + 1
    else hi = mid
  }
  if (lo > 0 && Math.abs(ts[lo - 1] - t) < Math.abs(ts[lo] - t)) return lo - 1
  return lo
}

export function formatValue(v: number): string {
  const digits = Math.abs(v) >= 100 ? 0 : 1
  return v.toLocaleString('ko-KR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export default function LineChart({ series, unit }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(720)
  const [hover, setHover] = useState<Hover | null>(null)

  useLayoutEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => setWidth(entries[0].contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const shown = useMemo(() => series.slice(0, MAX_SERIES), [series])

  const model = useMemo(() => {
    const times = shown.map((s) => s.points.map((p) => Date.parse(p.t)))
    const values = shown.map((s) => s.points.map((p) => p.v))

    const allT = times.flat()
    const allV = values.flat()
    const t0 = Math.min(...allT)
    const t1 = Math.max(...allT)
    const { lo, hi, ticks } = niceTicks(Math.min(...allV), Math.max(...allV))

    const innerW = Math.max(width - M.left - M.right, 10)
    const innerH = HEIGHT - M.top - M.bottom
    const xOf = (t: number) => M.left + (t1 === t0 ? innerW / 2 : ((t - t0) / (t1 - t0)) * innerW)
    const yOf = (v: number) => M.top + innerH - ((v - lo) / (hi - lo || 1)) * innerH

    const paths = times.map((ts, i) =>
      ts
        .map((t, j) => (j === 0 ? 'M' : 'L') + xOf(t).toFixed(1) + ' ' + yOf(values[i][j]).toFixed(1))
        .join(' '),
    )

    const spanHours = (t1 - t0) / 3600000
    const fmtX = (t: number) =>
      new Date(t).toLocaleString('ko-KR', {
        month: spanHours > 48 ? 'numeric' : undefined,
        day: spanHours > 48 ? 'numeric' : undefined,
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })

    const xTicks = Array.from({ length: 5 }, (_, i) => t0 + ((t1 - t0) * i) / 4)

    // 선 끝 직접 레이블 — 겹치면 아래로 밀어냅니다
    const ends = shown
      .map((s, i) => ({
        key: s.key,
        v: values[i][values[i].length - 1],
        y: yOf(values[i][values[i].length - 1]),
      }))
      .sort((a, b) => a.y - b.y)
    for (let i = 1; i < ends.length; i++) {
      if (ends[i].y - ends[i - 1].y < 15) ends[i].y = ends[i - 1].y + 15
    }

    return { times, values, innerW, innerH, xOf, yOf, paths, ticks, t0, t1, fmtX, xTicks, ends }
  }, [shown, width])

  if (shown.length === 0) return null

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const ratio = rect.width === 0 ? 0 : (e.clientX - rect.left) / rect.width
    const t = model.t0 + (model.t1 - model.t0) * Math.min(Math.max(ratio, 0), 1)

    const rows: HoverRow[] = shown.map((s, i) => {
      const idx = nearest(model.times[i], t)
      return { name: s.name, color: SERIES_COLORS[i], v: model.values[i][idx], t: model.times[i][idx] }
    })
    const snapped = rows.length > 0 ? rows[0].t : t
    setHover({ px: model.xOf(snapped), t: snapped, rows })
  }

  const flip = hover !== null && hover.px > width - 190

  return (
    <div>
      {/* 색만으로 구분되지 않도록 범례와 선 끝 레이블을 함께 둡니다 */}
      <div className="legend">
        {shown.map((s, i) => (
          <span className="legend-item" key={s.key}>
            <span className="swatch" style={{ background: SERIES_COLORS[i] }} />
            {s.name}
          </span>
        ))}
      </div>

      <div className="chart-wrap" ref={wrapRef}>
        <svg width="100%" height={HEIGHT} role="img" aria-label="센서 시계열 추이">
          {/* 가로 격자만 머리카락 굵기로 뒤에 깔립니다 */}
          {model.ticks.map((v) => (
            <g key={v}>
              <line
                x1={M.left}
                x2={M.left + model.innerW}
                y1={model.yOf(v)}
                y2={model.yOf(v)}
                stroke="var(--grid)"
                strokeWidth={1}
              />
              <text
                x={M.left - 8}
                y={model.yOf(v)}
                textAnchor="end"
                dominantBaseline="middle"
                fontSize={11}
                fill="var(--text-muted)"
                style={{ fontVariantNumeric: 'tabular-nums' }}
              >
                {formatValue(v)}
              </text>
            </g>
          ))}

          <line
            x1={M.left}
            x2={M.left + model.innerW}
            y1={M.top + model.innerH}
            y2={M.top + model.innerH}
            stroke="var(--axis)"
            strokeWidth={1}
          />

          {model.xTicks.map((t) => (
            <text
              key={t}
              x={model.xOf(t)}
              y={HEIGHT - 8}
              textAnchor="middle"
              fontSize={11}
              fill="var(--text-muted)"
              style={{ fontVariantNumeric: 'tabular-nums' }}
            >
              {model.fmtX(t)}
            </text>
          ))}

          {hover && (
            <line
              x1={hover.px}
              x2={hover.px}
              y1={M.top}
              y2={M.top + model.innerH}
              stroke="var(--axis)"
              strokeWidth={1}
            />
          )}

          {model.paths.map((d, i) => (
            <path
              key={shown[i].key}
              d={d}
              fill="none"
              stroke={SERIES_COLORS[i]}
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ))}

          {/* 호버 지점은 표면색 링으로 겹침을 분리합니다 */}
          {hover &&
            hover.rows.map((r, i) => (
              <circle
                key={shown[i].key}
                cx={hover.px}
                cy={model.yOf(r.v)}
                r={4.5}
                fill={r.color}
                stroke="var(--surface-1)"
                strokeWidth={2}
              />
            ))}

          {model.ends.map((e) => (
            <text
              key={e.key}
              x={M.left + model.innerW + 8}
              y={e.y}
              dominantBaseline="middle"
              fontSize={11}
              fill="var(--text-secondary)"
              style={{ fontVariantNumeric: 'tabular-nums' }}
            >
              {formatValue(e.v)}
              {unit ?? ''}
            </text>
          ))}

          <rect
            x={M.left}
            y={M.top}
            width={model.innerW}
            height={model.innerH}
            fill="transparent"
            style={{ touchAction: 'none' }}
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
          />
        </svg>

        {hover && (
          <div
            className="tooltip"
            style={{
              left: flip ? hover.px - 14 : hover.px + 14,
              top: M.top + 4,
              transform: flip ? 'translateX(-100%)' : undefined,
            }}
          >
            <div className="tooltip-time">
              {new Date(hover.t).toLocaleString('ko-KR', {
                month: 'numeric',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
                hour12: false,
              })}
            </div>
            {hover.rows.map((r) => (
              <div className="tooltip-row" key={r.name}>
                <span className="swatch" style={{ background: r.color }} />
                {r.name}
                <b>
                  {formatValue(r.v)}
                  {unit ? ' ' + unit : ''}
                </b>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
