# 아키텍처 — 초보자용 안내

이 프로젝트가 **무엇을 하는지**, **어떤 부품이 있는지**, **데이터가 어디서 어디로 흐르는지**를
식당에 빗대어 설명합니다. (더 자세한 그림은 [architecture.html](architecture.html))

## 1. 한 줄 요약

> 한국투자증권(KIS)에서 **주식 일봉**을 받아 → **DB에 쌓고** → **서버가 계산해서** → **화면에 그립니다.**
> 로그인이 있고, 역할(admin / user)에 따라 할 수 있는 일이 다릅니다.

## 2. 큰 그림 — 네 개의 컨테이너

`docker compose up` 한 번이면 아래 네 개가 함께 뜹니다.

```
   ┌──────────────┐
   │ 나 (브라우저) │
   └──────┬───────┘
          │ ① 화면 요청
          ▼
 ┌────────────────────┐   ② 데이터 요청 (/api/...)   ┌─────────────────────┐
 │  frontend (React)  │ ───────────────────────────▶ │  backend (FastAPI)  │
 │  localhost:5173    │ ◀─────────────────────────── │  localhost:8000     │
 │  = 홀 · 메뉴판     │        ⑤ 결과(JSON)          │  = 주방 · 요리사    │
 └────────────────────┘                              └───┬─────────┬───────┘
                                                         │         │
                                       ③ 읽기/쓰기 (SQL) │         │ ④ 일봉·휴장일 요청
                                                         ▼         ▼
                                       ┌────────────────────┐   ┌───────────────────┐
                                       │ timescaledb (DB)   │   │ 한국투자증권 KIS  │
                                       │ localhost:5432     │   │ (외부 서비스)     │
                                       │ = 냉장고 · 창고    │   │ = 식자재 납품업체 │
                                       └─────────▲──────────┘   └───────────────────┘
                                                 │ 들여다보기
                                       ┌─────────┴──────────┐
                                       │ adminer            │
                                       │ localhost:8080     │
                                       │ = 창고 점검용 유리창│
                                       └────────────────────┘
```

| 컨테이너 | 식당 비유 | 하는 일 | 기술 |
| --- | --- | --- | --- |
| frontend | 홀 · 메뉴판 | 로그인 화면, 차트, 표를 보여줌. 계산은 안 함 | React + Vite |
| backend | 주방 · 요리사 | 로그인·권한 확인, KIS에서 데이터 받기, 백테스트 계산 | FastAPI (Python) |
| timescaledb | 냉장고 | 일봉·종목·사용자·세션을 저장 | PostgreSQL + TimescaleDB |
| adminer | 냉장고 유리창 | DB 안을 눈으로 확인(확인용일 뿐, 기능 아님) | Adminer |

**핵심 규칙:** 화면은 DB에 직접 닿지 않습니다. 항상 `frontend → backend → DB` 순서입니다.

### DB를 직접 들여다보는 방법

DB(`localhost:5432`)는 내 PC에 포트가 열려 있어서, adminer 없이도 접속할 수 있습니다.
접속 정보(사용자·비밀번호·DB 이름)는 `.env` 의 `POSTGRES_*` 값입니다.

| 방법 | 주소 | 설명 |
| --- | --- | --- |
| Adminer | http://localhost:8080 | 브라우저로 보는 웹 화면. 서버 칸에는 `timescaledb` (컨테이너 안 이름) |
| DB 클라이언트 (DBeaver 등) | `localhost:5432` | PC에 설치한 도구로 직접 접속 |
| psql | `docker compose exec timescaledb psql -U <사용자> -d <DB이름>` | 터미널에서 접속 |

둘 다 **확인용**입니다. 여기서 줄을 고쳐 기능을 만들지 않습니다.

## 3. 폴더 지도

```
mini_trading_system/
├─ docker-compose.yml   ← 네 컨테이너를 어떻게 띄울지 적은 설계도
├─ .env.example         ← 필요한 설정의 "이름표" (값은 사람이 .env 에 채움. .env 는 건드리지 않음)
├─ CLAUDE.md            ← 이 프로젝트의 규칙·구조 요약
│
├─ frontend/            ← 【홀】 화면
│   └─ src/
│       ├─ main.tsx · App.tsx   앱 시작점, 로그인 껍데기와 탭
│       ├─ api.ts               백엔드에 말 거는 전화기 (fetch 래퍼)
│       └─ 화면 파일들 (아래 4장)
│
├─ backend/             ← 【주방】 서버
│   ├─ app/             서버 코드 (아래 5장)
│   │   └─ data/kospi100.csv   코스피 100 종목 목록
│   ├─ tests/           자동 테스트 (DB·KIS 없이도 돎)
│   └─ requirements.txt 필요한 파이썬 패키지 (머신러닝용 scikit-learn 포함 → 바뀌면 재빌드)
│
├─ db/init/             ← 【냉장고】 DB 설계도
│   ├─ 01_schema.sql    표(테이블) 만들기
│   └─ 02_seed.sql      종목 8개 + 수업용 계정 넣기
│
└─ docs/                ← 문서 (이 파일, schema.md, secrets-checklist.md …)
```

> 💡 `db/init/` 의 SQL 은 **빈 볼륨에 처음 뜰 때만** 실행됩니다.
> 스키마를 고쳤다면 `docker compose down -v && docker compose up -d --build`.

## 4. 화면(frontend) — 탭마다 파일 하나

| 파일 | 화면 | 누가 쓰나 |
| --- | --- | --- |
| `Login.tsx` | 로그인 | 모두 |
| `Market.tsx` | 시세 — 종목 고르기, 일봉 차트, 수집 | 조회: 모두 / 수집 버튼: admin |
| `Backtest.tsx` | 낮 vs 밤 vs 단순보유 백테스트 | 모두 |
| `Universe.tsx` | 여러 종목을 묶어 낮·밤·보유 비교 | 모두 |
| `Top5.tsx` | 매일 Top-K 종목 고르는 전략, 전략 비교 리더보드, **머신러닝(RandomForest) 결과 비교** | 조회: 모두 / 머신러닝 학습 버튼: admin |
| `Accounts.tsx` | 계좌 목록 (뒤 4자리만 보임) | user: 본인 것 / admin: 전체 |
| `Profile.tsx` | 내 정보 · 비밀번호 변경 | 본인 |
| `Users.tsx` | 사용자 관리 · KIS 토큰 | admin 전용 |
| `LineChart.tsx` | 선 차트 부품 (다른 화면이 가져다 씀) | — |

## 5. 서버(backend/app) — 역할별로 묶어 보기

파일이 많아 보이지만 **네 묶음**으로 나뉩니다.

```
        요청이 들어옴
            │
            ▼
 ┌─ ① 문지기 ──────────────────────────────┐
 │ main.py          모든 API 엔드포인트     │
 │ auth.py          로그인·세션·권한표      │
 │ access_log.py    접근 로그에서 계좌번호 가림│
 │ schemas.py       요청·응답 모양 정의     │
 └───────┬─────────────────────────────────┘
         │
   ┌─────┴───────────────────────┬───────────────────────────┐
   ▼                             ▼                           ▼
 ┌─ ② 데이터 들여오기 ─┐   ┌─ ③ 계산기 (DB 없는 순수 함수) ─┐   ┌─ ④ 기반 시설 ─┐
 │ kis.py        KIS 호출│   │ backtest.py   낮/밤/보유       │   │ db.py  DB 연결 │
 │ collection.py 증분 수집│   │ universe.py   종목 묶음 비교   │   └───────────────┘
 │ scheduler.py  평일 16시│   │ selection.py  Top-K 8대 전략   │
 │ trading_calendar.py   │   │ factors.py    파생 팩터 컬럼   │
 │   거래일 계산         │   │ ml_selection.py 머신러닝(RF)   │
 │ discord.py    수집 알림│   │ metrics.py    MDD·샤프·칼마    │
 └───────────────────────┘   └────────────────────────────────┘
```

- **① 문지기** — 요청이 오면 먼저 "로그인했나? 이 일을 할 권한이 있나?"를 확인합니다
  (`auth.require(user, "...")`). 화면에서 버튼을 숨기는 건 친절일 뿐, 진짜 차단은 여기서 합니다.
- **② 데이터 들여오기** — KIS에서 일봉을 가져와 DB에 넣습니다.
  버튼(수동)과 스케줄러(평일 16:00 자동)가 **같은 `collection.py`** 를 거칩니다.
- **③ 계산기** — DB를 모르는 **순수 함수**입니다. 숫자를 넣으면 숫자가 나옵니다.
  그래서 DB 없이 테스트하기 쉽습니다.
  - `selection.py` 는 점수식을 **사람이 정한** 8대 전략, `ml_selection.py` 는 점수식을
    **과거 데이터로 학습한** 머신러닝(RandomForest, scikit-learn) 전략입니다.
    피처는 `factors.py` 를 그대로 쓰고 선택·비용 차감·샤프 계산도 `selection.py` 를
    재사용해서, 둘을 같은 잣대로 비교합니다.
  - 학습한 모델은 컨테이너 안 `/tmp/ml-models/` 에만 저장됩니다(볼륨·git 으로 안 나감).
    이미지를 다시 빌드하면 사라지므로 admin 이 다시 학습하면 됩니다.
- **④ 기반 시설** — DB 연결 풀.

## 6. 데이터 흐름 세 가지

### 흐름 A. 데이터 쌓기 (수집)

```
 [평일 16:00 scheduler.py]  또는  [admin이 수집 버튼 클릭]
                 │
                 ▼
           collection.py ── 종목마다 반복 ──▶ kis.py ──▶ KIS 서버
                 │                              (토큰 발급·캐시)
                 │  ◀──────── 일봉 응답 ─────────┘
                 ▼
        daily_candles 테이블에 저장
        (같은 종목·같은 날짜는 덮어씀 → 여러 번 돌려도 줄이 안 늘어남)
                 │
                 ▼
          discord.py 로 요약 알림 (웹훅 없으면 조용히 건너뜀)
```

한 종목이 실패해도 나머지는 계속 돕니다 (예외를 전부 `KisError` 로 모으기 때문).

### 흐름 B. 화면에 그리기 (조회·백테스트)

```
 Top5.tsx 에서 전략 선택
      │  api.ts → GET /api/backtest/top5
      ▼
 main.py 가 로그인·권한 확인
      ▼
 DB에서 일봉 읽기 → selection.py 로 매일 Top-K 선택·종가 배팅 → metrics.py 로 MDD·샤프·칼마
      ▼
 JSON 으로 응답 → 화면이 타일·차트로 그림
```

### 흐름 C. 머신러닝 학습·비교 (Top5.tsx 안쪽)

```
 [admin: 학습 버튼]  POST /api/backtest/ml/train   (market:write 권한 필요)
      ▼
 ml_selection.py 가 선택한 기간 전체로 RandomForest 학습 → /tmp/ml-models/ 에 저장
      ▼
 [누구나: 화면 열기]  GET /api/backtest/ml/latest
      ▼
 저장된 모델을 재학습 없이 불러와 채점 → 8대 전략과 나란히 비교
 (학습된 모델이 없으면 404 model_not_trained → "admin 이 먼저 학습하세요" 안내)
```

## 7. DB 표 (db/init/01_schema.sql)

| 표 | 담는 것 | 비유 |
| --- | --- | --- |
| `stocks` | 종목 마스터 (티커, 이름) | 상품 목록 |
| `daily_candles` | 종목별 일봉 OHLCV (TimescaleDB 하이퍼테이블) | 매일 쌓이는 장부 |
| `trading_days` | 거래일 캘린더 | 영업일 달력 |
| `users` | 사용자 + 비밀번호 **해시** | 회원 명부 |
| `accounts` | 계좌 | 회원별 통장 |
| `sessions` | 로그인 세션 | 출입증 |

## 8. 꼭 지키는 안전 규칙 (왜 코드가 이렇게 생겼나)

| 규칙 | 이유 |
| --- | --- |
| 비밀번호는 bcrypt 해시로만 저장·비교 | 유출돼도 원문을 못 알아냄 |
| 앱키·토큰은 서버 밖으로 안 나감 | 브라우저엔 만료 시각과 앞 네 글자만 |
| Discord 웹훅 주소도 비밀 | URL 안에 토큰이 들어 있음 |
| 계좌번호는 뒤 4자리만 (화면·로그 모두) | 로그를 통한 유출 방지 |
| 실패를 셋으로 구분: 401 `invalid_credentials` / 401 `session_expired` / 403 `forbidden` | 원인에 맞는 화면 처리 |
| SQL(DDL)은 `db/init/` 에만 | 스키마가 한곳에서 관리됨 |
| 실습은 모의(paper) 도메인, 거래일 캘린더만 실전(live) 조회 | 모의 서버가 그 TR 을 거절함. 주문은 내지 않음 |

## 9. 처음 읽는 순서 추천

1. `docker-compose.yml` — 어떤 부품이 있는지
2. `frontend/src/App.tsx` → `api.ts` — 화면이 서버에 어떻게 말을 거는지
3. `backend/app/main.py` — 엔드포인트 목록(`/api/...`)
4. `backend/app/auth.py` — 권한표 `PERMISSIONS`
5. `backend/app/collection.py` → `kis.py` — 데이터가 들어오는 길
6. `backend/app/backtest.py` → `selection.py` — 계산이 어떻게 이뤄지는지
7. `backend/tests/` — 각 파일이 "무엇을 보장하는지" 이름만 봐도 알 수 있음
