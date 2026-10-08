# 스키마 — Adminer 로 읽은 것

`db/init/01_schema.sql` 이 만든 결과를 **실제 DB 에서 다시 읽어** 적었습니다.
SQL 파일을 옮겨 적은 것이 아니라, 돌고 있는 컨테이너의 `information_schema` ·
`pg_constraint` 를 조회한 값입니다.

확인한 날짜: 2026-10-08 · 조회 대상: `iot` 데이터베이스 (`iot-timescaledb`)
(센서를 걷어내고 종목·일봉으로 바꾼 뒤 다시 읽었습니다)

Adminer 에서 같은 것을 보려면 http://localhost:8080 → System `PostgreSQL` ·
Server `timescaledb` · Username·Password·Database 는 `.env` 의 값.

## 테이블 한눈에

| 테이블 | 무엇 | PK | FK |
| --- | --- | --- | --- |
| `stocks` | 종목 마스터 | `ticker` | — |
| `daily_candles` | 일봉 (하이퍼테이블) | 없음 — 유니크 인덱스로 대신 | `ticker` → `stocks` CASCADE |
| `users` | 로그인 계정 | `id` | — |
| `accounts` | 증권 계좌 | `account_no` | `user_id` → `users` CASCADE |
| `sessions` | 로그인 세션 | `session_id` | `user_id` → `users` CASCADE |

## stocks

| 칼럼 | 자료형 | NULL | 기본값 |
| --- | --- | --- | --- |
| `ticker` | text | NO | — |
| `name` | text | NO | — |
| `market` | text | NO | — |
| `created_at` | timestamptz | NO | `now()` |

- PK: `ticker` (종목코드 여섯 자리)
- CHECK: `market IN ('KOSPI','KOSDAQ')`
- 인덱스: `stocks_name_idx` (`name`) — 이름으로 찾는 검색이 씁니다
- 시드에 실제 종목 8개가 들어 있습니다 (삼성전자 005930 등)

## daily_candles

| 칼럼 | 자료형 | NULL | 기본값 |
| --- | --- | --- | --- |
| `time` | timestamptz | NO | — |
| `ticker` | text | NO | — |
| `open` / `high` / `low` / `close` | double precision | NO | — |
| `volume` | bigint | NO | — |

- **PK 가 없습니다.** 하이퍼테이블은 시간으로 쪼개 두기 때문에, PK 대신
  `daily_candles_ticker_time_idx` 유니크 인덱스 (`ticker`, `time DESC`) 로 막습니다.
  수집을 여러 번 돌려도 줄이 늘지 않는 근거가 이 인덱스입니다 (`ON CONFLICT` 가 이걸 봅니다).
- FK: `ticker` → `stocks(ticker)` **ON DELETE CASCADE**
- 인덱스: `daily_candles_time_idx` (`time DESC`)
- 하이퍼테이블 · 조각 2개 · `time` 은 거래일 00:00 (Asia/Seoul)
- 일봉은 하루에 한 줄이라 조각을 1년 단위로 잡았습니다 (센서값처럼 쏟아지지 않습니다)
- 현재 244행 — **KIS 모의 도메인에서 받은 삼성전자 실제 일봉** (2025-10-10 ~ 2026-10-08)

## users

| 칼럼 | 자료형 | NULL | 기본값 |
| --- | --- | --- | --- |
| `id` | integer | NO | `nextval('users_id_seq')` |
| `username` | text | NO | — |
| `password_hash` | text | NO | — |
| `role` | text | NO | `'user'` |
| `created_at` | timestamptz | NO | `now()` |

- PK: `id` · UNIQUE: `username`
- CHECK: `role IN ('admin','user')` — 역할은 DB 가 둘로 못박아 둡니다
- 기본값이 `'user'` 라서, 역할을 빼고 넣으면 항상 일반 사용자입니다

## accounts

| 칼럼 | 자료형 | NULL | 기본값 |
| --- | --- | --- | --- |
| `account_no` | text | NO | — |
| `user_id` | integer | NO | — |
| `broker` | text | NO | — |
| `mode` | text | NO | `'paper'` |
| `created_at` | timestamptz | NO | `now()` |

- PK: `account_no`
- FK: `user_id` → `users(id)` **ON DELETE CASCADE**
- CHECK: `mode IN ('paper','live')` — 모의·실전을 문자열 오타로 섞지 못하게
- 인덱스: `accounts_user_idx` (`user_id`) — 「내 계좌만」 조회가 이걸 씁니다

## sessions

| 칼럼 | 자료형 | NULL | 기본값 |
| --- | --- | --- | --- |
| `session_id` | text | NO | — |
| `user_id` | integer | NO | — |
| `created_at` | timestamptz | NO | `now()` |
| `expires_at` | timestamptz | NO | — |

- PK: `session_id` · FK: `user_id` → `users(id)` **ON DELETE CASCADE**
- 인덱스: `sessions_expires_idx` (`expires_at`) — 만료된 줄을 치울 때 씁니다

## 눈으로 확인한 것

**1. 비밀번호가 해시로 저장되는가 — 통과**

```
 username | prefix  | len
----------+---------+-----
 admin    | $2a$12$ |  60
 hong     | $2a$12$ |  60
```

평문은 없습니다. 다만 접두사가 `$2b$` 가 아니라 **`$2a$`** 입니다 —
`db/init/02_seed.sql` 이 pgcrypto 의 `crypt(..., gen_salt('bf',12))` 로 만든
해시라서 그렇습니다. 화면에서 추가한 사용자는 파이썬 `bcrypt` 가 만들어
`$2b$12$` 로 들어갑니다. 둘 다 bcrypt 이고 `cost=12` 이며,
`auth.verify_password` 는 두 접두사를 모두 확인합니다.

**2. 사용자를 지우면 계좌·세션도 사라지는가 — 통과**

임시 사용자에 계좌와 세션을 하나씩 달고 지워 봤습니다 (`ROLLBACK` 으로 되돌림).

```
   시점    | 계좌 | 세션
-----------+------+------
 지우기 전 |    1 |    1
 지운 후   |    0 |    0
```

**3. 외래키에 ON DELETE CASCADE 가 걸렸는가 — 통과**

```
accounts_user_id_fkey  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
sessions_user_id_fkey  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
readings_device_id_fkey FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
```

## 지금 데이터 상태에 관한 메모

센서(`devices` · `readings` · `readings_1m`)는 걷어냈습니다. 주식과 관계가 없고,
연습용 더미였습니다. 시간축 구조(하이퍼테이블 · 유니크 인덱스로 중복 막기)는
`daily_candles` 가 그대로 물려받았습니다.

일봉은 시드에 넣지 않습니다. 종목 마스터만 두고, 값은
`POST /api/stocks/{ticker}/collect` 로 KIS 에서 받아 채웁니다 (admin 전용).
