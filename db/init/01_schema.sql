CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ──────────────────────────────────────────────────────────────
-- 종목 · 일봉
-- ──────────────────────────────────────────────────────────────

-- 종목 마스터. 일봉은 반드시 여기 있는 종목에만 붙습니다
CREATE TABLE IF NOT EXISTS stocks (
  ticker     TEXT PRIMARY KEY,                -- 종목코드 여섯 자리
  name       TEXT NOT NULL,
  market     TEXT NOT NULL CHECK (market IN ('KOSPI', 'KOSDAQ')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 이름으로 찾는 조회가 이걸 씁니다
CREATE INDEX IF NOT EXISTS stocks_name_idx ON stocks (name);

CREATE TABLE IF NOT EXISTS daily_candles (
  time   TIMESTAMPTZ NOT NULL,                -- 거래일 00:00 (Asia/Seoul)
  ticker TEXT   NOT NULL REFERENCES stocks(ticker) ON DELETE CASCADE,
  open   DOUBLE PRECISION NOT NULL,
  high   DOUBLE PRECISION NOT NULL,
  low    DOUBLE PRECISION NOT NULL,
  close  DOUBLE PRECISION NOT NULL,
  volume BIGINT NOT NULL
);

-- 일봉은 하루에 한 줄이라 센서값만큼 쏟아지지 않습니다. 조각을 1년 단위로 잡습니다
SELECT create_hypertable('daily_candles', 'time',
                         if_not_exists => TRUE,
                         chunk_time_interval => INTERVAL '1 year');

-- 같은 종목·같은 날짜를 두 번 넣지 못하게 막습니다.
-- 수집을 여러 번 돌려도 안전한 근거가 이 인덱스입니다 (ON CONFLICT 가 이걸 봅니다)
CREATE UNIQUE INDEX IF NOT EXISTS daily_candles_ticker_time_idx
  ON daily_candles (ticker, time DESC);
CREATE INDEX IF NOT EXISTS daily_candles_time_idx
  ON daily_candles (time DESC);

-- 거래일 캘린더. KIS 국내휴장일조회(실전 도메인 전용)로 받아 채웁니다 —
-- 시드에는 넣지 않습니다. 증분 수집이 "마지막 적재일 다음 거래일"을 구할 때 씁니다.
CREATE TABLE IF NOT EXISTS trading_days (
  date    DATE PRIMARY KEY,
  is_open BOOLEAN NOT NULL
);

-- 증분 수집 신선도. 스케줄러가 돌 때마다 종목별로 한 줄씩 덮어씁니다.
CREATE TABLE IF NOT EXISTS collection_status (
  ticker     TEXT PRIMARY KEY REFERENCES stocks(ticker) ON DELETE CASCADE,
  last_date  DATE,
  checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  status     TEXT NOT NULL CHECK (status IN ('success', 'partial', 'failed')),
  message    TEXT
);

-- 지수 구성종목 이력. 코스피100 은 반기마다 바뀌는데, 지금 종목만 쓰면
-- 중간에 빠진 종목이 통째로 사라지는 생존편향이 생깁니다. 시점별로 무엇이
-- 편입되어 있었는지 이 표가 들고 있고, universe.py · selection.py ·
-- ml_selection.py 가 그날 편입된 종목만 후보로 씁니다.
CREATE TABLE IF NOT EXISTS index_members (
  id         SERIAL PRIMARY KEY,
  index_name TEXT NOT NULL,
  ticker     TEXT NOT NULL REFERENCES stocks(ticker) ON DELETE CASCADE,
  start_date DATE NOT NULL,
  end_date   DATE,                              -- NULL = 지금도 편입 중
  CHECK (end_date IS NULL OR end_date >= start_date)
);

-- 같은 종목이 같은 날짜에 다시 편입되는 걸로 재실행해도 줄이 안 늘어나게
-- 막습니다 (ON CONFLICT 가 이걸 봅니다)
CREATE UNIQUE INDEX IF NOT EXISTS index_members_unique_idx
  ON index_members (index_name, ticker, start_date);

-- "이 지수, 이 날짜에 편입되어 있던 종목" 조회가 이걸 씁니다
CREATE INDEX IF NOT EXISTS index_members_lookup_idx
  ON index_members (index_name, start_date, end_date);

-- ──────────────────────────────────────────────────────────────
-- 로그인 · 권한
-- ──────────────────────────────────────────────────────────────

-- 시드에서 비밀번호를 bcrypt 로 해시할 때 씁니다 (crypt / gen_salt)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
  id            SERIAL PRIMARY KEY,
  username      TEXT NOT NULL UNIQUE,
  -- bcrypt 해시만 들어갑니다. 평문이 보이면 저장 경로가 잘못된 것입니다
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 사용자를 지우면 그 사람의 계좌 연결도 함께 사라집니다
CREATE TABLE IF NOT EXISTS accounts (
  account_no TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  broker     TEXT NOT NULL,
  mode       TEXT NOT NULL DEFAULT 'paper' CHECK (mode IN ('paper', 'live')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS accounts_user_idx ON accounts (user_id);

-- 세션도 같은 규칙입니다. 사용자가 사라지면 그 사람 세션도 사라집니다
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expires_idx ON sessions (expires_at);
