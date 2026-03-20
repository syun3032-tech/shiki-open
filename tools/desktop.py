"""デスクトップ操作ツール - クロスプラットフォーム対応

platform_layer経由でOS固有の操作を実行。
ユーザー設定はuser_config.jsonから動的に取得。
"""

import asyncio
import logging

from platform_layer import get_platform
from security.url_validator import validate_url
import user_config

logger = logging.getLogger("shiki.tools")


async def open_app(app_name: str) -> dict:
    """アプリを起動する"""
    allowed = user_config.get_allowed_apps()
    if app_name not in allowed:
        return {"success": False, "error": f"許可されていないアプリ: {app_name}"}
    platform = get_platform()
    ok = await platform.open_app(app_name)
    if ok:
        logger.info(f"App opened: {app_name}")
    return {"success": ok, "output": f"{app_name}を起動した" if ok else "起動失敗"}


async def open_url_with_profile(url: str, profile: str) -> dict:
    """指定ブラウザプロファイルでURLを開く

    Args:
        url: 開くURL
        profile: メールアドレスまたはエイリアス
    """
    profiles = user_config.get_browser_profiles()
    aliases = user_config.get_browser_profile_aliases()

    # エイリアス解決
    email = aliases.get(profile, profile)
    profile_dir = profiles.get(email)
    if not profile_dir:
        if not profiles:
            return {"success": False, "error": "ブラウザプロファイルが設定されていません。user_config.jsonで設定してください。"}
        available = ", ".join(aliases.keys()) if aliases else ", ".join(profiles.keys())
        return {"success": False, "error": f"不明なプロファイル: {profile}。使えるのは: {available}"}

    # URLバリデーション
    if '"' in url or '\\' in url:
        return {"success": False, "error": "不正なURL文字"}

    url_check = validate_url(url)
    if not url_check["safe"]:
        if url_check["level"] == "blocked":
            return {"success": False, "error": f"セキュリティ: {url_check['reason']}"}
        elif url_check["level"] == "unknown":
            owner = user_config.get_display_name()
            return {"success": False, "error": f"知らないサイト: {url}。{owner}の許可が必要。", "needs_approval": True}

    try:
        proc = await asyncio.create_subprocess_exec(
            "open", "-na", "Google Chrome", "--args",
            f"--profile-directory={profile_dir}", url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            logger.info(f"URL opened with profile {email}: {url}")
            return {"success": True, "output": f"{email}のChromeで開いた"}
        return {"success": False, "error": stderr.decode().strip()}
    except asyncio.TimeoutError:
        return {"success": False, "error": "タイムアウト"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def open_url(url: str, browser: str = "Google Chrome") -> dict:
    """URLをブラウザで開く（安全性チェック付き）"""
    allowed = user_config.get_allowed_apps()
    if browser not in allowed:
        return {"success": False, "error": f"許可されていないブラウザ: {browser}"}
    if '"' in url or '\\' in url:
        return {"success": False, "error": "不正なURL文字"}

    url_check = validate_url(url)
    if not url_check["safe"]:
        if url_check["level"] == "blocked":
            logger.warning(f"URL BLOCKED: {url} - {url_check['reason']}")
            return {"success": False, "error": f"セキュリティ: {url_check['reason']}。このURLは開けない。", "blocked": True}
        elif url_check["level"] == "unknown":
            owner = user_config.get_display_name()
            logger.warning(f"URL UNKNOWN: {url} - {url_check['reason']}")
            return {
                "success": False,
                "error": f"知らないサイトだから開けない: {url}\n{owner}が「開いて」と言ってくれたら開くよ。",
                "needs_approval": True,
            }

    platform = get_platform()
    ok = await platform.open_url(url, browser)
    if ok:
        logger.info(f"URL opened (trusted): {url}")
    return {"success": ok, "output": f"URLを開いた: {url}" if ok else "URL開けなかった"}


async def get_frontmost_app() -> dict:
    """最前面のアプリ名を取得"""
    platform = get_platform()
    name = await platform.get_frontmost_app()
    return {"success": bool(name), "output": name}


async def get_app_list() -> dict:
    """実行中のアプリ一覧を取得"""
    platform = get_platform()
    apps = await platform.get_running_apps()
    return {"success": True, "output": ", ".join(apps)}


async def get_browser_info() -> dict:
    """最前面ブラウザのURL・タイトルを取得"""
    from security.output_validator import scan_output_for_leaks

    platform = get_platform()
    info = await platform.get_browser_info()

    url = info.get("url", "")
    # URLに認証情報が含まれていないかチェック
    leaks = scan_output_for_leaks(url)
    if leaks:
        logger.warning(f"Browser URL contains sensitive data: {leaks}")
        info["url"] = "[URLに認証情報が含まれていたため非表示]"

    info["success"] = bool(info.get("browser"))
    return info


async def get_window_info() -> dict:
    """最前面ウィンドウの詳細情報を取得"""
    platform = get_platform()
    info = await platform.get_window_info()
    info["success"] = True
    return info


async def set_volume(level: int) -> dict:
    """音量を設定（0-100）"""
    platform = get_platform()
    await platform.set_volume(level)
    return {"success": True, "output": f"音量を{level}に設定"}


async def toggle_dark_mode() -> dict:
    """ダークモード切替"""
    platform = get_platform()
    await platform.toggle_dark_mode()
    return {"success": True, "output": "ダークモードを切り替えた"}


async def show_notification(title: str, message: str) -> dict:
    """デスクトップ通知を表示"""
    platform = get_platform()
    await platform.show_notification(title[:100], message[:500])
    return {"success": True, "output": "通知を表示した"}


async def type_text(text: str) -> dict:
    """テキストを入力（クリップボード経由でIMEバイパス）"""
    text = text[:1000]
    try:
        platform = get_platform()
        await platform.type_text(text)
        logger.info(f"Text pasted: {text[:50]}...")
        return {"success": True, "output": "テキストを入力した"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def scroll(direction: str = "down", amount: int = 15) -> dict:
    """画面をスクロールする"""
    amount = max(1, min(50, amount))
    try:
        platform = get_platform()
        await platform.scroll(direction, amount)
        logger.info(f"Scrolled {direction} by {amount}")
        return {"success": True, "output": f"Scrolled {direction} by {amount}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def press_key(key: str, modifiers: list[str] | None = None) -> dict:
    """キーを押す（ショートカット対応）"""
    ALLOWED_KEYS = {
        "return", "tab", "escape", "space", "delete",
        "left", "right", "up", "down",
        "home", "end", "pageup", "pagedown",
        "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    }
    ALLOWED_MODIFIERS = {"command", "shift", "option", "control", "ctrl", "alt"}

    key_lower = key.lower()
    if key_lower not in ALLOWED_KEYS and len(key) != 1:
        return {"success": False, "error": f"許可されていないキー: {key}"}

    if modifiers:
        for m in modifiers:
            if m.lower() not in ALLOWED_MODIFIERS:
                return {"success": False, "error": f"許可されていない修飾キー: {m}"}

    try:
        platform = get_platform()
        await platform.press_key(key, modifiers)
        return {"success": True, "output": f"キーを押した: {key}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
