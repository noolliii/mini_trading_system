import { useCallback, useEffect, useState } from 'react'

import { ApiError, createUser, deleteUser, getUsers, issueKisToken } from './api'
import type { TokenIssue, User } from './api'

type Props = {
  meId: number
  onSessionLost: () => void
}

/**
 * admin 전용 화면입니다.
 * 화면을 숨기는 것은 친절일 뿐이고, 여기서 부르는 엔드포인트는 전부 서버에서 다시 막습니다.
 */
export default function Users({ meId, onSessionLost }: Props) {
  const [rows, setRows] = useState<User[]>([])
  const [error, setError] = useState<string | null>(null)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('user')
  const [busy, setBusy] = useState(false)
  const [token, setToken] = useState<TokenIssue | null>(null)
  const [tokenError, setTokenError] = useState<string | null>(null)

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
      setRows(await getUsers())
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
      await createUser(username, password, role)
      setUsername('')
      setPassword('') // 비밀번호는 보낸 즉시 화면 상태에서 지웁니다
      setError(null)
      await load()
    } catch (e) {
      handle(e)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (u: User) => {
    const warning =
      u.account_count > 0
        ? `${u.username} 을(를) 지우면 연결된 계좌 ${u.account_count}건도 함께 지워집니다. 계속할까요?`
        : `${u.username} 을(를) 지울까요?`
    if (!confirm(warning)) return
    try {
      await deleteUser(u.id)
      setError(null)
      await load()
    } catch (e) {
      handle(e)
    }
  }

  const issue = async (mode: 'paper' | 'live') => {
    setTokenError(null)
    setToken(null)
    try {
      setToken(await issueKisToken(mode))
    } catch (e) {
      if (e instanceof ApiError && e.code === 'session_expired') return onSessionLost()
      setTokenError(e instanceof ApiError ? e.message : (e as Error).message)
    }
  }

  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <h2>사용자</h2>
          <span className="hint">admin 만 보입니다 · 사용자를 지우면 계좌도 함께 지워집니다</span>
        </div>

        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}

        <table>
          <thead>
            <tr>
              <th>아이디</th>
              <th>역할</th>
              <th>계좌</th>
              <th>생성</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((u) => (
              <tr key={u.id}>
                <td>{u.username}</td>
                <td>
                  <span className={u.role === 'admin' ? 'badge badge-admin' : 'badge'}>
                    {u.role}
                  </span>
                </td>
                <td>{u.account_count}건</td>
                <td>{new Date(u.created_at).toLocaleDateString('ko-KR')}</td>
                <td className="row-actions">
                  <button onClick={() => void remove(u)} disabled={u.id === meId}>
                    {u.id === meId ? '본인' : '삭제'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <form className="inline-form" onSubmit={add}>
          <label className="field">
            <span>아이디</span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              minLength={3}
              required
            />
          </label>
          <label className="field">
            <span>비밀번호 (8자 이상)</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={8}
              autoComplete="new-password"
              required
            />
          </label>
          <label className="field">
            <span>역할</span>
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="user">user</option>
              <option value="admin">admin</option>
            </select>
          </label>
          <button type="submit" className="primary" disabled={busy}>
            추가
          </button>
        </form>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>KIS 접근 토큰</h2>
          <span className="hint">토큰은 서버에만 남습니다 · 화면에는 앞 네 글자만</span>
        </div>

        <div className="filters">
          <button onClick={() => void issue('paper')}>모의 토큰 받기</button>
          <button onClick={() => void issue('live')}>실전 토큰 받기</button>
        </div>

        {tokenError && (
          <div className="form-error" role="alert">
            {tokenError}
          </div>
        )}

        {token && (
          <div className="token-result">
            <div>
              <b>{token.mode === 'live' ? '실전' : '모의'}</b> · {token.host}
            </div>
            <div className="mono">{token.token_preview}</div>
            <div className="hint">
              {new Date(token.issued_at).toLocaleTimeString('ko-KR', { hour12: false })} 발급 ·
              {' '}
              {Math.round(token.expires_in / 3600)}시간 유효
            </div>
          </div>
        )}
      </section>
    </>
  )
}
