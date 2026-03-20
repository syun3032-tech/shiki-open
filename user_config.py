"""ユーザー設定管理

ハードコードされた個人情報を排除し、user_config.jsonで動的に管理する。
初回起動時にセットアップウィザードで生成、以降は読み込み。
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("shiki.user_config")

_CONFIG_PATH = Path(__file__).parent / "user_config.json"

# デフォルト設定
_DEFAULT_CONFIG = {
    "owner_name": "",
    "owner_display_name": "",
    "shiki_name": "識",
    "shiki_personality": "親しみやすい、頼りになる、丁寧、成長する",
    "language": "ja",
    "allowed_apps": [
        "Google Chrome", "Safari", "Firefox", "Arc",
        "Finder", "Notes", "Reminders", "Calendar", "Preview",
        "TextEdit", "Calculator",
        "Terminal", "Cursor", "Visual Studio Code",
        "Slack", "Discord",
        "Music", "Spotify",
    ],
    "browser_profiles": {},
    "browser_profile_aliases": {},
    "allowed_paths": [],  # 空 = デフォルト（Desktop, Documents, Downloads）
    "channels": {
        "line": False,
        "discord": False,
        "cli": True,
    },
    "observation": {
        "enabled": False,
        "interval_seconds": 5,
        "learn_patterns": True,
        "vision_enabled": False,
        "tier3_enabled": True,
        "sensitive_apps": [],
    },
}

_config_cache: dict | None = None


def load_config() -> dict:
    """ユーザー設定を読み込み（キャッシュ付き）"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    config = dict(_DEFAULT_CONFIG)

    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            # デフォルトにユーザー設定をマージ
            _deep_merge(config, user_config)
        except Exception as e:
            logger.warning(f"user_config.json読み込み失敗: {e}")

    _config_cache = config
    return config


def save_config(config: dict):
    """ユーザー設定を保存"""
    global _config_cache
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    _config_cache = config
    logger.info("user_config.json saved")


def is_configured() -> bool:
    """初回セットアップが完了しているか"""
    if not _CONFIG_PATH.exists():
        return False
    config = load_config()
    return bool(config.get("owner_name"))


def get(key: str, default=None):
    """設定値を取得（ドット記法対応: "observation.enabled"）"""
    config = load_config()
    keys = key.split(".")
    val = config
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
        if val is None:
            return default
    return val


def get_owner_name() -> str:
    """オーナー名を取得"""
    return get("owner_name", "ユーザー")


def get_display_name() -> str:
    """オーナーの表示名（呼び方）を取得"""
    return get("owner_display_name") or get_owner_name()


def get_allowed_apps() -> frozenset[str]:
    """許可アプリセットを取得"""
    return frozenset(get("allowed_apps", _DEFAULT_CONFIG["allowed_apps"]))


def get_browser_profiles() -> dict[str, str]:
    """Chromeプロファイル設定を取得"""
    return get("browser_profiles", {})


def get_browser_profile_aliases() -> dict[str, str]:
    """プロファイルエイリアスを取得"""
    return get("browser_profile_aliases", {})


def get_allowed_paths() -> list[str]:
    """許可パスを取得（空ならデフォルト）"""
    paths = get("allowed_paths", [])
    if not paths:
        home = str(Path.home())
        return [
            f"{home}/Desktop",
            f"{home}/Documents",
            f"{home}/Downloads",
        ]
    return paths


def _deep_merge(base: dict, override: dict):
    """dictを深くマージ（override優先）"""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
