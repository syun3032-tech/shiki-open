"""セッション・日次要約生成

Gemini Flashで安く要約を生成する。
"""

import logging

import google.genai as genai

from config import GEMINI_API_KEY

logger = logging.getLogger("shiki.memory")

# 要約用は安いFlashモデルを使う
_client = genai.Client(api_key=GEMINI_API_KEY)
SUMMARY_MODEL = "gemini-2.5-flash"


async def generate_session_summary(conversation: list[dict]) -> str:
    """会話履歴からセッション要約を生成"""
    if not conversation:
        return ""

    # 会話をテキストに変換
    lines = []
    for entry in conversation:
        prefix = "オーナー" if entry["role"] == "user" else "識"
        lines.append(f"{prefix}: {entry['text']}")
    conversation_text = "\n".join(lines)

    prompt = (
        "以下の会話を簡潔に要約してください。\n"
        "重要な事実、オーナーの好み・予定・依頼内容を中心に。\n"
        "箇条書きで、3-5行で。\n\n"
        f"--- 会話 ---\n{conversation_text}\n--- 会話終了 ---"
    )

    try:
        response = await _client.aio.models.generate_content(
            model=SUMMARY_MODEL,
            contents=[prompt],
            config=genai.types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=500,
            ),
        )
        summary = response.text or ""
        logger.info(f"Session summary generated: {len(summary)} chars")
        return summary
    except Exception as e:
        logger.error(f"Summary generation failed: {e}")
        # フォールバック: 最後の数メッセージをそのまま保存
        return "\n".join(lines[-6:])


async def generate_daily_summary(session_summaries: list[str]) -> str:
    """セッション要約群から日次要約を生成"""
    if not session_summaries:
        return ""

    all_sessions = "\n\n---\n\n".join(session_summaries)

    prompt = (
        "以下は今日の全セッション要約です。\n"
        "1日の出来事として統合して要約してください。\n"
        "重要な事実、決定事項、オーナーの状態を中心に。\n"
        "箇条書きで、5-10行で。\n\n"
        f"--- セッション群 ---\n{all_sessions}\n--- 終了 ---"
    )

    try:
        response = await _client.aio.models.generate_content(
            model=SUMMARY_MODEL,
            contents=[prompt],
            config=genai.types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=800,
            ),
        )
        return response.text or ""
    except Exception as e:
        logger.error(f"Daily summary generation failed: {e}")
        return all_sessions[:2000]


async def extract_learnings(conversation: list[dict]) -> dict:
    """会話からオーナーの好み・事実を抽出

    Returns:
        {"preferences": [...], "facts": [...], "schedule": [...]}
    """
    if len(conversation) < 4:
        return {}

    lines = []
    for entry in conversation:
        prefix = "オーナー" if entry["role"] == "user" else "識"
        lines.append(f"{prefix}: {entry['text']}")
    conversation_text = "\n".join(lines)

    prompt = (
        "以下の会話から、オーナーについて新しく分かった事実を抽出してください。\n"
        "好み、予定、人間関係、プロジェクト、癖、口調の特徴など。\n"
        "何も新しい情報がなければ「なし」とだけ答えてください。\n\n"
        "以下の形式で1行1項目で出力してください（タグは必ずつけて）:\n"
        "[好み] コーヒーはブラック派\n"
        "[事実] 大学院でCS専攻\n"
        "[予定] 来週月曜に歯医者\n\n"
        f"--- 会話 ---\n{conversation_text}\n--- 終了 ---"
    )

    try:
        response = await _client.aio.models.generate_content(
            model=SUMMARY_MODEL,
            contents=[prompt],
            config=genai.types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=500,
            ),
        )
        text = (response.text or "").strip()
        if not text or text == "なし":
            return {}

        # タグベースで行分割パース
        tag_map = {
            "[好み]": "preferences",
            "[事実]": "facts",
            "[予定]": "schedule",
        }
        result: dict[str, list[str]] = {"preferences": [], "facts": [], "schedule": []}

        for line in text.splitlines():
            line = line.strip().lstrip("- ・")
            if not line:
                continue
            for tag, key in tag_map.items():
                if tag in line:
                    content = line.replace(tag, "").strip()
                    if content:
                        result[key].append(content)
                    break

        # タグなしでも内容があれば facts に入れる
        logger.info(f"Learnings extracted: {sum(len(v) for v in result.values())} items")
        return result
    except Exception as e:
        logger.warning(f"Learning extraction failed: {e}")
        return {}
