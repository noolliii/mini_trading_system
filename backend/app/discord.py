"""증분 수집이 끝나면 Discord 로 요약을 보냅니다.

웹훅 주소 자체가 비밀입니다 — URL 안에 토큰이 박혀 있어서, 아는 사람은
그 주소만으로 그 채널에 글을 쓸 수 있습니다. 그래서 로그에도, 예외
메시지에도, 응답에도 주소를 싣지 않습니다.
"""

import logging
import os

import httpx

log = logging.getLogger(__name__)


def configured() -> bool:
    return bool(os.getenv("DISCORD_WEBHOOK_URL", "").strip())


async def send_summary(success: int, failed: int, skipped: int, details: list[str]) -> None:
    """설정이 안 되어 있으면 조용히 건너뜁니다 — 수업 환경에선 웹훅이 없는 게 기본입니다."""
    url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        log.info("DISCORD_WEBHOOK_URL 이 없어 알림을 건너뜁니다 (성공 %d · 실패 %d)", success, failed)
        return

    lines = [f"**증분 수집 완료** — 성공 {success} · 실패 {failed} · 건너뜀 {skipped}"]
    lines.extend(f"- {d}" for d in details[:20])  # 너무 길어지지 않게 자릅니다
    content = "\n".join(lines)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, json={"content": content})
        if res.status_code >= 300:
            # 본문에 우리가 보낸 내용이 그대로 되돌아올 수 있어 본문을 싣지 않습니다
            log.warning("Discord 알림이 거절되었습니다 (HTTP %d)", res.status_code)
    except httpx.HTTPError as e:
        # 알림이 실패해도 수집 자체는 끝난 일입니다 — 여기서 올려치지 않습니다
        log.warning("Discord 알림을 보내지 못했습니다: %s", e.__class__.__name__)
