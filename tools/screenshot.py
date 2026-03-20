"""スクリーンショット取得 - クロスプラットフォーム対応

platform_layer経由でOS固有のスクショ処理を実行。
Mac/Windows/Linux全対応。
"""

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime
from pathlib import Path

from config import STATIC_DIR
from platform_layer import get_platform

_logger = logging.getLogger("shiki.tools")

# 画面変更検知用ハッシュ
_last_screenshot_hash: str | None = None

# 最後のスクショサイズ（動的に更新、座標スケーリングで使用）
last_screenshot_width: int | None = None
last_screenshot_height: int | None = None


def _compute_image_hash(filepath: str) -> str:
    """画像のMD5ハッシュを計算（変更検知用）"""
    return hashlib.md5(Path(filepath).read_bytes()).hexdigest()


def did_screen_change(new_screenshot_path: str) -> bool:
    """前回のスクショと比較して画面が変わったかを検出"""
    global _last_screenshot_hash
    new_hash = _compute_image_hash(new_screenshot_path)
    changed = _last_screenshot_hash is not None and new_hash != _last_screenshot_hash
    _last_screenshot_hash = new_hash
    return changed


async def take_screenshot(resize: bool = True) -> dict:
    """スクリーンショットを撮影し、ファイルパスを返す（クロスプラットフォーム）"""
    global last_screenshot_width, last_screenshot_height

    platform = get_platform()
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    filename = f"ss_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
    filepath = STATIC_DIR / filename

    try:
        # === プラットフォーム経由でスクショ撮影 ===
        captured = await platform.take_screenshot(str(filepath))
        if not captured:
            return {"error": "スクリーンショットの保存に失敗（画面録画権限を確認してください）"}

        # リサイズ（トークン節約: 幅1024px、アスペクト比維持）
        if resize:
            resized = STATIC_DIR / f"r_{filename}"
            ok = await platform.resize_image(str(filepath), str(resized), 1024)
            if ok and resized.exists():
                filepath.unlink()
                filepath = resized

        # JPEG変換（トークン節約: PNG比3-5x小さい）
        jpeg_path = filepath.with_suffix(".jpg")
        ok = await platform.convert_to_jpeg(str(filepath), str(jpeg_path), quality=80)
        if ok and jpeg_path.exists():
            filepath.unlink()
            filepath = jpeg_path

        # リサイズ後の実際のサイズを取得（座標スケーリング用）
        w, h = await platform.get_image_dimensions(str(filepath))
        if w > 0 and h > 0:
            last_screenshot_width = w
            last_screenshot_height = h
        else:
            _logger.warning("Screenshot dimension query failed, scaling may be inaccurate")

        # 画面変更検知用ハッシュを更新
        global _last_screenshot_hash
        _last_screenshot_hash = _compute_image_hash(str(filepath))

        return {
            "path": str(filepath),
            "filename": filepath.name,
            "size_bytes": filepath.stat().st_size,
            "width": last_screenshot_width,
            "height": last_screenshot_height,
        }

    except asyncio.TimeoutError:
        return {"error": "スクリーンショットがタイムアウト"}
    except Exception as e:
        return {"error": f"スクリーンショット失敗: {e}"}


async def crop_screenshot(x: int, y: int, width: int, height: int) -> dict:
    """スクリーンショットの指定領域をクロップ（Agentic Vision）"""
    global last_screenshot_width, last_screenshot_height

    platform = get_platform()

    # まず最新のスクショを撮る
    ss_result = await take_screenshot()
    if ss_result.get("error"):
        return ss_result

    source_path = ss_result["path"]
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    # クロップ範囲のバリデーション
    img_w = ss_result.get("width", 1024)
    img_h = ss_result.get("height", 665)

    x = max(0, min(x, img_w - 1))
    y = max(0, min(y, img_h - 1))
    width = max(50, min(width, img_w - x))
    height = max(50, min(height, img_h - y))

    crop_filename = f"crop_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.jpg"
    crop_path = STATIC_DIR / crop_filename

    try:
        ok = await platform.crop_image(source_path, str(crop_path), x, y, width, height)
        if not ok or not crop_path.exists():
            return {"error": "クロップ失敗"}

        # クロップした領域を2倍に拡大（詳細視認用）
        target_width = min(width * 2, 1024)
        await platform.resize_image(str(crop_path), str(crop_path), target_width)

        return {
            "path": str(crop_path),
            "filename": crop_filename,
            "region": {"x": x, "y": y, "width": width, "height": height},
            "size_bytes": crop_path.stat().st_size,
        }

    except asyncio.TimeoutError:
        return {"error": "クロップがタイムアウト"}
    except Exception as e:
        return {"error": f"クロップ失敗: {e}"}


async def cleanup_old_screenshots(max_age_minutes: int = 5):
    """古いスクリーンショットを削除（デフォルト5分）"""
    if not STATIC_DIR.exists():
        return
    now = datetime.now().timestamp()
    for pattern in ("*.png", "*.jpg"):
        for f in STATIC_DIR.glob(pattern):
            if now - f.stat().st_mtime > max_age_minutes * 60:
                f.unlink()
