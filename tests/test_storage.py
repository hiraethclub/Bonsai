import json
import os
import stat

import pytest

from bonsai import storage
from bonsai.storage import Config, ConfigError, ensure_config_template, load_config, save_config


@pytest.fixture(autouse=True)
def tmp_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BONSAI_CONFIG_DIR", str(tmp_path / "cfg"))
    return tmp_path / "cfg"


def test_missing_config_is_empty():
    cfg = load_config()
    assert not cfg.is_complete


def test_round_trip(tmp_config_dir):
    path = save_config(Config("http://example.com:8080", "alice", "pw"))
    assert path == tmp_config_dir / "config.json"
    cfg = load_config()
    assert cfg.is_complete
    assert (cfg.server, cfg.username, cfg.password) == ("http://example.com:8080", "alice", "pw")


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
def test_config_is_private():
    path = save_config(Config("s", "u", "p"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_template_created_once():
    path, created = ensure_config_template()
    assert created
    assert not load_config().is_complete
    _, created_again = ensure_config_template()
    assert not created_again


def test_unknown_keys_ignored(tmp_config_dir):
    tmp_config_dir.mkdir(parents=True)
    (tmp_config_dir / "config.json").write_text(
        json.dumps({"server": "s", "username": "u", "password": "p", "extra": 1})
    )
    assert load_config().is_complete


def test_broken_json_raises(tmp_config_dir):
    tmp_config_dir.mkdir(parents=True)
    (tmp_config_dir / "config.json").write_text("{not json")
    with pytest.raises(ConfigError):
        load_config()


def test_default_dir_follows_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("BONSAI_CONFIG_DIR")
    monkeypatch.setattr(storage.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert storage.config_dir() == tmp_path / "bonsai"
