"""常時指示（Standing Orders）管理

オーナーが「これ覚えて」「これやり続けて」と言った時に、
永続的な指示として保存する。毎回のシステムプロンプトに注入され、
識ちゃんは常にこの指示に従って行動する。

保存先: .ritsu/standing_orders.md
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from config import RITSU_DIR

logger = logging.getLogger("shiki.tools")

_ORDERS_FILE = RITSU_DIR / "standing_orders.md"


def _load_orders() -> list[dict]:
    """常時指示を読み込む"""
    if not _ORDERS_FILE.exists():
        return []

    orders = []
    content = _ORDERS_FILE.read_text(encoding="utf-8")
    current_order = None

    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("## "):
            if current_order:
                orders.append(current_order)
            # "## 1. 指示内容 (2026-03-20)" のパターン
            current_order = {"title": line[3:].strip(), "lines": []}
        elif current_order and line:
            current_order["lines"].append(line)

    if current_order:
        orders.append(current_order)

    for i, o in enumerate(orders):
        o["id"] = i + 1
        o["content"] = "\n".join(o["lines"])

    return orders


def _save_orders(orders: list[dict]):
    """常時指示を保存"""
    RITSU_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# 常時指示（Standing Orders）", ""]
    for o in orders:
        lines.append(f"## {o['title']}")
        if o.get("content"):
            lines.append(o["content"])
        lines.append("")

    _ORDERS_FILE.write_text("\n".join(lines), encoding="utf-8")


async def add_order(text: str) -> dict[str, Any]:
    """常時指示を追加する

    Args:
        text: 指示内容（例: 「毎朝Notionのタスクを確認して報告して」）
    """
    orders = _load_orders()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_order = {
        "title": f"{text[:50]} ({now})",
        "content": text,
        "id": len(orders) + 1,
    }
    orders.append(new_order)
    _save_orders(orders)
    logger.info(f"Standing order added: {text[:50]}")
    return {
        "success": True,
        "output": f"覚えた！常時指示に追加したよ: {text[:80]}",
        "id": new_order["id"],
        "total": len(orders),
    }


async def list_orders() -> dict[str, Any]:
    """常時指示の一覧を取得"""
    orders = _load_orders()
    return {
        "success": True,
        "orders": [{"id": o["id"], "title": o["title"], "content": o["content"]} for o in orders],
        "count": len(orders),
    }


async def remove_order(order_id: int) -> dict[str, Any]:
    """常時指示を削除する

    Args:
        order_id: 削除する指示のID（list_ordersで確認）
    """
    orders = _load_orders()
    before = len(orders)
    orders = [o for o in orders if o["id"] != order_id]
    if len(orders) < before:
        _save_orders(orders)
        return {"success": True, "output": f"常時指示 #{order_id} を削除したよ"}
    return {"success": False, "error": f"指示 #{order_id} が見つからない"}
