# 새는 곳 점검표

비밀이 **로그 · 화면 · 커밋** 셋 중 어디로도 나가지 않는지 확인한 기록입니다.
칸마다 어떻게 확인했는지와 실제 결과를 함께 적었습니다.

확인한 날짜: 2026-10-08

## 결과

|  | 로그 | 화면 | 커밋 |
| --- | --- | --- | --- |
| 비밀번호 | 안 보임 | 안 보임 | 안 보임 |
| 접근 토큰 | 안 보임 | 앞 네 글자만 | 안 보임 |
| 앱키 · 시크릿 | 안 보임 | 안 보임 | 안 보임 |
| 계좌번호 | 뒤 네 자리만 | 뒤 네 자리만 | 지어낸 값만 |

## 비밀번호

- **저장** — bcrypt 해시만. 시드는 pgcrypto `crypt(gen_salt('bf',12))`, 화면에서
  추가한 사용자는 파이썬 `bcrypt`. 접두사가 `$2a$` / `$2b$` 로 갈리지만 둘 다 bcrypt 입니다.
- **비교** — `auth.verify_password` 한 곳에서만. 저장값이 bcrypt 가 아니면
  (= 평문이 들어가 있으면) 맞춰보지 않고 그냥 실패시킵니다.
- **응답** — `UserOut` · `Me` 에 비밀번호 칸이 없습니다. 해시도 내려보내지 않습니다.
- **로그** — 평문도 해시도 로그에 넣는 코드가 없습니다.

```bash
docker compose logs backend | grep -icE "password|비밀번호"   # 0
docker compose exec timescaledb psql -U iot -d iot \
  -c "SELECT username, left(password_hash,7) FROM users;"      # $2a$12$ / $2b$12$
```

## 접근 토큰 (KIS)

- **응답** — `kis.TokenIssue` 에 토큰 원문이 없습니다. `token_preview` 가
  앞 네 글자와 길이만 담습니다 (`kis.mask`).
- **로그** — 발급할 때 남기는 것은 모드와 **만료 시각**뿐입니다.
- **서버 안** — 메모리 캐시와 컨테이너 안 `/tmp/kis-tokens/*.json`(권한 0600).
  볼륨이 아니라 호스트·git 으로 나가지 않고, 컨테이너를 다시 만들면 사라집니다.
- 테스트가 이것을 지킵니다 — `tests/test_kis_cache.py::test_token_never_leaves_the_server`
  가 로그 전체와 응답 JSON 에 토큰 문자열이 없는지 확인합니다.

## 앱키 · 시크릿

- `.env` 에만 있고, 코드에는 키 이름만 있습니다 (`kis.credentials` 가 `os.getenv`).
- `.gitignore` 가 `.env` 를 막습니다. 커밋되는 `.env.example` 은 비밀 칸을 비워 둡니다.
- 발급이 거절될 때도 KIS 응답 본문을 그대로 싣지 않습니다 — 보낸 키가 되돌아오는
  경우가 있어서, 사람이 읽을 코드와 설명만 꺼냅니다 (`EGW00133` 등).
- 저장해 둔 KIS 응답(`tests/fixtures/daily_response.json`)에도 자격증명이 없습니다.

```bash
git check-ignore -v .env                                     # .gitignore:9:.env
git ls-files | xargs grep -lE "[A-Za-z0-9]{40,}"             # (없음)
grep -icE "appkey|appsecret|authorization" \
  backend/tests/fixtures/daily_response.json                 # 0
```

## 계좌번호

여기가 한 번 샜던 자리입니다.

- **화면** — `Accounts.tsx` 의 `maskAccountNo` 가 `••••-9999` 로 가립니다.
- **로그** — 계좌 해제는 계좌번호를 URL 경로에 담습니다
  (`DELETE /api/accounts/{account_no}`). uvicorn 접근 로그는 경로를 그대로 찍어서,
  **화면에서 가려도 로그로 샜습니다.**

  ```
  고치기 전:  "DELETE /api/accounts/50012345-01 HTTP/1.1" 204
  고친 후:    "DELETE /api/accounts/••••-5-01 HTTP/1.1" 204
  ```

  `app/access_log.py` 의 필터가 로그 한 줄이 만들어지기 직전에 경로를 바꿔 끼웁니다.
  줄을 지우는 것이 아니라 한 조각만 가리므로, 어떤 요청이 들어왔는지는 그대로 보입니다.
  `tests/test_access_log.py` 가 이것을 지킵니다.

- **커밋** — `db/init/02_seed.sql` 에 계좌번호 두 개(`50012345-01`, `50067890-01`)와
  `Accounts.tsx` 의 입력창 예시 하나가 있습니다. **모두 지어낸 값**이고 주석에 그렇게
  적어 두었습니다. 실계좌번호는 시드에도 커밋에도 넣지 않고, 화면에서 등록합니다.

```bash
docker compose logs backend | grep -oE "[0-9]{8}-[0-9]{2}"   # (없음)
```

## 다시 확인하는 방법

```bash
docker compose exec backend python -m pytest    # 33개. 누출 관련 테스트가 섞여 있습니다
docker compose logs backend | grep -icE "password|appkey|appsecret|access_token|Bearer "
docker compose logs backend | grep -oE "[0-9]{8}-[0-9]{2}"
git check-ignore -v .env
git ls-files | xargs grep -lE "[A-Za-z0-9]{40,}"
```

## 남은 권고

계좌번호를 URL 에서 아예 빼려면 `accounts` 에 별도 `id` 를 두고 경로에 그것을 쓰면
됩니다. 지금은 가리는 것으로 막았습니다 — 스키마를 바꾸는 일이라 따로 정하는 게 맞습니다.
