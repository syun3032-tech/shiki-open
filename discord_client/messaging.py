"""Discord メッセージング API ラッパー

line_client/messaging.py と同じインターフェース。
スケジューラーの push_fn として差し替え可能。
"""

import logging
from pathlib import Path

import discord

from config import STATIC_DIR

logger = logging.getLogger("shiki.discord")

# Discord クライアント（bot.py で設定される）
_client: discord.Client | None = None
_owner_id: int | None = None

# DMチャンネルキャッシュ（毎回fetch_user不要に）
_dm_channel_cache: discord.DMChannel | None = None

# 添付ファイルサイズ上限（10MB）
_MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024

# 画像送信の許可ディレクトリ
_ALLOWED_IMAGE_DIRS: list[Path] = [
    STATIC_DIR.resolve(),
    Path("/tmp").resolve(),
]


def set_client(client: discord.Client, owner_id: int):
    """bot.py から呼ばれる初期化"""
    global _client, _owner_id, _dm_channel_cache
    _client = client
    _owner_id = owner_id
    _dm_channel_cache = None  # クライアント変更時はキャッシュクリア


async def _get_dm_channel() -> discord.DMChannel | None:
    """オーナーのDMチャンネルを取得（キャッシュ付き）"""
    global _dm_channel_cache
    if _dm_channel_cache is not None:
        return _dm_channel_cache
    if not _client or not _owner_id:
        logger.warning("Discord client not initialized")
        return None
    try:
        user = await _client.fetch_user(_owner_id)
        _dm_channel_cache = await user.create_dm()
        return _dm_channel_cache
    except Exception as e:
        logger.error(f"Failed to get DM channel: {e}")
        return None


def _validate_image_path(image_path: str) -> Path | None:
    """画像パスを検証（パストラバーサル防止）"""
    try:
        path = Path(image_path).resolve()
        # 許可ディレクトリ内か確認
        if any(path.is_relative_to(d) for d in _ALLOWED_IMAGE_DIRS):
            if path.exists() and path.is_file():
                return path
        logger.warning(f"Image path blocked (not in allowed dirs): {path}")
    except (OSError, ValueError) as e:
        logger.warning(f"Invalid image path: {e}")
    return None


async def push_text(user_id: str, text: str):
    """テキストメッセージをDMで送信（push_fn互換）

    Args:
        user_id: 使わない（Discord版ではオーナー固定）
        text: 送信テキスト
    """
    channel = await _get_dm_channel()
    if not channel:
        return
    # 例外は伝播させる（_safe_pushでレート制限検知に使うため）
    for chunk in _split_message(text, 2000):
        await channel.send(chunk)


async def send_text(channel: discord.abc.Messageable, text: str):
    """指定チャンネルにテキスト送信"""
    try:
        for chunk in _split_message(text, 2000):
            await channel.send(chunk)
    except Exception as e:
        logger.error(f"Discord send_text failed: {e}")


async def send_text_and_image(
    channel: discord.abc.Messageable, text: str, image_path: str
):
    """テキスト + 画像ファイルを送信"""
    try:
        path = _validate_image_path(image_path)
        if path:
            file = discord.File(str(path), filename=path.name)
            await channel.send(content=text[:2000], file=file)
            # 2000文字超えた分は追加送信
            if len(text) > 2000:
                await send_text(channel, text[2000:])
        else:
            await send_text(channel, text)
    except Exception as e:
        logger.error(f"Discord send_text_and_image failed: {e}")


async def send_image(channel: discord.abc.Messageable, image_path: str):
    """画像ファイルのみ送信"""
    try:
        path = _validate_image_path(image_path)
        if path:
            file = discord.File(str(path), filename=path.name)
            await channel.send(file=file)
    except Exception as e:
        logger.error(f"Discord send_image failed: {e}")


async def show_typing(channel: discord.abc.Messageable):
    """タイピング表示を開始（コンテキストマネージャとして使う）"""
    return channel.typing()


async def download_attachment(attachment: discord.Attachment) -> bytes | None:
    """Discord添付ファイルをダウンロード（サイズ制限付き）"""
    # サイズチェック
    if attachment.size > _MAX_ATTACHMENT_SIZE:
        logger.warning(f"Attachment too large: {attachment.size} bytes (limit: {_MAX_ATTACHMENT_SIZE})")
        return None
    try:
        data = await attachment.read()
        logger.info(f"Attachment downloaded: {len(data)} bytes")
        return data
    except Exception as e:
        logger.error(f"Attachment download failed: {e}")
        return None


async def get_recent_messages(limit: int = 20) -> list[dict]:
    """オーナーとのDM履歴を取得（識ちゃん自身の送信も含む）

    Args:
        limit: 取得件数（最大50）

    Returns:
        [{"author": "識" or "オーナー", "content": "...", "timestamp": "..."}]
    """
    channel = await _get_dm_channel()
    if not channel:
        return []

    limit = min(limit, 50)
    messages = []
    try:
        async for msg in channel.history(limit=limit):
            is_bot = msg.author == _client.user if _client else False
            messages.append({
                "author": "識" if is_bot else "オーナー",
                "content": msg.content[:500],
                "timestamp": msg.created_at.isoformat(),
                "has_attachments": len(msg.attachments) > 0,
            })
        messages.reverse()  # 古い順に
    except Exception as e:
        logger.error(f"Failed to get message history: {e}")
    return messages


def _split_message(text: str, limit: int = 2000) -> list[str]:
    """長いメッセージを分割"""
    if len(text) <= limit:
        return [text]

    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # 改行で区切りの良いところを探す
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1 or split_at < limit // 2:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
