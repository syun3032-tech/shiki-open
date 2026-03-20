"""AI出力のセキュリティ検証

AI応答にcredentialが含まれていないかスキャン。
スクリーンショット内の機密情報もGemini Visionで検出。
OWASP AI Agent Security Cheat Sheet準拠。
"""

import re
import logging
from pathlib import Path

logger = logging.getLogger("shiki.security")

# credential検出パターン
SENSITIVE_PATTERNS = {
    "api_key": r"(?:sk-|AIza|ghp_|gho_|github_pat_|xox[bsrpa]-|glpat-|ANTHROPIC_API_KEY)[A-Za-z0-9_-]{20,}",
    "aws_key": r"AKIA[0-9A-Z]{16}",
    "aws_secret": r"(?:aws_secret_access_key|AWS_SECRET)\s*[=:]\s*[A-Za-z0-9/+=]{40}",
    "jwt": r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    "private_key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "password_in_url": r"://[^/\s]*:[^@/\s]+@",
    "credit_card": r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",
    "mac_keychain": r"security\s+find-(?:generic|internet)-password",
    "discord_token": r"[MN][A-Za-z\d]{23,}\.[\w-]{6}\.[\w-]{27,}",
    "notion_token": r"(?:ntn_|secret_)[A-Za-z0-9]{40,}",
    "generic_secret": r"(?:secret|token|password|passwd|credential)\s*[=:]\s*['\"][^'\"]{8,}['\"]",
}


def scan_output_for_leaks(text: str) -> list[str]:
    """AI出力にcredentialが含まれていないか検証"""
    findings = []
    for name, pattern in SENSITIVE_PATTERNS.items():
        if re.search(pattern, text):
            findings.append(name)
    return findings


# プロンプトインジェクション検知パターン（ユーザー入力のスクリーン上テキスト用）
_INJECTION_PATTERNS = [
    r"ignore\s+(?:all\s+)?(?:previous|above|prior)\s+instructions",
    r"system\s*:\s*you\s+are",
    r"forget\s+(?:all\s+)?(?:previous|your)\s+instructions",
    r"new\s+instructions?\s*:",
    r"act\s+as\s+(?:if|a|an)\s+",
    r"do\s+not\s+follow\s+(?:the|your)\s+(?:previous|original)",
]


def detect_injection(text: str) -> bool:
    """テキストにプロンプトインジェクションの兆候がないかチェック"""
    text_lower = text.lower()
    for pattern in _INJECTION_PATTERNS:
        if re.search(pattern, text_lower):
            logger.warning(f"Potential prompt injection detected: {pattern}")
            return True
    return False


def _clean_hallucinated_tags(text: str) -> str:
    """Geminiがハルシネーションで出力するタグを除去"""
    # <execute_tool>...</execute_tool> 等のXMLタグを除去
    text = re.sub(r"</?execute_tool>", "", text)
    text = re.sub(r"</?tool_call>", "", text)
    text = re.sub(r"</?function_call>", "", text)
    # print(agent.xxx()) みたいなコード片を除去
    text = re.sub(r"print\(agent\.\w+\([^)]*\)\)", "", text)
    return text.strip()


def sanitize_response(response: str) -> tuple[str, list[str]]:
    """AI応答をスキャンし、漏洩があればブロック。ハルシネーションタグも除去。"""
    # ハルシネーションタグの除去
    response = _clean_hallucinated_tags(response)

    # リークスキャンは切り詰め前に全文で行う（切り詰め後だと漏洩を見逃す）
    leaks = scan_output_for_leaks(response)
    if leaks:
        logger.critical(f"OUTPUT LEAK DETECTED: {leaks}")
        return (
            "[セキュリティ警告] 機密情報が検出されたため応答をブロックしました",
            leaks,
        )
    # 長すぎる応答を切り詰め（LINE 5000文字制限 + バッファ）
    if len(response) > 5000:
        response = response[:4900] + "\n...(長すぎるので省略)"

    return response, []


async def scan_screenshot_for_sensitive_info(image_path: str) -> dict:
    """スクショ内に機密情報（パスワード、カード番号等）がないかGemini Flashで高速チェック

    10秒タイムアウト付き。スキャン失敗時は警告付きで通す。

    Returns:
        {"safe": True} or {"safe": False, "reason": "検出された内容の説明"}
    """
    import asyncio

    try:
        import google.genai as genai
        from config import GEMINI_API_KEY

        filepath = Path(image_path)
        if not filepath.exists():
            return {"safe": True}

        # ファイルサイズチェック（10MB超はスキップ）
        if filepath.stat().st_size > 10 * 1024 * 1024:
            logger.warning("Screenshot too large for security scan, skipping")
            return {"safe": True, "scan_error": "file too large"}

        img_bytes = filepath.read_bytes()

        client = genai.Client(api_key=GEMINI_API_KEY)

        # 10秒タイムアウト
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    genai.types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                    genai.types.Part(text=(
                        "Does this screenshot contain visible sensitive information? "
                        "Check for: unmasked passwords, credit card numbers, API keys/secrets, "
                        "bank account numbers, private keys, social security numbers.\n\n"
                        "Reply ONLY with one word: SAFE or UNSAFE"
                    )),
                ],
                config=genai.types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=10,
                ),
            ),
            timeout=10.0,
        )

        text = response.text.strip().upper()
        logger.info(f"Screenshot scan result: {text}")

        if "UNSAFE" in text:
            logger.critical("SCREENSHOT SENSITIVE INFO DETECTED")
            return {"safe": False, "reason": "機密情報を検出"}

        return {"safe": True}

    except asyncio.TimeoutError:
        # タイムアウト時は安全とみなさない（スキャンできなかったものを通さない）
        logger.warning("Screenshot security scan timed out — blocking image to be safe")
        return {"safe": False, "reason": "セキュリティスキャンがタイムアウト。画像は送信せずテキストのみ返します"}
    except Exception as e:
        logger.warning(f"Screenshot security scan failed: {e} — blocking image to be safe")
        return {"safe": False, "reason": f"セキュリティスキャン失敗（{type(e).__name__}）。画像は送信せずテキストのみ返します"}
