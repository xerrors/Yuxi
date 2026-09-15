"""回收站期限规则；数据库竞争和权限在集成测试中另外验证。"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from yuxi.services.document_retention import retention_deadline, restore_window_open

DELETED_AT = datetime(2026, 9, 13, 0, 0, tzinfo=UTC)


def test_retention_is_exactly_30_days():
    assert retention_deadline(DELETED_AT) == datetime(2026, 10, 13, tzinfo=UTC)


@pytest.mark.parametrize(
    "offset, expected",
    [
        (timedelta(0), True),
        (timedelta(days=29, hours=23, minutes=59, seconds=59), True),
        (timedelta(days=30) - timedelta(microseconds=1), True),
        (timedelta(days=30), False),
        (timedelta(days=30, microseconds=1), False),
        (timedelta(days=90), False),
        (timedelta(microseconds=-1), False),
    ],
)
def test_restore_boundary(offset, expected):
    assert restore_window_open(DELETED_AT, now=DELETED_AT + offset) is expected


def test_same_instant_in_different_timezones():
    east = timezone(timedelta(hours=8))
    deleted = DELETED_AT.astimezone(east)
    assert retention_deadline(deleted) == retention_deadline(DELETED_AT)
    assert retention_deadline(deleted).tzinfo is UTC
    assert not restore_window_open(deleted, now=retention_deadline(DELETED_AT))


@pytest.mark.parametrize("value", [datetime(2026, 9, 13), datetime(2026, 10, 13)])
def test_naive_delete_time_is_rejected(value):
    with pytest.raises(ValueError, match="timezone"):
        retention_deadline(value)


def test_naive_current_time_is_rejected():
    with pytest.raises(ValueError, match="timezone"):
        restore_window_open(DELETED_AT, now=datetime(2026, 9, 14))


def test_month_and_leap_year_are_elapsed_days():
    assert retention_deadline(datetime(2028, 2, 1, tzinfo=UTC)) == datetime(2028, 3, 2, tzinfo=UTC)


def test_repeated_calculation_does_not_extend_deadline():
    original = retention_deadline(DELETED_AT)
    assert retention_deadline(DELETED_AT) == original
    assert not restore_window_open(DELETED_AT, now=original)
