"""권한이 화면이 아니라 서버에서 막히는지 봅니다.

user 세션으로 관리자 엔드포인트를 직접 때려서 403 이 나오는지 확인합니다.
여기서 쓰는 엔드포인트는 권한 검사에서 걸러지므로 DB 에 닿지 않습니다 —
그래서 이 테스트는 컨테이너가 떠 있지 않아도 돌아갑니다.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app


@pytest.fixture
def as_user() -> Iterator[TestClient]:
    """user 역할로 로그인한 것처럼 만듭니다."""
    hong = auth.CurrentUser(id=2, username="hong", role="user")
    app.dependency_overrides[auth.current_user] = lambda: hong
    # lifespan 을 띄우지 않으려고 with 없이 만듭니다. DB 연결이 필요 없습니다
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def anonymous() -> Iterator[TestClient]:
    """쿠키가 없는 상태입니다."""
    app.dependency_overrides.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_permission_table_has_two_roles() -> None:
    """역할은 admin 과 user 둘뿐입니다."""
    assert set(auth.PERMISSIONS) == {"admin", "user"}


def test_user_cannot_touch_other_users() -> None:
    """사용자 관리는 admin 만. 표 자체를 확인합니다."""
    assert auth.allows("admin", "user:write")
    assert not auth.allows("user", "user:write")
    assert not auth.allows("user", "user:read")


def test_both_roles_can_edit_their_own_profile() -> None:
    """내 정보 변경은 둘 다 됩니다."""
    assert auth.allows("admin", "user:update:own")
    assert auth.allows("user", "user:update:own")


def test_everyone_reads_the_market_but_only_admin_collects() -> None:
    """시세는 둘 다 읽고, KIS 로 나가는 수집은 admin 만 합니다."""
    assert auth.allows("admin", "market:read")
    assert auth.allows("user", "market:read")
    assert auth.allows("admin", "market:write")
    assert not auth.allows("user", "market:write")


def test_only_admin_reads_everyones_accounts() -> None:
    assert auth.allows("admin", "account:read:any")
    assert not auth.allows("user", "account:read:any")
    assert auth.allows("user", "account:read:own")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/users", {"username": "mallory", "password": "password123"}),
        ("PATCH", "/api/users/1", {"role": "admin"}),
        ("DELETE", "/api/users/1", None),
        ("POST", "/api/kis/token?mode=paper", None),
        ("POST", "/api/stocks/005930/collect?mode=paper", None),
    ],
)
def test_admin_endpoints_reject_user_session(
    as_user: TestClient, method: str, path: str, body: dict[str, str] | None
) -> None:
    """화면에서 버튼을 숨긴 것과 무관하게, 직접 호출해도 403 이 나와야 합니다."""
    res = as_user.request(method, path, json=body)
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "forbidden"


def test_user_session_can_read_own_profile(as_user: TestClient) -> None:
    """같은 세션으로 내 정보는 읽힙니다 — 403 이 전부에 걸리는 게 아닙니다."""
    res = as_user.get("/api/me")
    assert res.status_code == 200
    assert res.json()["username"] == "hong"
    assert "user:write" not in res.json()["permissions"]


def test_no_cookie_is_session_expired_not_forbidden(anonymous: TestClient) -> None:
    """쿠키가 없으면 401 session_expired 입니다. 403 과 섞지 않습니다."""
    res = anonymous.get("/api/me")
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "session_expired"
