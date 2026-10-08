# 미니 트레이딩 시스템

한국투자증권(KIS)에서 일봉을 받아 TimescaleDB 에 쌓고, FastAPI 로 읽어,
React 화면에 그립니다. 로그인과 역할(admin / user)이 붙어 있습니다.

## 실행

```bash
docker compose up -d --build
docker compose ps          # 네 컨테이너가 전부 up 이어야 합니다
docker compose logs -f backend
docker compose exec backend python -m pytest   # 권한 · 토큰 캐시 테스트
```

| 주소 | 무엇 |
| --- | --- |
| http://localhost:5173 | 화면 (React + Vite) |
| http://localhost:8000/docs | API 문서 (FastAPI) |
| http://localhost:8080 | Adminer — DB 를 들여다보는 도구 |

수업용 계정: `admin / admin1234`, `hong / hong1234`.

스키마와 시드는 `db/init/` 의 파일로, **빈 볼륨에 처음 뜰 때만** 실행됩니다.
스키마를 고쳤으면 `docker compose down -v && docker compose up -d --build` 로
볼륨째 다시 만듭니다 (측정값은 전부 더미 데이터입니다).

## 구조

```
backend/app/
  main.py      엔드포인트. 권한 검사를 여기서 호출합니다
  auth.py      비밀번호 해시 · 세션 · PERMISSIONS 표
  access_log.py  접근 로그에서 계좌번호를 가립니다
  kis.py       앱키 → 접근 토큰(캐시) → 일봉 · 거래일 캘린더 조회
  trading_calendar.py  거래일 계산(DB 없는 순수 함수) — 「n거래일 전」·「다음 거래일」
  db.py        asyncpg 연결 풀
  schemas.py   요청 · 응답 모델 (pydantic)
frontend/src/
  App.tsx      로그인 껍데기와 탭
  Login.tsx    로그인 화면
  Profile.tsx  내 정보 — 아이디 · 비밀번호 변경 (역할은 못 바꿉니다)
  Market.tsx   시세 — 종목 고르기 · 일봉 차트 · 수집 (수집 버튼은 admin 만)
  LineChart.tsx 선 차트. (키 · 이름 · 점들) 만 받습니다
  Accounts.tsx 계좌 (user 는 본인 것만)
  Users.tsx    사용자 · KIS 토큰 (admin 전용)
  api.ts       fetch 래퍼. ApiError 가 서버의 실패 코드를 그대로 들고 옵니다
db/init/
  01_schema.sql  stocks · daily_candles(하이퍼테이블) · trading_days ·
                 users / accounts / sessions
  02_seed.sql    종목 마스터 8개와 수업용 계정 (일봉·캘린더는 KIS 에서 받습니다)
backend/tests/
  test_permissions.py  user 세션으로 관리자 API → 403. DB 없이 돕니다
  test_kis_cache.py    발급 횟수 · 만료 · 재시작 · 로그 누출
  test_daily_candles.py  저장해 둔 KIS 응답으로 파서 확인
  test_trading_calendar.py  거래일 계산기 — 공휴일 fixture 로 확인
  test_access_log.py   계좌번호가 로그에 남지 않는지
  fixtures/daily_response.json  실제로 받은 일봉 응답 한 건
  fixtures/holiday_response.json  실제로 받은 휴장일 응답 (한글날 포함)
docs/
  schema.md      돌고 있는 DB 에서 읽어 적은 스키마 표
  secrets-checklist.md  비밀이 로그·화면·커밋으로 새지 않는지 확인한 기록
```

## 규칙

- **`.env` 는 읽지도 고치지도 않습니다.** 새 설정이 필요하면 `.env.example` 에 키 이름만
  추가하고, 값은 사람이 채우게 둡니다.
- **비밀번호는 평문으로 다루지 않습니다.** 저장은 bcrypt 해시로만 하고, 비교는
  `auth.verify_password` 로만 합니다. 로그·응답·예외 메시지에 원문이나 해시를 싣지 않습니다.
- **앱키와 토큰은 서버 밖으로 내보내지 않습니다.** 브라우저에는 발급 여부와 만료 시각,
  가려진 앞 네 글자만 내려보냅니다. 토큰은 컨테이너 안 `/tmp/kis-tokens` 에도
  남깁니다(0600) — KIS 가 발급을 1분에 한 번으로 제한해서, 재시작마다 새로 받으면
  막힙니다. 이 경로는 볼륨이 아니라 호스트·git 으로 나가지 않습니다.
- **실습은 모의(paper) 도메인으로 합니다.** 실전·모의 키를 섞으면 엉뚱한 서버에
  엉뚱한 키가 갑니다. 일봉 조회는 모의에서도 나옵니다.
- **거래일 캘린더(국내휴장일조회)만 예외로 실전(live) 도메인을 씁니다.** 이 TR 은
  계좌와 무관한 시장 전체의 공개 정보인데도 모의 도메인이 거절합니다
  (`EGW02006 모의투자 TR 이 아닙니다`). `kis.fetch_trading_calendar` 가 내부적으로
  항상 실전 도메인을 쓰고, mode 인자를 안 받는 것 자체가 그 뜻입니다. 조회만 하고
  주문은 내지 않습니다 — 2026-10-08 사용자 승인 하에 적용했습니다.
- **권한 판단은 `auth.PERMISSIONS` 를 봅니다.** 화면에서 버튼을 숨기는 것은 친절일 뿐이고,
  모든 엔드포인트에서 `auth.require(user, "...")` 로 다시 막습니다.
- **실패는 세 가지를 구분합니다.** 인증 실패(401 `invalid_credentials`),
  세션 만료(401 `session_expired`), 권한 부족(403 `forbidden`). 하나로 뭉치지 않습니다.
- **SQL 은 `db/init/` 에만 씁니다.** 애플리케이션 코드에서 DDL 을 실행하지 않습니다.
- **같은 종목·같은 날짜는 덮어씁니다.** 수집을 여러 번 돌려도 줄이 늘지 않아야 합니다
  (`daily_candles_ticker_time_idx` 유니크 인덱스 + `ON CONFLICT`).
- **Adminer 는 확인용 도구입니다.** 거기서 줄을 고쳐 기능을 만들지 않습니다.
- **계좌번호는 뒤 네 자리만 남깁니다.** 화면(`maskAccountNo`)뿐 아니라 접근 로그도
  그렇습니다 — 계좌번호가 URL 경로에 실려서 `access_log.py` 가 가립니다.
- 주석과 문서, 화면 문구는 한국어로 씁니다.
- 파이썬은 표준 라이브러리 → 서드파티 → 로컬 순으로 import 하고, 타입 힌트를 답니다.
  타입스크립트는 `strict` 입니다 — `any` 를 넣지 않습니다.

## 일을 시킬 때

1. 고치기 전에 **계획**을 먼저 달라고 합니다 — 건드릴 파일 목록, `.env` 를 건드리는지,
   스키마를 바꾸는지, 새 패키지를 까는지.
2. 계획을 읽고 승인한 뒤에 실행시킵니다.
3. 끝나면 바뀐 파일을 직접 열어 봅니다. 읽지 않은 코드는 커밋하지 않습니다.

명세로 시킬 때는 이 정도까지 적습니다.

```
POST /api/login
요청  username, password
응답  role, 세션 쿠키
실패  401 invalid_credentials / 401 session_expired / 403 forbidden
파일  backend/app/auth.py 에 로직, main.py 에 엔드포인트
금지  .env 수정, 평문 비교, 응답에 해시 포함
```
