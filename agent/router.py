"""スマートモデルルーティング（GenSpark Claw MoA inspired）

タスクの複雑度を分析して、最適なモデルにルーティング。
- 簡単なタスク → Gemini Flash（速い・安い）
- 複雑なタスク → Gemini Pro（賢い）

GenSpark Clawは9+モデルを混合するが、
識ちゃんは2モデルのルーティングでコスト最適化。
"""

import logging
import re

logger = logging.getLogger("shiki.router")

# モデル定義
MODEL_PRO = "gemini-2.5-pro"
MODEL_FLASH = "gemini-2.5-flash"

# === 複雑度分類ルール ===

# 簡単タスク（Flash で十分）
_SIMPLE_PATTERNS = [
    # 単純な情報取得
    r"^(今|いま).*(何時|時間|日付|曜日)",
    r"^(スクショ|スクリーンショット|画面).*(撮|取|見)",
    r"^(音量|ボリューム).*(上|下|変|設定|ミュート)",
    r"^(ダーク|ライト)モード",
    r"^(開|起動).*(して|しろ)$",
    # リマインダー操作
    r"リマインダー.*(一覧|リスト|見せ|確認)",
    # 単純な挨拶・雑談
    r"^(おはよう|こんにちは|こんばんは|おやすみ|ありがとう|了解)",
    r"^(うん|おけ|OK|ok|はい|いいよ|分かった)$",
]

# 複雑タスク（Pro 必須）
_COMPLEX_PATTERNS = [
    # マルチステップ操作
    r"(?:して|やって).{2,30}(?:して|やって)",  # 複数アクション（間が2-30文字＝実際の依頼文）
    r"(調べ|検索|リサーチ).*(まとめ|要約|教え)",
    # コード・開発
    r"(コード|プログラム|スクリプト|関数|クラス).*(書|作|修正|直|追加)",
    r"(デバッグ|バグ|エラー).*(直|修正|原因)",
    r"git\s+(rebase|merge|cherry-pick)",
    # 分析・判断
    r"(比較|分析|評価|レビュー)",
    r"(なぜ|どうして|原因|理由)",
    r"(最適|ベスト|おすすめ|提案)",
    # ファイル操作 + 判断
    r"(ファイル|フォルダ).*(整理|リファクタ|構造)",
    # Web操作 + 判断
    r"(サイト|ページ).*(登録|申し込|設定|フォーム)",
]

# コンパイル
_SIMPLE_RE = [re.compile(p, re.IGNORECASE) for p in _SIMPLE_PATTERNS]
_COMPLEX_RE = [re.compile(p, re.IGNORECASE) for p in _COMPLEX_PATTERNS]


def classify_complexity(message: str) -> str:
    """メッセージの複雑度を分類

    Returns:
        "simple" | "complex"
    """
    # 画像付き → 常にPro（視覚推論が必要）
    # （呼び出し元で image_bytes がある場合は "complex" 扱い）

    # 簡単パターンを先にチェック（誤ルーティング防止）
    for pattern in _SIMPLE_RE:
        if pattern.search(message):
            return "simple"

    # 複雑パターン
    for pattern in _COMPLEX_RE:
        if pattern.search(message):
            return "complex"

    # メッセージの長さでヒューリスティック
    if len(message) > 100:
        return "complex"

    # 短いメッセージ（100文字以下）でパターンにも一致しない → Flash で十分
    # 例: 「〇〇開いて」「音量50」「今何してる？」等
    return "simple"


def select_model(message: str, has_image: bool = False, has_tools: bool = True) -> str:
    """タスクに最適なモデルを選択

    Args:
        message: ユーザーメッセージ
        has_image: 画像添付があるか
        has_tools: ツール使用が必要か

    Returns:
        モデル名
    """
    # 画像付き → 常にPro
    if has_image:
        logger.info(f"Router: Pro (image attached)")
        return MODEL_PRO

    complexity = classify_complexity(message)

    if complexity == "simple" and not has_tools:
        # 簡単な会話 → Flash
        logger.info(f"Router: Flash (simple, no tools)")
        return MODEL_FLASH

    if complexity == "simple":
        # 簡単だけどツール使用あり → Flash（ツール呼び出しはFlashでも十分）
        logger.info(f"Router: Flash (simple task)")
        return MODEL_FLASH

    # 複雑 → Pro
    logger.info(f"Router: Pro (complex task)")
    return MODEL_PRO


def select_model_for_iteration(iteration: int, base_model: str) -> str:
    """ReActループ中のモデル選択

    最初の数イテレーションはベースモデル。
    長引いたらProにエスカレート（Flashで詰まった場合）。
    """
    if base_model == MODEL_FLASH and iteration >= 5:
        logger.info(f"Router: Escalating to Pro at iteration {iteration}")
        return MODEL_PRO
    return base_model
