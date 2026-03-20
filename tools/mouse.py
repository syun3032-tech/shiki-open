"""マウス操作ツール - クロスプラットフォーム対応

platform_layer経由でOS固有のマウス操作を実行。
macOSではQuartz CGEvent（ベジェ曲線移動）、
Windows/LinuxではpyautoguiにフォールバックMost。
"""

import logging

from platform_layer import get_platform

logger = logging.getLogger("shiki.tools")


async def click(x: int, y: int) -> dict:
    """左クリック"""
    platform = get_platform()
    w, h = platform.get_screen_size()
    if not (0 <= x < w and 0 <= y < h):
        return {"success": False, "error": f"座標が画面外: ({x}, {y})、画面サイズ: {w}x{h}"}
    try:
        await platform.click(x, y)
        logger.info(f"Clicked at ({x}, {y})")
        return {"success": True, "output": f"Clicked at ({x}, {y})"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def double_click(x: int, y: int) -> dict:
    """ダブルクリック"""
    platform = get_platform()
    w, h = platform.get_screen_size()
    if not (0 <= x < w and 0 <= y < h):
        return {"success": False, "error": f"座標が画面外: ({x}, {y})"}
    try:
        await platform.double_click(x, y)
        logger.info(f"Double-clicked at ({x}, {y})")
        return {"success": True, "output": f"Double-clicked at ({x}, {y})"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def right_click(x: int, y: int) -> dict:
    """右クリック"""
    platform = get_platform()
    w, h = platform.get_screen_size()
    if not (0 <= x < w and 0 <= y < h):
        return {"success": False, "error": f"座標が画面外: ({x}, {y})"}
    try:
        await platform.right_click(x, y)
        logger.info(f"Right-clicked at ({x}, {y})")
        return {"success": True, "output": f"Right-clicked at ({x}, {y})"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def move_mouse(x: int, y: int) -> dict:
    """マウスカーソルを移動"""
    platform = get_platform()
    w, h = platform.get_screen_size()
    if not (0 <= x < w and 0 <= y < h):
        return {"success": False, "error": f"座標が画面外: ({x}, {y})"}
    try:
        await platform.move_mouse(x, y)
        return {"success": True, "output": f"Mouse moved to ({x}, {y})"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5) -> dict:
    """ドラッグ操作"""
    platform = get_platform()
    w, h = platform.get_screen_size()
    for label, x, y in [("始点", x1, y1), ("終点", x2, y2)]:
        if not (0 <= x < w and 0 <= y < h):
            return {"success": False, "error": f"{label}が画面外: ({x}, {y})"}
    try:
        await platform.drag(x1, y1, x2, y2, duration)
        logger.info(f"Dragged from ({x1},{y1}) to ({x2},{y2})")
        return {"success": True, "output": f"Dragged from ({x1},{y1}) to ({x2},{y2})"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def get_screen_size() -> dict:
    """画面サイズを取得"""
    platform = get_platform()
    w, h = platform.get_screen_size()
    return {"success": True, "width": w, "height": h}
