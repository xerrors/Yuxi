import pytest

from yuxi.modules.identity.services.usernames import validate_username


@pytest.mark.parametrize("name", ["bob", "张三", "a_1", "Zed99"])
def test_validate_username_accepts_plain_names(name):
    assert validate_username(name) == (True, "")


@pytest.mark.parametrize("name", ["admin\n", "bob\n\n", "a b", "bob\r", "a-b", "\n"])
def test_validate_username_rejects_whitespace_and_symbols(name):
    is_valid, _ = validate_username(name)
    assert not is_valid
