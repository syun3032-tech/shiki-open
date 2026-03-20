"""プラットフォーム抽象化層

OS固有の操作（スクリーンショット、マウス、キーボード、デスクトップ制御）を
統一インターフェースで提供。起動時にOSを自動判定し、適切な実装を選択する。

使い方:
    from platform_layer import get_platform
    p = get_platform()
    await p.take_screenshot("/tmp/ss.png")
    await p.click(100, 200)
"""

import sys

from platform_layer.base import PlatformBase


def get_platform() -> PlatformBase:
    """現在のOSに対応するPlatform実装を返す（シングルトン）"""
    global _instance
    if _instance is not None:
        return _instance

    os_name = sys.platform
    if os_name == "darwin":
        from platform_layer.macos import MacOSPlatform
        _instance = MacOSPlatform()
    elif os_name == "win32":
        from platform_layer.windows import WindowsPlatform
        _instance = WindowsPlatform()
    elif os_name.startswith("linux"):
        from platform_layer.linux import LinuxPlatform
        _instance = LinuxPlatform()
    else:
        raise RuntimeError(f"未対応のOS: {os_name}")

    return _instance


_instance: PlatformBase | None = None
