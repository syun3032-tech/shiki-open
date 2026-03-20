"""ツール定義・スキーマ・バリデーション

Gemini Function Calling用のツール定義、引数バリデーション、
座標スケーリングを一元管理。

ツールレベルの正（Single Source of Truth）は security/gate.py の TOOL_LEVELS。
TOOL_FUNCTIONS にはレベルを持たない。
"""

import logging

import google.genai as genai

from security.gate import ToolLevel, TOOL_LEVELS
from tools.screenshot import take_screenshot, crop_screenshot
from tools.code_executor import execute_code
from tools import desktop
from tools import mouse
from tools import filesystem
from tools import terminal
from tools import browser
from tools import claude_code
from tools import revenue_tracker
from tools import notion
from tools import notion_executor
from tools import standing_orders
from tools import self_evolution
from agent.multi_agent import dispatch_agents

logger = logging.getLogger("shiki.agent")

# === ツール実行関数マッピング（レベルはTOOL_LEVELSが正） ===
TOOL_FUNCTIONS = {
    "take_screenshot": lambda **kwargs: take_screenshot(),
    "crop_screenshot": lambda **kwargs: crop_screenshot(
        x=kwargs["x"], y=kwargs["y"],
        width=kwargs["width"], height=kwargs["height"],
    ),
    "open_app": lambda **kwargs: desktop.open_app(kwargs["app_name"]),
    "open_url": lambda **kwargs: desktop.open_url(
        kwargs["url"], kwargs.get("browser", "Google Chrome")
    ),
    "open_url_with_profile": lambda **kwargs: desktop.open_url_with_profile(
        kwargs["url"], kwargs["profile"]
    ),
    "get_frontmost_app": lambda **kwargs: desktop.get_frontmost_app(),
    "get_running_apps": lambda **kwargs: desktop.get_app_list(),
    "set_volume": lambda **kwargs: desktop.set_volume(kwargs["level"]),
    "toggle_dark_mode": lambda **kwargs: desktop.toggle_dark_mode(),
    "show_notification": lambda **kwargs: desktop.show_notification(
        kwargs["title"], kwargs["message"]
    ),
    "type_text": lambda **kwargs: desktop.type_text(kwargs["text"]),
    "press_key": lambda **kwargs: desktop.press_key(
        kwargs["key"], kwargs.get("modifiers")
    ),
    "scroll": lambda **kwargs: desktop.scroll(
        kwargs.get("direction", "down"), kwargs.get("amount", 15)
    ),
    "click": lambda **kwargs: mouse.click(int(kwargs["x"]), int(kwargs["y"])),
    "double_click": lambda **kwargs: mouse.double_click(int(kwargs["x"]), int(kwargs["y"])),
    "right_click": lambda **kwargs: mouse.right_click(int(kwargs["x"]), int(kwargs["y"])),
    "get_screen_size": lambda **kwargs: mouse.get_screen_size(),
    "read_file": lambda **kwargs: filesystem.read_file(kwargs["path"]),
    "write_file": lambda **kwargs: filesystem.write_file(kwargs["path"], kwargs["content"]),
    "list_directory": lambda **kwargs: filesystem.list_directory(kwargs["path"]),
    "move_file": lambda **kwargs: filesystem.move_file(kwargs["src"], kwargs["dst"]),
    "run_command": lambda **kwargs: terminal.run_command(kwargs["command"], kwargs.get("cwd")),
    "drag": lambda **kwargs: mouse.drag(
        int(kwargs["x1"]), int(kwargs["y1"]),
        int(kwargs["x2"]), int(kwargs["y2"]),
    ),
    "get_browser_info": lambda **kwargs: desktop.get_browser_info(),
    "get_window_info": lambda **kwargs: desktop.get_window_info(),
    "add_reminder": lambda **kwargs: _add_reminder_sync(**kwargs),
    "list_reminders": lambda **kwargs: _list_reminders_sync(),
    "delete_reminder": lambda **kwargs: _delete_reminder_sync(int(kwargs["reminder_id"])),
    "browse_url": lambda **kwargs: browser.browse_url(kwargs["url"]),
    "search_web": lambda **kwargs: browser.search_web(kwargs["query"]),
    "get_page_text": lambda **kwargs: browser.get_page_text(kwargs["url"]),
    "get_page_elements": lambda **kwargs: browser.get_page_elements(kwargs["url"]),
    "interact_page_element": lambda **kwargs: browser.interact_page_element(
        kwargs["url"], int(kwargs["element_index"]),
        kwargs["action"], kwargs.get("value", ""),
    ),
    "get_accessibility_tree": lambda **kwargs: browser.get_accessibility_tree(kwargs["url"]),
    "execute_code": lambda **kwargs: execute_code(kwargs["code"]),
    "update_plan": lambda **kwargs: _update_plan_sync(kwargs["plan"]),
    "delegate_to_claude": lambda **kwargs: claude_code.delegate_to_claude(
        task=kwargs["task"],
        context=kwargs.get("context"),
        cwd=kwargs.get("cwd"),
        timeout=int(kwargs.get("timeout", 300)),
        max_turns=int(kwargs.get("max_turns", 15)),
        allowed_tools=kwargs.get("allowed_tools"),
        session_id=kwargs.get("session_id"),
    ),
    # Cronジョブ（自律タスク実行）
    "schedule_task": lambda **kwargs: _schedule_task_sync(**kwargs),
    "list_scheduled_tasks": lambda **kwargs: _list_scheduled_tasks_sync(),
    "delete_scheduled_task": lambda **kwargs: _delete_scheduled_task_sync(int(kwargs["job_id"])),
    # マルチエージェント
    "dispatch_agents": lambda **kwargs: dispatch_agents(
        task=kwargs["task"],
        agents=kwargs.get("agents"),
        context=kwargs.get("context", ""),
    ),
    # 収益トラッカー
    "check_revenue": lambda **kwargs: revenue_tracker.check_revenue(
        kwargs.get("platform", "all"),
    ),
    "get_revenue_summary": lambda **kwargs: revenue_tracker.get_revenue_summary(
        kwargs.get("period", "month"),
    ),
    # Discord履歴
    "get_discord_history": lambda **kwargs: _get_discord_history_sync(
        int(kwargs.get("limit", 20)),
    ),
    # Notion連携
    "notion_list_projects": lambda **kwargs: notion.list_projects(
        status=kwargs.get("status"),
        category=kwargs.get("category"),
    ),
    "notion_get_project": lambda **kwargs: notion.get_project(kwargs["project_id"]),
    "notion_update_project": lambda **kwargs: notion.update_project(
        kwargs["project_id"], kwargs["updates"],
    ),
    "notion_create_project": lambda **kwargs: notion.create_project(
        name=kwargs["name"],
        category=kwargs.get("category", "プロダクト"),
        status=kwargs.get("status", "準備中"),
        memo=kwargs.get("memo", ""),
    ),
    "notion_list_tasks": lambda **kwargs: notion.list_tasks(
        project_id=kwargs.get("project_id"),
        status=kwargs.get("status"),
        priority=kwargs.get("priority"),
    ),
    "notion_create_task": lambda **kwargs: notion.create_task(
        name=kwargs["name"],
        project_id=kwargs.get("project_id"),
        status=kwargs.get("status", "未着手"),
        priority=kwargs.get("priority", "中"),
        memo=kwargs.get("memo", ""),
        deadline=kwargs.get("deadline"),
        estimated_hours=float(kwargs["estimated_hours"]) if kwargs.get("estimated_hours") else None,
    ),
    "notion_update_task": lambda **kwargs: notion.update_task(
        kwargs["task_id"], kwargs["updates"],
    ),
    "notion_batch_create_tasks": lambda **kwargs: notion.batch_create_tasks(
        kwargs["tasks_json"],
        project_id=kwargs.get("project_id"),
    ),
    "notion_search": lambda **kwargs: notion.search_notion(kwargs["query"]),
    "notion_get_page_content": lambda **kwargs: notion.get_page_content(kwargs["page_id"]),
    "notion_add_comment": lambda **kwargs: notion.add_comment(
        kwargs["page_id"], kwargs["text"],
    ),
    "notion_list_comments": lambda **kwargs: notion.list_comments(kwargs["page_id"]),
    "notion_update_block": lambda **kwargs: notion.update_block(
        kwargs["block_id"], kwargs["updates"],
    ),
    "notion_append_blocks": lambda **kwargs: notion.append_blocks(
        kwargs["page_id"], kwargs["blocks_json"],
    ),
    # Notionタスク自動実行
    "notion_execute_tasks": lambda **kwargs: notion_executor.execute_project_tasks(
        project_id=kwargs.get("project_id"),
        max_tasks=int(kwargs.get("max_tasks", 10)),
        push_fn=_get_push_fn(),
    ),
    "notion_execute_single_task": lambda **kwargs: _execute_single_task_sync(
        kwargs["task_id"], kwargs.get("project_name", ""),
    ),
    "notion_execution_status": lambda **kwargs: notion_executor.get_execution_status(),
    "notion_get_reflections": lambda **kwargs: notion_executor.get_recent_reflections(
        int(kwargs.get("days", 3)),
    ),
    # 常時指示（Standing Orders）
    "add_standing_order": lambda **kwargs: standing_orders.add_order(kwargs["text"]),
    "list_standing_orders": lambda **kwargs: standing_orders.list_orders(),
    "remove_standing_order": lambda **kwargs: standing_orders.remove_order(int(kwargs["order_id"])),
    # 自己進化
    "run_self_evolution": lambda **kwargs: self_evolution.run_evolution_cycle(
        push_fn=_get_push_fn(),
    ),
    # 動的ツール生成
    "generate_tool": lambda **kwargs: _generate_tool_sync(**kwargs),
    "list_dynamic_tools": lambda **kwargs: _list_dynamic_tools_sync(),
    "delete_dynamic_tool": lambda **kwargs: _delete_dynamic_tool_sync(kwargs["name"]),
}

# === 計画ツール（遅延import） ===

def _get_push_fn():
    """Discord push_fnを取得（遅延import）"""
    try:
        from discord_client.messaging import push_text
        return push_text
    except Exception:
        return None


async def _execute_single_task_sync(task_id: str, project_name: str = ""):
    """単一タスクを実行（タスクIDから情報を取得して実行）"""
    from tools.notion import get_page_content, list_comments
    import httpx
    from tools.notion import _HEADERS, _BASE_URL, _parse_page_properties

    # タスク情報を取得
    async with httpx.AsyncClient(headers=_HEADERS, timeout=30.0) as client:
        resp = await client.get(f"{_BASE_URL}/pages/{task_id}")
        if resp.status_code != 200:
            return {"success": False, "error": f"タスク取得失敗: {resp.status_code}"}
        page = resp.json()
        task = _parse_page_properties(page["properties"])
        task["id"] = page["id"]

    return await notion_executor.execute_single_task(
        task, project_name or "不明", push_fn=_get_push_fn(),
    )


async def _get_discord_history_sync(limit: int = 20):
    try:
        from discord_client.messaging import get_recent_messages
        messages = await get_recent_messages(limit)
        return {"success": True, "messages": messages, "count": len(messages)}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _update_plan_sync(plan: str):
    from agent.history import update_plan
    return await update_plan(plan)


# === リマインダーツール（遅延import） ===

async def _add_reminder_sync(text: str, datetime_str: str, repeat: str | None = None):
    from agent.scheduler import add_reminder
    from datetime import datetime
    try:
        remind_at = datetime.fromisoformat(datetime_str)
    except ValueError:
        return {"error": f"日時フォーマットエラー: {datetime_str}（ISO 8601形式: 2026-03-14T09:00:00）"}
    result = add_reminder(text, remind_at, repeat)
    return {"success": True, "reminder": result}


async def _list_reminders_sync():
    from agent.scheduler import list_reminders
    reminders = list_reminders()
    active = [r for r in reminders if not r.get("done")]
    return {"success": True, "reminders": active, "count": len(active)}


async def _delete_reminder_sync(reminder_id: int):
    from agent.scheduler import delete_reminder
    deleted = delete_reminder(reminder_id)
    if deleted:
        return {"success": True}
    return {"error": f"リマインダーID {reminder_id} が見つからない"}


# === Cronジョブツール（遅延import） ===

async def _schedule_task_sync(
    name: str, task_prompt: str, interval_minutes: int,
    active_start_hour: int = 9, active_end_hour: int = 22,
    notify: str = "true",
):
    from agent.scheduler import schedule_task
    job = await schedule_task(
        name=name,
        task_prompt=task_prompt,
        interval_minutes=int(interval_minutes),
        active_hours=(int(active_start_hour), int(active_end_hour)),
        notify=notify.lower() != "false" if isinstance(notify, str) else bool(notify),
    )
    return {"success": True, "job": job}


async def _list_scheduled_tasks_sync():
    from agent.scheduler import list_cron_jobs
    jobs = list_cron_jobs()
    active = [j for j in jobs if j.get("enabled", True)]
    return {"success": True, "jobs": jobs, "active_count": len(active), "total_count": len(jobs)}


async def _delete_scheduled_task_sync(job_id: int):
    from agent.scheduler import delete_cron_job
    deleted = await delete_cron_job(job_id)
    if deleted:
        return {"success": True}
    return {"error": f"ジョブID {job_id} が見つからない"}


# === 動的ツール生成（遅延import） ===

async def _generate_tool_sync(name: str, description: str, code: str):
    from agent.tool_generator import generate_tool
    return await generate_tool(name, description, code)


async def _list_dynamic_tools_sync():
    from agent.tool_generator import list_dynamic_tools
    tools = list_dynamic_tools()
    return {"success": True, "tools": tools, "count": len(tools)}


async def _delete_dynamic_tool_sync(name: str):
    from agent.tool_generator import delete_dynamic_tool
    deleted = delete_dynamic_tool(name)
    if deleted:
        return {"success": True, "deleted": name}
    return {"error": f"動的ツール '{name}' が見つからない"}


# === Gemini用ツールスキーマ ===
GEMINI_TOOLS = genai.types.Tool(function_declarations=[
    genai.types.FunctionDeclaration(
        name="take_screenshot",
        description=(
            "PC画面のスクリーンショットを撮影する。"
            "使う場面: 画面を確認したい時、クリック位置を確認する前、GUI操作の結果確認時。"
            "使わない場面: run_commandの結果確認時。"
        ),
        parameters=genai.types.Schema(type="OBJECT", properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="crop_screenshot",
        description=(
            "スクリーンショットの指定領域をクロップ・拡大して詳細確認する（Agentic Vision）。"
            "使う場面: 小さい文字やボタンが読めない時、特定エリアを詳しく見たい時。"
            "座標はtake_screenshotの画像上の座標（幅1024px基準）。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "x": genai.types.Schema(type="INTEGER", description="左上X座標（0-1024）"),
                "y": genai.types.Schema(type="INTEGER", description="左上Y座標"),
                "width": genai.types.Schema(type="INTEGER", description="クロップ幅（最小50）"),
                "height": genai.types.Schema(type="INTEGER", description="クロップ高さ（最小50）"),
            },
            required=["x", "y", "width", "height"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="open_app",
        description=(
            "Macアプリを起動する。"
            "使う場面: アプリを開く時。例: Cursor, Google Chrome, Finder, Terminal, Slack。"
            "使わない場面: コマンド実行時（run_commandを使う）。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "app_name": genai.types.Schema(
                    type="STRING",
                    description="アプリ名。例: 'Google Chrome', 'Cursor', 'Terminal', 'Finder', 'Slack'",
                ),
            },
            required=["app_name"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="open_url",
        description=(
            "URLをブラウザで開く。"
            "使う場面: Web検索（https://www.google.com/search?q=検索語）、特定サイトを開く時。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "url": genai.types.Schema(type="STRING", description="URL。例: 'https://www.google.com/search?q=天気'"),
            },
            required=["url"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="open_url_with_profile",
        description=(
            "指定したChromeアカウント（プロファイル）でURLを開く。"
            "ユーザーが「個人アカウントで開いて」「会社のChromeで」等と言った時に使う。"
            "profile: メールアドレスまたはuser_config.jsonで設定したエイリアス名"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "url": genai.types.Schema(type="STRING", description="開くURL"),
                "profile": genai.types.Schema(
                    type="STRING",
                    description="Chromeプロファイル。user_config.jsonで設定したエイリアス名、またはメールアドレス直指定",
                ),
            },
            required=["url", "profile"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_frontmost_app",
        description="今アクティブなアプリ名を取得。",
        parameters=genai.types.Schema(type="OBJECT", properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_running_apps",
        description="実行中アプリ一覧を取得。",
        parameters=genai.types.Schema(type="OBJECT", properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="set_volume",
        description="Mac音量を設定。0=ミュート、50=中間、100=最大。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "level": genai.types.Schema(type="INTEGER", description="音量0〜100"),
            },
            required=["level"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="toggle_dark_mode",
        description="ダークモード切替。",
        parameters=genai.types.Schema(type="OBJECT", properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="show_notification",
        description="macOS通知を表示。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "title": genai.types.Schema(type="STRING", description="通知タイトル"),
                "message": genai.types.Schema(type="STRING", description="通知本文"),
            },
            required=["title", "message"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="type_text",
        description=(
            "テキスト入力（クリップボード経由、IME影響なし）。"
            "使う場面: GUIアプリ内でテキスト入力が必要な時。"
            "使わない場面: コマンド実行（run_commandを使う）。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "text": genai.types.Schema(type="STRING", description="入力テキスト"),
            },
            required=["text"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="press_key",
        description=(
            "キーボードキーを押す。ショートカット対応。"
            "使わない場面: スクロール（scrollを使う）、テキスト入力（type_textを使う）。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "key": genai.types.Schema(type="STRING", description="キー。例: 'return', 'tab', 'escape', 'space', 'a'"),
                "modifiers": genai.types.Schema(
                    type="ARRAY", items=genai.types.Schema(type="STRING"),
                    description="修飾キー。例: ['command'], ['command', 'shift']",
                ),
            },
            required=["key"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="scroll",
        description="画面スクロール。Webページやアプリの内容をスクロールする。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "direction": genai.types.Schema(type="STRING", enum=["up", "down"], description="スクロール方向"),
                "amount": genai.types.Schema(type="INTEGER", description="量1-50。普通=15、大きく=30-50"),
            },
        ),
    ),
    genai.types.FunctionDeclaration(
        name="click",
        description=(
            "画面座標を左クリック。必ず先にtake_screenshotで座標を確認すること。"
            "重要: 座標はスクリーンショット画像上のピクセル座標で指定。"
            "左上が(0,0)。画像の右下端が最大値。自動的に実画面座標に変換される。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "x": genai.types.Schema(type="INTEGER", description="X座標（スクリーンショット画像上のピクセル位置）"),
                "y": genai.types.Schema(type="INTEGER", description="Y座標（スクリーンショット画像上のピクセル位置）"),
            },
            required=["x", "y"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="double_click",
        description=(
            "座標をダブルクリック。先にtake_screenshotで確認。"
            "座標はスクリーンショット画像上のピクセル座標。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "x": genai.types.Schema(type="INTEGER", description="X座標（スクリーンショット画像上のピクセル位置）"),
                "y": genai.types.Schema(type="INTEGER", description="Y座標（スクリーンショット画像上のピクセル位置）"),
            },
            required=["x", "y"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="right_click",
        description=(
            "座標を右クリック。コンテキストメニューを開く時。先にtake_screenshotで確認。"
            "座標はスクリーンショット画像上のピクセル座標。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "x": genai.types.Schema(type="INTEGER", description="X座標（スクリーンショット画像上のピクセル位置）"),
                "y": genai.types.Schema(type="INTEGER", description="Y座標（スクリーンショット画像上のピクセル位置）"),
            },
            required=["x", "y"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="drag",
        description=(
            "ドラッグ操作。(x1,y1)から(x2,y2)へマウスドラッグ。"
            "使う場面: ウィンドウリサイズ、テキスト選択、ファイルドラッグ&ドロップ。"
            "先にtake_screenshotで座標を確認すること。"
            "座標はスクリーンショット画像上のピクセル座標。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "x1": genai.types.Schema(type="INTEGER", description="始点X座標（スクリーンショット画像上）"),
                "y1": genai.types.Schema(type="INTEGER", description="始点Y座標（スクリーンショット画像上）"),
                "x2": genai.types.Schema(type="INTEGER", description="終点X座標（スクリーンショット画像上）"),
                "y2": genai.types.Schema(type="INTEGER", description="終点Y座標（スクリーンショット画像上）"),
            },
            required=["x1", "y1", "x2", "y2"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_screen_size",
        description="画面サイズ取得。クリック座標の参考に。",
        parameters=genai.types.Schema(type="OBJECT", properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="read_file",
        description="ファイル内容を読む（Desktop/Documents/Downloads内のみ）。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "path": genai.types.Schema(type="STRING", description="フルパス"),
            },
            required=["path"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="write_file",
        description="ファイルに書き込む（Desktop/Documents/Downloads内のみ）。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "path": genai.types.Schema(type="STRING", description="フルパス"),
                "content": genai.types.Schema(type="STRING", description="書き込む内容"),
            },
            required=["path", "content"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="list_directory",
        description="フォルダの中身一覧。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "path": genai.types.Schema(type="STRING", description="フォルダのフルパス"),
            },
            required=["path"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="move_file",
        description="ファイル移動/リネーム。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "src": genai.types.Schema(type="STRING", description="移動元フルパス"),
                "dst": genai.types.Schema(type="STRING", description="移動先フルパス"),
            },
            required=["src", "dst"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="run_command",
        description=(
            "ターミナルでコマンド実行。ls, grep, git, sort, diff等。"
            "使う場面: ファイル検索、git操作、テキスト処理。"
            "cwdで作業ディレクトリ指定可（Desktop/Documents/Downloads内）。"
            "禁止: sudo, rm, curl, python3, osascript。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "command": genai.types.Schema(type="STRING", description="コマンド。例: 'git status', 'ls -la'"),
                "cwd": genai.types.Schema(type="STRING", description="作業ディレクトリ。例: '~/Desktop/myproject'"),
            },
            required=["command"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_browser_info",
        description=(
            "ブラウザの現在のURL・タイトルを取得（Chrome/Safari/Arc対応）。"
            "使う場面: ユーザーが見ているページを知りたい時、ブラウザ操作の前後確認。"
        ),
        parameters=genai.types.Schema(type="OBJECT", properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_window_info",
        description=(
            "最前面ウィンドウの詳細情報を取得（アプリ名・ウィンドウタイトル・サイズ・位置）。"
            "使う場面: 今どのアプリのどの画面が開いているか確認したい時。"
        ),
        parameters=genai.types.Schema(type="OBJECT", properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="add_reminder",
        description=(
            "リマインダーを設定する。指定時刻にLINEで通知。"
            "使う場面: 「〇〇時に教えて」「明日の朝リマインドして」等。"
            "datetime_strはISO 8601形式（例: 2026-03-14T09:00:00）。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "text": genai.types.Schema(type="STRING", description="リマインダー内容"),
                "datetime_str": genai.types.Schema(
                    type="STRING",
                    description="通知日時（ISO 8601）。例: '2026-03-14T09:00:00'",
                ),
                "repeat": genai.types.Schema(
                    type="STRING", enum=["daily", "weekly"],
                    description="繰り返し。daily=毎日、weekly=毎週。省略で1回限り。",
                ),
            },
            required=["text", "datetime_str"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="list_reminders",
        description="設定済みのリマインダー一覧を取得。",
        parameters=genai.types.Schema(type="OBJECT", properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="delete_reminder",
        description="リマインダーを削除する。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "reminder_id": genai.types.Schema(type="INTEGER", description="リマインダーID"),
            },
            required=["reminder_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="browse_url",
        description=(
            "URLのページ内容をテキストで取得（Playwright headless）。"
            "使う場面: Webページの内容を読みたい時、記事の要約、情報収集。"
            "スクショ不要でトークン効率的。タイトル・本文・リンク一覧を返す。"
            "使わない場面: ユーザーにページを見せたい時（open_urlを使う）。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "url": genai.types.Schema(type="STRING", description="取得するURL"),
            },
            required=["url"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="search_web",
        description=(
            "Google検索してトップ10結果を返す（Playwright headless）。"
            "使う場面: Web検索して情報を集めたい時、最新ニュース確認。"
            "各結果のタイトル・URL・スニペットを返す。詳細はbrowse_urlで個別ページを読む。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "query": genai.types.Schema(type="STRING", description="検索クエリ。例: 'Claude 4 リリース日'"),
            },
            required=["query"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_page_text",
        description=(
            "URLからテキストのみ取得（軽量版browse_url）。"
            "使う場面: 記事本文だけ欲しい時。リンク一覧は不要な場合に。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "url": genai.types.Schema(type="STRING", description="取得するURL"),
            },
            required=["url"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_page_elements",
        description=(
            "URLを開いてインタラクティブ要素を番号付きリストで返す（Playwright headless）。"
            "ボタン・リンク・入力欄・タブ等に番号が振られる。"
            "使う場面: Webフォーム入力、ボタンクリック、リンク遷移等のWeb自動操作。"
            "この後interact_page_elementで要素番号を指定して操作する。"
            "使わない場面: 単にページを読みたい時（browse_url/get_page_textを使う）。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "url": genai.types.Schema(type="STRING", description="対象ページURL"),
            },
            required=["url"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="interact_page_element",
        description=(
            "get_page_elementsで取得した番号付き要素に操作を実行する。"
            "座標不要で正確にクリック・入力できる。"
            "使う前に必ずget_page_elementsでページを開いておくこと。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "url": genai.types.Schema(type="STRING", description="対象ページURL（get_page_elementsと同じ）"),
                "element_index": genai.types.Schema(type="INTEGER", description="操作する要素の番号（get_page_elementsの結果のindex）"),
                "action": genai.types.Schema(
                    type="STRING", enum=["click", "fill", "select"],
                    description="操作種別。click=クリック、fill=テキスト入力、select=選択肢選択",
                ),
                "value": genai.types.Schema(type="STRING", description="fill/selectの場合の入力値。clickの場合は不要。"),
            },
            required=["url", "element_index", "action"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_accessibility_tree",
        description=(
            "URLのAccessibility Tree（UI構造）をテキストで取得。超トークン効率的。"
            "スクショ15,000トークン → Accessibility Tree 200-400トークン。"
            "使う場面: ページの構造を素早く把握したい時、フォームの入力欄を確認したい時。"
            "使わない場面: ページの見た目や画像を確認したい時（take_screenshotを使う）。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "url": genai.types.Schema(type="STRING", description="対象ページURL"),
            },
            required=["url"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="execute_code",
        description=(
            "Pythonコードを実行する（CodeAct）。既存ツールでは対応できない処理に使う。"
            "使う場面: データ変換、数値計算、テキスト加工、JSON整形、日付計算、リスト操作等。"
            "使えるモジュール: math, json, re, datetime, collections, itertools, functools, statistics, decimal, fractions, random, string, textwrap, csv, base64, hashlib, hmac, struct, binascii, html, xml, urllib.parse, enum, dataclasses, copy, pprint, typing。"
            "使えないもの: os, subprocess, socket, open(), ファイルI/O, ネットワーク。"
            "ファイル操作はread_file/write_file、Web操作はbrowse_url/search_web等の既存ツールを使うこと。"
            "結果はprint()で出力する。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "code": genai.types.Schema(
                    type="STRING",
                    description="実行するPythonコード。print()で結果を出力する。",
                ),
            },
            required=["code"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="update_plan",
        description=(
            "タスクの計画を作成・更新する（Manus AI todo.md方式）。"
            "使う場面: 複雑なタスク（3ステップ以上）の開始時に計画を立てる。進捗に応じて更新する。"
            "マークダウンのチェックリスト形式推奨。例:\n"
            "# タスク: 〇〇\n"
            "- [x] ステップ1: 完了\n"
            "- [ ] ステップ2: 進行中\n"
            "- [ ] ステップ3: 未着手"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "plan": genai.types.Schema(
                    type="STRING",
                    description="マークダウン形式の計画テキスト",
                ),
            },
            required=["plan"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="schedule_task",
        description=(
            "自律タスク（Cronジョブ）を登録する。識ちゃんが自動で定期実行する。"
            "使う場面: 「毎朝ランサーズの案件チェックして」「1時間おきにメール確認して」等。"
            "登録すると、指定間隔で自動的にtask_promptを実行し、結果を通知する。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "name": genai.types.Schema(type="STRING", description="ジョブ名。例: 'ランサーズ案件巡回'"),
                "task_prompt": genai.types.Schema(
                    type="STRING",
                    description="自動実行するプロンプト。具体的に書く。例: 'ランサーズでGAS自動化の案件を検索して、報酬5万円以上のものをNotionに追加して'",
                ),
                "interval_minutes": genai.types.Schema(
                    type="INTEGER",
                    description="実行間隔（分）。最小15分。例: 60=1時間、180=3時間、1440=1日",
                ),
                "active_start_hour": genai.types.Schema(
                    type="INTEGER", description="開始時刻（時）。デフォルト9。",
                ),
                "active_end_hour": genai.types.Schema(
                    type="INTEGER", description="終了時刻（時）。デフォルト22。夜中は実行しない。",
                ),
                "notify": genai.types.Schema(
                    type="STRING", enum=["true", "false"],
                    description="結果をDiscord/LINEに通知するか。デフォルトtrue。",
                ),
            },
            required=["name", "task_prompt", "interval_minutes"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="list_scheduled_tasks",
        description="登録済みの自律タスク（Cronジョブ）一覧を取得。",
        parameters=genai.types.Schema(type="OBJECT", properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="delete_scheduled_task",
        description="自律タスク（Cronジョブ）を削除する。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "job_id": genai.types.Schema(type="INTEGER", description="ジョブID"),
            },
            required=["job_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="delegate_to_claude",
        description=(
            "Claude Code（Anthropic社のAIエージェント）にタスクを委譲する。"
            "使う場面: コーディング、デバッグ、リファクタリング、コードレビュー、"
            "設計、技術調査、壁打ち・アイデア出し、複雑な分析。"
            "Claude Codeはファイルの読み書き・コマンド実行・Web検索ができる最強のコーディングAI。"
            "使わない場面: 単純なファイル操作（read_file/write_file）、GUI操作（click等）、"
            "簡単な計算（execute_code）。これらは自分でやる方が速い。"
            "重要: taskには具体的な指示を書く。曖昧だとClaude Codeも困る。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "task": genai.types.Schema(
                    type="STRING",
                    description=(
                        "Claude Codeへの指示。具体的に書く。"
                        "例: 'discord_bot.pyのon_messageハンドラでメッセージ分割が"
                        "2000文字で切れるバグを修正して'"
                    ),
                ),
                "context": genai.types.Schema(
                    type="STRING",
                    description="追加コンテキスト。エラーメッセージ、要件、関連コード等。",
                ),
                "cwd": genai.types.Schema(
                    type="STRING",
                    description="作業ディレクトリ。省略時はプロジェクトルート。",
                ),
                "timeout": genai.types.Schema(
                    type="INTEGER",
                    description="タイムアウト秒数。デフォルト300（5分）。最大900（15分）。",
                ),
                "max_turns": genai.types.Schema(
                    type="INTEGER",
                    description="最大ターン数。デフォルト15。複雑なタスクなら30。",
                ),
                "allowed_tools": genai.types.Schema(
                    type="STRING",
                    description="Claude Codeに許可するツール（カンマ区切り）。例: 'Read,Edit,Bash'",
                ),
                "session_id": genai.types.Schema(
                    type="STRING",
                    description="前回セッションを継続する場合のID。結果のsession_idを指定。",
                ),
            },
            required=["task"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="dispatch_agents",
        description=(
            "複数の専門サブエージェントにタスクを並列ディスパッチする（マルチエージェント）。"
            "使う場面: 調査+コーディング+文章作成など、複数の専門性が必要なタスク。"
            "並列実行で高速。例: 「〇〇を調べてコードを書いてレポートにまとめて」。"
            "サブエージェント: researcher（Web調査）, coder（コーディング）, "
            "writer（文章作成）, analyst（データ分析）。"
            "agentsを省略するとタスク内容から自動選択。"
            "使わない場面: 単一タスク（search_web, delegate_to_claude等を直接使う方が速い）。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "task": genai.types.Schema(
                    type="STRING",
                    description="タスクの説明。具体的に書く。",
                ),
                "agents": genai.types.Schema(
                    type="STRING",
                    description=(
                        "使用するサブエージェント（カンマ区切り）。"
                        "選択肢: researcher, coder, writer, analyst。"
                        "省略するとタスクから自動判定。"
                        "例: 'researcher,writer'"
                    ),
                ),
                "context": genai.types.Schema(
                    type="STRING",
                    description="追加コンテキスト。背景情報、要件、制約等。",
                ),
            },
            required=["task"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="check_revenue",
        description=(
            "フリーランスプラットフォーム（Lancers/CrowdWorks）の報酬を確認する。"
            "ブラウザでログイン済みセッションを使って報酬管理ページにアクセスし、"
            "今月の報酬・累計報酬・未払い報酬を取得してローカルに保存する。"
            "使う場面: 「今月いくら稼いだ？」「ランサーズの報酬確認して」等。"
            "未ログインの場合はログイン方法を案内する。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "platform": genai.types.Schema(
                    type="STRING",
                    enum=["lancers", "crowdworks", "all"],
                    description="対象プラットフォーム。all=両方確認。デフォルトall。",
                ),
            },
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_revenue_summary",
        description=(
            "ローカルに保存された報酬データからサマリーを取得する。"
            "check_revenueで取得済みのデータを集計・分析する（ブラウザアクセス不要）。"
            "使う場面: 「今月の売上まとめて」「先月との比較は？」「報酬の推移は？」等。"
            "データがない場合はcheck_revenueの実行を促す。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "period": genai.types.Schema(
                    type="STRING",
                    enum=["week", "month", "all"],
                    description="集計期間。week=過去7日、month=今月、all=全期間。デフォルトmonth。",
                ),
            },
        ),
    ),
    # === Discord履歴 ===
    genai.types.FunctionDeclaration(
        name="get_discord_history",
        description=(
            "オーナーとのDiscord DM履歴を取得する（識ちゃん自身の送信メッセージも含む）。"
            "使う場面: 自分が以前送った通知やメッセージを確認したい時、"
            "会話の文脈を思い出したい時、前回何を伝えたか確認したい時。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "limit": genai.types.Schema(
                    type="INTEGER",
                    description="取得件数（デフォルト20、最大50）",
                ),
            },
        ),
    ),
    # === Notion連携ツール ===
    genai.types.FunctionDeclaration(
        name="notion_list_projects",
        description=(
            "Notionのプロジェクト一覧を取得する。"
            "使う場面: 「プロジェクト一覧見せて」「進行中のプロジェクトは？」等。"
            "statusやcategoryでフィルタ可能。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "status": genai.types.Schema(
                    type="STRING", enum=["準備中", "進行中", "完了", "保留"],
                    description="ステータスでフィルタ。省略で全件。",
                ),
                "category": genai.types.Schema(
                    type="STRING", enum=["プロダクト", "受託開発", "フリーランス", "研修・セミナー"],
                    description="カテゴリでフィルタ。省略で全件。",
                ),
            },
        ),
    ),
    genai.types.FunctionDeclaration(
        name="notion_get_project",
        description="Notionのプロジェクト詳細を取得する。IDはnotion_list_projectsで確認。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "project_id": genai.types.Schema(type="STRING", description="プロジェクトのページID"),
            },
            required=["project_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="notion_update_project",
        description=(
            "Notionのプロジェクトを更新する。"
            "使う場面: 「Maya Holdingsを進行中にして」「メモを追加して」等。"
            "updatesはJSON文字列で、変更したいプロパティのみ指定。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "project_id": genai.types.Schema(type="STRING", description="プロジェクトのページID"),
                "updates": genai.types.Schema(
                    type="STRING",
                    description='更新内容のJSON。例: \'{"ステータス": "完了", "メモ": "納品済み"}\'',
                ),
            },
            required=["project_id", "updates"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="notion_create_project",
        description=(
            "Notionに新規プロジェクトを作成する。"
            "使う場面: 「新しいプロジェクト作って」「〇〇プロジェクトをNotionに追加して」等。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "name": genai.types.Schema(type="STRING", description="プロジェクト名"),
                "category": genai.types.Schema(
                    type="STRING", enum=["プロダクト", "受託開発", "フリーランス", "研修・セミナー"],
                    description="カテゴリ。デフォルト: プロダクト",
                ),
                "status": genai.types.Schema(
                    type="STRING", enum=["準備中", "進行中", "完了", "保留"],
                    description="ステータス。デフォルト: 準備中",
                ),
                "memo": genai.types.Schema(type="STRING", description="メモ（省略可）"),
            },
            required=["name"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="notion_list_tasks",
        description=(
            "Notionのタスク一覧を取得する。"
            "使う場面: 「TimeTurn AIのタスク見せて」「未着手のタスクは？」「高優先度のタスク一覧」等。"
            "project_id, status, priorityでフィルタ可能。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "project_id": genai.types.Schema(type="STRING", description="プロジェクトIDでフィルタ（省略で全タスク）"),
                "status": genai.types.Schema(
                    type="STRING", enum=["未着手", "進行中", "レビュー", "完了"],
                    description="ステータスでフィルタ",
                ),
                "priority": genai.types.Schema(
                    type="STRING", enum=["高", "中", "低"],
                    description="優先度でフィルタ",
                ),
            },
        ),
    ),
    genai.types.FunctionDeclaration(
        name="notion_create_task",
        description=(
            "Notionに新規タスクを作成する。"
            "使う場面: 「〇〇タスクを追加して」「TimeTurn AIにタスク作って」等。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "name": genai.types.Schema(type="STRING", description="タスク名"),
                "project_id": genai.types.Schema(type="STRING", description="紐づけるプロジェクトID（省略可）"),
                "status": genai.types.Schema(
                    type="STRING", enum=["未着手", "進行中", "レビュー", "完了"],
                    description="ステータス。デフォルト: 未着手",
                ),
                "priority": genai.types.Schema(
                    type="STRING", enum=["高", "中", "低"],
                    description="優先度。デフォルト: 中",
                ),
                "memo": genai.types.Schema(type="STRING", description="メモ"),
                "deadline": genai.types.Schema(type="STRING", description="期限（YYYY-MM-DD形式）"),
                "estimated_hours": genai.types.Schema(type="NUMBER", description="見積工数（時間）"),
            },
            required=["name"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="notion_update_task",
        description=(
            "Notionのタスクを更新する。"
            "使う場面: 「タスクを完了にして」「進捗率を50%にして」「優先度を高にして」等。"
            "updatesはJSON文字列で、変更したいプロパティのみ指定。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "task_id": genai.types.Schema(type="STRING", description="タスクのページID"),
                "updates": genai.types.Schema(
                    type="STRING",
                    description='更新内容のJSON。例: \'{"ステータス": "完了", "進捗率": 100, "実績工数(h)": 3.5}\'',
                ),
            },
            required=["task_id", "updates"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="notion_batch_create_tasks",
        description=(
            "Notionに複数タスクを一括作成する。"
            "使う場面: 「TimeTurn AIにタスクを5個追加して」「チェックリストをNotionに入れて」等。"
            "tasks_jsonに配列形式でタスク情報を渡す。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "tasks_json": genai.types.Schema(
                    type="STRING",
                    description='タスク配列のJSON。例: \'[{"name": "設計", "priority": "高", "deadline": "2026-04-01"}, {"name": "実装"}]\'',
                ),
                "project_id": genai.types.Schema(type="STRING", description="全タスクに紐づけるプロジェクトID（省略可）"),
            },
            required=["tasks_json"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="notion_search",
        description=(
            "Notionワークスペース全体をキーワード検索する。"
            "使う場面: 「Notionで〇〇を探して」「〇〇に関するページある？」等。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "query": genai.types.Schema(type="STRING", description="検索キーワード"),
            },
            required=["query"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="notion_get_page_content",
        description=(
            "Notionページのブロックコンテンツ（本文）を取得する。"
            "使う場面: ページの中身を詳しく読みたい時、to-doリストの確認等。"
            "各ブロックのIDも返すので、update_blockでチェックボックス操作等に使える。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "page_id": genai.types.Schema(type="STRING", description="ページID"),
            },
            required=["page_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="notion_add_comment",
        description=(
            "Notionページにコメント（ディスカッション）を追加する。"
            "使う場面: タスク完了時の作業報告、進捗メモ、質問・確認等。"
            "プロジェクトページやタスクページにコメントを残せる。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "page_id": genai.types.Schema(type="STRING", description="コメント先のページID"),
                "text": genai.types.Schema(type="STRING", description="コメント内容"),
            },
            required=["page_id", "text"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="notion_list_comments",
        description=(
            "Notionページのコメント一覧を取得する。"
            "使う場面: 過去のコメントや作業履歴を確認したい時。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "page_id": genai.types.Schema(type="STRING", description="ページID"),
            },
            required=["page_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="notion_update_block",
        description=(
            "Notionのブロックを更新する（チェックボックスのオン/オフ、テキスト変更等）。"
            "使う場面: to-doを完了にする、テキストを書き換える等。"
            "先にnotion_get_page_contentでblock_idを確認すること。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "block_id": genai.types.Schema(type="STRING", description="ブロックID"),
                "updates": genai.types.Schema(
                    type="STRING",
                    description=(
                        '更新内容のJSON。例:\n'
                        'チェックボックス: \'{"type": "to_do", "checked": true}\'\n'
                        'テキスト変更: \'{"type": "paragraph", "text": "新しいテキスト"}\''
                    ),
                ),
            },
            required=["block_id", "updates"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="notion_append_blocks",
        description=(
            "Notionページにブロック（テキスト、to-do、見出し等）を追記する。"
            "使う場面: タスクページに作業ログを書く、チェックリストを追加する等。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "page_id": genai.types.Schema(type="STRING", description="追記先のページID"),
                "blocks_json": genai.types.Schema(
                    type="STRING",
                    description=(
                        'ブロック配列のJSON。例:\n'
                        '\'[{"type": "to_do", "text": "タスク1"}, '
                        '{"type": "paragraph", "text": "メモ"}, '
                        '{"type": "heading_2", "text": "セクション"}]\'\n'
                        '対応type: paragraph, heading_1/2/3, to_do, '
                        'bulleted_list_item, numbered_list_item, quote, divider'
                    ),
                ),
            },
            required=["page_id", "blocks_json"],
        ),
    ),
    # === Notionタスク自動実行 ===
    genai.types.FunctionDeclaration(
        name="notion_execute_tasks",
        description=(
            "Notionの未着手タスクを自動実行する。オーナーが「やって」「タスクやって」と言った時に使う。"
            "未着手タスクを優先度順にガガガっと実行して、完了したらNotionコメント+Discordで報告する。"
            "実行中にオーナーがNotionにコメントしたら、それを拾って方向修正する。"
            "成果物は~/識ちゃん/フォルダに自動保存。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "project_id": genai.types.Schema(
                    type="STRING",
                    description="特定プロジェクトのタスクのみ実行。省略で全プロジェクト。",
                ),
                "max_tasks": genai.types.Schema(
                    type="INTEGER",
                    description="最大実行数。デフォルト10。暴走防止。",
                ),
            },
        ),
    ),
    genai.types.FunctionDeclaration(
        name="notion_execute_single_task",
        description=(
            "Notionの特定タスク1件を実行する。"
            "使う場面: 「このタスクやって」とタスクIDが分かっている時。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "task_id": genai.types.Schema(type="STRING", description="タスクのページID"),
                "project_name": genai.types.Schema(type="STRING", description="プロジェクト名（表示用）"),
            },
            required=["task_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="notion_execution_status",
        description="タスク自動実行の現在の状態を確認する（実行中か、何件完了したか等）。",
        parameters=genai.types.Schema(type="OBJECT", properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="notion_get_reflections",
        description=(
            "過去のタスク実行の振り返りログを取得する（Reflexion）。"
            "使う場面: 過去の教訓を確認したい時、同じ失敗を繰り返さないための参照。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "days": genai.types.Schema(type="INTEGER", description="取得日数（デフォルト3日）"),
            },
        ),
    ),
    # === 常時指示（Standing Orders） ===
    genai.types.FunctionDeclaration(
        name="add_standing_order",
        description=(
            "オーナーからの永続的な指示を記憶する。"
            "使う場面: 「これ覚えて」「これやり続けて」「毎回〇〇して」等。"
            "保存された指示は毎回のセッションで自動的に読み込まれ、永久に有効。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "text": genai.types.Schema(type="STRING", description="記憶する指示内容"),
            },
            required=["text"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="list_standing_orders",
        description="現在の常時指示一覧を確認する。",
        parameters=genai.types.Schema(type="OBJECT", properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="remove_standing_order",
        description="常時指示を削除する。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "order_id": genai.types.Schema(type="INTEGER", description="削除する指示のID"),
            },
            required=["order_id"],
        ),
    ),
    # === 自己進化 ===
    genai.types.FunctionDeclaration(
        name="run_self_evolution",
        description=(
            "自己進化サイクルを手動実行する。X + Web検索で最新AI/開発/マーケ情報を収集し、"
            "識ちゃんの強化に使えるインサイトを分析してNotionに記録する。"
            "通常は4時間ごとに自動実行されるが、手動でも実行可能。"
        ),
        parameters=genai.types.Schema(type="OBJECT", properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="generate_tool",
        description=(
            "新しいツールを動的に生成・登録する。既存ツールでは対応できない処理をツール化する。"
            "使う場面: ユーザーが繰り返し使いそうな処理をツールとして保存したい時。"
            "例: 「JSONをCSVに変換するツール作って」「マークダウンをHTMLに変換するツール作って」。"
            "codeにはasync def関数を定義するPythonコードを書く。"
            "使えないもの: os, subprocess, socket, open(), ファイルI/O, ネットワーク。"
            "使わない場面: 一度きりの処理（execute_codeを使う）。"
        ),
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "name": genai.types.Schema(
                    type="STRING",
                    description="ツール名（snake_case、3-50文字）。例: 'json_to_csv', 'markdown_to_html'",
                ),
                "description": genai.types.Schema(
                    type="STRING",
                    description="ツールの説明。何をするツールか。",
                ),
                "code": genai.types.Schema(
                    type="STRING",
                    description=(
                        "async def関数を定義するPythonコード。関数名はnameと一致させる。"
                        "戻り値はdict。例: async def json_to_csv(json_data: str) -> dict: ..."
                    ),
                ),
            },
            required=["name", "description", "code"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="list_dynamic_tools",
        description="動的に生成されたツールの一覧を取得する。",
        parameters=genai.types.Schema(type="OBJECT", properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="delete_dynamic_tool",
        description="動的に生成されたツールを削除する。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "name": genai.types.Schema(type="STRING", description="削除するツール名"),
            },
            required=["name"],
        ),
    ),
])

# GUI操作ツール（自動スクリーンショット対象）
GUI_TOOLS = frozenset({"click", "double_click", "right_click", "drag", "type_text", "press_key", "scroll"})

# ツール実行時の進捗メッセージ
TOOL_STATUS_MESSAGES = {
    "take_screenshot": "画面を撮影中...",
    "crop_screenshot": "画面の一部を拡大中...",
    "open_app": "アプリを起動中...",
    "open_url": "ページを開いてる...",
    "open_url_with_profile": "指定プロファイルでページを開いてる...",
    "get_frontmost_app": "確認中...",
    "get_running_apps": "アプリ一覧を取得中...",
    "set_volume": "音量を変更中...",
    "toggle_dark_mode": "切り替え中...",
    "show_notification": "通知を送信中...",
    "type_text": "入力中...",
    "press_key": "操作中...",
    "scroll": "スクロール中...",
    "click": "クリック中...",
    "double_click": "ダブルクリック中...",
    "right_click": "右クリック中...",
    "get_screen_size": "画面サイズ確認中...",
    "read_file": "ファイルを読んでる...",
    "write_file": "ファイルに書き込み中...",
    "list_directory": "フォルダを見てる...",
    "move_file": "ファイルを移動中...",
    "run_command": "コマンド実行中...",
    "drag": "ドラッグ中...",
    "get_browser_info": "ブラウザ情報を取得中...",
    "get_window_info": "ウィンドウ情報を取得中...",
    "add_reminder": "リマインダーを設定中...",
    "list_reminders": "リマインダー一覧を取得中...",
    "delete_reminder": "リマインダーを削除中...",
    "browse_url": "ページを読んでる...",
    "search_web": "Web検索中...",
    "get_page_text": "テキスト取得中...",
    "get_page_elements": "ページ要素を解析中...",
    "interact_page_element": "Web操作中...",
    "get_accessibility_tree": "ページ構造を取得中...",
    "execute_code": "コード実行中...",
    "update_plan": "計画を更新中...",
    "delegate_to_claude": "Claude Codeに委譲中...（数分かかることがある）",
    "schedule_task": "自律タスクを登録中...",
    "list_scheduled_tasks": "自律タスク一覧を取得中...",
    "delete_scheduled_task": "自律タスクを削除中...",
    "dispatch_agents": "サブエージェントを並列実行中...（しばらくかかることがある）",
    "check_revenue": "報酬データを取得中...（ページアクセスに少し時間がかかる）",
    "get_revenue_summary": "報酬サマリーを集計中...",
    "get_discord_history": "Discord履歴を取得中...",
    "notion_list_projects": "Notionからプロジェクト一覧を取得中...",
    "notion_get_project": "プロジェクト詳細を取得中...",
    "notion_update_project": "プロジェクトを更新中...",
    "notion_create_project": "新規プロジェクトを作成中...",
    "notion_list_tasks": "タスク一覧を取得中...",
    "notion_create_task": "タスクを作成中...",
    "notion_update_task": "タスクを更新中...",
    "notion_batch_create_tasks": "タスクを一括作成中...",
    "notion_search": "Notionを検索中...",
    "notion_get_page_content": "ページ内容を取得中...",
    "notion_add_comment": "コメントを追加中...",
    "notion_list_comments": "コメント一覧を取得中...",
    "notion_update_block": "ブロックを更新中...",
    "notion_append_blocks": "ブロックを追記中...",
    "notion_execute_tasks": "タスクを自動実行中...（しばらくかかることがある）",
    "notion_execute_single_task": "タスクを実行中...",
    "notion_execution_status": "実行状態を確認中...",
    "notion_get_reflections": "振り返りログを取得中...",
    "add_standing_order": "覚えてる...",
    "list_standing_orders": "常時指示を確認中...",
    "remove_standing_order": "常時指示を削除中...",
    "run_self_evolution": "自己進化サイクル実行中...（情報収集→分析→Notion記録）",
    "generate_tool": "新しいツールを生成中...",
    "list_dynamic_tools": "動的ツール一覧を取得中...",
    "delete_dynamic_tool": "動的ツールを削除中...",
}

# GUI操作後の待機時間（スマートウェイト）
GUI_WAIT_TIMES = {
    "click": 2.0,
    "double_click": 2.0,
    "right_click": 1.0,
    "type_text": 0.3,
    "scroll": 0.3,
    "press_key": 0.5,
    "drag": 0.5,
}

# スクショリサイズ幅（screenshot.pyと一致させること）
SCREENSHOT_WIDTH = 1024

# === バリデーション ===

_REQUIRED_ARGS = {
    "open_app": ["app_name"], "open_url": ["url"], "open_url_with_profile": ["url", "profile"],
    "set_volume": ["level"], "show_notification": ["title", "message"],
    "type_text": ["text"], "press_key": ["key"],
    "click": ["x", "y"], "double_click": ["x", "y"], "right_click": ["x", "y"],
    "drag": ["x1", "y1", "x2", "y2"],
    "read_file": ["path"], "write_file": ["path", "content"],
    "list_directory": ["path"], "move_file": ["src", "dst"],
    "run_command": ["command"],
    "add_reminder": ["text", "datetime_str"],
    "delete_reminder": ["reminder_id"],
    "browse_url": ["url"],
    "search_web": ["query"],
    "get_page_text": ["url"],
    "get_page_elements": ["url"],
    "interact_page_element": ["url", "element_index", "action"],
    "get_accessibility_tree": ["url"],
    "execute_code": ["code"],
    "update_plan": ["plan"],
    "delegate_to_claude": ["task"],
    "schedule_task": ["name", "task_prompt", "interval_minutes"],
    "delete_scheduled_task": ["job_id"],
    "dispatch_agents": ["task"],
    # Notion
    "notion_get_project": ["project_id"],
    "notion_update_project": ["project_id", "updates"],
    "notion_create_project": ["name"],
    "notion_create_task": ["name"],
    "notion_update_task": ["task_id", "updates"],
    "notion_batch_create_tasks": ["tasks_json"],
    "notion_search": ["query"],
    "notion_get_page_content": ["page_id"],
    "notion_add_comment": ["page_id", "text"],
    "notion_list_comments": ["page_id"],
    "notion_update_block": ["block_id", "updates"],
    "notion_append_blocks": ["page_id", "blocks_json"],
    "notion_execute_single_task": ["task_id"],
    # notion_execute_tasks, notion_execution_status: 引数はすべてオプション
    # notion_list_projects, notion_list_tasks: 引数はすべてオプション
    # check_revenue, get_revenue_summary: 引数はすべてオプション（デフォルト値あり）
    "add_standing_order": ["text"],
    "remove_standing_order": ["order_id"],
    # list_standing_orders: 引数なし
    "generate_tool": ["name", "description", "code"],
    "delete_dynamic_tool": ["name"],
}


def validate_tool_args(tool_name: str, tool_args: dict) -> str | None:
    """ツール引数バリデーション。エラーがあれば文字列、OKならNone。"""
    for r in _REQUIRED_ARGS.get(tool_name, []):
        if r not in tool_args or tool_args[r] is None:
            return f"必須引数 '{r}' が未指定"

    if tool_name == "set_volume":
        try:
            if not (0 <= int(tool_args["level"]) <= 100):
                return "音量は0〜100"
        except (ValueError, TypeError):
            return "音量は数値で"

    if tool_name in ("click", "double_click", "right_click"):
        try:
            x, y = int(tool_args["x"]), int(tool_args["y"])
            if x < 0 or y < 0 or x > 5000 or y > 5000:
                return f"座標異常: ({x}, {y})"
        except (ValueError, TypeError):
            return "座標は数値で"

    if tool_name == "interact_page_element":
        action = tool_args.get("action", "")
        if action not in ("click", "fill", "select"):
            return f"actionは click/fill/select のみ: {action}"
        if action in ("fill", "select") and not tool_args.get("value"):
            return f"{action}にはvalueが必要"

    return None


def scale_coordinates(tool_name: str, tool_args: dict) -> dict:
    """スクショ座標→実画面座標にスケーリング（Retina/HiDPI対応）

    スクショ(1024xH)上の座標 → CGEvent論理座標系(screen_w x screen_h)に変換。
    X方向とY方向を独立にスケーリング（アスペクト比の差に対応）。
    """
    if tool_name not in ("click", "double_click", "right_click", "drag"):
        return tool_args

    try:
        from platform_layer import get_platform
        from tools.screenshot import last_screenshot_width, last_screenshot_height

        screen_w, screen_h = get_platform().get_screen_size()

        # スクショサイズが未取得の場合はスケーリングしない（安全側に倒す）
        if last_screenshot_width is None or last_screenshot_height is None:
            logger.warning("Screenshot dimensions not yet available, skipping coordinate scaling")
            return tool_args

        ss_w = last_screenshot_width
        ss_h = last_screenshot_height

        if screen_w <= ss_w:
            return tool_args

        scale_x = screen_w / ss_w
        scale_y = screen_h / ss_h

        if tool_name == "drag":
            tool_args = {
                **tool_args,
                "x1": max(0, min(round(int(tool_args["x1"]) * scale_x), screen_w - 1)),
                "y1": max(0, min(round(int(tool_args["y1"]) * scale_y), screen_h - 1)),
                "x2": max(0, min(round(int(tool_args["x2"]) * scale_x), screen_w - 1)),
                "y2": max(0, min(round(int(tool_args["y2"]) * scale_y), screen_h - 1)),
            }
        else:
            x = round(int(tool_args["x"]) * scale_x)
            y = round(int(tool_args["y"]) * scale_y)
            # クランプ + 警告ログ（サイレントエラー防止）
            clamped_x = max(0, min(x, screen_w - 1))
            clamped_y = max(0, min(y, screen_h - 1))
            if clamped_x != x or clamped_y != y:
                logger.warning(
                    f"Coordinate clamped: ({x},{y}) → ({clamped_x},{clamped_y}), "
                    f"screen={screen_w}x{screen_h}. AI may have specified wrong coordinates."
                )
            tool_args = {**tool_args, "x": clamped_x, "y": clamped_y}
        logger.info(
            f"Coordinate scaling: screen={screen_w}x{screen_h}, "
            f"screenshot={ss_w}x{ss_h}, scale_x={scale_x:.3f}, scale_y={scale_y:.3f}"
        )
    except Exception as e:
        logger.warning(f"Coordinate scaling failed: {e}")

    return tool_args


# === 起動時の同期チェック ===
def _validate_tool_sync():
    """TOOL_FUNCTIONS, GEMINI_TOOLS, TOOL_LEVELS の3辞書が同期しているか検証"""
    func_names = set(TOOL_FUNCTIONS.keys())
    gemini_names = {fd.name for fd in GEMINI_TOOLS.function_declarations}
    level_names = set(TOOL_LEVELS.keys())

    errors = []
    if func_names != gemini_names:
        missing_gemini = func_names - gemini_names
        extra_gemini = gemini_names - func_names
        if missing_gemini:
            errors.append(f"GEMINI_TOOLSに不足: {missing_gemini}")
        if extra_gemini:
            errors.append(f"TOOL_FUNCTIONSに不足(Gemini側に余分): {extra_gemini}")

    if func_names != level_names:
        missing_levels = func_names - level_names
        extra_levels = level_names - func_names
        if missing_levels:
            errors.append(f"TOOL_LEVELSに不足: {missing_levels}")
        if extra_levels:
            errors.append(f"TOOL_FUNCTIONSに不足(Level側に余分): {extra_levels}")

    if errors:
        for e in errors:
            logger.error(f"TOOL SYNC ERROR: {e}")
        raise RuntimeError(f"ツール定義の同期エラー: {'; '.join(errors)}")

    logger.info(f"Tool sync OK: {len(func_names)} tools verified")


_validate_tool_sync()
