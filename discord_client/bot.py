"""識（しき）Discord Bot

LINE の代わりにDiscordでオーナーと対話。
DMのみ受付。オーナー以外は無視。
"""

import asyncio
import logging
from pathlib import Path

import discord

from config import DISCORD_BOT_TOKEN, DISCORD_OWNER_ID
from agent.loop import process_message, set_progress_callback, _auto_save_session
from agent.scheduler import start_all_schedulers
from mcp_ext.client import connect_all_servers as mcp_connect_all, disconnect_all as mcp_disconnect_all
from mcp_ext.bridge import register_mcp_tools
from security.output_validator import scan_screenshot_for_sensitive_info, detect_injection
from security.gate import SecurityGate
from security.mac_hardening import full_mac_audit
from security.rate_limiter import message_limiter
from security.anomaly_detector import anomaly_detector
from memory.manager import memory
from memory.summarizer import generate_daily_summary
from tools.screenshot import cleanup_old_screenshots
from discord_client.messaging import (
    set_client, push_text, send_text, send_text_and_image, show_typing, download_attachment,
)

logger = logging.getLogger("shiki.discord")

# === Intents ===
intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True

client = discord.Client(intents=intents)

# セキュリティゲート
_security_gate: SecurityGate | None = None
_scheduler_tasks: list[asyncio.Task] = []


def _is_owner(user_id: int) -> bool:
    """オーナーチェック"""
    if not DISCORD_OWNER_ID:
        logger.warning("DISCORD_OWNER_ID 未設定 — 全ユーザー拒否")
        return False
    return user_id == DISCORD_OWNER_ID


@client.event
async def on_ready():
    """起動完了"""
    global _security_gate, _scheduler_tasks

    logger.info("=" * 50)
    logger.info(f"識（しき）Discord Bot 起動: {client.user}")
    logger.info("=" * 50)

    # Discord messaging モジュールにクライアントを渡す
    set_client(client, DISCORD_OWNER_ID)

    # macOSセキュリティ監査
    mac_results = full_mac_audit()
    for check, passed in mac_results.items():
        status = "OK" if passed else "WARNING"
        logger.info(f"  macOS {check}: {status}")

    # 古いスクショ削除
    await cleanup_old_screenshots()

    # MCP外部サービス接続
    mcp_count = await mcp_connect_all()
    if mcp_count > 0:
        await register_mcp_tools()
        logger.info(f"MCP: {mcp_count} servers connected")

    # セキュリティゲート
    from config import LOG_DIR
    _security_gate = SecurityGate(LOG_DIR)

    # スケジューラー起動（push_fn = Discord DM送信）
    _scheduler_tasks = await start_all_schedulers(push_text)

    # 日次要約スケジューラー
    _scheduler_tasks.append(asyncio.create_task(_daily_summary_scheduler()))

    logger.info("識（しき）Discord Bot 準備完了!")

    # オーナーに起動通知
    if DISCORD_OWNER_ID:
        await push_text(str(DISCORD_OWNER_ID), "起動したよ。なんでも聞いて。")


@client.event
async def on_message(message: discord.Message):
    """メッセージ受信"""
    # Bot自身のメッセージは無視
    if message.author == client.user:
        return

    # DM以外は無視
    if not isinstance(message.channel, discord.DMChannel):
        return

    # オーナーチェック
    if not _is_owner(message.author.id):
        logger.warning(f"Unauthorized Discord user: {message.author.id} ({message.author.name})")
        return

    # レートリミット
    user_id_str = str(message.author.id)
    if not message_limiter.is_allowed(user_id_str):
        logger.warning(f"Rate limited: {user_id_str}")
        await message.channel.send("ちょっと速すぎるよ...少し待って。")
        return

    # メッセージ内容を取得
    user_message = message.content.strip()
    image_bytes = None

    # 画像添付チェック
    if message.attachments:
        for att in message.attachments:
            if att.content_type and att.content_type.startswith("image/"):
                image_bytes = await download_attachment(att)
                if not user_message:
                    user_message = "この画像を見て、内容を教えて。"
                break

    if not user_message and not image_bytes:
        return

    logger.info(f"Message from owner: {user_message[:100]}")

    # メッセージ長制限
    if len(user_message) > 10000:
        await message.channel.send("メッセージが長すぎるよ...10000文字以内にして。")
        return

    # プロンプトインジェクション検知（ログのみ）
    if detect_injection(user_message):
        logger.warning(f"Injection pattern in owner message (allowed): {user_message[:100]}")

    try:
        # タイピング表示
        async with message.channel.typing():
            # 進捗コールバック
            progress_channel = message.channel

            async def send_progress(msg: str):
                await progress_channel.send(f"... {msg}")

            set_progress_callback(send_progress)

            # エージェント処理
            result = await process_message(user_message, image_bytes=image_bytes)

        # 結果送信
        text = result.get("text", "")
        image_path = result.get("image_path")

        if image_path:
            # スクショのセキュリティスキャン
            ss_scan = await scan_screenshot_for_sensitive_info(image_path)
            if not ss_scan.get("safe", True):
                logger.warning(f"Screenshot blocked: {ss_scan.get('reason')}")
                await send_text(
                    message.channel,
                    f"{text}\n\n:warning: スクショに機密情報が映ってたから画像は送らなかったよ。",
                )
            else:
                # 画像ファイルを直接送信（URLトークン不要！）
                await send_text_and_image(message.channel, text, image_path)
        elif text:
            await send_text(message.channel, text)
        else:
            await message.channel.send("...（処理完了、結果なし）")

    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)
        await message.channel.send("ごめん、エラーが出ちゃった...")


async def _daily_summary_scheduler():
    """毎日23:30に日次要約を自動生成"""
    from datetime import datetime, timedelta

    while True:
        try:
            now = datetime.now()
            target = now.replace(hour=23, minute=30, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            logger.info(f"Daily summary scheduled at {target.strftime('%H:%M')} ({wait_seconds:.0f}s)")
            await asyncio.sleep(wait_seconds)

            logger.info("Running daily summary...")
            await _auto_save_session()

            sessions = memory.get_today_sessions()
            if sessions:
                summary = await generate_daily_summary(sessions)
                if summary:
                    memory.save_daily_summary(summary)
                    logger.info("Daily summary saved!")
            else:
                logger.info("No sessions today, skipping")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Daily summary error: {e}")
            await asyncio.sleep(60)


async def shutdown():
    """シャットダウン処理"""
    for task in _scheduler_tasks:
        task.cancel()
    # キャンセルしたタスクの完了を待つ
    if _scheduler_tasks:
        await asyncio.gather(*_scheduler_tasks, return_exceptions=True)
    await mcp_disconnect_all()
    try:
        from tools.browser import close_browser
        await close_browser()
    except Exception:
        pass
    try:
        await _auto_save_session()
        logger.info("Session saved on shutdown")
    except Exception as e:
        logger.error(f"Session save failed: {e}")
    await cleanup_old_screenshots(max_age_minutes=0)


def run():
    """Discord Bot を起動"""
    import os
    import sys
    from config import GEMINI_API_KEY, LOG_DIR

    # ログ設定
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_DIR / "shiki.log", encoding="utf-8"),
        ],
    )

    # バリデーション
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY 必須。終了します。")
        sys.exit(1)
    if not DISCORD_BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN 必須。終了します。")
        sys.exit(1)
    if not DISCORD_OWNER_ID:
        logger.warning("DISCORD_OWNER_ID 未設定 — 全メッセージ拒否されます")

    # .env パーミッション
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        mode = oct(env_file.stat().st_mode)[-3:]
        if mode != "600":
            logger.warning(f".env のパーミッション {mode} → 600 に修正")
            os.chmod(env_file, 0o600)

    logger.info("識（しき）Discord Bot 起動中...")
    client.run(DISCORD_BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    run()
