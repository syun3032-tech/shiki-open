"""識（しき）- メインサーバー

FastAPI + LINE Bot Webhook + セキュリティ基盤
1行目からセキュリティが動いている。
"""

import asyncio
import hmac
import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from linebot.v3.webhook import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent

import config
from config import (
    LINE_CHANNEL_SECRET, OWNER_LINE_USER_ID,
    HOST, PORT, STATIC_DIR, LOG_DIR, validate_config,
)
from agent.loop import process_message, set_progress_callback, _auto_save_session
from agent.scheduler import start_all_schedulers
from mcp_ext.client import connect_all_servers as mcp_connect_all, disconnect_all as mcp_disconnect_all
from mcp_ext.bridge import register_mcp_tools
from security.output_validator import scan_screenshot_for_sensitive_info, detect_injection
from line_client.messaging import reply_text, reply_text_and_image, show_loading, push_text, get_message_image
from memory.manager import memory
from memory.summarizer import generate_daily_summary
from security.gate import SecurityGate
from security.mac_hardening import full_mac_audit
from security.anomaly_detector import anomaly_detector
from security.rate_limiter import message_limiter
from tools.screenshot import cleanup_old_screenshots

# === ログ設定 ===
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "shiki.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("shiki")

# === LINE SDK ===
parser = WebhookParser(LINE_CHANNEL_SECRET)

# === Security Gate ===
security_gate = SecurityGate(LOG_DIR)


# === 認証 ===
def is_authorized(user_id: str) -> bool:
    """オーナーのuser_idのみ許可（timing-safe比較）"""
    if not OWNER_LINE_USER_ID:
        logger.warning("OWNER_LINE_USER_ID未設定 - 全ユーザー拒否")
        return False
    return hmac.compare_digest(user_id, OWNER_LINE_USER_ID)


# === 日次要約スケジューラー ===
DAILY_SUMMARY_HOUR = 23
DAILY_SUMMARY_MINUTE = 30


async def _daily_summary_scheduler():
    """毎日23:30に日次要約を自動生成"""
    while True:
        try:
            now = datetime.now()
            target = now.replace(
                hour=DAILY_SUMMARY_HOUR, minute=DAILY_SUMMARY_MINUTE, second=0, microsecond=0
            )
            if target <= now:
                target += timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            logger.info(f"Daily summary scheduled at {target.strftime('%H:%M')} ({wait_seconds:.0f}s from now)")
            await asyncio.sleep(wait_seconds)

            # 日次要約生成
            logger.info("Running daily summary...")
            await _auto_save_session()  # まず現在のセッションを保存

            sessions = memory.get_today_sessions()
            if sessions:
                summary = await generate_daily_summary(sessions)
                if summary:
                    memory.save_daily_summary(summary)
                    logger.info("Daily summary saved!")
            else:
                logger.info("No sessions today, skipping daily summary")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Daily summary scheduler error: {e}")
            await asyncio.sleep(60)


# === 起動時処理 ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    """起動時セキュリティ監査 + 終了時クリーンアップ"""
    logger.info("=" * 50)
    logger.info("識（しき）起動中...")
    logger.info("=" * 50)

    # 設定バリデーション
    issues = validate_config()
    if issues:
        logger.warning("設定の問題:")
        for issue in issues:
            logger.warning(f"  - {issue}")
        # LINE未設定でも起動は続ける（開発用）
        if "GEMINI_API_KEY" in str(issues):
            logger.error("GEMINI_API_KEY必須。終了します。")
            sys.exit(1)

    # .envパーミッション確認
    env_file = config.PROJECT_ROOT / ".env"
    if env_file.exists():
        mode = oct(env_file.stat().st_mode)[-3:]
        if mode != "600":
            logger.warning(f".envのパーミッションが{mode}です。600に修正します。")
            os.chmod(env_file, 0o600)

    # .ritsu/パーミッション確認
    if config.RITSU_DIR.exists():
        mode = oct(config.RITSU_DIR.stat().st_mode)[-3:]
        if mode != "700":
            os.chmod(config.RITSU_DIR, 0o700)

    # macOSセキュリティ監査
    mac_results = full_mac_audit()
    for check, passed in mac_results.items():
        status = "OK" if passed else "WARNING"
        logger.info(f"  macOS {check}: {status}")

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

    # 古いスクショ削除
    await cleanup_old_screenshots()

    logger.info("識（しき）起動完了!")
    logger.info(f"Webhook: http://{HOST}:{PORT}/webhook")
    logger.info("-" * 50)

    # MCP外部サービス接続
    mcp_count = await mcp_connect_all()
    if mcp_count > 0:
        await register_mcp_tools()
        logger.info(f"MCP: {mcp_count} servers connected, tools registered")

    # 動的ツール読み込み
    try:
        from agent.tool_generator import load_dynamic_tools
        load_dynamic_tools()
    except Exception as e:
        logger.warning(f"Dynamic tools load error: {e}")

    # 日次要約スケジューラー起動
    daily_task = asyncio.create_task(_daily_summary_scheduler())

    # プロアクティブスケジューラー起動（朝ブリーフィング + リマインダー）
    scheduler_tasks = await start_all_schedulers(push_text)

    # 観察・学習システム起動
    observer_task = None
    try:
        from agent.continuous_observer import start_observation_loop
        observer_task = await start_observation_loop(push_callback=push_text)
        if observer_task:
            logger.info("Observation loop started")
    except Exception as e:
        logger.warning(f"Observation start failed: {e}")

    # アクティビティトラッカー起動（バックグラウンドでユーザーの行動を学習）
    activity_task = None
    try:
        from agent.activity_tracker import start_activity_tracker
        activity_task = await start_activity_tracker()
        if activity_task:
            logger.info("Activity tracker started (learning user behavior)")
    except Exception as e:
        logger.warning(f"Activity tracker start failed: {e}")

    yield  # サーバー稼働中

    # シャットダウン
    if activity_task:
        from agent.activity_tracker import stop_activity_tracker
        await stop_activity_tracker()
    daily_task.cancel()
    for task in scheduler_tasks:
        task.cancel()
    if observer_task:
        observer_task.cancel()
        try:
            from agent.continuous_observer import stop_observation
            await stop_observation()
        except Exception:
            pass
    await mcp_disconnect_all()
    # Playwrightブラウザ閉じる
    try:
        from tools.browser import close_browser
        await close_browser()
    except Exception:
        pass
    logger.info("識（しき）終了中...")
    # セッション要約を保存
    try:
        await _auto_save_session()
        logger.info("Session saved on shutdown")
    except Exception as e:
        logger.error(f"Session save on shutdown failed: {e}")
    await cleanup_old_screenshots(max_age_minutes=0)


# === FastAPIアプリ ===
app = FastAPI(
    title="識（しき）",
    description="自己識別型環境統合制御体",
    lifespan=lifespan,
)

# 静的ファイル配信（スクショ用）
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# スクショ用ワンタイムトークン管理
# {token: {"filename": str, "created": float}}
_screenshot_tokens: dict[str, dict] = {}
_SCREENSHOT_TOKEN_TTL = 300  # 5分で期限切れ


def create_screenshot_token(filename: str) -> str:
    """スクショ用のワンタイムトークンを発行"""
    _cleanup_expired_tokens()
    token = secrets.token_urlsafe(32)
    _screenshot_tokens[token] = {
        "filename": filename,
        "created": asyncio.get_event_loop().time(),
    }
    return token


def _cleanup_expired_tokens():
    """期限切れトークンを削除"""
    try:
        now = asyncio.get_event_loop().time()
    except RuntimeError:
        return
    expired = [
        t for t, v in _screenshot_tokens.items()
        if now - v["created"] > _SCREENSHOT_TOKEN_TTL
    ]
    for t in expired:
        del _screenshot_tokens[t]


# === エンドポイント ===
@app.post("/webhook")
async def webhook(request: Request):
    """LINE Webhook受信エンドポイント"""
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    # 署名検証（HMAC-SHA256）
    try:
        events = parser.parse(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        logger.warning(f"Invalid webhook signature from {request.client.host}")
        raise HTTPException(status_code=403, detail="Invalid signature")

    # イベント処理
    for event in events:
        if not isinstance(event, MessageEvent):
            continue

        user_id = event.source.user_id

        # 認証チェック
        if not is_authorized(user_id):
            logger.warning(f"Unauthorized user: {user_id}")
            continue

        # レートリミット
        if not message_limiter.is_allowed(user_id):
            logger.warning(f"Rate limited: {user_id}")
            continue

        # テキストメッセージ or 画像メッセージを判定
        user_message = ""
        image_bytes = None

        if isinstance(event.message, TextMessageContent):
            user_message = event.message.text
        elif isinstance(event.message, ImageMessageContent):
            # 画像メッセージ: LINEからダウンロード
            image_bytes = await get_message_image(event.message.id)
            if not image_bytes:
                await reply_text(event.reply_token, "画像の取得に失敗しちゃった...")
                continue
            user_message = "この画像を見て、内容を教えて。"
            logger.info(f"Image from owner: {len(image_bytes)} bytes")
        else:
            continue

        logger.info(f"Message from owner: {user_message[:100]}")

        # メッセージ長制限（10KB超は拒否）
        if len(user_message) > 10000:
            await reply_text(event.reply_token, "メッセージが長すぎるよ...10000文字以内にして。")
            continue

        # プロンプトインジェクション検知（LINEメッセージ自体は信頼するが、ログは残す）
        if detect_injection(user_message):
            logger.warning(f"Injection pattern in owner message (allowed): {user_message[:100]}")

        try:
            # 考え中アニメーション表示
            await show_loading(user_id)

            # ツール実行時の進捗通知を設定
            async def send_progress(msg: str):
                await push_text(user_id, f"... {msg}")
            set_progress_callback(send_progress)

            result = await process_message(user_message, image_bytes=image_bytes)

            if result.get("image_path"):
                # スクショのセキュリティスキャン（LINE送信前）
                ss_scan = await scan_screenshot_for_sensitive_info(result["image_path"])
                if not ss_scan.get("safe", True):
                    # 機密情報検出 → スクショは送らずテキストのみ
                    logger.warning(f"Screenshot blocked: {ss_scan.get('reason')}")
                    await reply_text(
                        event.reply_token,
                        f"{result['text']}\n\n⚠️ スクショに機密情報が映ってたから画像は送らなかったよ。"
                    )
                else:
                    # 安全 → トークン付きURLで送信
                    image_filename = Path(result["image_path"]).name
                    token = create_screenshot_token(image_filename)
                    host = request.headers.get("host", f"{HOST}:{PORT}")
                    scheme = "https" if "trycloudflare.com" in host else "http"
                    image_url = f"{scheme}://{host}/screenshot/{image_filename}?token={token}"
                    await reply_text_and_image(
                        event.reply_token, result["text"], image_url
                    )
            else:
                await reply_text(event.reply_token, result["text"])

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            await reply_text(event.reply_token, "ごめん、エラーが出ちゃった...")

    return JSONResponse(content={"status": "ok"})


@app.get("/health")
async def health(request: Request):
    """ヘルスチェック（ローカルのみ詳細表示）"""
    client_host = request.client.host if request.client else ""
    if client_host in ("127.0.0.1", "::1", "localhost"):
        return {
            "status": "running",
            "name": "識（しき）",
            "anomaly_shutdown": anomaly_detector.should_shutdown,
        }
    # 外部からは最小限の情報のみ
    return {"status": "running"}


@app.post("/save-memory")
async def save_memory(request: Request):
    """手動でセッション記憶を保存（ローカルのみ許可）"""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Local access only")
    try:
        await _auto_save_session()
        return {"status": "saved"}
    except Exception as e:
        return {"status": "error"}


@app.get("/screenshot/{filename}")
async def get_screenshot(filename: str, token: str = ""):
    """スクリーンショット画像を返す（トークン認証付き）"""
    # トークン検証（timing-safe比較でタイミング攻撃を防止）
    import hmac as _hmac
    token_data = None
    for stored_token, data in _screenshot_tokens.items():
        if _hmac.compare_digest(stored_token, token):
            token_data = data
            break
    if not token or token_data is None:
        raise HTTPException(status_code=403, detail="Invalid or expired token")

    # ファイル名一致チェック（timing-safe）
    if not _hmac.compare_digest(token_data["filename"], filename):
        raise HTTPException(status_code=403, detail="Token mismatch")

    # 期限チェック
    try:
        now = asyncio.get_event_loop().time()
        if now - token_data["created"] > _SCREENSHOT_TOKEN_TTL:
            del _screenshot_tokens[token]
            raise HTTPException(status_code=403, detail="Token expired")
    except RuntimeError:
        pass

    filepath = STATIC_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=404)
    # パストラバーサル防止
    if not filepath.resolve().is_relative_to(STATIC_DIR.resolve()):
        raise HTTPException(status_code=403)

    # 使い捨て: アクセス後にトークン削除（LINE は2回フェッチするので残す）
    # ただし TTL で自動消滅するので問題なし
    return FileResponse(str(filepath), media_type="image/png")


# === 起動 ===
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=HOST,
        port=PORT,
        reload=False,  # 本番はreload無効
        log_level="info",
    )
