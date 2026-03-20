"""プロアクティブ行動スケジューラー

識ちゃんが自発的に動く仕組み:
- 朝の挨拶 + 今日の予定通知
- リマインダー（ユーザーが設定）
- 定期チェック（将来のMCP連携用フック）

asyncioベースの軽量スケジューラー。APScheduler不要。
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import google.genai as genai

from config import GEMINI_API_KEY, GEMINI_MODEL, OWNER_LINE_USER_ID, DISCORD_OWNER_ID
from agent.context import load_soul, load_memory_index, load_recent_daily_summaries
from memory.manager import memory
import user_config

logger = logging.getLogger("shiki.scheduler")

# Geminiクライアント（朝ブリーフィング生成用）
_client = genai.Client(api_key=GEMINI_API_KEY)

# push_fn のレートリミット検知フラグ（429検知で全ループ停止）
_push_rate_limited = False
_push_consecutive_errors = 0


async def _safe_push(push_fn, user_id: str, text: str, context: str = "") -> bool:
    """push_fnの安全ラッパー。429検知で全プッシュを停止する。

    Returns: True=成功, False=失敗
    """
    global _push_rate_limited, _push_consecutive_errors

    if _push_rate_limited:
        return False  # レートリミット中は全スキップ

    try:
        await push_fn(user_id, text)
        _push_consecutive_errors = 0
        return True
    except Exception as e:
        _push_consecutive_errors += 1
        error_str = str(e).lower()
        if any(x in error_str for x in ("429", "rate", "quota", "monthly limit", "too many")):
            _push_rate_limited = True
            logger.error(f"Push rate limited ({context}) — 全プッシュ停止。再起動で解除。")
            return False
        # 連続5回失敗でバックオフ
        if _push_consecutive_errors >= 5:
            logger.error(f"Push failed {_push_consecutive_errors} times ({context}), backing off")
            await asyncio.sleep(min(600, 60 * _push_consecutive_errors))
        else:
            logger.warning(f"Push failed ({context}): {e}")
        return False

# リマインダー保存先
_REMINDERS_FILE = Path(__file__).parent.parent / ".ritsu" / "reminders.json"


def _get_owner_id() -> str:
    """オーナーIDを取得（LINE or Discord）"""
    if OWNER_LINE_USER_ID:
        return OWNER_LINE_USER_ID
    if DISCORD_OWNER_ID:
        return str(DISCORD_OWNER_ID)
    return ""

# 朝の挨拶時刻
MORNING_HOUR = 8
MORNING_MINUTE = 0


# === リマインダー管理 ===

def _load_reminders() -> list[dict]:
    if _REMINDERS_FILE.exists():
        try:
            return json.loads(_REMINDERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_reminders(reminders: list[dict]):
    try:
        _REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _REMINDERS_FILE.write_text(
            json.dumps(reminders, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error(f"Reminder save failed: {e}")


def add_reminder(text: str, remind_at: datetime, repeat: str | None = None) -> dict:
    """リマインダーを追加

    Args:
        text: リマインダーの内容
        remind_at: 通知時刻
        repeat: 繰り返し ("daily", "weekly", None)
    """
    reminders = _load_reminders()
    reminder = {
        "id": max((r["id"] for r in reminders), default=0) + 1,
        "text": text,
        "remind_at": remind_at.isoformat(),
        "repeat": repeat,
        "created_at": datetime.now().isoformat(),
        "done": False,
    }
    reminders.append(reminder)
    _save_reminders(reminders)
    logger.info(f"Reminder added: {text} at {remind_at}")
    return reminder


def get_pending_reminders() -> list[dict]:
    """未実行で時刻が来たリマインダーを取得"""
    reminders = _load_reminders()
    now = datetime.now()
    pending = []
    for r in reminders:
        if r.get("done"):
            continue
        remind_at = datetime.fromisoformat(r["remind_at"])
        if remind_at <= now:
            pending.append(r)
    return pending


def mark_reminder_done(reminder_id: int):
    """リマインダーを完了にする（repeatの場合は次回を設定）"""
    reminders = _load_reminders()
    for r in reminders:
        if r["id"] == reminder_id:
            if r.get("repeat") == "daily":
                next_time = datetime.fromisoformat(r["remind_at"]) + timedelta(days=1)
                r["remind_at"] = next_time.isoformat()
                logger.info(f"Reminder {reminder_id} rescheduled to {next_time}")
            elif r.get("repeat") == "weekly":
                next_time = datetime.fromisoformat(r["remind_at"]) + timedelta(weeks=1)
                r["remind_at"] = next_time.isoformat()
                logger.info(f"Reminder {reminder_id} rescheduled to {next_time}")
            else:
                r["done"] = True
                logger.info(f"Reminder {reminder_id} marked done")
            break
    _save_reminders(reminders)


def list_reminders() -> list[dict]:
    """全リマインダーを取得（完了含む）"""
    return _load_reminders()


def delete_reminder(reminder_id: int) -> bool:
    """リマインダーを削除"""
    reminders = _load_reminders()
    before = len(reminders)
    reminders = [r for r in reminders if r["id"] != reminder_id]
    if len(reminders) < before:
        _save_reminders(reminders)
        return True
    return False


# === 朝のブリーフィング ===

async def generate_morning_briefing() -> str:
    """朝のブリーフィングを生成（Geminiで自然な挨拶 + 予定 + 天気の提案）"""
    now = datetime.now()
    weekday_ja = ["月", "火", "水", "木", "金", "土", "日"][now.weekday()]

    # メモリからコンテキスト収集
    soul = load_soul()
    memory_index = load_memory_index()
    daily_summaries = load_recent_daily_summaries(days=2)
    schedule = memory.get_topic("schedule")

    # 今日のリマインダー
    reminders = _load_reminders()
    today_reminders = []
    for r in reminders:
        if r.get("done"):
            continue
        remind_at = datetime.fromisoformat(r["remind_at"])
        if remind_at.date() == now.date():
            today_reminders.append(f"- {remind_at.strftime('%H:%M')} {r['text']}")

    reminder_text = "\n".join(today_reminders) if today_reminders else "なし"

    # Notionタスク状況を取得
    notion_status = ""
    try:
        from tools.notion import list_projects, list_tasks
        proj_result = await list_projects()
        if proj_result.get("success"):
            active_projects = [
                p for p in proj_result["projects"]
                if p.get("ステータス") in ("進行中", "準備中")
            ]
            total_pending = 0
            total_in_progress = 0
            high_priority = []
            # 全プロジェクトのタスクを並列取得（N+1→1+1）
            task_results = await asyncio.gather(*(
                list_tasks(project_id=proj["id"])
                for proj in active_projects
            ))
            for proj, tasks_result in zip(active_projects, task_results):
                pname = proj.get("プロジェクト名", "?")
                if tasks_result.get("success"):
                    for t in tasks_result.get("tasks", []):
                        st = t.get("ステータス", "")
                        if st == "未着手":
                            total_pending += 1
                            if t.get("優先度") == "高":
                                high_priority.append(f"{pname}: {t.get('タスク名', '?')}")
                        elif st == "進行中":
                            total_in_progress += 1

            notion_lines = []
            if total_pending > 0 or total_in_progress > 0:
                notion_lines.append(f"未着手: {total_pending}件、進行中: {total_in_progress}件")
            if high_priority:
                notion_lines.append(f"高優先度: {', '.join(high_priority[:3])}")
            if active_projects:
                notion_lines.append(f"アクティブプロジェクト: {', '.join(p.get('プロジェクト名', '?') for p in active_projects[:5])}")
            notion_status = "\n".join(notion_lines) if notion_lines else "タスクなし"
    except Exception as e:
        logger.warning(f"Morning briefing Notion check failed: {e}")
        notion_status = "Notion接続エラー"

    # 昨日の完了タスク数
    yesterday_completed = ""
    try:
        from tools.notion_executor import _load_state
        state = _load_state()
        yesterday_completed = f"累計完了タスク: {state.get('total_completed', 0)}件"
    except Exception:
        pass

    owner = user_config.get_display_name()

    prompt = f"""あなたは識（しき）、{owner}専属のAI秘書。
今は{now.strftime('%Y年%m月%d日')}（{weekday_ja}曜日）の朝{now.strftime('%H:%M')}。

{owner}に朝のブリーフィングを送る。

# ルール
- 5-8行で簡潔に
- 挨拶 + Notionタスク状況 + 今日の予定/リマインダー + 一言
- タメ口、でもちゃんと秘書らしく
- 絵文字は控えめ（1-2個まで）
- 高優先度タスクがあれば強調して伝える

# Notionタスク状況
{notion_status}
{yesterday_completed}

# 今日のリマインダー
{reminder_text}

# {owner}の予定メモ
{schedule if schedule else 'まだ予定情報なし'}

# 最近の出来事
{daily_summaries[:500] if daily_summaries else 'まだ日次要約なし'}

# 性格
{soul[:300] if soul else ''}

朝のブリーフィングメッセージを書いて。Notionのタスク状況を必ず含めること。"""

    try:
        response = await _client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.8,
                max_output_tokens=300,
            ),
        )
        text = response.text or ""
        if text:
            logger.info("Morning briefing generated")
            return text.strip()
    except Exception as e:
        logger.error(f"Morning briefing generation failed: {e}")

    # フォールバック
    return f"おはよう、{owner}。{now.strftime('%m月%d日')}（{weekday_ja}）だよ。今日もがんばろ。"


# === スケジューラーループ ===

async def morning_briefing_loop(push_fn):
    """毎朝のブリーフィングループ

    Args:
        push_fn: async fn(user_id, text) — LINE push送信関数
    """
    while True:
        try:
            now = datetime.now()
            target = now.replace(
                hour=MORNING_HOUR, minute=MORNING_MINUTE, second=0, microsecond=0
            )
            if target <= now:
                target += timedelta(days=1)

            wait_seconds = (target - now).total_seconds()
            logger.info(f"Morning briefing scheduled at {target.strftime('%H:%M')} ({wait_seconds:.0f}s from now)")
            await asyncio.sleep(wait_seconds)

            # ブリーフィング生成・送信
            briefing = await generate_morning_briefing()
            owner_id = _get_owner_id()
            if owner_id:
                if await _safe_push(push_fn, owner_id, briefing, "morning_briefing"):
                    logger.info("Morning briefing sent!")
            else:
                logger.warning("Owner ID not set, skipping morning briefing")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Morning briefing error: {e}")
            await asyncio.sleep(60)


async def reminder_check_loop(push_fn):
    """リマインダーチェックループ（1分ごと）

    Args:
        push_fn: async fn(user_id, text) — LINE push送信関数
    """
    while True:
        try:
            await asyncio.sleep(60)  # 1分ごとにチェック

            pending = get_pending_reminders()
            owner_id = _get_owner_id()
            for r in pending:
                if owner_id:
                    msg = f"リマインダー: {r['text']}"
                    if await _safe_push(push_fn, owner_id, msg, "reminder"):
                        logger.info(f"Reminder sent: {r['text']}")
                        mark_reminder_done(r["id"])
                    # push失敗時はmark_reminder_doneしない（次回リトライ）
                else:
                    mark_reminder_done(r["id"])

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Reminder check error: {e}")
            await asyncio.sleep(60)


# === Topic Patrol（ニュース巡回） ===

# 巡回間隔（秒）
TOPIC_PATROL_INTERVAL = 3 * 60 * 60  # 3時間ごと
# 巡回時間帯（この時間外は送らない）
PATROL_START_HOUR = 9
PATROL_END_HOUR = 22

# オーナーの興味分野（SOUL.md/topicsから自動拡張予定）
DEFAULT_INTERESTS = [
    "AI agent", "LLM", "Claude", "Gemini",
    "個人開発", "副業 エンジニア",
    "プログラミング 最新",
]


async def _fetch_news_via_browser(query: str) -> str:
    """Google検索 → 上位記事を読み → Geminiで要約

    Phase 1: Playwrightで実際にWeb検索
    フォールバック: Geminiで直接生成（ブラウザ使えない場合）
    """
    # まずPlaywrightでの実Web検索を試みる
    try:
        from tools.browser import search_web, get_page_text

        search_result = await search_web(f"{query} 最新ニュース")
        if search_result.get("error") or not search_result.get("results"):
            raise ValueError("Search returned no results")

        # 上位3件の記事テキストを取得
        articles = []
        for r in search_result["results"][:3]:
            try:
                page = await get_page_text(r["url"])
                if page.get("text") and len(page["text"]) > 50:
                    articles.append({
                        "title": r["title"],
                        "url": r["url"],
                        "text": page["text"][:2000],
                    })
            except Exception:
                # 個別記事の取得失敗は無視
                continue

        if not articles:
            raise ValueError("No articles fetched")

        # 記事をGeminiで要約
        articles_text = "\n\n".join([
            f"## {a['title']}\nURL: {a['url']}\n{a['text'][:1000]}"
            for a in articles
        ])

        prompt = f"""以下はWeb検索で見つけた「{query}」に関する最新記事。
この中から最も面白いトピックを1つ選んで、オーナーに教えるメッセージを書いて。

# ルール
- 2-4行で簡潔に
- 具体的な事実や数字を含める
- 「〜らしいよ」「〜だって」のカジュアルな語尾
- ソースURLも1つ付ける
- オーナー（AIエンジニア・個人開発者）が興味を持ちそうな角度で

# 記事
{articles_text}"""

        response = await _client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.7,
                max_output_tokens=300,
            ),
        )
        text = (response.text or "").strip()
        if text:
            logger.info(f"Topic patrol: real web search + summary for '{query}'")
            return text

    except Exception as e:
        logger.info(f"Web search fallback (Playwright unavailable): {e}")

    # フォールバック: Geminiで直接生成
    prompt = f"""あなたはニュースキュレーター。
「{query}」に関する最新の面白いトピックを1つ紹介して。

# ルール
- 2-3行で簡潔に
- 具体的な事実や数字を含める
- 「〜らしいよ」「〜だって」のカジュアルな語尾
- オーナー（AIエンジニア・個人開発者）が興味を持ちそうな角度で

例: 「OpenAIがGPT-5のベンチマーク公開したんだけど、コーディング能力がGPT-4の2倍になってるらしいよ。特にエージェント系タスクの精度がヤバいって。」"""

    try:
        response = await _client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.9,
                max_output_tokens=200,
            ),
        )
        return (response.text or "").strip()
    except Exception as e:
        logger.error(f"News generation failed: {e}")
        return ""


async def topic_patrol_loop(push_fn):
    """定期的にニュースを巡回してLINE push

    Args:
        push_fn: async fn(user_id, text)
    """
    # 起動直後は少し待つ
    await asyncio.sleep(60)

    while True:
        try:
            now = datetime.now()

            # 時間帯チェック（夜中は送らない）
            if now.hour < PATROL_START_HOUR or now.hour >= PATROL_END_HOUR:
                # 次の開始時刻まで待つ
                if now.hour >= PATROL_END_HOUR:
                    next_start = now.replace(
                        hour=PATROL_START_HOUR, minute=0, second=0
                    ) + timedelta(days=1)
                else:
                    next_start = now.replace(
                        hour=PATROL_START_HOUR, minute=0, second=0
                    )
                wait = (next_start - now).total_seconds()
                logger.info(f"Topic patrol sleeping until {PATROL_START_HOUR}:00 ({wait:.0f}s)")
                await asyncio.sleep(wait)
                continue

            # 興味分野からランダムに1つ選んでニュース取得
            import random
            # メモリからカスタム興味も読み込み
            interests = list(DEFAULT_INTERESTS)
            prefs = memory.get_topic("preferences")
            if prefs:
                # preferences.mdから追加キーワードを抽出
                for line in prefs.split("\n"):
                    line = line.strip().lstrip("- ")
                    if line and len(line) > 2:
                        interests.append(line)

            query = random.choice(interests)
            logger.info(f"Topic patrol: searching for '{query}'")

            news = await _fetch_news_via_browser(query)
            owner_id = _get_owner_id()
            if news and owner_id:
                msg = f"ねえ、面白い情報見つけた。\n\n{news}"
                if await _safe_push(push_fn, owner_id, msg, "topic_patrol"):
                    logger.info(f"Topic patrol sent: {query}")

            await asyncio.sleep(TOPIC_PATROL_INTERVAL)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Topic patrol error: {e}")
            await asyncio.sleep(300)  # エラー時5分待ち


# === 休憩リマインダー（PC使用時間監視） ===

# 連続PC使用の上限（秒）
BREAK_THRESHOLD = 90 * 60  # 90分
BREAK_CHECK_INTERVAL = 10 * 60  # 10分ごとにチェック


async def _get_idle_seconds() -> float:
    """macOSのアイドル時間を取得（HIDIdleTime — キーボード/マウス無操作時間）"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "/usr/sbin/ioreg", "-c", "IOHIDSystem",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        output = stdout.decode()
        # "HIDIdleTime" = 1234567890 (ナノ秒)
        for line in output.split("\n"):
            if "HIDIdleTime" in line:
                # 数値を抽出
                parts = line.split("=")
                if len(parts) >= 2:
                    ns = int(parts[-1].strip())
                    return ns / 1_000_000_000  # ナノ秒→秒
    except Exception as e:
        logger.debug(f"Idle time check failed: {e}")
    return 0.0


async def break_reminder_loop(push_fn):
    """PC使用時間を監視して休憩を促す

    macOSのHIDIdleTimeでキーボード/マウスの無操作時間を取得。
    90分以上連続使用（アイドル5分未満）していたらLINEで声かけ。
    """
    _continuous_use_start = asyncio.get_event_loop().time()

    while True:
        try:
            await asyncio.sleep(BREAK_CHECK_INTERVAL)

            now_loop = asyncio.get_event_loop().time()
            idle_secs = await _get_idle_seconds()

            # 5分以上アイドル → 休憩したとみなしてリセット
            if idle_secs > 300:
                _continuous_use_start = now_loop
                continue

            # 連続使用時間を計算
            continuous_minutes = (now_loop - _continuous_use_start) / 60

            if continuous_minutes >= (BREAK_THRESHOLD / 60):
                # 時間帯チェック（深夜は送らない）
                hour = datetime.now().hour
                if PATROL_START_HOUR <= hour < PATROL_END_HOUR:
                    owner_id = _get_owner_id()
                    if owner_id:
                        mins = int(continuous_minutes)
                        owner_name = user_config.get_display_name()
                        messages = [
                            f"{owner_name}、もう{mins}分くらい連続でPC使ってるよ。ちょっと休憩しない？目と体は大事にして。",
                            f"{mins}分経ったよ〜。少し立ち上がってストレッチでもどう？",
                            f"ねぇ、{mins}分ぶっ通しだよ？水飲んで、ちょっと遠くを見て。",
                        ]
                        import random
                        msg = random.choice(messages)
                        if await _safe_push(push_fn, owner_id, msg, "break_reminder"):
                            logger.info(f"Break reminder sent ({mins} min continuous use)")

                # リセット（送った後は再カウント）
                _continuous_use_start = now_loop

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Break reminder error: {e}")
            await asyncio.sleep(60)


# === Cronジョブ（ユーザー定義の自律タスク）===
# OpenClaw inspired: 識ちゃんが24時間自律でタスク実行

_CRON_JOBS_FILE = Path(__file__).parent.parent / ".ritsu" / "cron_jobs.json"
_cron_tasks: list[asyncio.Task] = []

# Google Calendar同期用
_SHIKI_CALENDAR_ID = None

def _get_calendar_id() -> str:
    """識ちゃんカレンダーIDを取得"""
    global _SHIKI_CALENDAR_ID
    if _SHIKI_CALENDAR_ID is None:
        from config import GOOGLE_CALENDAR_ID
        _SHIKI_CALENDAR_ID = GOOGLE_CALENDAR_ID
    return _SHIKI_CALENDAR_ID


def _interval_to_readable(minutes: int) -> str:
    """間隔を人間が読める文字列に"""
    if minutes >= 1440:
        return f"{minutes // 1440}日ごと"
    if minutes >= 60:
        return f"{minutes // 60}時間ごと"
    return f"{minutes}分ごと"


async def _sync_job_to_calendar(job: dict) -> str | None:
    """Cronジョブをgoogleカレンダーに同期（繰り返し予定として追加）

    Returns: カレンダーイベントID or None
    """
    try:
        from mcp_ext.client import call_tool

        cal_id = _get_calendar_id()
        if not cal_id or cal_id == "primary":
            return None

        # 実行時間帯の開始時刻を予定の時刻にする
        active_hours = job.get("active_hours", [9, 22])
        start_hour = active_hours[0]

        # 明日の実行開始時刻
        tomorrow = (datetime.now() + timedelta(days=1)).date()
        start_time = datetime(tomorrow.year, tomorrow.month, tomorrow.day, start_hour, 0)
        end_time = start_time + timedelta(minutes=30)  # 30分枠で表示

        # RRULE（繰り返しルール）を計算
        interval_min = job["interval_minutes"]
        if interval_min >= 1440:
            # 日単位
            days = interval_min // 1440
            rrule = f"RRULE:FREQ=DAILY;INTERVAL={days}"
        elif interval_min >= 60:
            # 時間単位
            hours = interval_min // 60
            rrule = f"RRULE:FREQ=HOURLY;INTERVAL={hours}"
        else:
            # 分単位（カレンダーには日次で表示）
            rrule = "RRULE:FREQ=DAILY;INTERVAL=1"

        summary = f"識ちゃん: {job['name']}"
        description = (
            f"識ちゃん自律タスク（Cron ID: {job['id']}）\n"
            f"間隔: {_interval_to_readable(interval_min)}\n"
            f"時間帯: {active_hours[0]}時〜{active_hours[1]}時\n"
            f"---\n"
            f"{job['task_prompt'][:300]}"
        )

        result = await call_tool("mcp_google_calendar_create-event", {
            "calendarId": cal_id,
            "summary": summary,
            "description": description,
            "start": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "end": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "recurrence": [rrule],
            "timeZone": "Asia/Tokyo",
        })

        if result.get("success"):
            # レスポンスからイベントIDを抽出（JSON内に含まれる場合）
            output = result.get("output", "")
            import re
            id_match = re.search(r'"id"\s*:\s*"([^"]+)"', output)
            event_id = id_match.group(1) if id_match else None
            logger.info(f"Calendar event created for cron job '{job['name']}': {event_id}")
            return event_id
        else:
            logger.warning(f"Calendar sync failed for '{job['name']}': {result}")
            return None
    except Exception as e:
        logger.warning(f"Calendar sync error for '{job['name']}': {e}")
        return None


async def _delete_calendar_event(event_id: str) -> bool:
    """カレンダーからイベントを削除"""
    try:
        from mcp_ext.client import call_tool
        cal_id = _get_calendar_id()
        if not cal_id or cal_id == "primary" or not event_id:
            return False

        result = await call_tool("mcp_google_calendar_delete-event", {
            "calendarId": cal_id,
            "eventId": event_id,
        })
        return result.get("success", False)
    except Exception as e:
        logger.warning(f"Calendar event delete failed: {e}")
        return False


def _load_cron_jobs() -> list[dict]:
    if _CRON_JOBS_FILE.exists():
        try:
            return json.loads(_CRON_JOBS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_cron_jobs(jobs: list[dict]):
    try:
        _CRON_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CRON_JOBS_FILE.write_text(
            json.dumps(jobs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error(f"Cron jobs save failed: {e}")


async def schedule_task(
    name: str,
    task_prompt: str,
    interval_minutes: int,
    active_hours: tuple[int, int] = (9, 22),
    notify: bool = True,
) -> dict:
    """Cronジョブを登録（Googleカレンダーにも自動同期）

    Args:
        name: ジョブ名（例: "ランサーズ巡回"）
        task_prompt: 識ちゃんに実行させるプロンプト（例: "ランサーズでGAS案件を検索して良さそうなのをNotionに追加"）
        interval_minutes: 実行間隔（分）。最小15分。
        active_hours: 実行時間帯 (start_hour, end_hour)。夜中は実行しない。
        notify: 結果をDiscord/LINEに通知するか
    """
    interval_minutes = max(15, min(1440, interval_minutes))  # 15分〜24時間

    jobs = _load_cron_jobs()
    job = {
        "id": max((j["id"] for j in jobs), default=0) + 1,
        "name": name,
        "task_prompt": task_prompt[:2000],
        "interval_minutes": interval_minutes,
        "active_hours": list(active_hours),
        "notify": notify,
        "enabled": True,
        "created_at": datetime.now().isoformat(),
        "last_run": None,
        "run_count": 0,
        "last_result": None,
        "calendar_event_id": None,
    }

    # Googleカレンダーに繰り返し予定を追加
    event_id = await _sync_job_to_calendar(job)
    if event_id:
        job["calendar_event_id"] = event_id

    jobs.append(job)
    _save_cron_jobs(jobs)
    logger.info(f"Cron job added: {name} (every {interval_minutes}min, cal_event={event_id})")
    return job


def list_cron_jobs() -> list[dict]:
    """登録済みCronジョブ一覧"""
    return _load_cron_jobs()


async def delete_cron_job(job_id: int) -> bool:
    """Cronジョブを削除（Googleカレンダーからも削除）"""
    jobs = _load_cron_jobs()
    target = None
    for j in jobs:
        if j["id"] == job_id:
            target = j
            break

    if not target:
        return False

    # カレンダーイベントも削除
    event_id = target.get("calendar_event_id")
    if event_id:
        await _delete_calendar_event(event_id)

    jobs = [j for j in jobs if j["id"] != job_id]
    _save_cron_jobs(jobs)
    logger.info(f"Cron job deleted: {job_id}")
    return True


def toggle_cron_job(job_id: int) -> dict | None:
    """Cronジョブの有効/無効を切り替え"""
    jobs = _load_cron_jobs()
    for j in jobs:
        if j["id"] == job_id:
            j["enabled"] = not j["enabled"]
            _save_cron_jobs(jobs)
            status = "有効" if j["enabled"] else "無効"
            logger.info(f"Cron job {job_id} toggled: {status}")
            return j
    return None


async def _execute_cron_job(job: dict, push_fn) -> str:
    """Cronジョブを実行（agentループに渡す）"""
    from agent.loop import process_message

    logger.info(f"Cron job executing: {job['name']} (id={job['id']})")
    try:
        result = await asyncio.wait_for(
            process_message(job["task_prompt"]),
            timeout=300,  # 5分タイムアウト
        )
        result_text = result.get("text", "完了")

        # 結果を保存
        jobs = _load_cron_jobs()
        for j in jobs:
            if j["id"] == job["id"]:
                j["last_run"] = datetime.now().isoformat()
                j["run_count"] = j.get("run_count", 0) + 1
                j["last_result"] = result_text[:500]
                break
        _save_cron_jobs(jobs)

        # 通知
        if job.get("notify", True):
            owner_id = _get_owner_id()
            if owner_id:
                msg = f"[自律タスク完了] {job['name']}\n{result_text[:1000]}"
                await _safe_push(push_fn, owner_id, msg, f"cron:{job['name']}")

        logger.info(f"Cron job completed: {job['name']}")
        return result_text

    except asyncio.TimeoutError:
        logger.error(f"Cron job timed out: {job['name']}")
        return "タイムアウト"
    except Exception as e:
        logger.error(f"Cron job failed: {job['name']} - {e}")
        return f"エラー: {e}"


async def cron_job_loop(push_fn):
    """Cronジョブ実行ループ（1分ごとにチェック）"""
    await asyncio.sleep(30)  # 起動直後は待つ

    # 起動時に未同期のCronジョブをカレンダーに同期
    try:
        jobs = _load_cron_jobs()
        synced = 0
        for job in jobs:
            if job.get("enabled", True) and not job.get("calendar_event_id"):
                event_id = await _sync_job_to_calendar(job)
                if event_id:
                    job["calendar_event_id"] = event_id
                    synced += 1
        if synced > 0:
            _save_cron_jobs(jobs)
            logger.info(f"Synced {synced} cron jobs to Google Calendar")
    except Exception as e:
        logger.warning(f"Calendar sync on startup failed: {e}")

    while True:
        try:
            await asyncio.sleep(60)  # 1分ごとにチェック

            jobs = _load_cron_jobs()
            now = datetime.now()

            for job in jobs:
                if not job.get("enabled", True):
                    continue

                # 時間帯チェック
                active_hours = job.get("active_hours", [9, 22])
                if not (active_hours[0] <= now.hour < active_hours[1]):
                    continue

                # 実行間隔チェック
                last_run = job.get("last_run")
                if last_run:
                    last_run_dt = datetime.fromisoformat(last_run)
                    elapsed = (now - last_run_dt).total_seconds() / 60
                    if elapsed < job["interval_minutes"]:
                        continue

                # 実行
                await _execute_cron_job(job, push_fn)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Cron loop error: {e}")
            await asyncio.sleep(60)


# === Notionタスク巡回 ===
# 識ちゃんがNotionのタスクを定期チェックして、未着手タスクを提案・実行

NOTION_PATROL_INTERVAL = 10 * 60  # 10分ごとにチェック（タスク自動実行用）
NOTION_NOTIFY_INTERVAL = 24 * 60 * 60  # Discord通知は1日1回
NOTION_PATROL_START_HOUR = 9
NOTION_PATROL_END_HOUR = 23

# 通知済みタスクIDの永続化ファイル
_NOTIFIED_TASKS_FILE = Path(__file__).parent.parent / ".ritsu" / "notified_task_ids.json"

def _load_notified_ids() -> set[str]:
    if _NOTIFIED_TASKS_FILE.exists():
        try:
            data = json.loads(_NOTIFIED_TASKS_FILE.read_text(encoding="utf-8"))
            return set(data[-200:])  # 最新200件のみ
        except Exception:
            pass
    return set()

def _save_notified_ids(ids: set[str]):
    try:
        _NOTIFIED_TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _NOTIFIED_TASKS_FILE.write_text(
            json.dumps(list(ids)[-200:], ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"Notified IDs save failed: {e}")

_notified_task_ids: set[str] = set()
_last_patrol_notify: datetime | None = None


async def _check_notion_tasks() -> dict:
    """Notionの全プロジェクトからタスクを取得して状況を整理"""
    from tools.notion import list_projects, list_tasks

    result = {"pending": [], "in_progress": [], "review": [], "projects": {}}

    proj_result = await list_projects()
    if not proj_result.get("success"):
        return result

    active_projects = [
        p for p in proj_result["projects"]
        if p.get("ステータス", "") not in ("完了", "保留")
    ]

    # 全プロジェクトのタスクを並列取得
    task_results = await asyncio.gather(*(
        list_tasks(project_id=proj["id"])
        for proj in active_projects
    ))

    for proj, tasks_result in zip(active_projects, task_results):
        pid = proj["id"]
        pname = proj.get("プロジェクト名", "?")

        if not tasks_result.get("success"):
            continue

        result["projects"][pid] = pname

        for task in tasks_result.get("tasks", []):
            task["_project_name"] = pname
            task["_project_id"] = pid
            status = task.get("ステータス", "")
            if status == "未着手":
                result["pending"].append(task)
            elif status == "進行中":
                result["in_progress"].append(task)
            elif status == "レビュー":
                result["review"].append(task)

    return result


async def notion_task_patrol_loop(push_fn):
    """Notionタスク巡回ループ

    - 新しい未着手タスクを検知→Discordで報告＆提案
    - 進行中タスクの状況確認
    - レビュー待ちタスクの通知
    """
    global _notified_task_ids, _last_patrol_notify
    _notified_task_ids = _load_notified_ids()  # 永続化ファイルから復元
    await asyncio.sleep(90)  # 起動後少し待つ

    while True:
        try:
            now = datetime.now()

            # 時間帯チェック
            if not (NOTION_PATROL_START_HOUR <= now.hour < NOTION_PATROL_END_HOUR):
                await asyncio.sleep(NOTION_PATROL_INTERVAL)
                continue

            logger.info("Notion task patrol: checking...")
            task_status = await _check_notion_tasks()

            pending = task_status["pending"]
            in_progress = task_status["in_progress"]
            review = task_status["review"]

            # メモリリーク防止: 現在のpending IDだけ残して古いIDを削除
            current_pending_ids = {t["id"] for t in pending}
            _notified_task_ids.intersection_update(current_pending_ids)

            # 新しい未着手タスクを検知
            new_pending = [
                t for t in pending
                if t["id"] not in _notified_task_ids
            ]

            # 高優先度タスクは自動実行、それ以外は通知
            high_priority_new = [t for t in new_pending if t.get("優先度") == "高"]
            normal_new = [t for t in new_pending if t.get("優先度") != "高"]

            # 高優先度タスクは通知してから自動実行
            if high_priority_new:
                owner_id = _get_owner_id()
                # まず全部まとめて通知
                if owner_id:
                    task_list = "\n".join(
                        f"  [{t.get('_project_name', '?')}] {t.get('タスク名', '?')}"
                        for t in high_priority_new
                    )
                    await _safe_push(
                        push_fn, owner_id,
                        f"高優先度タスク{len(high_priority_new)}件検知！やるね〜\n{task_list}",
                        "notion_patrol_auto",
                    )
                # 順番に実行
                for t in high_priority_new:
                    pname = t.get("_project_name", "?")
                    tname = t.get("タスク名", "?")
                    try:
                        from tools.notion_executor import execute_single_task
                        await execute_single_task(t, pname, push_fn)
                        _notified_task_ids.add(t["id"])
                    except Exception as exec_err:
                        logger.error(f"Auto-exec failed: {tname} — {exec_err}")
                        _notified_task_ids.add(t["id"])

            # 通常タスクは通知のみ（1日1回まで）
            if normal_new or review:
                now_ts = datetime.now()
                should_notify = (
                    _last_patrol_notify is None
                    or (now_ts - _last_patrol_notify).total_seconds() >= NOTION_NOTIFY_INTERVAL
                )
                owner_id = _get_owner_id()
                if owner_id and should_notify:
                    lines = []

                    if normal_new:
                        lines.append(f"新しいタスクが{len(normal_new)}件入ってるよ！")
                        for t in normal_new[:5]:
                            pname = t.get("_project_name", "?")
                            tname = t.get("タスク名", "?")
                            priority = t.get("優先度", "中")
                            lines.append(f"  [{pname}] {tname}（優先度: {priority}）")
                        if len(normal_new) > 5:
                            lines.append(f"  ...他{len(normal_new) - 5}件")
                        lines.append("")
                        lines.append("やっとく？ 「やって」って言ってくれたらガガガっと進めるよ。")

                    if review:
                        lines.append(f"\nレビュー待ちが{len(review)}件あるよ:")
                        for t in review[:3]:
                            pname = t.get("_project_name", "?")
                            tname = t.get("タスク名", "?")
                            lines.append(f"  [{pname}] {tname}")

                    msg = "\n".join(lines)
                    if await _safe_push(push_fn, owner_id, msg, "notion_patrol"):
                        _last_patrol_notify = now_ts
                        for t in normal_new:
                            _notified_task_ids.add(t["id"])
                        logger.info(
                            f"Notion patrol: {len(high_priority_new)} auto-executed, "
                            f"{len(normal_new)} notified, {len(review)} review"
                        )

            # 通知済みIDを永続化
            _save_notified_ids(_notified_task_ids)

            # 進行中タスクのログ
            if in_progress:
                logger.info(f"Notion patrol: {len(in_progress)} tasks in progress")

            await asyncio.sleep(NOTION_PATROL_INTERVAL)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Notion task patrol error: {e}")
            await asyncio.sleep(120)


# === カレンダー連動プロアクティブアシスタント ===
# 予定の前に通知、提案、声かけを自動で行う

CALENDAR_CHECK_INTERVAL = 5 * 60  # 5分ごとにチェック
CALENDAR_NOTIFY_BEFORE_MIN = 15   # 予定の15分前に通知
_calendar_notified_events: set[str] = set()  # 通知済みイベントID


async def _fetch_upcoming_events(minutes_ahead: int = 60) -> list[dict]:
    """今からN分以内のカレンダー予定を取得"""
    try:
        from mcp_ext.client import call_tool
        now = datetime.now()
        time_min = now.strftime("%Y-%m-%dT%H:%M:%S")
        time_max = (now + timedelta(minutes=minutes_ahead)).strftime("%Y-%m-%dT%H:%M:%S")

        result = await call_tool("mcp_google_calendar_list-events", {
            "timeMin": time_min,
            "timeMax": time_max,
            "timeZone": "Asia/Tokyo",
        })

        if not result.get("success"):
            return []

        # MCPレスポンスからイベントを抽出
        import re
        output = result.get("output", "")
        events = []

        # JSON形式のイベント情報を探す
        try:
            import json as _json
            # outputがJSON配列の場合
            if output.strip().startswith("["):
                parsed = _json.loads(output)
                if isinstance(parsed, list):
                    events = parsed
            elif output.strip().startswith("{"):
                parsed = _json.loads(output)
                items = parsed.get("items", [parsed])
                events = items
            else:
                # テキスト形式の場合、そのまま返す
                if output.strip():
                    events = [{"raw_text": output, "summary": "予定あり"}]
        except Exception:
            if output.strip():
                events = [{"raw_text": output, "summary": "予定あり"}]

        return events
    except Exception as e:
        logger.warning(f"Calendar fetch error: {e}")
        return []


async def _generate_calendar_notification(event: dict, minutes_until: int) -> str:
    """予定に合わせた通知メッセージをGeminiで生成"""
    summary = event.get("summary", "予定")
    description = event.get("description", "")
    location = event.get("location", "")
    start = event.get("start", {})
    start_time = start.get("dateTime", start.get("date", ""))

    import user_config
    owner = user_config.get_display_name()

    prompt = f"""{owner}の秘書として、予定のリマインドを短く送って。

予定: {summary}
時間: {start_time}
あと: {minutes_until}分後
場所: {location or 'なし'}
詳細: {description[:200] if description else 'なし'}

ルール:
- 3行以内で簡潔に
- 絵文字は使わない
- 予定の内容に合わせた一言を添える（準備が必要そうなら提案、移動なら「そろそろ出る時間」等）
- タメ口で"""

    try:
        response = await _client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.7,
                max_output_tokens=150,
            ),
        )
        return (response.text or "").strip()
    except Exception as e:
        logger.warning(f"Calendar notification generation failed: {e}")
        return f"あと{minutes_until}分で「{summary}」だよ。"


async def calendar_assistant_loop(push_fn):
    """カレンダー連動プロアクティブアシスタントループ

    5分ごとにカレンダーをチェック:
    - 15分前: 通知 + 準備提案
    - 予定中: 邪魔しない（通知抑制）
    """
    global _calendar_notified_events
    await asyncio.sleep(60)  # 起動後少し待つ

    while True:
        try:
            now = datetime.now()
            # 活動時間帯のみ (7:00-23:00)
            if not (7 <= now.hour < 23):
                await asyncio.sleep(CALENDAR_CHECK_INTERVAL)
                continue

            # 今から30分以内の予定を取得
            events = await _fetch_upcoming_events(minutes_ahead=30)

            for event in events:
                event_id = event.get("id", event.get("summary", ""))
                if not event_id or event_id in _calendar_notified_events:
                    continue

                # 開始時刻を計算
                start = event.get("start", {})
                start_str = start.get("dateTime", "") if isinstance(start, dict) else ""

                if not start_str:
                    # 終日イベントやraw_textはスキップ
                    continue

                try:
                    # ISO形式の時刻をパース
                    start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    # タイムゾーンを除去して比較
                    if start_dt.tzinfo:
                        start_dt = start_dt.replace(tzinfo=None)
                    minutes_until = (start_dt - now).total_seconds() / 60
                except Exception:
                    continue

                # 15分前〜5分前の範囲で通知
                if 5 <= minutes_until <= CALENDAR_NOTIFY_BEFORE_MIN:
                    msg = await _generate_calendar_notification(event, int(minutes_until))
                    owner_id = _get_owner_id()
                    if owner_id and msg:
                        if await _safe_push(push_fn, owner_id, msg, "calendar_assist"):
                            _calendar_notified_events.add(event_id)
                            logger.info(f"Calendar notification sent: {event.get('summary', '?')} ({int(minutes_until)}min)")

            # 古い通知IDをクリア（100件超えたら）
            if len(_calendar_notified_events) > 100:
                _calendar_notified_events = set(list(_calendar_notified_events)[-50:])

            await asyncio.sleep(CALENDAR_CHECK_INTERVAL)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Calendar assistant error: {e}")
            await asyncio.sleep(120)


async def self_evolution_loop(push_fn):
    """自己進化ループ: X + Web巡回で情報収集 → 分析 → Notion保存（4時間ごと）"""
    await asyncio.sleep(180)
    while True:
        try:
            now = datetime.now()
            if not (9 <= now.hour < 23):
                await asyncio.sleep(3600)
                continue
            from tools.self_evolution import run_evolution_cycle
            result = await run_evolution_cycle(push_fn)
            logger.info(f"Self-evolution: {result.get('findings', 0)} findings, {result.get('insights', 0)} insights")
            await asyncio.sleep(4 * 60 * 60)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Self-evolution error: {e}")
            await asyncio.sleep(600)


async def self_heal_loop(push_fn):
    """自己修復ループ: ログからエラー検出→修正→通知（1時間ごと）"""
    await asyncio.sleep(300)  # 起動後5分待つ
    while True:
        try:
            now = datetime.now()
            if not (9 <= now.hour < 23):
                await asyncio.sleep(3600)
                continue
            from tools.self_heal import self_heal_cycle
            result = await self_heal_cycle(push_fn)
            if result.get("patches_created", 0) > 0:
                logger.info(f"Self-heal: {result['patches_created']} patches created")
            await asyncio.sleep(60 * 60)  # 1時間ごと
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Self-heal error: {e}")
            await asyncio.sleep(600)


async def meta_learning_loop(push_fn):
    """メタ学習ループ: 日次指標 + クロスシステム学習 + Claude Code壁打ち（1日1回、23:00）"""
    await asyncio.sleep(300)
    while True:
        try:
            now = datetime.now()
            # 23:00に実行
            target = now.replace(hour=23, minute=0, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            wait = (target - now).total_seconds()
            logger.info(f"Meta-learning scheduled at 23:00 ({wait:.0f}s from now)")
            await asyncio.sleep(wait)

            from agent.meta_learner import meta_learning_cycle, self_improvement_session

            # メタ学習（指標記録 + クロスシステム学習）
            await meta_learning_cycle(push_fn)

            # Claude Code壁打ち（自己改善セッション）
            await self_improvement_session(push_fn)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Meta-learning error: {e}")
            await asyncio.sleep(3600)


async def start_all_schedulers(push_fn) -> list[asyncio.Task]:
    """全スケジューラーを起動し、タスクのリストを返す"""
    global _cron_tasks
    tasks = [
        asyncio.create_task(morning_briefing_loop(push_fn)),
        asyncio.create_task(reminder_check_loop(push_fn)),
        asyncio.create_task(topic_patrol_loop(push_fn)),
        asyncio.create_task(break_reminder_loop(push_fn)),
        asyncio.create_task(cron_job_loop(push_fn)),
        asyncio.create_task(notion_task_patrol_loop(push_fn)),
        asyncio.create_task(self_evolution_loop(push_fn)),
        asyncio.create_task(calendar_assistant_loop(push_fn)),
        asyncio.create_task(self_heal_loop(push_fn)),
        asyncio.create_task(meta_learning_loop(push_fn)),
    ]
    _cron_tasks = tasks
    logger.info(f"Started {len(tasks)} scheduler tasks")
    return tasks
