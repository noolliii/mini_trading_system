import { useCallback, useEffect, useState } from 'react'

import { ApiError, getCollectionStatus, runCollectionNow } from './api'
import type { CollectionStatus, Me } from './api'

type Props = {
  me: Me
  onSessionLost: () => void
}

const STATUS_LABEL: Record<CollectionStatus['status'], string> = {
  success: '성공',
  partial: '부분 성공',
  failed: '실패',
}

/**
 * 신선도 — 종목별로 마지막 수집이 언제, 어떤 상태로 끝났는지.
 *
 * 평일 16:00(KST) 에 스케줄러가 자동으로 채웁니다. "지금 실행" 버튼은
 * cron 시각을 기다리거나 당기지 않고, 같은 함수를 바로 불러 시험하는 길입니다.
 */
export default function Freshness({ me, onSessionLost }: Props) {
  const [rows, setRows] = useState<CollectionStatus[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const canRun = me.permissions.includes('market:write')

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
      setRows(await getCollectionStatus())
      setError(null)
    } catch (e) {
      handle(e)
    }
  }, [handle])

  useEffect(() => {
    void load()
  }, [load])

  const runNow = async () => {
    setBusy(true)
    setNotice(null)
    try {
      const r = await runCollectionNow('paper')
      setNotice(
        r.skipped_holiday
          ? '오늘은 휴장일이라 건너뛰었습니다.'
          : `종목 ${r.tickers}개 — 성공 ${r.success} · 실패 ${r.failed}.`,
      )
      setError(null)
      await load()
    } catch (e) {
      handle(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>신선도</h2>
        <span className="hint">
          평일 16:00(KST) 스케줄러가 자동으로 채웁니다 · 조회는 누구나, 실행은 admin 만
        </span>
      </div>

      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
      {notice && <div className="banner">{notice}</div>}

      <table>
        <thead>
          <tr>
            <th>종목</th>
            <th>마지막 적재일</th>
            <th>상태</th>
            <th>확인 시각</th>
            <th>메모</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={5} className="empty">
                아직 수집 기록이 없습니다.
              </td>
            </tr>
          )}
          {rows.map((r) => (
            <tr key={r.ticker}>
              <td>
                {r.name} <span className="mono">{r.ticker}</span>
              </td>
              <td className="mono">{r.last_date ?? '—'}</td>
              <td>
                <span className={r.status === 'failed' ? 'badge badge-admin' : 'badge'}>
                  {STATUS_LABEL[r.status]}
                </span>
              </td>
              <td className="mono">{new Date(r.checked_at).toLocaleString('ko-KR')}</td>
              <td className="hint">{r.message ?? ''}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {canRun && (
        <div className="filters">
          <button onClick={() => void runNow()} disabled={busy}>
            {busy ? '실행 중…' : '지금 전체 증분 수집 실행'}
          </button>
        </div>
      )}
    </section>
  )
}
