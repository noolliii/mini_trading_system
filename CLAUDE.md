# IoT 센서 대시보드

센서 측정값을 TimescaleDB 에 쌓고, FastAPI 로 읽어, React 화면에 그립니다.
로그인과 역할(admin / user)이 붙어 있습니다.

## 실행

```bash
docker compose up -d --build
docker compose ps          # 네 컨테이너가 전부 up 이어야 합니다
docker compose logs -f backend
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
  kis.py       한국투자증권 앱키 → 접근 토큰
  db.py        asyncpg 연결 풀
  schemas.py   요청 · 응답 모델 (pydantic)
frontend/src/
  App.tsx      로그인 껍데기와 탭
  Login.tsx    로그인 화면
  Dashboard.tsx 센서 차트
  Accounts.tsx 계좌 (user 는 본인 것만)
  Users.tsx    사용자 · KIS 토큰 (admin 전용)
  api.ts       fetch 래퍼. ApiError 가 서버의 실패 코드를 그대로 들고 옵니다
db/init/
  01_schema.sql  테이블 · 하이퍼테이블 · 연속 집계 · users / accounts / sessions
  02_seed.sql    더미 측정값과 수업용 계정
```

## 규칙

- **`.env` 는 읽지도 고치지도 않습니다.** 새 설정이 필요하면 `.env.example` 에 키 이름만
  추가하고, 값은 사람이 채우게 둡니다.
- **비밀번호는 평문으로 다루지 않습니다.** 저장은 bcrypt 해시로만 하고, 비교는
  `auth.verify_password` 로만 합니다. 로그·응답·예외 메시지에 원문이나 해시를 싣지 않습니다.
- **앱키와 토큰은 서버 밖으로 내보내지 않습니다.** 브라우저에는 발급 여부와 만료 시각,
  가려진 앞 네 글자만 내려보냅니다.
- **권한 판단은 `auth.PERMISSIONS` 를 봅니다.** 화면에서 버튼을 숨기는 것은 친절일 뿐이고,
  모든 엔드포인트에서 `auth.require(user, "...")` 로 다시 막습니다.
- **실패는 세 가지를 구분합니다.** 인증 실패(401 `invalid_credentials`),
  세션 만료(401 `session_expired`), 권한 부족(403 `forbidden`). 하나로 뭉치지 않습니다.
- **SQL 은 `db/init/` 에만 씁니다.** 애플리케이션 코드에서 DDL 을 실행하지 않습니다.
- **Adminer 는 확인용 도구입니다.** 거기서 줄을 고쳐 기능을 만들지 않습니다.
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
