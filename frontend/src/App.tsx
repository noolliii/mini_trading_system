import { useCallback, useEffect, useState } from 'react'

import Accounts from './Accounts'
import Login from './Login'
import Market from './Market'
import Profile from './Profile'
import Top5 from './Top5'
import Universe from './Universe'
import Users from './Users'
import { ApiError, getMe, logout } from './api'
import type { Me } from './api'

type Tab = 'market' | 'universe' | 'top5' | 'accounts' | 'profile' | 'users'

/**
 * 로그인 껍데기.
 *
 * 처음 뜰 때 /api/me 를 한 번 물어봅니다. 세션이 없거나 만료면 로그인 화면,
 * 있으면 역할에 맞는 탭을 보여줍니다. 탭을 숨기는 것은 편의일 뿐이고,
 * 실제 차단은 모든 엔드포인트에서 백엔드가 다시 합니다.
 */
export default function App() {
  const [me, setMe] = useState<Me | null>(null)
  const [checking, setChecking] = useState(true)
  const [notice, setNotice] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('market')

  const refresh = useCallback(async () => {
    try {
      const who = await getMe()
      setMe(who)
      setNotice(null)
    } catch (e) {
      setMe(null)
      // 처음 방문이면 안내가 필요 없습니다. 만료된 경우에만 사유를 남깁니다
      if (e instanceof ApiError && e.code === 'session_expired' && !checking) {
        setNotice('세션이 만료되었습니다. 다시 로그인해 주세요.')
      }
    } finally {
      setChecking(false)
    }
  }, [checking])

  useEffect(() => {
    void refresh()
    // 최초 1회만 확인합니다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const onSessionLost = useCallback(() => {
    setMe(null)
    setNotice('세션이 만료되었습니다. 다시 로그인해 주세요.')
  }, [])

  const signOut = async () => {
    try {
      await logout()
    } finally {
      setMe(null)
      setNotice(null)
      setTab('market')
    }
  }

  const toggleTheme = () => {
    const root = document.documentElement
    root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark'
  }

  if (checking) {
    return (
      <div className="page">
        <div className="state">확인 중…</div>
      </div>
    )
  }

  if (!me) {
    return <Login notice={notice} onSignedIn={() => void refresh()} />
  }

  const isAdmin = me.role === 'admin'

  return (
    <div className="page">
      <div className="page-head">
        <h1>미니 트레이딩 시스템</h1>
        <div className="who">
          <span className={isAdmin ? 'badge badge-admin' : 'badge'}>{me.role}</span>
          <span>{me.username}</span>
          <button onClick={toggleTheme}>테마 전환</button>
          <button onClick={() => void signOut()}>로그아웃</button>
        </div>
      </div>

      <nav className="tabs">
        <button aria-pressed={tab === 'market'} onClick={() => setTab('market')}>
          시세
        </button>
        <button aria-pressed={tab === 'universe'} onClick={() => setTab('universe')}>
          유니버스
        </button>
        <button aria-pressed={tab === 'top5'} onClick={() => setTab('top5')}>
          Top-K 전략
        </button>
        <button aria-pressed={tab === 'accounts'} onClick={() => setTab('accounts')}>
          계좌
        </button>
        <button aria-pressed={tab === 'profile'} onClick={() => setTab('profile')}>
          내 정보
        </button>
        {isAdmin && (
          <button aria-pressed={tab === 'users'} onClick={() => setTab('users')}>
            사용자 · 토큰
          </button>
        )}
      </nav>

      {tab === 'market' && <Market me={me} onSessionLost={onSessionLost} />}
      {tab === 'universe' && <Universe me={me} onSessionLost={onSessionLost} />}
      {tab === 'top5' && <Top5 me={me} onSessionLost={onSessionLost} />}
      {tab === 'accounts' && <Accounts me={me} onSessionLost={onSessionLost} />}
      {tab === 'profile' && (
        <Profile me={me} onUpdated={() => void refresh()} onSessionLost={onSessionLost} />
      )}
      {tab === 'users' && isAdmin && <Users meId={me.id} onSessionLost={onSessionLost} />}
    </div>
  )
}
