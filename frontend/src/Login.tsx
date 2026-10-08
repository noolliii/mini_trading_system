import { useState } from 'react'

import { ApiError, login, signup } from './api'

type Props = {
  /** 세션이 끊겨서 돌아온 경우 그 사유를 한 줄로 받습니다 */
  notice?: string | null
  onSignedIn: () => void
}

type Mode = 'login' | 'signup'

/** 로그인과 회원가입을 한 화면에서 전환합니다. 둘 다 같은 카드 모양을 씁니다. */
export default function Login({ notice, onSignedIn }: Props) {
  const [mode, setMode] = useState<Mode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const switchMode = (next: Mode) => {
    setMode(next)
    setError(null)
    setPassword('')
    setConfirm('')
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (mode === 'signup' && password !== confirm) {
      setError('비밀번호가 서로 다릅니다.')
      return
    }

    setBusy(true)
    try {
      if (mode === 'login') {
        await login(username, password)
      } else {
        // 가입과 동시에 로그인됩니다. 역할은 항상 user 로 만들어집니다
        await signup(username, password)
      }
      // 비밀번호는 화면 상태에서도 바로 지웁니다
      setPassword('')
      setConfirm('')
      onSignedIn()
    } catch (err) {
      // 서버가 아이디와 비밀번호 중 어느 쪽이 틀렸는지 알려주지 않습니다. 그대로 보여줍니다
      setError(
        err instanceof ApiError
          ? err.message
          : mode === 'login'
            ? '로그인에 실패했습니다'
            : '가입에 실패했습니다',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <form className="card login-card" onSubmit={submit}>
        <h1>미니 트레이딩 시스템</h1>
        <p className="sub">
          {mode === 'login' ? '계속하려면 로그인하세요.' : '아이디와 비밀번호만으로 가입합니다.'}
        </p>

        {notice && <div className="banner">{notice}</div>}

        <label className="field">
          <span>아이디</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            minLength={mode === 'signup' ? 3 : undefined}
            required
          />
        </label>

        <label className="field">
          <span>비밀번호</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            minLength={mode === 'signup' ? 8 : undefined}
            required
          />
        </label>

        {mode === 'signup' && (
          <label className="field">
            <span>비밀번호 확인</span>
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="new-password"
              minLength={8}
              required
            />
          </label>
        )}

        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}

        <button type="submit" className="primary" disabled={busy}>
          {busy ? '확인 중…' : mode === 'login' ? '로그인' : '가입하기'}
        </button>

        <button
          type="button"
          className="link-button"
          onClick={() => switchMode(mode === 'login' ? 'signup' : 'login')}
        >
          {mode === 'login' ? '계정이 없으신가요? 회원가입' : '이미 계정이 있으신가요? 로그인'}
        </button>
      </form>
    </div>
  )
}
