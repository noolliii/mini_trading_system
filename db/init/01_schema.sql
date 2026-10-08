CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS devices (
  device_id  TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  location   TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS readings (
  time      TIMESTAMPTZ      NOT NULL,
  device_id TEXT             NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
  metric    TEXT             NOT NULL,
  value     DOUBLE PRECISION NOT NULL
);

SELECT create_hypertable('readings', 'time',
                         if_not_exists => TRUE,
                         chunk_time_interval => INTERVAL '1 day');

-- 같은 장치/지표/시각의 중복 적재를 막아 재전송에 안전하게 만듭니다
CREATE UNIQUE INDEX IF NOT EXISTS readings_device_metric_time_idx
  ON readings (device_id, metric, time DESC);
CREATE INDEX IF NOT EXISTS readings_metric_time_idx
  ON readings (metric, time DESC);

ALTER TABLE readings SET (
  timescaledb.compress,
  timescaledb.compress_segmentby = 'device_id, metric'
);
SELECT add_compression_policy('readings', INTERVAL '7 days', if_not_exists => TRUE);

-- 넓은 구간 조회는 원본 대신 1분 연속 집계를 읽습니다
CREATE MATERIALIZED VIEW IF NOT EXISTS readings_1m
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 minute', time) AS bucket,
       device_id,
       metric,
       avg(value) AS avg_value,
       min(value) AS min_value,
       max(value) AS max_value
FROM readings
GROUP BY bucket, device_id, metric
WITH NO DATA;

SELECT add_continuous_aggregate_policy('readings_1m',
  start_offset      => INTERVAL '3 hours',
  end_offset        => INTERVAL '1 minute',
  schedule_interval => INTERVAL '1 minute',
  if_not_exists     => TRUE);

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
