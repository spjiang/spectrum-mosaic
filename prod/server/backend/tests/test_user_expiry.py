"""用户使用期限：空值不限期，到期时刻起不能登录，也不能再自动开拼图。"""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.auth import access_block_reason, owner_may_autostart
from app.schemas import UserUpdate


def _user(*, active=True, expires_at=None):
    return SimpleNamespace(is_active=active, expires_at=expires_at)


def test_blank_expiry_stays_open():
    assert access_block_reason(_user()) is None


def test_deadline_instant_is_already_closed():
    deadline = datetime(2026, 12, 31, 15, 59, 59, tzinfo=timezone.utc)
    assert access_block_reason(_user(expires_at=deadline), now=deadline) == "试用已到期"


def test_still_open_before_deadline():
    deadline = datetime(2026, 12, 31, 15, 59, 59, tzinfo=timezone.utc)
    earlier = datetime(2026, 12, 31, 15, 59, 58, tzinfo=timezone.utc)
    assert access_block_reason(_user(expires_at=deadline), now=earlier) is None


def test_naive_deadline_is_treated_as_utc():
    deadline = datetime(2026, 1, 1, 0, 0, 0)
    assert access_block_reason(_user(expires_at=deadline), now=datetime(2026, 1, 1, tzinfo=timezone.utc)) == "试用已到期"


def test_disabled_wins_over_expiry():
    past = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert access_block_reason(_user(active=False, expires_at=past)) == "账号已停用"


def test_ownerless_project_can_still_autostart():
    assert owner_may_autostart(None, None) is True


def test_expired_owner_does_not_autostart():
    past = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert owner_may_autostart(7, _user(expires_at=past)) is False


def test_missing_owner_does_not_autostart():
    assert owner_may_autostart(7, None) is False


def test_clearing_expiry_is_distinct_from_omitting_it():
    cleared = UserUpdate(expires_at=None)
    omitted = UserUpdate()
    assert "expires_at" in cleared.model_fields_set
    assert "expires_at" not in omitted.model_fields_set
