-- ──────────────────────────────────────────────────────────────
-- 종목 마스터
-- 실제 종목코드입니다. 일봉은 여기 넣지 않습니다 — KIS 에서 받아 채웁니다.
-- ──────────────────────────────────────────────────────────────
INSERT INTO stocks (ticker, name, market) VALUES
  ('005930', '삼성전자',     'KOSPI'),
  ('000660', 'SK하이닉스',   'KOSPI'),
  ('035420', 'NAVER',        'KOSPI'),
  ('035720', '카카오',       'KOSPI'),
  ('005380', '현대차',       'KOSPI'),
  ('068270', '셀트리온',     'KOSPI'),
  ('247540', '에코프로비엠', 'KOSDAQ'),
  ('091990', '셀트리온헬스케어', 'KOSDAQ')
ON CONFLICT (ticker) DO NOTHING;

-- ──────────────────────────────────────────────────────────────
-- 수업용 계정 두 개
-- 비밀번호는 pgcrypto 로 bcrypt 해시해서 넣습니다. 평문은 어디에도 저장하지 않습니다.
-- gen_salt('bf', 12) 가 줄마다 다른 소금을 만들어서, 같은 비밀번호라도 저장값이 달라집니다.
-- ──────────────────────────────────────────────────────────────
INSERT INTO users (username, password_hash, role) VALUES
  ('admin', crypt('admin1234', gen_salt('bf', 12)), 'admin'),
  ('hong',  crypt('hong1234',  gen_salt('bf', 12)), 'user')
ON CONFLICT (username) DO NOTHING;

-- 아래 계좌번호 두 개는 수업용으로 지어낸 값입니다. 실제 계좌가 아닙니다.
-- 실계좌번호는 시드에도 커밋에도 넣지 않습니다 — 화면에서 직접 등록하세요.
INSERT INTO accounts (account_no, user_id, broker, mode)
SELECT '50012345-01', id, '한국투자증권', 'paper' FROM users WHERE username = 'admin'
ON CONFLICT (account_no) DO NOTHING;

INSERT INTO accounts (account_no, user_id, broker, mode)
SELECT '50067890-01', id, '한국투자증권', 'paper' FROM users WHERE username = 'hong'
ON CONFLICT (account_no) DO NOTHING;
