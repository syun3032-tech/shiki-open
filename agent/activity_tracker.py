"""アクティビティトラッカー — ユーザーの普段の行動を学習

5秒ごとにフロントアプリ+ウィンドウタイトルを取得（APIコスト$0）。
スクショもVision APIも使わない。osascriptだけで完結。

蓄積したログを5分ごとにAIで要約・統合し、
「オーナーが普段どんな作業をしているか」を永続的に学習する。

ストレージ: テキストのみ、1日数十KB。
APIコスト: 要約時のみFlash使用（5分に1回、入力~500トークン）。
"""

import asyncio
import json
import logging
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from config import GEMINI_API_KEY, RITSU_DIR

logger = logging.getLogger("shiki.activity_tracker")

# === 設定 ===
CAPTURE_INTERVAL = 5          # アプリ情報取得間隔（秒）
SUMMARY_INTERVAL = 300        # 要約統合間隔（秒 = 5分）
MAX_RAW_ENTRIES = 200         # 要約前にバッファする最大エントリ数

# === 保存先 ===
ACTIVITY_DIR = RITSU_DIR / "activity"
ACTIVITY_LOG_FILE = ACTIVITY_DIR / "current_raw.jsonl"
ACTIVITY_SUMMARY_FILE = ACTIVITY_DIR / "summaries.jsonl"
ACTIVITY_DAILY_DIR = ACTIVITY_DIR / "daily"
ACTIVITY_PROFILE_FILE = ACTIVITY_DIR / "profile.json"


# === アプリ情報取得（APIコスト$0） ===

async def _get_front_app_info() -> dict | None:
    """フロントアプリ名+ウィンドウタイトルを取得（osascript）"""
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
        if browser.get("title"):
            info["title"] = browser["title"]

        return info

    except Exception as e:
        logger.debug(f"App info failed: {e}")
        return None


# === ログ管理 ===

def _append_raw(timestamp: str, app: str, title: str, url: str = ""):
    """生ログをJSONLに追記"""
    ACTIVITY_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"t": timestamp, "app": app, "title": title}
    if url:
        entry["url"] = url
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


# === 要約（Gemini Flash、5分に1回だけ） ===

_SUMMARY_PROMPT = (
    "以下はユーザーのPC操作ログ（アプリ名+ウィンドウタイトル、5秒間隔で記録）です。\n"
    "これを作業フローとして要約してください。\n\n"
    "ルール:\n"
    "- 同じアプリで同じ作業をしている期間はまとめる\n"
    "  例: 「10:05-10:20 CursorでPythonファイルを編集」\n"
    "- アプリの切り替えや作業内容の変化を重点的に記録\n"
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
                    max_output_tokens=300,
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


# === 行動プロファイル（長期学習） ===

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
        # t は "HH:MM:SS" 形式
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
            # ドメインだけ取る
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc
                if domain:
                    sites[domain] = sites.get(domain, 0) + 1
            except Exception:
                pass

    profile["app_usage"] = app_counts
    profile["hourly_apps"] = hourly
    profile["frequent_sites"] = sites
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


# === 日次まとめ ===

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

    daily_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Daily activity log saved: {daily_path}")

    # 要約ファイルをクリア
    if ACTIVITY_SUMMARY_FILE.exists():
        ACTIVITY_SUMMARY_FILE.write_text("", encoding="utf-8")


# === メインループ ===

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
        last_app = ""
        last_title = ""
        capture_count = 0

        logger.info(
            f"Activity tracker started "
            f"(interval: {CAPTURE_INTERVAL}s, summary: {SUMMARY_INTERVAL}s)"
        )

        while _tracker_running:
            try:
                # 1. アプリ情報取得（APIコスト$0）
                info = await _get_front_app_info()
                if info is None:
                    await asyncio.sleep(CAPTURE_INTERVAL)
                    continue

                app = info["app"]
                title = info.get("title", "")
                url = info.get("url", "")

                # 2. 変化があった時だけ記録（同じ画面ならスキップ）
                if app != last_app or title != last_title:
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    _append_raw(timestamp, app, title, url)
                    last_app = app
                    last_title = title
                    capture_count += 1

                # 3. 一定間隔で要約統合
                elapsed = time.monotonic() - last_summary_time
                raw_entries = _load_raw()

                if (elapsed >= SUMMARY_INTERVAL and len(raw_entries) >= 3) or \
                   len(raw_entries) >= MAX_RAW_ENTRIES:
                    # プロファイル更新
                    _update_profile(raw_entries)

                    # AI要約
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

                # 4. 日次フラッシュ（23:30に実行）
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


# === 外部API ===

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
            lines.append(line)

    return "\n".join(lines)


def get_user_profile_summary() -> str:
    """ユーザーの行動プロファイルを要約（コンテキスト注入用）"""
    profile = _load_profile()
    if not profile:
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
