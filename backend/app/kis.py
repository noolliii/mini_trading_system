"""앱키에서 토큰까지.

app_key + app_secret → .env → 토큰 요청 → 접근 토큰.

- 실전과 모의를 두 벌로 나눠서 읽습니다. 섞어 쓰면 엉뚱한 서버에 엉뚱한 키를 보냅니다.
- 키와 토큰은 코드에 적지 않고 서버 안에만 둡니다. 브라우저로는 발급 여부와
  만료 시각, 그리고 가려진 앞 네 글자만 내려보냅니다.
- 함수 정리와 토큰 재사용 캐시는 다음 주에 합니다. 지금은 부를 때마다 새로 받습니다.
"""

import os
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel

HOSTS = {
    "paper": "https://openapivts.koreainvestment.com:29443",  # 모의투자
    "live": "https://openapi.koreainvestment.com:9443",  # 실전투자
}

ENV_PREFIX = {"paper": "KIS_PAPER", "live": "KIS_LIVE"}

TOKEN_PATH = "/oauth2/tokenP"


class TokenIssue(BaseModel):
    """브라우저로 내려보내도 되는 것만 담습니다. access_token 자체는 들어가지 않습니다."""

    mode: str
    host: str
    issued_at: datetime
    expires_in: int
    token_preview: str


class KisError(RuntimeError):
    """설정이 없거나 발급이 거절된 경우. 메시지에 키 값을 넣지 않습니다."""


def credentials(mode: str) -> tuple[str, str]:
    if mode not in ENV_PREFIX:
        raise KisError("mode 는 paper 또는 live 여야 합니다")

    prefix = ENV_PREFIX[mode]
    app_key = os.getenv(f"{prefix}_APP_KEY", "").strip()
    app_secret = os.getenv(f"{prefix}_APP_SECRET", "").strip()

    if not app_key or not app_secret:
        raise KisError(f"{prefix}_APP_KEY 와 {prefix}_APP_SECRET 를 .env 에 채워 주세요")

    return app_key, app_secret


def mask(token: str) -> str:
    """토큰을 그대로 보여주지 않습니다. 앞 네 글자와 길이만."""
    return f"{token[:4]}… ({len(token)}자)"


async def request_access_token(mode: str) -> TokenIssue:
    app_key, app_secret = credentials(mode)
    host = HOSTS[mode]

    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.post(
            f"{host}{TOKEN_PATH}",
            json={
                "grant_type": "client_credentials",
                "appkey": app_key,
                "appsecret": app_secret,
            },
        )

    if res.status_code != 200:
        # 응답 본문에 보낸 키가 그대로 되돌아오는 경우가 있어 본문을 싣지 않습니다
        raise KisError(f"토큰 발급이 거절되었습니다 (HTTP {res.status_code})")

    body = res.json()
    token = body.get("access_token")
    if not token:
        raise KisError("응답에 access_token 이 없습니다")

    return TokenIssue(
        mode=mode,
        host=host,
        issued_at=datetime.now(timezone.utc),
        expires_in=int(body.get("expires_in", 0)),
        token_preview=mask(token),
    )
