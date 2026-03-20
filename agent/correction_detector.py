"""Correction Detector — OpenClaw self-improving inspired

ユーザーの訂正パターンを検出し、学習メモリに記録する。
日本語の訂正表現に特化。

検出のみ（キーワードマッチ、LLM不要）→ 高速・ゼロコスト。
内容抽出は Gemini Flash で行う（必要時のみ）。
"""

import logging
import re

logger = logging.getLogger("shiki.agent")

# 日本語の訂正パターン（カテゴリ別）
CORRECTION_PATTERNS: dict[str, list[str]] = {
    # 明示的な訂正
    "explicit": [
        "違う", "ちがう", "そうじゃなくて", "じゃなくて", "そうじゃない",
        "間違", "まちが", "正しくない",
    ],
    # 繰り返し指摘（フラストレーション）
    "repeated": [
        "前も言った", "何回も", "いつも言ってる", "何度も",
        "さっきも", "前にも", "また同じ",
    ],
    # 行動停止要求
    "stop": [
        "やめて", "しないで", "するな", "禁止", "勝手に",
        "いらない", "余計な",
    ],
    # 好み表明
    "preference": [
        "こっちがいい", "の方がいい", "の方が好き", "にして",
        "こうして", "ああして",
    ],
}

# コンパイル済みパターン
_COMPILED: dict[str, list[re.Pattern]] = {}
for cat, patterns in CORRECTION_PATTERNS.items():
    _COMPILED[cat] = [re.compile(re.escape(p)) for p in patterns]


def detect_correction(user_message: str) -> dict | None:
    """ユーザーメッセージに訂正パターンが含まれるか検出

    Returns:
        {"type": "explicit"|"repeated"|"stop"|"preference",
         "trigger": matched_string,
         "message": original_message}
        or None if no correction detected.
    """
    # 短すぎるメッセージは無視（「うん」「おけ」等の誤検出防止）
    if len(user_message) < 4:
        return None

    # 優先度: repeated > explicit > stop > preference
    priority_order = ["repeated", "explicit", "stop", "preference"]

    for category in priority_order:
        for pattern in _COMPILED[category]:
            match = pattern.search(user_message)
            if match:
                logger.info(f"Correction detected: type={category}, trigger={match.group()}")
                return {
                    "type": category,
                    "trigger": match.group(),
                    "message": user_message,
                }

    return None


async def extract_correction_content(
    conversation_history: list[dict],
    correction: dict,
) -> dict | None:
    """会話履歴から訂正の内容を抽出（Gemini Flash使用）

    Returns:
        {"correct_behavior": "...", "wrong_behavior": "...", "context": "..."}
        or None if extraction fails.
    """
    import asyncio

    try:
        import google.genai as genai
        from config import GEMINI_API_KEY

        client = genai.Client(api_key=GEMINI_API_KEY)

        # 直近の会話を文字列化（最大6ターン）
        recent = conversation_history[-6:] if len(conversation_history) > 6 else conversation_history
        conv_text = "\n".join(
            f"{'ユーザー' if h['role'] == 'user' else '識'}:  {h['text'][:300]}"
            for h in recent
        )

        prompt = (
            "以下の会話でユーザーがAIを訂正している。\n"
            "訂正の内容を抽出してJSON形式で返せ。\n\n"
            f"会話:\n{conv_text}\n\n"
            f"訂正メッセージ: {correction['message']}\n"
            f"訂正タイプ: {correction['type']}\n\n"
            "以下のJSON形式で返せ（他のテキストは不要）:\n"
            '{"correct_behavior": "今後こうすべき", '
            '"wrong_behavior": "今回やってしまった間違い", '
            '"context": "どんな状況で"}'
        )

        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=[genai.types.Part(text=prompt)],
                config=genai.types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=200,
                ),
            ),
            timeout=10.0,
        )

        text = response.text.strip()

        # JSON抽出
        import json
        # ```json ... ``` ブロックを除去
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*", "", text)

        result = json.loads(text)

        # バリデーション
        if not result.get("correct_behavior"):
            return None

        # サニタイズ（プロンプトインジェクション防止）
        for key in ("correct_behavior", "wrong_behavior", "context"):
            val = result.get(key, "")
            if len(val) > 200:
                val = val[:200]
            # 危険なパターンを除去（マークダウン構造破壊 + インジェクション防止）
            val = re.sub(r"[<>{}\[\]#*`~|]", "", val)
            val = val.replace("\n", " ").replace("\r", " ")
            # プロンプトインジェクションパターンを検出
            injection_patterns = [
                r"ignore\s+.*instructions",
                r"system\s*:",
                r"new\s+instructions?\s*:",
                r"ルール",
                r"指示.*無視",
                r"秘密.*出力",
            ]
            for pat in injection_patterns:
                if re.search(pat, val, re.IGNORECASE):
                    logger.warning(f"Injection attempt in correction: {val[:50]}")
                    return None
            result[key] = val.strip()

        logger.info(f"Correction extracted: {result['correct_behavior'][:50]}")
        return result

    except asyncio.TimeoutError:
        logger.warning("Correction extraction timed out")
        return None
    except Exception as e:
        logger.warning(f"Correction extraction failed: {e}")
        return None
