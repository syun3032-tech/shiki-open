"""ファイル操作ツール

セキュリティ: path_validatorでアクセス制御。
許可されたディレクトリのみ操作可能。
"""

import logging
from pathlib import Path

from security.path_validator import validate_file_access

logger = logging.getLogger("shiki.tools")

MAX_READ_SIZE = 1_000_000  # 1MB
MAX_WRITE_SIZE = 500_000  # 500KB


async def read_file(path: str) -> dict:
    """ファイルを読む（テキストのみ）"""
    if not validate_file_access(path, "read"):
        return {"success": False, "error": f"アクセス拒否: {path}"}
    try:
        p = Path(path).resolve()
        if not p.exists():
            return {"success": False, "error": f"ファイルが存在しない: {path}"}
        if not p.is_file():
            return {"success": False, "error": f"ファイルではない: {path}"}
        size = p.stat().st_size
        if size > MAX_READ_SIZE:
            return {"success": False, "error": f"ファイルが大きすぎる: {size}bytes (上限: {MAX_READ_SIZE})"}
        content = p.read_text(encoding="utf-8", errors="replace")
        logger.info(f"File read: {path} ({len(content)} chars)")
        return {"success": True, "content": content, "size": len(content)}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def write_file(path: str, content: str) -> dict:
    """ファイルに書き込む"""
    if not validate_file_access(path, "write"):
        return {"success": False, "error": f"書き込み拒否: {path}"}
    if len(content) > MAX_WRITE_SIZE:
        return {"success": False, "error": f"内容が大きすぎる: {len(content)}chars (上限: {MAX_WRITE_SIZE})"}
    try:
        p = Path(path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        logger.info(f"File written: {path} ({len(content)} chars)")
        return {"success": True, "output": f"書き込み完了: {path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def list_directory(path: str) -> dict:
    """ディレクトリの内容を一覧"""
    if not validate_file_access(path, "read"):
        return {"success": False, "error": f"アクセス拒否: {path}"}
    try:
        p = Path(path).resolve()
        if not p.exists():
            return {"success": False, "error": f"存在しない: {path}"}
        if not p.is_dir():
            return {"success": False, "error": f"ディレクトリではない: {path}"}
        items = []
        for item in sorted(p.iterdir()):
            if item.name.startswith("."):
                continue  # 隠しファイルはスキップ
            kind = "dir" if item.is_dir() else "file"
            size = item.stat().st_size if item.is_file() else 0
            items.append({"name": item.name, "type": kind, "size": size})
        logger.info(f"Listed directory: {path} ({len(items)} items)")
        return {"success": True, "items": items[:500]}  # 最大500件
    except Exception as e:
        return {"success": False, "error": str(e)}


async def move_file(src: str, dst: str) -> dict:
    """ファイルを移動/リネーム"""
    if not validate_file_access(src, "read"):
        return {"success": False, "error": f"移動元アクセス拒否: {src}"}
    if not validate_file_access(dst, "write"):
        return {"success": False, "error": f"移動先アクセス拒否: {dst}"}
    try:
        src_path = Path(src).resolve()
        dst_path = Path(dst).resolve()
        if not src_path.exists():
            return {"success": False, "error": f"移動元が存在しない: {src}"}
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        src_path.rename(dst_path)
        logger.info(f"File moved: {src} -> {dst}")
        return {"success": True, "output": f"移動完了: {src} → {dst}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
