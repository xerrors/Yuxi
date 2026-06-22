from yuxi_cli.config import ConfigStore, normalize_remote_url


def test_normalize_remote_url_adds_scheme_and_removes_api_suffix():
    assert normalize_remote_url("localhost:5173") == "http://localhost:5173"
    assert normalize_remote_url("https://example.com/api") == "https://example.com"
    assert normalize_remote_url("https://example.com/yuxi/api") == "https://example.com/yuxi"


def test_config_store_uses_implicit_local_default(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")
    config = store.load()

    assert config.current == "local"
    assert config.remotes["local"].url == "http://localhost:5173"


def test_config_store_persists_remote_credentials(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")
    config = store.load()
    remote = config.set_remote("prod", "https://example.com/api")
    remote.api_key = "yxkey_test"
    remote.api_key_id = "12"
    config.use_remote("prod")
    store.save(config)

    loaded = store.load()
    assert loaded.current == "prod"
    assert loaded.remotes["prod"].url == "https://example.com"
    assert loaded.remotes["prod"].api_key == "yxkey_test"
    assert loaded.remotes["prod"].api_key_id == "12"


def test_remote_url_change_clears_credentials(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")
    config = store.load()
    remote = config.set_remote("prod", "https://one.example.com")
    remote.api_key = "yxkey_old"
    remote.api_key_id = "12"

    config.set_remote("prod", "https://two.example.com")

    assert config.remotes["prod"].api_key == ""
    assert config.remotes["prod"].api_key_id == ""


def test_config_escapes_special_chars_in_remote_name(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")
    config = store.load()
    # remote 名称由用户输入，可能包含引号/反斜杠，需转义后仍能往返解析。
    name = 'pr"o\\d'
    config.set_remote(name, "https://example.com")
    config.use_remote(name)
    store.save(config)

    loaded = store.load()
    assert loaded.current == name
    assert loaded.remotes[name].url == "https://example.com"


def test_config_file_is_created_with_owner_only_permissions(tmp_path):
    import stat as stat_module

    path = tmp_path / "config.toml"
    store = ConfigStore(path)
    store.save(store.load())

    mode = stat_module.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
