"""自己進化エンジン — X(Twitter)巡回 + 分析 + Notion記録

目的:
1. 識ちゃん自身の強化 — 他のAIエージェントの新機能・アーキテクチャを発見し、取り入れる
2. オーナーの事業支援 — マーケ・組織・開発の最新知見をオーナーの事業に活かす
3. 技術トレンド把握 — AI/LLM/エージェント分野の最前線を常にキャッチアップ

フロー:
1. X(Twitter) + Web検索で関連ニュースを巡回
2. 識ちゃんの既存機能と照らし合わせて「何が足りないか」を分析
3. 引用元URLを明記してNotionにまとめる
4. 実装可能なものは自分でNotionタスクに追加

スケジューラーから4時間ごとに定期実行。
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import google.genai as genai

from config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger("shiki.evolution")

# X巡回の検索キーワード
EVOLUTION_TOPICS = [
    "AI agent framework",
    "OpenClaw update",
    "Claude Code tips",
    "AI secretary automation",
    "Notion AI automation",
    "LLM agent architecture",
    "AI self-improvement",
    "Manus AI",
    "Devin AI",
    "AI coding agent",
    "プロンプトエンジニアリング",
    "AIエージェント 開発",
    "個人開発 AI",
]

# 巡回間隔
EVOLUTION_INTERVAL = 4 * 60 * 60  # 4時間ごと
EVOLUTION_START_HOUR = 9
EVOLUTION_END_HOUR = 23

# 既存機能リスト（分析時の比較用）
CURRENT_CAPABILITIES = [
    "Notion連携（プロジェクト/タスクCRUD、コメント、チェックボックス）",
    "タスク自動実行エンジン（Notion→実行→報告）",
    "Discord/LINE通知",
    "Playwright headlessブラウザ（Web検索・操作）",
    "Claude Code委譲（コーディング・設計）",
    "PC操作（スクリーンショット・マウス・キーボード）",
    "自己振り返り（Reflexionパターン）",
    "常時指示（Standing Orders）",
    "朝ブリーフィング（Notion統合）",
    "10分ごとタスク巡回",
    "マルチエージェント（researcher/coder/writer/analyst）",
    "Skill Injection（Paperclipパターン）",
    "収益トラッカー（Lancers/CrowdWorks）",
]

# Geminiクライアント
_client = genai.Client(api_key=GEMINI_API_KEY)

# 記録済みURLのキャッシュ（重複防止）
_SEEN_URLS_FILE = Path(__file__).parent.parent / ".ritsu" / "evolution_seen.json"


def _load_seen_urls() -> set[str]:
    if _SEEN_URLS_FILE.exists():
        try:
            data = json.loads(_SEEN_URLS_FILE.read_text(encoding="utf-8"))
            return set(data[-500:])  # 最新500件のみ保持
        except Exception:
            pass
    return set()


def _save_seen_urls(urls: set[str]):
    _SEEN_URLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SEEN_URLS_FILE.write_text(
        json.dumps(list(urls)[-500:], ensure_ascii=False),
        encoding="utf-8",
    )


async def _fetch_x_timeline() -> list[dict]:
    """X(Twitter)のタイムラインから関連ポストを取得"""
    from tools.browser import browse_url

    posts = []
    try:
        # Xのホームタイムライン取得（ログイン済みセッション使用）
        result = await browse_url("https://x.com/home")
        if result.get("error"):
            logger.warning(f"X timeline fetch failed: {result['error']}")
            return posts

        text = result.get("text", "")
        links = result.get("links", [])

        # テキストからポスト情報を抽出
        if text:
            posts.append({
                "source": "timeline",
                "text": text[:5000],
                "links": links[:20],
                "url": "https://x.com/home",
            })
    except Exception as e:
        logger.warning(f"X timeline error: {e}")

    return posts


async def _search_x_topics() -> list[dict]:
    """X(Twitter)で関連トピックを検索"""
    from tools.browser import search_web, get_page_text
    import random

    # ランダムに2-3トピック選択
    topics = random.sample(EVOLUTION_TOPICS, min(3, len(EVOLUTION_TOPICS)))
    results = []

    for topic in topics:
        try:
            # Google検索でX/Twitterの投稿を探す
            search_result = await search_web(f"site:x.com {topic} 最新")
            if search_result.get("error") or not search_result.get("results"):
                # フォールバック: 一般Web検索
                search_result = await search_web(f"{topic} 2026 最新")

            if search_result.get("results"):
                for r in search_result["results"][:3]:
                    results.append({
                        "source": "search",
                        "topic": topic,
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": r.get("snippet", ""),
                    })

                # 上位1件の記事を詳しく読む
                top_url = search_result["results"][0].get("url", "")
                if top_url:
                    try:
                        page = await get_page_text(top_url)
                        if page.get("text"):
                            results[-len(search_result["results"][:3])]["full_text"] = page["text"][:3000]
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Search failed for {topic}: {e}")

        # API負荷軽減
        await asyncio.sleep(2)

    return results


async def _analyze_findings(findings: list[dict]) -> dict[str, Any]:
    """収集した情報を分析: 識ちゃんの強化に使えるか？"""
    if not findings:
        return {"insights": [], "improvements": []}

    # 情報をテキストにまとめる
    findings_text = ""
    for i, f in enumerate(findings[:10]):
        findings_text += f"\n## [{i+1}] {f.get('title', f.get('topic', ''))}\n"
        findings_text += f"URL: {f.get('url', '')}\n"
        if f.get("snippet"):
            findings_text += f"概要: {f['snippet']}\n"
        if f.get("full_text"):
            findings_text += f"本文抜粋: {f['full_text'][:1000]}\n"
        if f.get("text"):
            findings_text += f"内容: {f['text'][:1000]}\n"

    capabilities_text = "\n".join(f"- {c}" for c in CURRENT_CAPABILITIES)

    prompt = f"""あなたはAIエージェント「識（しき）」の自己進化分析エンジン。
以下の最新情報を分析して、識ちゃんの強化に使えるインサイトを抽出して。

# 識ちゃんの現在の機能
{capabilities_text}

# 収集した最新情報
{findings_text}

# 分析ルール
1. 各情報から「識ちゃんに取り入れられそうなアイデア」を抽出
2. 既に持っている機能との差分を明確にする
3. 実装の優先度（高/中/低）と難易度（簡単/普通/難しい）を判定
4. 引用元URLを必ず付ける

# 出力形式（JSON）
{{
  "insights": [
    {{
      "title": "インサイトのタイトル",
      "description": "具体的な内容（2-3行）",
      "source_url": "引用元URL",
      "relevance": "高/中/低",
      "difficulty": "簡単/普通/難しい",
      "category": "AI/マーケ/UI/開発/組織"
    }}
  ],
  "improvements": [
    {{
      "title": "識ちゃんへの改善提案",
      "description": "何をどう変えるか",
      "priority": "高/中/低"
    }}
  ],
  "summary": "今回の巡回サマリー（3行以内）"
}}

JSONのみ出力して。"""

    try:
        response = await _client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=1500,
            ),
        )
        text = (response.text or "").strip()
        # JSONブロック抽出
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        return json.loads(text)
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        return {"insights": [], "improvements": [], "summary": "分析失敗"}


async def _save_to_notion(analysis: dict) -> dict[str, Any]:
    """分析結果をNotionに保存"""
    from tools.notion import search_notion, create_project, add_comment, append_blocks

    insights = analysis.get("insights", [])
    improvements = analysis.get("improvements", [])
    summary = analysis.get("summary", "")

    if not insights and not improvements:
        return {"success": True, "saved": 0}

    # Notionに進化ログページを追記（プロジェクトページのコメントとして）
    # まずプロジェクト一覧から検索
    from tools.notion import list_projects
    proj_result = await list_projects()

    # 全プロジェクトの親ページにコメント（なければ最初のプロジェクトに）
    target_page_id = None
    if proj_result.get("success"):
        for p in proj_result["projects"]:
            if "TimeTurn" in p.get("プロジェクト名", ""):
                target_page_id = p["id"]
                break
        if not target_page_id and proj_result["projects"]:
            target_page_id = proj_result["projects"][0]["id"]

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    saved = 0

    # ブロックとして追記
    blocks = []
    blocks.append({"type": "heading_2", "text": f"自己進化レポート ({now})"})

    if summary:
        blocks.append({"type": "paragraph", "text": summary})

    for insight in insights[:5]:
        title = insight.get("title", "")
        desc = insight.get("description", "")
        url = insight.get("source_url", "")
        relevance = insight.get("relevance", "中")
        text = f"[{relevance}] {title}: {desc}"
        if url:
            text += f" (出典: {url})"
        blocks.append({"type": "bulleted_list_item", "text": text})
        saved += 1

    if improvements:
        blocks.append({"type": "heading_3", "text": "改善提案"})
        for imp in improvements[:3]:
            text = f"[{imp.get('priority', '中')}] {imp.get('title', '')}: {imp.get('description', '')}"
            blocks.append({"type": "to_do", "text": text})
            saved += 1

    # Notionに保存
    if target_page_id and blocks:
        import json as _json
        await append_blocks(target_page_id, _json.dumps(blocks, ensure_ascii=False))

    # 改善提案をNotionタスクに変換（承認ゲート付き）
    # 重要: 自動実行はしない。「提案」ステータスで作成し、オーナーが承認してから実行される。
    # これにより不要な機能が勝手に追加されることを防ぐ。
    tasks_created = 0
    if improvements and target_page_id:
        from tools.notion import create_task
        for imp in improvements:
            if imp.get("priority") == "高":
                # ステータスは「提案」（未着手ではない）→ オーナーが確認して「未着手」に変えたら実行対象になる
                task_result = await create_task(
                    name=f"[自己進化提案] {imp.get('title', '')[:40]}",
                    project_id=target_page_id,
                    status="提案",
                    priority="低",  # 自動提案は低優先度（オーナーが判断して上げる）
                    memo=(
                        f"{imp.get('description', '')}\n\n"
                        f"---\n"
                        f"⚠️ これは識ちゃんの自己進化エンジンが自動提案したタスクです。\n"
                        f"実行するには、ステータスを「未着手」に変更してください。\n"
                        f"不要なら「却下」に変更またはアーカイブしてください。"
                    ),
                )
                if task_result.get("success"):
                    tasks_created += 1
                    logger.info(f"Proposal created (pending approval): {imp.get('title', '')[:40]}")

    return {
        "success": True,
        "saved": saved,
        "tasks_created": tasks_created,
        "page_id": target_page_id,
    }


async def run_evolution_cycle(push_fn=None) -> dict[str, Any]:
    """自己進化サイクルを1回実行

    1. X + Web検索で情報収集
    2. Geminiで分析
    3. Notionに保存
    4. Discord通知
    """
    logger.info("=== Self-evolution cycle start ===")
    seen_urls = _load_seen_urls()

    # 1. 情報収集（Xタイムライン + トピック検索を並列）
    timeline_task = _fetch_x_timeline()
    search_task = _search_x_topics()
    timeline_results, search_results = await asyncio.gather(
        timeline_task, search_task,
    )

    all_findings = timeline_results + search_results

    # 重複除外
    new_findings = []
    for f in all_findings:
        url = f.get("url", "")
        if url and url not in seen_urls:
            new_findings.append(f)
            seen_urls.add(url)

    if not new_findings:
        logger.info("No new findings this cycle")
        return {"success": True, "findings": 0, "insights": 0}

    _save_seen_urls(seen_urls)

    # 2. 分析
    analysis = await _analyze_findings(new_findings)

    # 3. Notion保存
    notion_result = await _save_to_notion(analysis)

    # 4. Discord通知
    insights_count = len(analysis.get("insights", []))
    improvements_count = len(analysis.get("improvements", []))
    summary = analysis.get("summary", "")

    tasks_created = notion_result.get("tasks_created", 0)

    if push_fn and (insights_count > 0 or improvements_count > 0):
        from config import DISCORD_OWNER_ID
        msg = (
            f"[自己進化レポート]\n"
            f"情報収集: {len(new_findings)}件 → インサイト: {insights_count}件 → 改善提案: {improvements_count}件\n"
        )
        if tasks_created > 0:
            msg += f"Notionに{tasks_created}件の提案を追加したよ。実行するにはステータスを「未着手」に変えてね。\n"
        if summary:
            msg += f"\n{summary}\n"
        msg += "\nNotionに記録済み。"
        try:
            await push_fn(str(DISCORD_OWNER_ID), msg)
        except Exception as e:
            logger.warning(f"Discord notify failed: {e}")

    logger.info(
        f"=== Self-evolution cycle done: "
        f"{len(new_findings)} findings, {insights_count} insights, "
        f"{improvements_count} improvements ==="
    )

    return {
        "success": True,
        "findings": len(new_findings),
        "insights": insights_count,
        "improvements": improvements_count,
        "summary": summary,
        "notion_saved": notion_result.get("saved", 0),
    }
