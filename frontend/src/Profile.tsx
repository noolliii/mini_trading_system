import { useState } from 'react'

import { ApiError, updateMe } from './api'
import type { Me } from './api'

type Props = {
  me: Me
  onUpdated: () => void
  onSessionLost: () => void
}

/**
 * 내 정보 화면.
 *
 * 아이디와 비밀번호를 본인만 바꿉니다. 역할은 여기서 바꿀 수 없습니다 —
 * 서버의 MeUpdate 에 role 이 아예 없어서, 요청에 끼워 넣어도 무시됩니다.
 * 바꾸려면 지금 비밀번호를 함께 보내야 하고, 비교는 서버가 해시로만 합니다.
 */
export default function Profile({ me, onUpdated, onSessionLost }: Props) {
  const [username, setUsername] = useState(me.username)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setDone(null)

    const renaming = username !== me.username

    if (!renaming && !newPassword) {
      setError('바꿀 항목이 없습니다.')
      return
    }
    if (newPassword && newPassword !== confirm) {
      setError('새 비밀번호가 서로 다릅니다.')
      return
    }

    setBusy(true)
    try {
      await updateMe({
        current_password: currentPassword,
        ...(renaming ? { username } : {}),
        ...(newPassword ? { new_password: newPassword } : {}),
      })
      setCurrentPassword('')
      setNewPassword('')
      setConfirm('')
      setDone(
        newPassword
          ? '바꿨습니다. 다른 기기에 남아 있던 로그인은 끊었습니다.'
          : '바꿨습니다.',
      )
      onUpdated()
    } catch (err) {
      if (err instanceof ApiError && err.code === 'session_expired') {
        onSessionLost()
        return
      }
      setError(err instanceof ApiError ? err.message : (err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>내 정보</h2>
        <span className="hint">아이디와 비밀번호만 바꿉니다 · 역할은 admin 이 바꿉니다</span>
      </div>

      <table>
        <tbody>
          <tr>
            <th>아이디</th>
            <td>{me.username}</td>
          </tr>
          <tr>
            <th>역할</th>
            <td>
              <span className={me.role === 'admin' ? 'badge badge-admin' : 'badge'}>
                {me.role}
              </span>
            </td>
          </tr>
          <tr>
            <th>가진 권한</th>
            <td className="mono">{me.permissions.join(' · ')}</td>
          </tr>
        </tbody>
      </table>

      <p className="hint">
        이 권한 표는 서버의 <span className="mono">auth.PERMISSIONS</span> 를 그대로 받은
        것입니다. 화면에서 보이는 것과 별개로, 모든 요청은 서버가 다시 검사합니다.
      </p>

      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
      {done && <div className="banner">{done}</div>}

      <form className="inline-form" onSubmit={submit}>
        <label className="field">
          <span>아이디</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            minLength={3}
            maxLength={64}
            required
          />
        </label>
        <label className="field">
          <span>지금 비밀번호</span>
          <input
            type="password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        <label className="field">
          <span>새 비밀번호</span>
          <input
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            placeholder="바꾸지 않으면 비웁니다"
            minLength={8}
            autoComplete="new-password"
          />
        </label>
        <label className="field">
          <span>새 비밀번호 확인</span>
          <input
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            minLength={8}
            autoComplete="new-password"
          />
        </label>
        <button type="submit" className="primary" disabled={busy}>
          저장
        </button>
      </form>
    </section>
  )
}
