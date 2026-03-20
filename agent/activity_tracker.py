"""アクティビティトラッカー — ユーザーの行動を多角的に学習

3層のデータ収集でユーザーを深く理解する:

Tier 1 (毎5秒, コスト$0):
  - フロントアプリ + ウィンドウタイトル
  - ブラウザURL + ページタイトル
  - アプリ遷移パターン

Tier 2 (アプリ切替時のみ, 低コスト):
  - スクショ → Vision AIで画面内容をテキスト化
  - 画像は即破棄、テキストだけ残す

Tier 3 (30分ごと, 自律学習):
  - AIが蓄積データを分析して「もっと知りたいこと」を自分で決める
  - 足りない情報を能動的に取りに行く

学習するもの:
  - よく使うアプリ、時間帯パターン
  - よく見るサイト（ドメイン+ページ内容）
  - 作業フロー（何→何→何の順で作業するか）
  - プロジェクト構成（どのフォルダで何をしているか）
  - コミュニケーション傾向（Slack/Discord/メールの使い分け）
  - 興味・関心トピック（閲覧サイトから抽出）

ストレージ: テキストのみ、1日数十KB。
APIコスト: Tier2+3合わせて1日数十円程度。
"""

import asyncio
import hashlib
import json
import logging
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from config import GEMINI_API_KEY, RITSU_DIR

logger = logging.getLogger("shiki.activity_tracker")

# === 設定 ===
CAPTURE_INTERVAL = 5          # Tier1: アプリ情報取得間隔（秒）
SUMMARY_INTERVAL = 300        # 要約統合間隔（秒 = 5分）
DEEP_ANALYSIS_INTERVAL = 1800 # Tier3: 自律学習間隔（秒 = 30分）
MAX_RAW_ENTRIES = 200         # 要約前にバッファする最大エントリ数
SCREENSHOT_BUDGET_PER_HOUR = 30  # Tier2: 1時間あたりのスクショ上限

# === 保存先 ===
ACTIVITY_DIR = RITSU_DIR / "activity"
ACTIVITY_LOG_FILE = ACTIVITY_DIR / "current_raw.jsonl"
ACTIVITY_SUMMARY_FILE = ACTIVITY_DIR / "summaries.jsonl"
ACTIVITY_DAILY_DIR = ACTIVITY_DIR / "daily"
ACTIVITY_PROFILE_FILE = ACTIVITY_DIR / "profile.json"
ACTIVITY_INSIGHTS_FILE = ACTIVITY_DIR / "insights.json"


# ============================================================
# Tier 1: アプリ情報取得（APIコスト$0）
# ============================================================

async def _get_front_app_info() -> dict | None:
    """フロントアプリ名+ウィンドウタイトル+ブラウザURL+タブ数を取得"""
    try:
        from platform_layer import get_platform
        platform = get_platform()

        app = await platform.get_frontmost_app()
        window = await platform.get_window_info()
        browser = await platform.get_browser_info()

        if not app:
            return None

        info = {
            "app": app,
            "title": window.get("title", ""),
        }

        # ブラウザならURL/タイトルも
        if browser.get("url"):
            info["url"] = browser["url"]
            # URLからカテゴリを推定
            info["url_category"] = _categorize_url(browser["url"])
        if browser.get("title"):
            info["title"] = browser["title"]

        # エディタならファイルパス/プロジェクト情報を抽出
        if app in _EDITOR_APPS:
            project_info = _extract_project_info(window.get("title", ""))
            if project_info:
                info["project"] = project_info

        return info

    except Exception as e:
        logger.debug(f"App info failed: {e}")
        return None


_EDITOR_APPS = {"Cursor", "Visual Studio Code", "Code", "Xcode", "IntelliJ IDEA", "PyCharm", "Vim", "Neovim"}

_BROWSER_APPS = {"Google Chrome", "Safari", "Firefox", "Arc", "Brave Browser", "Microsoft Edge"}

_COMMS_APPS = {"Slack", "Discord", "Microsoft Teams", "Zoom", "Messages", "Mail", "Spark", "Thunderbird"}


def _extract_project_info(title: str) -> str | None:
    """エディタのウィンドウタイトルからプロジェクト名を抽出"""
    if not title:
        return None
    import re
    # "file.py — ProjectName" or "file.py - ProjectName"
    parts = re.split(r"\s*[—\-|]\s*", title)
    if len(parts) >= 2:
        return parts[-1].strip()
    return None


# URL→カテゴリ分類（よく見るジャンルを学習）
_URL_CATEGORIES = {
    "github.com": "development",
    "stackoverflow.com": "development",
    "qiita.com": "development",
    "zenn.dev": "development",
    "docs.python.org": "development",
    "developer.mozilla.org": "development",
    "notion.so": "productivity",
    "trello.com": "productivity",
    "asana.com": "productivity",
    "linear.app": "productivity",
    "figma.com": "design",
    "canva.com": "design",
    "twitter.com": "social",
    "x.com": "social",
    "linkedin.com": "social",
    "youtube.com": "media",
    "netflix.com": "media",
    "spotify.com": "media",
    "mail.google.com": "communication",
    "outlook.com": "communication",
    "slack.com": "communication",
    "discord.com": "communication",
    "chatgpt.com": "ai",
    "claude.ai": "ai",
    "gemini.google.com": "ai",
    "aistudio.google.com": "ai",
}


def _categorize_url(url: str) -> str:
    """URLからカテゴリを推定"""
    try:
        domain = urlparse(url).netloc.lower().lstrip("www.")
        # 完全一致
        if domain in _URL_CATEGORIES:
            return _URL_CATEGORIES[domain]
        # サブドメイン含む部分一致
        for pattern, cat in _URL_CATEGORIES.items():
            if pattern in domain:
                return cat
        return "other"
    except Exception:
        return "other"


# ============================================================
# Tier 2: スクショ → Vision AIテキスト化（アプリ切替時のみ）
# ============================================================

_screenshot_count_this_hour = 0
_screenshot_hour = -1
_last_screenshot_hash: str | None = None


async def _capture_screen_context() -> str | None:
    """スクショを撮ってVision AIでテキスト化。画像は即破棄。"""
    global _screenshot_count_this_hour, _screenshot_hour

    # 1時間あたりの上限チェック
    current_hour = datetime.now().hour
    if current_hour != _screenshot_hour:
        _screenshot_count_this_hour = 0
        _screenshot_hour = current_hour
    if _screenshot_count_this_hour >= SCREENSHOT_BUDGET_PER_HOUR:
        return None

    if not GEMINI_API_KEY:
        return None

    try:
        from platform_layer import get_platform
        platform = get_platform()

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            tmp_path = f.name

        captured = await platform.take_screenshot(tmp_path)
        if not captured:
            return None

        # リサイズ（512px、トークン節約）
        resized_path = tmp_path.replace(".jpg", "_s.jpg")
        resized = await platform.resize_image(tmp_path, resized_path, 512)
        target = resized_path if resized else tmp_path
        img_bytes = Path(target).read_bytes()

        # 即削除
        Path(tmp_path).unlink(missing_ok=True)
        Path(resized_path).unlink(missing_ok=True)

        # 画面変化チェック
        global _last_screenshot_hash
        h = hashlib.md5(img_bytes).hexdigest()
        if h == _last_screenshot_hash:
            return None
        _last_screenshot_hash = h

        # Vision AIでテキスト化
        import google.genai as genai
        client = genai.Client(api_key=GEMINI_API_KEY)

        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model="gemini-2.0-flash",
                contents=[
                    genai.types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                    genai.types.Part(text=(
                        "この画面を見て、ユーザーが何をしているかを2-3行で記述してください。\n"
                        "含める情報: アプリ名、作業内容、開いているファイル/URL/チャンネル、\n"
                        "画面に見える重要なテキスト（エラーメッセージ、通知等）。\n"
                        "日本語で簡潔に。"
                    )),
                ],
                config=genai.types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=150,
                ),
            ),
            timeout=10,
        )

        _screenshot_count_this_hour += 1

        if response and response.text:
            return response.text.strip()

    except Exception as e:
        logger.debug(f"Screen context capture failed: {e}")

    return None


# ============================================================
# ログ管理
# ============================================================

def _append_raw(timestamp: str, app: str, title: str, url: str = "",
                url_category: str = "", project: str = "",
                screen_context: str = ""):
    """生ログをJSONLに追記"""
    ACTIVITY_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"t": timestamp, "app": app, "title": title}
    if url:
        entry["url"] = url
    if url_category:
        entry["cat"] = url_category
    if project:
        entry["proj"] = project
    if screen_context:
        entry["screen"] = screen_context
    with open(ACTIVITY_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _load_raw() -> list[dict]:
    """生ログを読み込み"""
    if not ACTIVITY_LOG_FILE.exists():
        return []
    entries = []
    for line in ACTIVITY_LOG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def _clear_raw():
    """生ログをクリア"""
    if ACTIVITY_LOG_FILE.exists():
        ACTIVITY_LOG_FILE.write_text("", encoding="utf-8")


# ============================================================
# 要約（Gemini Flash、5分に1回）
# ============================================================

_SUMMARY_PROMPT = (
    "以下はユーザーのPC操作ログ（5秒間隔で記録）です。\n"
    "これを作業フローとして要約してください。\n\n"
    "ルール:\n"
    "- 同じアプリで同じ作業をしている期間はまとめる\n"
    "  例: 「10:05-10:20 CursorでPythonファイルを編集」\n"
    "- アプリの切り替えや作業内容の変化を重点的に記録\n"
    "- スクリーンコンテキスト（[screen]タグ）があれば作業内容の理解に活用\n"
    "- 箇条書き、時刻付き、簡潔に\n"
    "- 日本語で\n\n"
    "--- ログ ---\n{log}\n--- ログ終了 ---"
)


async def _summarize_raw(entries: list[dict]) -> str | None:
    """生ログをフロー要約に統合"""
    if not entries or not GEMINI_API_KEY:
        return _fallback_summary(entries)

    log_lines = []
    for e in entries:
        line = f"{e['t']} [{e['app']}] {e.get('title', '')}"
        if e.get("url"):
            line += f" ({e['url']})"
        if e.get("proj"):
            line += f" [project: {e['proj']}]"
        if e.get("screen"):
            line += f"\n  [screen] {e['screen']}"
        log_lines.append(line)
    log_text = "\n".join(log_lines)

    try:
        import google.genai as genai
        client = genai.Client(api_key=GEMINI_API_KEY)

        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=[_SUMMARY_PROMPT.format(log=log_text)],
                config=genai.types.GenerateContentConfig(
                    temperature=0.3,
                    max_output_tokens=400,
                ),
            ),
            timeout=15,
        )
        if response and response.text:
            return response.text.strip()
    except Exception as e:
        logger.warning(f"Summary failed: {e}")

    return _fallback_summary(entries)


def _fallback_summary(entries: list[dict]) -> str:
    """AIなしのフォールバック要約（重複除去して結合）"""
    if not entries:
        return ""
    seen = set()
    lines = []
    for e in entries:
        key = f"{e['app']}|{e.get('title', '')[:50]}"
        if key not in seen:
            seen.add(key)
            lines.append(f"- {e['t']} [{e['app']}] {e.get('title', '')}")
    return "\n".join(lines[-20:])


def _append_summary(summary: str, period_start: str, period_end: str):
    """要約をJSONLに追記"""
    ACTIVITY_DIR.mkdir(parents=True, exist_ok=True)
    entry = json.dumps(
        {"start": period_start, "end": period_end, "summary": summary},
        ensure_ascii=False,
    )
    with open(ACTIVITY_SUMMARY_FILE, "a", encoding="utf-8") as f:
        f.write(entry + "\n")


def _load_summaries() -> list[dict]:
    """要約を読み込み"""
    if not ACTIVITY_SUMMARY_FILE.exists():
        return []
    entries = []
    for line in ACTIVITY_SUMMARY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


# ============================================================
# Tier 3: 自律学習（AIが自分で「もっと知りたいこと」を決める）
# ============================================================

_DEEP_ANALYSIS_PROMPT = (
    "あなたはユーザーの行動を学習するAIアシスタントです。\n"
    "以下はユーザーの直近の作業ログと、これまでの行動プロファイルです。\n\n"
    "--- 直近の作業ログ ---\n{recent_log}\n--- ログ終了 ---\n\n"
    "--- 行動プロファイル ---\n{profile}\n--- プロファイル終了 ---\n\n"
    "以下のJSON形式でユーザーについての新しい発見を出力してください:\n"
    '{{\n'
    '  "work_style": "（作業スタイルの特徴。例: マルチタスク型、集中型等）",\n'
    '  "interests": ["関心がありそうなトピック1", "トピック2"],\n'
    '  "active_projects": ["取り組んでいるプロジェクト名1"],\n'
    '  "communication_style": "（コミュニケーション傾向。例: Slackメイン、メール少なめ等）",\n'
    '  "productivity_pattern": "（生産性パターン。例: 午前中はコーディング、午後はミーティング）",\n'
    '  "tools_mastery": {{"ツール名": "習熟度(beginner/intermediate/advanced)"}},\n'
    '  "suggestions": ["AIとしてもっと役に立てそうなこと1", "提案2"]\n'
    '}}\n\n'
    "新しい発見がなければ空のJSONを返してください。JSONのみ出力。"
)


async def _deep_analysis(recent_summaries: list[dict], profile: dict) -> dict | None:
    """蓄積データをAIが分析して深い洞察を抽出"""
    if not GEMINI_API_KEY or not recent_summaries:
        return None

    recent_log = "\n".join(
        f"{s['start']}-{s['end']}: {s['summary']}"
        for s in recent_summaries[-6:]
    )
    profile_text = json.dumps(profile, ensure_ascii=False, indent=2)[:2000]

    try:
        import google.genai as genai
        client = genai.Client(api_key=GEMINI_API_KEY)

        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=[_DEEP_ANALYSIS_PROMPT.format(
                    recent_log=recent_log, profile=profile_text,
                )],
                config=genai.types.GenerateContentConfig(
                    temperature=0.3,
                    max_output_tokens=500,
                ),
            ),
            timeout=20,
        )

        if response and response.text:
            text = response.text.strip()
            # JSON部分を抽出
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text)

    except (json.JSONDecodeError, Exception) as e:
        logger.debug(f"Deep analysis failed: {e}")

    return None


def _merge_insights(existing: dict, new_insights: dict) -> dict:
    """新しい洞察を既存のインサイトにマージ"""
    if not new_insights:
        return existing

    # 上書き系
    for key in ("work_style", "communication_style", "productivity_pattern"):
        if new_insights.get(key):
            existing[key] = new_insights[key]

    # 追記系（重複排除）
    for key in ("interests", "active_projects", "suggestions"):
        old = set(existing.get(key, []))
        new = new_insights.get(key, [])
        merged = list(old | set(new))
        # 最新20件に制限
        existing[key] = merged[-20:]

    # ツール習熟度（更新）
    old_tools = existing.get("tools_mastery", {})
    new_tools = new_insights.get("tools_mastery", {})
    old_tools.update(new_tools)
    existing["tools_mastery"] = old_tools

    existing["last_analysis"] = datetime.now().isoformat()
    return existing


def _load_insights() -> dict:
    if ACTIVITY_INSIGHTS_FILE.exists():
        try:
            return json.loads(ACTIVITY_INSIGHTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_insights(insights: dict):
    ACTIVITY_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVITY_INSIGHTS_FILE.write_text(
        json.dumps(insights, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ============================================================
# 行動プロファイル（長期学習）
# ============================================================

def _update_profile(entries: list[dict]):
    """蓄積データから行動プロファイルを更新"""
    profile = _load_profile()

    # アプリ使用頻度
    app_counts = profile.get("app_usage", {})
    for e in entries:
        app = e["app"]
        app_counts[app] = app_counts.get(app, 0) + 1

    # 時間帯×アプリ
    hourly = profile.get("hourly_apps", {})
    for e in entries:
        hour = e["t"][:2]
        if hour not in hourly:
            hourly[hour] = {}
        app = e["app"]
        hourly[hour][app] = hourly[hour].get(app, 0) + 1

    # よく見るサイト
    sites = profile.get("frequent_sites", {})
    for e in entries:
        url = e.get("url", "")
        if url:
            try:
                domain = urlparse(url).netloc
                if domain:
                    sites[domain] = sites.get(domain, 0) + 1
            except Exception:
                pass

    # URLカテゴリ統計
    categories = profile.get("url_categories", {})
    for e in entries:
        cat = e.get("cat", "")
        if cat:
            categories[cat] = categories.get(cat, 0) + 1

    # アプリ遷移パターン
    transitions = profile.get("app_transitions", {})
    prev_app = None
    for e in entries:
        app = e["app"]
        if prev_app and prev_app != app:
            key = f"{prev_app} → {app}"
            transitions[key] = transitions.get(key, 0) + 1
        prev_app = app

    # プロジェクト作業時間
    projects = profile.get("projects", {})
    for e in entries:
        proj = e.get("proj", "")
        if proj:
            projects[proj] = projects.get(proj, 0) + 1

    # コミュニケーションアプリ使用統計
    comms = profile.get("communication_apps", {})
    for e in entries:
        if e["app"] in _COMMS_APPS:
            comms[e["app"]] = comms.get(e["app"], 0) + 1

    profile["app_usage"] = app_counts
    profile["hourly_apps"] = hourly
    profile["frequent_sites"] = sites
    profile["url_categories"] = categories
    profile["app_transitions"] = transitions
    profile["projects"] = projects
    profile["communication_apps"] = comms
    profile["last_updated"] = datetime.now().isoformat()
    profile["total_observations"] = profile.get("total_observations", 0) + len(entries)

    _save_profile(profile)


def _load_profile() -> dict:
    if ACTIVITY_PROFILE_FILE.exists():
        try:
            return json.loads(ACTIVITY_PROFILE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_profile(profile: dict):
    ACTIVITY_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVITY_PROFILE_FILE.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ============================================================
# 日次まとめ
# ============================================================

async def _flush_daily():
    """日次まとめを生成して保存"""
    summaries = _load_summaries()
    if not summaries:
        return

    today = datetime.now().strftime("%Y-%m-%d")
    ACTIVITY_DAILY_DIR.mkdir(parents=True, exist_ok=True)
    daily_path = ACTIVITY_DAILY_DIR / f"{today}.md"

    lines = [f"# アクティビティログ {today}\n"]
    for s in summaries:
        lines.append(f"## {s['start']} - {s['end']}")
        lines.append(s["summary"])
        lines.append("")

    # インサイトも追記
    insights = _load_insights()
    if insights:
        lines.append("## AI分析による洞察")
        for key in ("work_style", "productivity_pattern", "communication_style"):
            if insights.get(key):
                lines.append(f"- {key}: {insights[key]}")
        if insights.get("interests"):
            lines.append(f"- 関心トピック: {', '.join(insights['interests'][:5])}")
        if insights.get("active_projects"):
            lines.append(f"- アクティブプロジェクト: {', '.join(insights['active_projects'][:5])}")
        lines.append("")

    daily_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Daily activity log saved: {daily_path}")

    if ACTIVITY_SUMMARY_FILE.exists():
        ACTIVITY_SUMMARY_FILE.write_text("", encoding="utf-8")


# ============================================================
# メインループ
# ============================================================

_tracker_running = False
_tracker_task: asyncio.Task | None = None


async def start_activity_tracker() -> asyncio.Task | None:
    """アクティビティトラッカーを開始"""
    global _tracker_running, _tracker_task

    if _tracker_running:
        return _tracker_task

    _tracker_running = True

    async def _loop():
        global _tracker_running
        last_summary_time = time.monotonic()
        last_deep_analysis_time = time.monotonic()
        last_app = ""
        last_title = ""
        capture_count = 0

        logger.info(
            f"Activity tracker started "
            f"(Tier1: {CAPTURE_INTERVAL}s, Tier2: on app switch, "
            f"Tier3: {DEEP_ANALYSIS_INTERVAL}s)"
        )

        while _tracker_running:
            try:
                # === Tier 1: アプリ情報取得（コスト$0） ===
                info = await _get_front_app_info()
                if info is None:
                    await asyncio.sleep(CAPTURE_INTERVAL)
                    continue

                app = info["app"]
                title = info.get("title", "")
                url = info.get("url", "")
                url_category = info.get("url_category", "")
                project = info.get("project", "")

                # 変化があった時だけ記録
                app_changed = app != last_app
                title_changed = title != last_title

                if app_changed or title_changed:
                    timestamp = datetime.now().strftime("%H:%M:%S")

                    # === Tier 2: アプリ切替時にスクショ（低コスト） ===
                    screen_context = ""
                    if app_changed and last_app:
                        screen_context = await _capture_screen_context() or ""

                    _append_raw(
                        timestamp, app, title, url,
                        url_category, project, screen_context,
                    )
                    last_app = app
                    last_title = title
                    capture_count += 1

                # === 5分ごとに要約統合 ===
                elapsed = time.monotonic() - last_summary_time
                raw_entries = _load_raw()

                if (elapsed >= SUMMARY_INTERVAL and len(raw_entries) >= 3) or \
                   len(raw_entries) >= MAX_RAW_ENTRIES:
                    _update_profile(raw_entries)

                    summary = await _summarize_raw(raw_entries)
                    if summary:
                        _append_summary(
                            summary,
                            raw_entries[0]["t"],
                            raw_entries[-1]["t"],
                        )
                        _clear_raw()
                        logger.info(
                            f"Activity summary: {len(raw_entries)} entries → summary"
                        )
                    last_summary_time = time.monotonic()

                # === Tier 3: 30分ごとに自律学習 ===
                deep_elapsed = time.monotonic() - last_deep_analysis_time
                if deep_elapsed >= DEEP_ANALYSIS_INTERVAL:
                    summaries = _load_summaries()
                    profile = _load_profile()
                    if summaries:
                        new_insights = await _deep_analysis(summaries, profile)
                        if new_insights:
                            existing = _load_insights()
                            merged = _merge_insights(existing, new_insights)
                            _save_insights(merged)
                            logger.info(f"Deep analysis complete: {list(new_insights.keys())}")
                    last_deep_analysis_time = time.monotonic()

                # === 日次フラッシュ（23:30に実行） ===
                now = datetime.now()
                if now.hour == 23 and now.minute == 30:
                    await _flush_daily()

                await asyncio.sleep(CAPTURE_INTERVAL)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Activity tracker error: {e}")
                await asyncio.sleep(30)

        # 終了時に残りを保存
        raw_entries = _load_raw()
        if raw_entries:
            _update_profile(raw_entries)
            summary = await _summarize_raw(raw_entries)
            if summary:
                _append_summary(summary, raw_entries[0]["t"], raw_entries[-1]["t"])
                _clear_raw()
        await _flush_daily()
        _tracker_running = False
        logger.info(f"Activity tracker stopped (total: {capture_count} captures)")

    _tracker_task = asyncio.create_task(_loop())
    return _tracker_task


async def stop_activity_tracker():
    """アクティビティトラッカーを停止"""
    global _tracker_running, _tracker_task
    _tracker_running = False
    if _tracker_task:
        _tracker_task.cancel()
        try:
            await _tracker_task
        except asyncio.CancelledError:
            pass
        _tracker_task = None


# ============================================================
# 外部API（コンテキスト注入用）
# ============================================================

def get_recent_activity() -> str:
    """直近のアクティビティを取得（コンテキスト注入用）"""
    summaries = _load_summaries()
    raw = _load_raw()

    lines = []
    if summaries:
        lines.append("# 直近の作業フロー")
        for s in summaries[-3:]:
            lines.append(f"**{s['start']}-{s['end']}**")
            lines.append(s["summary"])

    if raw:
        lines.append("\n## 今やっていること")
        for entry in raw[-5:]:
            line = f"- {entry['t']} [{entry['app']}] {entry.get('title', '')}"
            if entry.get("screen"):
                line += f"\n  → {entry['screen']}"
            lines.append(line)

    return "\n".join(lines)


def get_user_profile_summary() -> str:
    """ユーザーの行動プロファイルを要約（コンテキスト注入用）"""
    profile = _load_profile()
    insights = _load_insights()

    if not profile and not insights:
        return ""

    lines = ["# ユーザーの行動パターン（自動学習）"]

    # よく使うアプリTOP5
    app_usage = profile.get("app_usage", {})
    if app_usage:
        top_apps = sorted(app_usage.items(), key=lambda x: x[1], reverse=True)[:5]
        apps_str = ", ".join(f"{app}({count}回)" for app, count in top_apps)
        lines.append(f"- よく使うアプリ: {apps_str}")

    # 今の時間帯に使いがちなアプリ
    hourly = profile.get("hourly_apps", {})
    current_hour = f"{datetime.now().hour:02d}"
    if current_hour in hourly:
        hour_apps = sorted(hourly[current_hour].items(), key=lambda x: x[1], reverse=True)[:3]
        hour_str = ", ".join(app for app, _ in hour_apps)
        lines.append(f"- この時間帯({current_hour}時台)によく使う: {hour_str}")

    # よく見るサイトTOP5
    sites = profile.get("frequent_sites", {})
    if sites:
        top_sites = sorted(sites.items(), key=lambda x: x[1], reverse=True)[:5]
        sites_str = ", ".join(f"{domain}({count}回)" for domain, count in top_sites)
        lines.append(f"- よく見るサイト: {sites_str}")

    # URLカテゴリ
    categories = profile.get("url_categories", {})
    if categories:
        top_cats = sorted(categories.items(), key=lambda x: x[1], reverse=True)[:3]
        cats_str = ", ".join(f"{cat}({count})" for cat, count in top_cats)
        lines.append(f"- Web利用傾向: {cats_str}")

    # アプリ遷移パターンTOP3
    transitions = profile.get("app_transitions", {})
    if transitions:
        top_trans = sorted(transitions.items(), key=lambda x: x[1], reverse=True)[:3]
        trans_str = ", ".join(f"{t}({c}回)" for t, c in top_trans)
        lines.append(f"- よくある作業遷移: {trans_str}")

    # プロジェクト
    projects = profile.get("projects", {})
    if projects:
        top_proj = sorted(projects.items(), key=lambda x: x[1], reverse=True)[:3]
        proj_str = ", ".join(name for name, _ in top_proj)
        lines.append(f"- アクティブプロジェクト: {proj_str}")

    # AI分析の洞察
    if insights:
        if insights.get("work_style"):
            lines.append(f"- 作業スタイル: {insights['work_style']}")
        if insights.get("productivity_pattern"):
            lines.append(f"- 生産性パターン: {insights['productivity_pattern']}")
        if insights.get("interests"):
            lines.append(f"- 関心トピック: {', '.join(insights['interests'][:5])}")

    total = profile.get("total_observations", 0)
    if total:
        lines.append(f"- 総観測回数: {total}")

    return "\n".join(lines)


def get_daily_log(date: str | None = None) -> str:
    """指定日のアクティビティログを取得"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    daily_path = ACTIVITY_DAILY_DIR / f"{date}.md"
    if daily_path.exists():
        return daily_path.read_text(encoding="utf-8")
    return ""
