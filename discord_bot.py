"""識（しき）Discord Bot

DMまたは指定チャンネルでのメンションに反応。
オーナー以外のメッセージは完全無視。

使い方:
  python discord_bot.py          → 単体起動
  shiki discord                  → CLI経由
"""

import asyncio
import io
import logging
import os
import sys

import user_config
from pathlib import Path

import discord
from discord import Intents, Message

from config import DISCORD_BOT_TOKEN, DISCORD_OWNER_ID, DISCORD_OWNER_IDS
from agent.loop import process_message, set_progress_callback
from security.anomaly_detector import anomaly_detector

logger = logging.getLogger("shiki.discord")

# === Bot設定 ===
intents = Intents.default()
intents.message_content = True  # Privileged Intent（Developer Portalで有効化必須）
intents.guilds = True           # サーバー/チャンネル/スレッド情報

bot = discord.Client(intents=intents)

# 進捗メッセージ用（処理中のチャンネル参照）
_current_channel = None

# 処理中フラグ（asyncio.Lock で安全に排他制御）
_processing_lock = asyncio.Lock()
_processing = False

# === 接続ヘルスチェック ===
_HEALTH_CHECK_INTERVAL = 30   # 30秒ごとにチェック
_MAX_DISCONNECT_SECS = 90     # 90秒以上切断なら自動終了→launchdが再起動
_disconnect_since: float | None = None


def _is_owner(user_id: int) -> bool:
    """オーナーかどうか判定（複数アカウント対応）"""
    return user_id in DISCORD_OWNER_IDS


@bot.event
async def on_ready():
    global _disconnect_since
    _disconnect_since = None  # 接続成功→切断タイマーリセット
    logger.info(f"Discord Bot起動: {bot.user} (ID: {bot.user.id})")
    logger.info(f"Owner IDs: {DISCORD_OWNER_IDS}")
    # ステータス設定
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{user_config.get_display_name()}のPC",
        )
    )

    # Discord messaging初期化（スケジューラーのpush_fn用）
    from discord_client.messaging import set_client as set_discord_client
    from discord_client.messaging import push_text as discord_push_text
    set_discord_client(bot, DISCORD_OWNER_ID)

    # スケジューラー起動（朝ブリーフィング + リマインダー + Cronジョブ）
    from agent.scheduler import start_all_schedulers
    scheduler_tasks = await start_all_schedulers(discord_push_text)
    logger.info(f"Schedulers started: {len(scheduler_tasks)} tasks")


@bot.event
async def on_disconnect():
    """Discord接続が切れた時に切断タイマーを開始"""
    global _disconnect_since
    import time
    if _disconnect_since is None:
        _disconnect_since = time.monotonic()
        logger.warning("Discord connection lost, starting disconnect timer")


@bot.event
async def on_resumed():
    """再接続成功時にタイマーリセット"""
    global _disconnect_since
    if _disconnect_since is not None:
        import time
        elapsed = time.monotonic() - _disconnect_since
        logger.info(f"Discord reconnected after {elapsed:.0f}s")
    _disconnect_since = None


@bot.event
async def on_message(message: Message):
    global _current_channel, _processing

    # Bot自身のメッセージは無視
    # Note: _processing_lock で排他制御（レースコンディション防止）
    if message.author == bot.user:
        return

    logger.info(f"on_message: author={message.author} (ID={message.author.id}), content={message.content[:50]}")

    # オーナー以外は完全無視
    if not _is_owner(message.author.id):
        logger.info(f"Not owner (expected {DISCORD_OWNER_IDS}), ignoring")
        return

    # DM / メンション / チャンネル / スレッド — どこでも反応
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = bot.user in message.mentions if message.guild else False
    is_thread = isinstance(message.channel, discord.Thread)

    # サーバーチャンネルではメンションがないと反応しない（スレッド内は常に反応）
    if message.guild and not is_mentioned and not is_thread:
        return

    # メッセージ本文を取得（メンション部分を除去）
    content = message.content
    if is_mentioned:
        content = content.replace(f"<@{bot.user.id}>", "").strip()
        content = content.replace(f"<@!{bot.user.id}>", "").strip()

    if not content:
        return

    # 多重実行防止（asyncio.Lock で排他制御）
    async with _processing_lock:
        if _processing:
            await message.reply("ちょっと待って、今別の処理中...")
            return
        _processing = True
        _current_channel = message.channel

    try:
        # 画像添付がある場合
        image_bytes = None
        if message.attachments:
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    image_bytes = await attachment.read()
                    if not content or content == "":
                        content = "この画像を見て、内容を教えて。"
                    break

        # メッセージ長制限
        if len(content) > 10000:
            await message.reply("メッセージ長すぎ...10000文字以内にして。")
            return

        logger.info(f"Discord message from owner: {content[:100]}")

        # タイピングインジケーターを維持しながらエージェント処理
        async with message.channel.typing():
            try:
                result = await asyncio.wait_for(
                    process_message(content, image_bytes=image_bytes),
                    timeout=660,  # ReActループ(600s) + 余裕
                )
            except asyncio.TimeoutError:
                result = {"text": "処理がタイムアウトした...タスクを分割して再度試してみて。", "image_path": None}

        text = result.get("text", "")
        image_path = result.get("image_path")

        if not text and not image_path:
            text = "..."

        # 応答送信（2000文字制限対応）
        if image_path and Path(image_path).exists():
            file = discord.File(image_path, filename="screenshot.png")
            if text:
                for chunk in _split_message(text):
                    await message.reply(chunk)
                await message.channel.send(file=file)
            else:
                await message.reply(file=file)
        elif text:
            for chunk in _split_message(text):
                await message.reply(chunk)

    except Exception as e:
        logger.error(f"Discord message processing error: {e}", exc_info=True)
        try:
            await message.reply(f"ごめん、エラー出た: {type(e).__name__}")
        except Exception:
            pass
    finally:
        _processing = False
        _current_channel = None


def _split_message(text: str, max_len: int = 1900) -> list[str]:
    """Discord 2000文字制限に合わせてメッセージを分割"""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # 改行で切れるところを探す
        split_pos = text.rfind("\n", 0, max_len)
        if split_pos == -1:
            split_pos = max_len
        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")

    return chunks


async def _health_check_loop():
    """接続ヘルスチェック: 長時間切断を検知して自動終了"""
    import time
    await asyncio.sleep(30)  # 起動直後はスキップ

    while True:
        try:
            await asyncio.sleep(_HEALTH_CHECK_INTERVAL)

            if _disconnect_since is not None:
                elapsed = time.monotonic() - _disconnect_since
                if elapsed > _MAX_DISCONNECT_SECS:
                    logger.critical(
                        f"Discord disconnected for {elapsed:.0f}s (>{_MAX_DISCONNECT_SECS}s). "
                        "Exiting for auto-restart by launchd."
                    )
                    os._exit(1)  # 即座にプロセス終了（launchdが再起動する）
                else:
                    logger.warning(f"Discord disconnected for {elapsed:.0f}s, waiting...")

            # WebSocket接続の追加チェック
            if bot.ws is not None and bot.ws.socket is not None:
                if bot.ws.socket.closed:
                    logger.warning("WebSocket is closed but no disconnect event fired")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Health check error: {e}")
            await asyncio.sleep(10)


async def start_bot():
    """Discord Bot起動"""
    if not DISCORD_BOT_TOKEN:
        print("エラー: DISCORD_BOT_TOKEN が .env に未設定")
        sys.exit(1)
    if not DISCORD_OWNER_IDS:
        print("エラー: DISCORD_OWNER_ID が .env に未設定")
        sys.exit(1)

    # セキュリティ監査
    try:
        from security.mac_hardening import run_security_audit
        run_security_audit()
    except Exception:
        pass

    # MCP接続 + ツール登録
    try:
        from mcp_ext.client import connect_all_servers
        mcp_count = await connect_all_servers()
        if mcp_count > 0:
            from mcp_ext.bridge import register_mcp_tools
            await register_mcp_tools()
    except Exception as e:
        logger.warning(f"MCP init error: {e}")

    # 動的ツール読み込み
    try:
        from agent.tool_generator import load_dynamic_tools
        load_dynamic_tools()
    except Exception as e:
        logger.warning(f"Dynamic tools load error: {e}")

    # ツールレベル同期検証
    try:
        from security.gate import validate_tool_levels_sync
        validate_tool_levels_sync()
    except Exception:
        pass

    # WALリカバリー（前回クラッシュした未完了タスクの検出）
    try:
        from agent.wal import wal_recover, wal_rotate
        recovery = wal_recover()
        if recovery:
            logger.warning(
                f"WAL recovery: 未完了タスク検出 — "
                f"'{recovery['user_message'][:50]}' "
                f"(phase={recovery['last_phase']}, iter={recovery['iteration']})"
            )
        wal_rotate()
    except Exception as e:
        logger.error(f"WAL recovery failed: {e}", exc_info=True)

    # ヘルスチェックタスク起動
    asyncio.create_task(_health_check_loop())

    logger.info("Discord Bot starting...")
    await bot.start(DISCORD_BOT_TOKEN)


def main():
    """エントリーポイント"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    print(f"\n  識（しき）Discord Bot 起動中...")
    print(f"  Owner ID: {DISCORD_OWNER_ID}")
    print(f"  終了: Ctrl+C\n")

    try:
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        print("\n  Discord Bot 終了。")


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()
