"""user_config.py のテスト"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import user_config


@pytest.fixture(autouse=True)
def reset_config_cache():
    """各テストでキャッシュをクリア"""
    user_config._config_cache = None
    yield
    user_config._config_cache = None


class TestDefaultConfig:
    """デフォルト設定のテスト"""

    def test_has_expected_keys(self):
        defaults = user_config._DEFAULT_CONFIG
        expected_keys = [
            "owner_name", "owner_display_name", "shiki_name",
            "language", "allowed_apps", "channels", "observation",
        ]
        for key in expected_keys:
            assert key in defaults, f"Default config missing key: {key}"

    def test_default_language_is_ja(self):
        assert user_config._DEFAULT_CONFIG["language"] == "ja"

    def test_default_shiki_name(self):
        assert user_config._DEFAULT_CONFIG["shiki_name"] == "識"


class TestGetOwnerName:
    """get_owner_name のテスト"""

    def test_returns_default_when_no_config(self):
        # owner_nameが空文字の場合、get()は""を返す（Noneではない）
        # get_owner_nameのデフォルト"ユーザー"はowner_nameキー自体が
        # 存在しない場合のみ使われる。デフォルト設定では空文字。
        with patch.object(user_config, "_CONFIG_PATH", Path("/nonexistent/user_config.json")):
            user_config._config_cache = None
            result = user_config.get_owner_name()
            # デフォルト設定の owner_name は空文字
            # get("owner_name", "ユーザー") → "" （キーは存在するのでデフォルト不使用）
            assert result == ""

    def test_returns_configured_name(self, tmp_path):
        config_file = tmp_path / "user_config.json"
        config_file.write_text(json.dumps({"owner_name": "テスト太郎"}), encoding="utf-8")
        with patch.object(user_config, "_CONFIG_PATH", config_file):
            user_config._config_cache = None
            result = user_config.get_owner_name()
            assert result == "テスト太郎"


class TestGetDisplayName:
    """get_display_name のテスト"""

    def test_falls_back_to_owner_name(self, tmp_path):
        # display_nameが空なら owner_name にフォールバック
        config_file = tmp_path / "user_config.json"
        config_file.write_text(
            json.dumps({"owner_name": "花子", "owner_display_name": ""}),
            encoding="utf-8",
        )
        with patch.object(user_config, "_CONFIG_PATH", config_file):
            user_config._config_cache = None
            result = user_config.get_display_name()
            assert result == "花子"

    def test_uses_display_name_when_set(self, tmp_path):
        config_file = tmp_path / "user_config.json"
        config_file.write_text(
            json.dumps({"owner_name": "花子", "owner_display_name": "花子さん"}),
            encoding="utf-8",
        )
        with patch.object(user_config, "_CONFIG_PATH", config_file):
            user_config._config_cache = None
            result = user_config.get_display_name()
            assert result == "花子さん"


class TestDotNotationAccess:
    """get() ドット記法のテスト"""

    def test_simple_key(self, tmp_path):
        config_file = tmp_path / "user_config.json"
        config_file.write_text(json.dumps({"language": "en"}), encoding="utf-8")
        with patch.object(user_config, "_CONFIG_PATH", config_file):
            user_config._config_cache = None
            assert user_config.get("language") == "en"

    def test_nested_key(self, tmp_path):
        config_file = tmp_path / "user_config.json"
        config_file.write_text(
            json.dumps({"observation": {"enabled": True}}),
            encoding="utf-8",
        )
        with patch.object(user_config, "_CONFIG_PATH", config_file):
            user_config._config_cache = None
            assert user_config.get("observation.enabled") is True

    def test_missing_key_returns_default(self, tmp_path):
        config_file = tmp_path / "user_config.json"
        config_file.write_text(json.dumps({}), encoding="utf-8")
        with patch.object(user_config, "_CONFIG_PATH", config_file):
            user_config._config_cache = None
            assert user_config.get("nonexistent.key", "fallback") == "fallback"


class TestDeepMerge:
    """_deep_merge のテスト"""

    def test_simple_override(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3}
        user_config._deep_merge(base, override)
        assert base == {"a": 1, "b": 3}

    def test_nested_merge(self):
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"b": 3, "c": 4}}
        user_config._deep_merge(base, override)
        assert base == {"x": {"a": 1, "b": 3, "c": 4}}

    def test_override_adds_new_keys(self):
        base = {"a": 1}
        override = {"b": 2}
        user_config._deep_merge(base, override)
        assert base == {"a": 1, "b": 2}

    def test_non_dict_override_replaces(self):
        base = {"a": {"nested": True}}
        override = {"a": "replaced"}
        user_config._deep_merge(base, override)
        assert base == {"a": "replaced"}
