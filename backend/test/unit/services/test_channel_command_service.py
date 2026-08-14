from __future__ import annotations

import pytest

from yuxi.services.channel_command_service import parse_slash_command


def test_parse_plain_text_returns_none():
    assert parse_slash_command("hello /state") is None


def test_parse_supported_command_and_arguments():
    assert parse_slash_command(" /approve ").name == "approve"
    assert parse_slash_command("/state").name == "state"
    assert parse_slash_command('/approve "run-1"').args == ("run-1",)


def test_parse_malformed_command_raises():
    with pytest.raises(ValueError, match="格式无效"):
        parse_slash_command('/approve "unterminated')
