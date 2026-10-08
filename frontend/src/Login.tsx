import { useState } from 'react'

import { ApiError, login } from './api'

type Props = {
  /** 세션이 끊겨서 돌아온 경우 그 사유를 한 줄로 받습니다 */
  notice?: string | null
  onSignedIn: () => void
}

export default function Login({ notice, onSignedIn }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username, password)
      // 비밀번호는 화면 상태에서도 바로 지웁니다
      setPassword('')
      onSignedIn()
    } catch (err) {
      // 서버가 아이디와 비밀번호 중 어느 쪽이 틀렸는지 알려주지 않습니다. 그대로 보여줍니다
      setError(err instanceof ApiError ? err.message : '로그인에 실패했습니다')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <form className="card login-card" onSubmit={submit}>
        <h1>IoT 센서 대시보드</h1>
        <p className="sub">계속하려면 로그인하세요.</p>

        {notice && <div className="banner">{notice}</div>}

        <label className="field">
          <span>아이디</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            required
          />
        </label>

        <label className="field">
          <span>비밀번호</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>

        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}

        <button type="submit" className="primary" disabled={busy}>
          {busy ? '확인 중…' : '로그인'}
        </button>
      </form>
    </div>
  )
}
