"""접근 로그에서 계좌번호를 가립니다.

계좌 해제는 계좌번호를 URL 경로에 담습니다 (`DELETE /api/accounts/{account_no}`).
uvicorn 의 접근 로그는 경로를 그대로 찍기 때문에, 화면에서 `••••-9999` 로 가려도
로그에는 전체 번호가 남습니다. 찍히기 직전에 뒤 네 자리만 남기고 가립니다.

로그를 지우는 것이 아니라 한 조각만 바꿔 끼웁니다 — 어떤 요청이 들어왔는지는
그대로 보입니다.
"""

import logging
import re
from urllib.parse import unquote

# 경로 안의 계좌번호 자리. 쿼리스트링(?) 앞까지만 봅니다
ACCOUNT_PATH = re.compile(r"(?P<head>/api/accounts/)(?P<no>[^/?]+)")

# uvicorn 접근 로그는 (client_addr, method, path, http_version, status_code) 다섯 짝입니다
ACCESS_ARG_COUNT = 5
PATH_INDEX = 2


def mask_account_path(path: str) -> str:
    """경로에 계좌번호가 있으면 뒤 네 자리만 남깁니다. 없으면 그대로 돌려줍니다."""

    def replace(m: re.Match[str]) -> str:
        # 퍼센트 인코딩으로 들어와도 같은 모양으로 가립니다
        number = unquote(m.group("no"))
        tail = f"••••-{number[-4:]}" if len(number) > 4 else "••••"
        return f"{m.group('head')}{tail}"

    return ACCOUNT_PATH.sub(replace, path)


class MaskAccountNumbers(logging.Filter):
    """접근 로그 한 줄이 만들어지기 전에 경로를 바꿔 끼웁니다."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args

        if (
            isinstance(args, tuple)
            and len(args) == ACCESS_ARG_COUNT
            and isinstance(args[PATH_INDEX], str)
        ):
            masked = mask_account_path(args[PATH_INDEX])
            if masked != args[PATH_INDEX]:
                record.args = args[:PATH_INDEX] + (masked,) + args[PATH_INDEX + 1 :]

        # 거르지 않습니다. 가리기만 하고 줄은 그대로 남깁니다
        return True


def install() -> None:
    logging.getLogger("uvicorn.access").addFilter(MaskAccountNumbers())
