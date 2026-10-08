import { useCallback, useEffect, useState } from 'react'

import { ApiError, createAccount, deleteAccount, getAccounts } from './api'
import type { Account, Me } from './api'

type Props = {
  me: Me
  onSessionLost: () => void
}

/** 계좌번호는 뒤 4자리만 보여줍니다. 전체 번호는 화면에 띄우지 않습니다. */
function maskAccountNo(no: string): string {
  const tail = no.slice(-4)
  return `••••-${tail}`
}

export default function Accounts({ me, onSessionLost }: Props) {
  const [rows, setRows] = useState<Account[]>([])
  const [error, setError] = useState<string | null>(null)
  const [accountNo, setAccountNo] = useState('')
  const [broker, setBroker] = useState('한국투자증권')
  const [mode, setMode] = useState('paper')
  const [busy, setBusy] = useState(false)

  const seesEveryone = me.permissions.includes('account:read:any')

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
      setRows(await getAccounts())
      setError(null)
    } catch (e) {
      handle(e)
    }
  }, [handle])

  useEffect(() => {
    void load()
  }, [load])

  const add = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      await createAccount({ account_no: accountNo, broker, mode })
      setAccountNo('')
      setError(null)
      await load()
    } catch (e) {
      handle(e)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (no: string) => {
    try {
      await deleteAccount(no)
      setError(null)
      await load()
    } catch (e) {
      handle(e)
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{seesEveryone ? '계좌 — 전체' : '내 계좌'}</h2>
        <span className="hint">
          {seesEveryone
            ? 'admin 이라 모든 사용자의 계좌가 보입니다'
            : 'user 라 본인 계좌만 보입니다'}
        </span>
      </div>

      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}

      <table>
        <thead>
          <tr>
            <th>계좌번호</th>
            {seesEveryone && <th>소유자</th>}
            <th>증권사</th>
            <th>구분</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={seesEveryone ? 5 : 4} className="empty">
                등록된 계좌가 없습니다.
              </td>
            </tr>
          )}
          {rows.map((a) => (
            <tr key={a.account_no}>
              <td className="mono">{maskAccountNo(a.account_no)}</td>
              {seesEveryone && <td>{a.username}</td>}
              <td>{a.broker}</td>
              <td>{a.mode === 'live' ? '실전' : '모의'}</td>
              <td className="row-actions">
                <button onClick={() => void remove(a.account_no)}>해제</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <form className="inline-form" onSubmit={add}>
        <label className="field">
          <span>계좌번호</span>
          <input
            value={accountNo}
            onChange={(e) => setAccountNo(e.target.value)}
            placeholder="50012345-01"
            required
          />
        </label>
        <label className="field">
          <span>증권사</span>
          <input value={broker} onChange={(e) => setBroker(e.target.value)} required />
        </label>
        <label className="field">
          <span>구분</span>
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="paper">모의</option>
            <option value="live">실전</option>
          </select>
        </label>
        <button type="submit" className="primary" disabled={busy}>
          내 계정에 연결
        </button>
      </form>
    </section>
  )
}
