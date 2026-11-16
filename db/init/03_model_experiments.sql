-- ──────────────────────────────────────────────────────────────
-- Week 10 — 뉴럴 네트워크 IS/Val/OOS 비교 결과
-- ──────────────────────────────────────────────────────────────

-- 세 세대(룰베이스 · 전통 ML · 신경망)의 Train(IS)/Val/OOS 백테스트 성과와
-- 모델 메타데이터를 남깁니다. generation·model_key·period 조합마다 한 줄이고,
-- 같은 조합으로 다시 학습·채점해도 줄이 늘지 않습니다(ON CONFLICT 가 덮어씀) —
-- daily_candles_ticker_time_idx 와 같은 컨벤션입니다.
CREATE TABLE IF NOT EXISTS model_experiments (
  id              BIGSERIAL PRIMARY KEY,
  run_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  generation      TEXT NOT NULL CHECK (generation IN ('rule_based', 'traditional_ml', 'neural_net')),
  model_key       TEXT NOT NULL,                    -- 예: "vol_filtered_edge" · "random_forest" · "lstm"
  model_label     TEXT NOT NULL,                    -- 화면에 보여줄 이름
  period          TEXT NOT NULL CHECK (period IN ('is', 'val', 'oos')),
  k               INTEGER NOT NULL,                 -- Week 10 규격상 항상 5 고정이지만 그대로 남깁니다
  cost            DOUBLE PRECISION NOT NULL,
  final_portfolio DOUBLE PRECISION NOT NULL,
  final_universe  DOUBLE PRECISION NOT NULL,
  win_rate        DOUBLE PRECISION NOT NULL,
  trading_nights  INTEGER NOT NULL,
  mdd             DOUBLE PRECISION NOT NULL,
  sharpe          DOUBLE PRECISION NOT NULL,
  calmar          DOUBLE PRECISION NOT NULL,
  cagr            DOUBLE PRECISION NOT NULL,
  universe_mdd    DOUBLE PRECISION NOT NULL,
  universe_sharpe DOUBLE PRECISION NOT NULL,
  universe_calmar DOUBLE PRECISION NOT NULL,
  universe_cagr   DOUBLE PRECISION NOT NULL,
  hyperparams     JSONB,                            -- 신경망·전통ML만: hidden_dim·dropout·weight_decay 등
  param_count     INTEGER,                           -- 신경망만: 총 파라미터 수
  loss_curve      JSONB,                             -- 신경망만: [{epoch, train_loss, val_loss}, ...]
  points          JSONB NOT NULL                     -- [{time, cum_portfolio, cum_universe}, ...] — 화면 누적수익률 차트용
);

CREATE UNIQUE INDEX IF NOT EXISTS model_experiments_unique_idx
  ON model_experiments (generation, model_key, period);

-- "최신 비교 결과 전체 조회" 가 이걸 씁니다
CREATE INDEX IF NOT EXISTS model_experiments_lookup_idx
  ON model_experiments (generation, period);
