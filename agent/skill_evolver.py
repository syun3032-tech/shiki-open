"""スキル自動進化エンジン（MetaClaw inspired）

会話セッションと失敗ログから新しいスキルを自動生成・品質管理する。
GPUもファインチューニングも不要 — Gemini Flashでスキル抽出するだけ。

進化のトリガー:
1. セッション終了時 → 成功パターンをスキル化
2. 失敗蓄積時 → 失敗パターンから修正スキルを生成
3. 定期プルーニング → 使われないスキルを自動削除
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

import google.genai as genai

from config import GEMINI_API_KEY

logger = logging.getLogger("shiki.skill_evolver")

# Gemini Flash（遅延初期化）
_client: genai.Client | None = None
_EVOLVER_MODEL = "gemini-2.5-flash"

# 進化スキル保存先
_EVOLVED_SKILLS_FILE = Path(__file__).parent.parent / ".ritsu" / "evolved_skills.json"

# 制限
MAX_EVOLVED_SKILLS = 50
MIN_SCORE_TO_KEEP = 0.2  # これ以下は自動削除
SKILL_DECAY_RATE = 0.95  # 使われないスキルのスコア減衰率

# インメモリキャッシュ
_skills_cache: list[dict] | None = None


def _get_client() -> genai.Client:
    """Geminiクライアント遅延初期化"""
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY not set")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


# === プロンプトインジェクション防御 ===

_INJECTION_PATTERNS = re.compile(
    r"(?i)"
    r"(ignore\s+(previous|all|above)\s+instructions?"
    r"|system\s*prompt"
    r"|you\s+are\s+now"
    r"|disregard\s+(everything|all)"
    r"|forget\s+(your|all)\s+(rules|instructions)"
    r"|new\s+instructions?\s*:"
    r"|act\s+as\s+(if|a)\b"
    r"|pretend\s+(to\s+be|you)"
    r"|override\s+(safety|rules|restrictions)"
    r"|jailbreak"
    r"|\[system\]"
    r"|<\s*/?system\s*>)"
)

_MAX_FIELD_LENGTH = 200  # スキルフィールドの最大長


def _sanitize_skill_field(text: str) -> str:
    """スキルフィールドからインジェクションパターンを除去"""
    if not isinstance(text, str):
        return ""
    # 長さ制限
    text = text[:_MAX_FIELD_LENGTH]
    # インジェクションパターン除去
    text = _INJECTION_PATTERNS.sub("[FILTERED]", text)
    # ゼロ幅文字除去
    text = re.sub(r"[\u200b-\u200f\u2028-\u202f\u2060-\u206f\ufeff]", "", text)
    return text.strip()


def _sanitize_skill(skill: dict) -> dict:
    """スキル全体をサニタイズ"""
    for field in ("name", "description", "rule", "steps_description", "category"):
        if field in skill:
            skill[field] = _sanitize_skill_field(skill[field])
    # trigger_keywordsもサニタイズ
    if "trigger_keywords" in skill:
        skill["trigger_keywords"] = [
            _sanitize_skill_field(kw)[:50]
            for kw in skill.get("trigger_keywords", [])
            if isinstance(kw, str)
        ][:10]  # 最大10キーワード
    return skill


def _extract_json_from_response(text: str) -> str:
    """LLMレスポンスからJSONを抽出"""
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def _load_evolved_skills() -> list[dict]:
    """進化スキルを読み込み（インメモリキャッシュ付き）"""
    global _skills_cache
    if _skills_cache is not None:
        return _skills_cache
    if _EVOLVED_SKILLS_FILE.exists():
        try:
            _skills_cache = json.loads(_EVOLVED_SKILLS_FILE.read_text(encoding="utf-8"))
            return _skills_cache
        except Exception:
            _skills_cache = []
            return []
    _skills_cache = []
    return []


def _save_evolved_skills(skills: list[dict]):
    """進化スキルを保存（キャッシュも更新）"""
    global _skills_cache
    _skills_cache = skills
    try:
        _EVOLVED_SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _EVOLVED_SKILLS_FILE.write_text(
            json.dumps(skills, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error(f"Failed to save evolved skills: {e}")


async def evolve_from_session(conversation: list[dict], tool_calls: list[dict]) -> list[dict]:
    """セッションから新しいスキルを自動生成

    MetaClaw の auto_evolve に相当。
    成功した操作パターンを汎用スキルとして抽出する。

    Args:
        conversation: [{"role": "user|assistant", "text": "..."}]
        tool_calls: [{"tool": "name", "args": {...}, "success": bool}]

    Returns:
        新しく生成されたスキルのリスト
    """
    if not tool_calls or len(tool_calls) < 2:
        return []

    # 成功したツールコールのみ
    successful = [tc for tc in tool_calls if tc.get("success", True)]
    if len(successful) < 2:
        return []

    # 会話テキスト
    conv_text = "\n".join([
        f"{'ユーザー' if m['role'] == 'user' else '識'}: {m['text'][:300]}"
        for m in conversation[-10:]
    ])

    # ツールコール履歴
    tools_text = "\n".join([
        f"- {tc['tool']}({json.dumps(tc.get('args', {}), ensure_ascii=False)[:100]}) → {'成功' if tc.get('success', True) else '失敗'}"
        for tc in tool_calls[-15:]
    ])

    prompt = f"""あなたはAIエージェントのスキル抽出エンジン。
以下の会話とツール実行履歴から、再利用可能なスキル（操作パターン）を抽出して。

# 会話
{conv_text}

# ツール実行
{tools_text}

# ルール
- 汎用的に再利用できるパターンのみ抽出（固有名詞に依存するものは除外）
- 各スキルは JSON で出力:
  {{"name": "スキル名（短く）", "description": "何をするか", "trigger_keywords": ["キーワード1", "キーワード2"], "steps_description": "手順の説明", "category": "web|mac|file|dev|info"}}
- 最大3個まで
- 既に当たり前すぎるもの（スクショ撮る、アプリ開く等）は除外
- 複数ツールの組み合わせパターンを優先

JSONの配列だけ出力して。説明は不要。スキルが無ければ空配列 [] を返して。"""

    try:
        response = await _get_client().aio.models.generate_content(
            model=_EVOLVER_MODEL,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=800,
            ),
        )
        text = (response.text or "").strip()
        text = _extract_json_from_response(text)

        new_skills = json.loads(text)
        if not isinstance(new_skills, list):
            return []

        # サニタイズ + メタデータ追加
        now = datetime.now().isoformat()
        sanitized = []
        for skill in new_skills:
            if not isinstance(skill, dict) or not skill.get("name"):
                continue
            skill = _sanitize_skill(skill)
            skill["created_at"] = now
            skill["source"] = "session"
            skill["score"] = 0.5
            skill["usage_count"] = 0
            skill["success_count"] = 0
            sanitized.append(skill)

        if sanitized:
            _merge_and_save(sanitized)
            logger.info(f"Evolved {len(sanitized)} skills from session")

        return sanitized

    except Exception as e:
        logger.warning(f"Skill evolution from session failed: {e}")
        return []


async def evolve_from_failures(failures: list[dict]) -> list[dict]:
    """失敗パターンから修正スキルを生成

    MetaClaw の evolver に相当。
    失敗から学んで「次はこうしろ」というスキルを生成。

    Args:
        failures: [{"tool": "name", "args_summary": "...", "error": "..."}]

    Returns:
        新しく生成された修正スキルのリスト
    """
    if not failures or len(failures) < 2:
        return []

    failures_text = "\n".join([
        f"- {f['tool']}: {f.get('args_summary', '')} → エラー: {f['error'][:200]}"
        for f in failures[-10:]
    ])

    prompt = f"""あなたはAIエージェントのデバッグスキル生成エンジン。
以下のツール実行失敗パターンから、同じ失敗を防ぐためのスキル（ルール）を生成して。

# 失敗パターン
{failures_text}

# ルール
- 「〇〇する前に△△を確認しろ」のような防御的スキルを生成
- 各スキルは JSON で出力:
  {{"name": "スキル名", "description": "何を防ぐか", "trigger_keywords": ["関連キーワード"], "rule": "具体的なルール文", "category": "防御"}}
- 最大2個まで
- 同じツールの同じエラーが複数回あるパターンを優先

JSONの配列だけ出力して。スキルが無ければ空配列 [] を返して。"""

    try:
        response = await _get_client().aio.models.generate_content(
            model=_EVOLVER_MODEL,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=500,
            ),
        )
        text = (response.text or "").strip()
        text = _extract_json_from_response(text)

        new_skills = json.loads(text)
        if not isinstance(new_skills, list):
            return []

        now = datetime.now().isoformat()
        sanitized = []
        for skill in new_skills:
            if not isinstance(skill, dict) or not skill.get("name"):
                continue
            skill = _sanitize_skill(skill)
            skill["created_at"] = now
            skill["source"] = "failure"
            skill["score"] = 0.6
            skill["usage_count"] = 0
            skill["success_count"] = 0
            sanitized.append(skill)

        if sanitized:
            _merge_and_save(sanitized)
            logger.info(f"Evolved {len(sanitized)} skills from failures")

        return sanitized

    except Exception as e:
        logger.warning(f"Skill evolution from failures failed: {e}")
        return []


def record_skill_usage(skill_name: str, success: bool):
    """スキル使用結果を記録してスコア更新

    MetaClaw の PRM（Process Reward Model）の簡易版。
    """
    skills = _load_evolved_skills()

    for skill in skills:
        if skill["name"] == skill_name:
            skill["usage_count"] = skill.get("usage_count", 0) + 1
            if success:
                skill["success_count"] = skill.get("success_count", 0) + 1

            # スコア更新（指数移動平均）
            total = skill["usage_count"]
            if total > 0:
                success_rate = skill["success_count"] / total
                # 既存スコアと成功率のブレンド（新しい結果を重視）
                old_score = skill.get("score", 0.5)
                skill["score"] = old_score * 0.7 + success_rate * 0.3

            skill["last_used"] = datetime.now().isoformat()
            break

    _save_evolved_skills(skills)


def get_relevant_skills(user_message: str, top_k: int = 5) -> list[dict]:
    """メッセージに関連するスキルを取得

    MetaClaw の skill injection に相当。
    キーワードマッチ + スコアでランキング。
    """
    skills = _load_evolved_skills()
    if not skills:
        return []

    scored = []
    message_lower = user_message.lower()

    for skill in skills:
        # キーワードマッチスコア
        keywords = skill.get("trigger_keywords", [])
        keyword_hits = sum(1 for kw in keywords if kw.lower() in message_lower)

        # カテゴリマッチ（ざっくり）
        category_bonus = 0
        cat = skill.get("category", "")
        if cat == "web" and any(w in message_lower for w in ["検索", "サイト", "ページ", "ブラウザ", "url"]):
            category_bonus = 0.3
        elif cat == "mac" and any(w in message_lower for w in ["アプリ", "開い", "音量", "ダーク"]):
            category_bonus = 0.3
        elif cat == "file" and any(w in message_lower for w in ["ファイル", "フォルダ", "保存", "読"]):
            category_bonus = 0.3
        elif cat == "dev" and any(w in message_lower for w in ["git", "コード", "ビルド", "テスト"]):
            category_bonus = 0.3

        if keyword_hits == 0 and category_bonus == 0:
            continue

        quality_score = skill.get("score", 0.5)
        total_score = (keyword_hits * 0.4) + (quality_score * 0.4) + category_bonus + (0.1 if skill.get("source") == "failure" else 0)

        scored.append((total_score, skill))

    # スコア降順でtop_k
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s[1] for s in scored[:top_k]]


def format_skills_for_prompt(skills: list[dict]) -> str:
    """スキルをシステムプロンプト注入用にフォーマット

    MetaClaw のスキルインジェクションに相当。
    """
    if not skills:
        return ""

    lines = ["## 学習済みスキル（自動進化）"]
    for skill in skills:
        name = skill.get("name", "")
        desc = skill.get("description", "")
        rule = skill.get("rule", "")
        steps = skill.get("steps_description", "")
        score = skill.get("score", 0)
        source_label = "失敗から学習" if skill.get("source") == "failure" else "経験から学習"

        lines.append(f"- **{name}** ({source_label}, 信頼度{score:.0%})")
        if rule:
            lines.append(f"  ルール: {rule}")
        elif steps:
            lines.append(f"  手順: {steps}")
        elif desc:
            lines.append(f"  {desc}")

    return "\n".join(lines)


def prune_skills():
    """低品質スキルを削除 + スコア減衰

    定期的に呼ぶことで、使われないスキルが自然消滅する。
    """
    skills = _load_evolved_skills()
    if not skills:
        return

    # スコア減衰（使われていないスキルのスコアを下げる）
    for skill in skills:
        if skill.get("usage_count", 0) == 0:
            skill["score"] = skill.get("score", 0.5) * SKILL_DECAY_RATE

    # 低スコア削除
    before = len(skills)
    skills = [s for s in skills if s.get("score", 0) >= MIN_SCORE_TO_KEEP]

    # 上限超過 → スコア低い順に削除
    if len(skills) > MAX_EVOLVED_SKILLS:
        skills.sort(key=lambda s: s.get("score", 0), reverse=True)
        skills = skills[:MAX_EVOLVED_SKILLS]

    pruned = before - len(skills)
    if pruned > 0:
        logger.info(f"Pruned {pruned} low-quality skills ({before} → {len(skills)})")

    _save_evolved_skills(skills)


def _merge_and_save(new_skills: list[dict]):
    """新しいスキルを既存とマージして保存（重複チェック付き）"""
    existing = _load_evolved_skills()

    existing_names = {s["name"].lower() for s in existing}

    added = 0
    for skill in new_skills:
        name = skill.get("name", "").lower()
        if not name:
            continue

        # 名前の重複チェック（類似もチェック）
        if name in existing_names:
            continue

        # 類似キーワードチェック
        new_kws = set(kw.lower() for kw in skill.get("trigger_keywords", []))
        is_duplicate = False
        for ex in existing:
            ex_kws = set(kw.lower() for kw in ex.get("trigger_keywords", []))
            if new_kws and ex_kws:
                overlap = len(new_kws & ex_kws) / max(len(new_kws), len(ex_kws))
                if overlap > 0.7:
                    is_duplicate = True
                    break

        if not is_duplicate:
            existing.append(skill)
            existing_names.add(name)
            added += 1

    if added > 0:
        # 上限チェック
        if len(existing) > MAX_EVOLVED_SKILLS:
            existing.sort(key=lambda s: s.get("score", 0), reverse=True)
            existing = existing[:MAX_EVOLVED_SKILLS]

        _save_evolved_skills(existing)
        logger.info(f"Merged {added} new evolved skills (total: {len(existing)})")


def get_stats() -> dict:
    """進化スキルの統計"""
    skills = _load_evolved_skills()
    if not skills:
        return {"total": 0}

    from_session = sum(1 for s in skills if s.get("source") == "session")
    from_failure = sum(1 for s in skills if s.get("source") == "failure")
    avg_score = sum(s.get("score", 0) for s in skills) / len(skills)
    total_usage = sum(s.get("usage_count", 0) for s in skills)

    return {
        "total": len(skills),
        "from_session": from_session,
        "from_failure": from_failure,
        "avg_score": round(avg_score, 2),
        "total_usage": total_usage,
    }
