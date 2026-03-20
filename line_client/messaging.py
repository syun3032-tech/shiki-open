"""LINE Messaging API ラッパー

line-bot-sdk v3 の async API を使用。
遅延初期化でイベントループの問題を回避。
"""

import logging

from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    AsyncMessagingApiBlob,
    Configuration,
    ReplyMessageRequest,
    PushMessageRequest,
    ShowLoadingAnimationRequest,
    TextMessage,
    ImageMessage,
)

from config import LINE_CHANNEL_ACCESS_TOKEN

logger = logging.getLogger("shiki.line")

# 遅延初期化
_messaging_api: AsyncMessagingApi | None = None
_blob_api: AsyncMessagingApiBlob | None = None


def _get_api() -> AsyncMessagingApi:
    global _messaging_api
    if _messaging_api is None:
        config = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
        client = AsyncApiClient(config)
        _messaging_api = AsyncMessagingApi(client)
    return _messaging_api


def _get_blob_api() -> AsyncMessagingApiBlob:
    global _blob_api
    if _blob_api is None:
        config = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
        client = AsyncApiClient(config)
        _blob_api = AsyncMessagingApiBlob(client)
    return _blob_api


async def show_loading(chat_id: str, seconds: int = 20):
    """考え中アニメーション表示（最大60秒）"""
    try:
        await _get_api().show_loading_animation(
            ShowLoadingAnimationRequest(
                chatId=chat_id,
                loadingSeconds=min(seconds, 60),
            )
        )
    except Exception as e:
        logger.warning(f"Loading animation failed: {e}")


async def reply_text(reply_token: str, text: str):
    """テキストで返信"""
    await _get_api().reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=text[:5000])],
        )
    )


async def reply_image(reply_token: str, image_url: str, preview_url: str | None = None):
    """画像で返信"""
    await _get_api().reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[
                ImageMessage(
                    original_content_url=image_url,
                    preview_image_url=preview_url or image_url,
                )
            ],
        )
    )


async def reply_text_and_image(reply_token: str, text: str, image_url: str):
    """テキスト + 画像で返信"""
    await _get_api().reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[
                TextMessage(text=text[:5000]),
                ImageMessage(
                    original_content_url=image_url,
                    preview_image_url=image_url,
                ),
            ],
        )
    )


async def push_text(user_id: str, text: str):
    """プッシュメッセージ（返信トークン不要）"""
    await _get_api().push_message(
        PushMessageRequest(
            to=user_id,
            messages=[TextMessage(text=text[:5000])],
        )
    )


async def get_message_image(message_id: str) -> bytes | None:
    """LINEメッセージの画像コンテンツをダウンロード"""
    try:
        content = await _get_blob_api().get_message_content(message_id)
        logger.info(f"Image downloaded: {len(content)} bytes")
        return bytes(content)
    except Exception as e:
        logger.error(f"Image download failed: {e}")
        return None
