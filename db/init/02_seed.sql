INSERT INTO devices (device_id, name, location) VALUES
  ('dev-001', '거실 센서',   '거실'),
  ('dev-002', '침실 센서',   '침실'),
  ('dev-003', '베란다 센서', '베란다')
ON CONFLICT (device_id) DO NOTHING;

-- 최근 24시간, 1분 간격의 더미 데이터 (일주기 사인파 + 노이즈)
INSERT INTO readings (time, device_id, metric, value)
SELECT t,
       d.device_id,
       m.metric,
       round((m.base
              + m.amp * sin(extract(epoch FROM t) / 86400.0 * 2 * pi() + d.phase)
              + (random() - 0.5) * m.noise)::numeric, 2)::double precision
FROM generate_series(now() - INTERVAL '24 hours', now(), INTERVAL '1 minute') AS t
CROSS JOIN (VALUES ('dev-001', 0.0), ('dev-002', 0.6), ('dev-003', 1.2))
       AS d(device_id, phase)
CROSS JOIN (VALUES ('temperature', 23.0,  3.0,  0.6),
                   ('humidity',    48.0,  8.0,  1.5),
                   ('co2',        620.0, 120.0, 30.0))
       AS m(metric, base, amp, noise)
ON CONFLICT DO NOTHING;

CALL refresh_continuous_aggregate('readings_1m', NULL, NULL);

-- ──────────────────────────────────────────────────────────────
-- 수업용 계정 두 개
-- 비밀번호는 pgcrypto 로 bcrypt 해시해서 넣습니다. 평문은 어디에도 저장하지 않습니다.
-- gen_salt('bf', 12) 가 줄마다 다른 소금을 만들어서, 같은 비밀번호라도 저장값이 달라집니다.
-- ──────────────────────────────────────────────────────────────
INSERT INTO users (username, password_hash, role) VALUES
  ('admin', crypt('admin1234', gen_salt('bf', 12)), 'admin'),
  ('hong',  crypt('hong1234',  gen_salt('bf', 12)), 'user')
ON CONFLICT (username) DO NOTHING;

INSERT INTO accounts (account_no, user_id, broker, mode)
SELECT '50012345-01', id, '한국투자증권', 'paper' FROM users WHERE username = 'admin'
ON CONFLICT (account_no) DO NOTHING;

INSERT INTO accounts (account_no, user_id, broker, mode)
SELECT '50067890-01', id, '한국투자증권', 'paper' FROM users WHERE username = 'hong'
ON CONFLICT (account_no) DO NOTHING;
