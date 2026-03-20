"""Notion連携ツール

プロジェクト・タスクDBの読み書き、コメント、チェックボックス操作。
各プロジェクトのインラインタスクDBを動的に検出して操作する。

セキュリティ:
- API Keyはconfig.pyから読み込み
- 書き込みはELEVATEDレベル（LINE通知付き）
"""

import json
import logging
from datetime import datetime
from typing import Any

import httpx

from config import NOTION_API_KEY

logger = logging.getLogger("shiki.tools")

# === Notion API設定 ===
_BASE_URL = "https://api.notion.com/v1"
_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# === データベースID ===
PROJECT_DB_ID = "d0d136d570bb44daa339efff76489d45"

# プロジェクトID→インラインタスクDB IDのキャッシュ
_task_db_cache: dict[str, str] = {}


def _safe_error(status_code: int, resp_text: str) -> str:
    """APIエラーを安全な形式に変換（内部情報を除去）"""
    try:
        data = json.loads(resp_text)
        code = data.get("code", "unknown")
        msg = data.get("message", "")[:150]
        return f"Notion API {status_code}: {code} — {msg}"
    except Exception:
        return f"Notion API {status_code}"


# コネクションプール付きシングルトンクライアント
_shared_client: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    """コネクション再利用のシングルトンクライアント"""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            headers=_HEADERS,
            timeout=30.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _shared_client


def _rich_text(text: str) -> list[dict]:
    if not text:
        return []
    return [{"type": "text", "text": {"content": text}}]


def _parse_rich_text(rt_list: list) -> str:
    return "".join(item.get("plain_text", "") for item in rt_list)


def _parse_page_properties(props: dict) -> dict:
    result = {}
    for name, prop in props.items():
        ptype = prop.get("type", "")
        if ptype == "title":
            result[name] = _parse_rich_text(prop.get("title", []))
        elif ptype == "rich_text":
            result[name] = _parse_rich_text(prop.get("rich_text", []))
        elif ptype == "select":
            sel = prop.get("select")
            result[name] = sel["name"] if sel else None
        elif ptype == "multi_select":
            result[name] = [s["name"] for s in prop.get("multi_select", [])]
        elif ptype == "number":
            result[name] = prop.get("number")
        elif ptype == "checkbox":
            result[name] = prop.get("checkbox", False)
        elif ptype == "date":
            d = prop.get("date")
            if d:
                result[name] = d.get("start")
                if d.get("end"):
                    result[name] = f"{d['start']} ~ {d['end']}"
            else:
                result[name] = None
        elif ptype == "people":
            result[name] = [
                p.get("name", p.get("id", ""))
                for p in prop.get("people", [])
            ]
        elif ptype == "relation":
            result[name] = [r["id"] for r in prop.get("relation", [])]
        elif ptype == "url":
            result[name] = prop.get("url")
        elif ptype == "status":
            s = prop.get("status")
            result[name] = s["name"] if s else None
        else:
            result[name] = f"({ptype})"
    return result


# プロパティ名→型のマッピング
_SELECT_PROPS = {"カテゴリ", "ステータス", "優先度"}
_NUMBER_PROPS = {"見積工数(h)", "実績工数(h)", "進捗率"}
_DATE_PROPS = {"完了条件", "期限"}
_TITLE_PROPS = {"プロジェクト名", "タスク名"}
_TEXT_PROPS = {"メモ"}


def _build_properties(updates: dict) -> dict:
    props = {}
    for key, value in updates.items():
        if key in _TITLE_PROPS:
            props[key] = {"title": _rich_text(str(value))}
        elif key in _SELECT_PROPS:
            props[key] = {"select": {"name": str(value)} if value else None}
        elif key in _NUMBER_PROPS:
            try:
                props[key] = {"number": float(value) if value is not None else None}
            except (ValueError, TypeError):
                logger.warning(f"Invalid number for {key}: {value}")
                continue
        elif key in _DATE_PROPS:
            props[key] = {"date": {"start": str(value)} if value else None}
        elif key in _TEXT_PROPS:
            props[key] = {"rich_text": _rich_text(str(value))}
        else:
            props[key] = {"rich_text": _rich_text(str(value))}
    return props


async def _find_task_db_id(project_id: str) -> str | None:
    """プロジェクトページ内のインラインタスクDBのIDを検出"""
    if project_id in _task_db_cache:
        return _task_db_cache[project_id]

    client = _client()
    resp = await client.get(
        f"{_BASE_URL}/blocks/{project_id}/children",
        params={"page_size": 50},
    )
    if resp.status_code != 200:
        return None

    for block in resp.json().get("results", []):
        if block.get("type") == "child_database":
            db_id = block["id"]
            _task_db_cache[project_id] = db_id
            logger.info(f"Found task DB {db_id} for project {project_id}")
            return db_id
    return None


# =============================================
# プロジェクト操作
# =============================================

async def list_projects(
    status: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """プロジェクト一覧を取得"""
    filters = []
    if status:
        filters.append({"property": "ステータス", "select": {"equals": status}})
    if category:
        filters.append({"property": "カテゴリ", "select": {"equals": category}})

    body: dict[str, Any] = {}
    if len(filters) == 1:
        body["filter"] = filters[0]
    elif len(filters) > 1:
        body["filter"] = {"and": filters}
    body["sorts"] = [{"property": "ステータス", "direction": "ascending"}]

    client = _client()
    resp = await client.post(f"{_BASE_URL}/databases/{PROJECT_DB_ID}/query", json=body)
    if resp.status_code != 200:
        return {"success": False, "error": _safe_error(resp.status_code, resp.text)}

    projects = []
    for page in resp.json().get("results", []):
        parsed = _parse_page_properties(page["properties"])
        parsed["id"] = page["id"]
        parsed["url"] = page["url"]
        projects.append(parsed)
    return {"success": True, "projects": projects, "count": len(projects)}


async def get_project(project_id: str) -> dict[str, Any]:
    """プロジェクト詳細を取得"""
    client = _client()
    resp = await client.get(f"{_BASE_URL}/pages/{project_id}")
    if resp.status_code != 200:
        return {"success": False, "error": _safe_error(resp.status_code, resp.text)}
    page = resp.json()
    parsed = _parse_page_properties(page["properties"])
    parsed["id"] = page["id"]
    parsed["url"] = page["url"]
    return {"success": True, "project": parsed}


async def update_project(project_id: str, updates: str) -> dict[str, Any]:
    """プロジェクトのプロパティを更新"""
    try:
        update_dict = json.loads(updates)
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"JSON解析エラー: {e}"}

    props = _build_properties(update_dict)
    client = _client()
    resp = await client.patch(f"{_BASE_URL}/pages/{project_id}", json={"properties": props})
    if resp.status_code != 200:
        return {"success": False, "error": _safe_error(resp.status_code, resp.text)}
    return {"success": True, "output": f"プロジェクト更新完了: {list(update_dict.keys())}"}


async def create_project(
    name: str, category: str = "プロダクト", status: str = "準備中", memo: str = "",
) -> dict[str, Any]:
    """新規プロジェクト作成"""
    props = _build_properties({"プロジェクト名": name, "カテゴリ": category, "ステータス": status})
    if memo:
        props["メモ"] = {"rich_text": _rich_text(memo)}

    client = _client()
    resp = await client.post(f"{_BASE_URL}/pages", json={
        "parent": {"database_id": PROJECT_DB_ID}, "properties": props,
    })
    if resp.status_code != 200:
        return {"success": False, "error": _safe_error(resp.status_code, resp.text)}
    page = resp.json()
    return {"success": True, "output": f"プロジェクト作成完了: {name}", "id": page["id"], "url": page["url"]}


# =============================================
# タスク操作（各プロジェクトのインラインDB対応）
# =============================================

async def list_tasks(
    project_id: str | None = None,
    status: str | None = None,
    priority: str | None = None,
) -> dict[str, Any]:
    """タスク一覧を取得

    project_id指定: そのプロジェクトのインラインタスクDBから取得
    project_id省略: 全プロジェクトのタスクを横断取得
    """
    if project_id:
        return await _list_tasks_for_project(project_id, status, priority)

    # 全プロジェクトを横断
    proj_result = await list_projects()
    if not proj_result["success"]:
        return proj_result

    all_tasks = []
    for proj in proj_result["projects"]:
        pid = proj["id"]
        pname = proj.get("プロジェクト名", "?")
        result = await _list_tasks_for_project(pid, status, priority)
        if result["success"]:
            for task in result["tasks"]:
                task["_project_name"] = pname
                task["_project_id"] = pid
            all_tasks.extend(result["tasks"])

    return {"success": True, "tasks": all_tasks, "count": len(all_tasks)}


async def _list_tasks_for_project(
    project_id: str, status: str | None = None, priority: str | None = None,
) -> dict[str, Any]:
    """特定プロジェクトのインラインタスクDBからタスクを取得"""
    task_db_id = await _find_task_db_id(project_id)
    if not task_db_id:
        return {"success": False, "error": f"プロジェクト {project_id} のタスクDBが見つかりません"}

    filters = []
    if status:
        filters.append({"property": "ステータス", "select": {"equals": status}})
    if priority:
        filters.append({"property": "優先度", "select": {"equals": priority}})

    body: dict[str, Any] = {}
    if len(filters) == 1:
        body["filter"] = filters[0]
    elif len(filters) > 1:
        body["filter"] = {"and": filters}

    client = _client()
    resp = await client.post(f"{_BASE_URL}/databases/{task_db_id}/query", json=body)
    if resp.status_code != 200:
        return {"success": False, "error": _safe_error(resp.status_code, resp.text)}

    tasks = []
    for page in resp.json().get("results", []):
        parsed = _parse_page_properties(page["properties"])
        parsed["id"] = page["id"]
        parsed["url"] = page["url"]
        tasks.append(parsed)
    return {"success": True, "tasks": tasks, "count": len(tasks), "task_db_id": task_db_id}


async def create_task(
    name: str,
    project_id: str | None = None,
    status: str = "未着手",
    priority: str = "中",
    memo: str = "",
    deadline: str | None = None,
    estimated_hours: float | None = None,
) -> dict[str, Any]:
    """新規タスク作成（プロジェクトのインラインDBに追加）"""
    if not project_id:
        return {"success": False, "error": "project_idが必要です（どのプロジェクトに追加する？）"}

    task_db_id = await _find_task_db_id(project_id)
    if not task_db_id:
        return {"success": False, "error": f"プロジェクト {project_id} のタスクDBが見つかりません"}

    update_dict: dict[str, Any] = {"タスク名": name, "ステータス": status, "優先度": priority}
    if memo:
        update_dict["メモ"] = memo
    if deadline:
        update_dict["期限"] = deadline
    if estimated_hours is not None:
        update_dict["見積工数(h)"] = estimated_hours

    props = _build_properties(update_dict)

    client = _client()
    resp = await client.post(f"{_BASE_URL}/pages", json={
        "parent": {"database_id": task_db_id}, "properties": props,
    })
    if resp.status_code != 200:
        return {"success": False, "error": _safe_error(resp.status_code, resp.text)}
    page = resp.json()
    return {"success": True, "output": f"タスク作成完了: {name}", "id": page["id"], "url": page["url"]}


async def update_task(task_id: str, updates: str) -> dict[str, Any]:
    """タスクのプロパティを更新"""
    try:
        update_dict = json.loads(updates)
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"JSON解析エラー: {e}"}

    props = _build_properties(update_dict)
    client = _client()
    resp = await client.patch(f"{_BASE_URL}/pages/{task_id}", json={"properties": props})
    if resp.status_code != 200:
        return {"success": False, "error": _safe_error(resp.status_code, resp.text)}
    return {"success": True, "output": f"タスク更新完了: {list(update_dict.keys())}"}


async def batch_create_tasks(tasks_json: str, project_id: str | None = None) -> dict[str, Any]:
    """複数タスクを一括作成"""
    try:
        tasks_list = json.loads(tasks_json)
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"JSON解析エラー: {e}"}

    if not isinstance(tasks_list, list):
        return {"success": False, "error": "tasks_jsonは配列である必要があります"}

    results = []
    for task_data in tasks_list:
        name = task_data.get("name", task_data.get("タスク名", ""))
        if not name:
            results.append({"success": False, "error": "タスク名が未指定"})
            continue
        result = await create_task(
            name=name,
            project_id=project_id or task_data.get("project_id"),
            status=task_data.get("status", task_data.get("ステータス", "未着手")),
            priority=task_data.get("priority", task_data.get("優先度", "中")),
            memo=task_data.get("memo", task_data.get("メモ", "")),
            deadline=task_data.get("deadline", task_data.get("期限")),
            estimated_hours=task_data.get("estimated_hours", task_data.get("見積工数(h)")),
        )
        results.append(result)

    created = sum(1 for r in results if r.get("success"))
    return {
        "success": created > 0,
        "output": f"{created}/{len(tasks_list)}件のタスクを作成しました",
        "results": results,
    }


# =============================================
# コメント（ディスカッション）
# =============================================

async def add_comment(page_id: str, text: str) -> dict[str, Any]:
    """ページにコメントを追加する

    Args:
        page_id: コメント先のページID（プロジェクトやタスク）
        text: コメント内容
    """
    client = _client()
    resp = await client.post(f"{_BASE_URL}/comments", json={
        "parent": {"page_id": page_id},
        "rich_text": _rich_text(text),
    })
    if resp.status_code != 200:
        return {"success": False, "error": _safe_error(resp.status_code, resp.text)}
    comment = resp.json()
    return {
        "success": True,
        "output": f"コメント追加完了",
        "comment_id": comment["id"],
    }


async def list_comments(page_id: str) -> dict[str, Any]:
    """ページのコメント一覧を取得"""
    client = _client()
    resp = await client.get(
        f"{_BASE_URL}/comments",
        params={"block_id": page_id, "page_size": 50},
    )
    if resp.status_code != 200:
        return {"success": False, "error": _safe_error(resp.status_code, resp.text)}

    comments = []
    for c in resp.json().get("results", []):
        comments.append({
            "id": c["id"],
            "text": _parse_rich_text(c.get("rich_text", [])),
            "created_by": c.get("created_by", {}).get("id", ""),
            "created_time": c.get("created_time", ""),
        })
    return {"success": True, "comments": comments, "count": len(comments)}


# =============================================
# ブロック操作（チェックボックス、コンテンツ追記）
# =============================================

async def get_page_content(page_id: str) -> dict[str, Any]:
    """ページのブロックコンテンツを取得"""
    client = _client()
    resp = await client.get(f"{_BASE_URL}/blocks/{page_id}/children", params={"page_size": 100})
    if resp.status_code != 200:
        return {"success": False, "error": _safe_error(resp.status_code, resp.text)}

    blocks = []
    for block in resp.json().get("results", []):
        btype = block.get("type", "")
        content = ""
        if btype in ("paragraph", "heading_1", "heading_2", "heading_3",
                     "bulleted_list_item", "numbered_list_item", "to_do",
                     "quote", "callout", "toggle"):
            block_data = block.get(btype, {})
            rt = block_data.get("rich_text", block_data.get("text", []))
            content = _parse_rich_text(rt)
            if btype == "to_do":
                checked = block_data.get("checked", False)
                content = f"[{'x' if checked else ' '}] {content}"
        elif btype == "child_database":
            content = f"[子DB: {block.get('child_database', {}).get('title', '')}]"
        elif btype == "child_page":
            content = f"[子ページ: {block.get('child_page', {}).get('title', '')}]"

        blocks.append({"type": btype, "content": content, "id": block["id"]})
    return {"success": True, "blocks": blocks, "count": len(blocks)}


async def update_block(block_id: str, updates: str) -> dict[str, Any]:
    """ブロックを更新する（チェックボックスのオン/オフ、テキスト変更等）

    Args:
        block_id: ブロックID
        updates: JSON文字列 例:
            チェックボックス: {"type": "to_do", "checked": true}
            テキスト変更: {"type": "paragraph", "text": "新しいテキスト"}
    """
    try:
        update_dict = json.loads(updates)
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"JSON解析エラー: {e}"}

    btype = update_dict.get("type", "")
    body: dict[str, Any] = {}

    if btype == "to_do":
        to_do_data: dict[str, Any] = {}
        if "checked" in update_dict:
            to_do_data["checked"] = bool(update_dict["checked"])
        if "text" in update_dict:
            to_do_data["rich_text"] = _rich_text(update_dict["text"])
        body["to_do"] = to_do_data
    elif btype in ("paragraph", "heading_1", "heading_2", "heading_3",
                    "bulleted_list_item", "numbered_list_item", "quote"):
        if "text" in update_dict:
            body[btype] = {"rich_text": _rich_text(update_dict["text"])}
    else:
        return {"success": False, "error": f"未対応のブロックタイプ: {btype}"}

    client = _client()
    resp = await client.patch(f"{_BASE_URL}/blocks/{block_id}", json=body)
    if resp.status_code != 200:
        return {"success": False, "error": _safe_error(resp.status_code, resp.text)}
    return {"success": True, "output": f"ブロック更新完了: {block_id}"}


async def append_blocks(page_id: str, blocks_json: str) -> dict[str, Any]:
    """ページにブロックを追記する

    Args:
        page_id: 追記先のページID
        blocks_json: ブロック配列のJSON文字列
            例: [{"type": "to_do", "text": "タスク1"}, {"type": "paragraph", "text": "メモ"}]
            対応type: paragraph, heading_1/2/3, to_do, bulleted_list_item, numbered_list_item
    """
    try:
        blocks_list = json.loads(blocks_json)
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"JSON解析エラー: {e}"}

    children = []
    for b in blocks_list:
        btype = b.get("type", "paragraph")
        text = b.get("text", "")
        block_obj: dict[str, Any] = {"object": "block", "type": btype}

        if btype == "to_do":
            block_obj["to_do"] = {
                "rich_text": _rich_text(text),
                "checked": b.get("checked", False),
            }
        elif btype in ("paragraph", "heading_1", "heading_2", "heading_3",
                        "bulleted_list_item", "numbered_list_item", "quote"):
            block_obj[btype] = {"rich_text": _rich_text(text)}
        elif btype == "divider":
            block_obj["divider"] = {}
        else:
            block_obj["type"] = "paragraph"
            block_obj["paragraph"] = {"rich_text": _rich_text(text)}

        children.append(block_obj)

    client = _client()
    resp = await client.patch(
        f"{_BASE_URL}/blocks/{page_id}/children",
        json={"children": children},
    )
    if resp.status_code != 200:
        return {"success": False, "error": _safe_error(resp.status_code, resp.text)}

    return {
        "success": True,
        "output": f"{len(children)}個のブロックを追加しました",
    }


# =============================================
# 検索
# =============================================

async def search_notion(query: str) -> dict[str, Any]:
    """Notionワークスペース全体を検索"""
    client = _client()
    resp = await client.post(f"{_BASE_URL}/search", json={"query": query, "page_size": 10})
    if resp.status_code != 200:
        return {"success": False, "error": _safe_error(resp.status_code, resp.text)}

    results = []
    for item in resp.json().get("results", []):
        obj_type = item.get("object", "")
        title = ""
        if obj_type == "page":
            for prop in item.get("properties", {}).values():
                if prop.get("type") == "title":
                    title = _parse_rich_text(prop.get("title", []))
                    break
        elif obj_type == "database":
            title = _parse_rich_text(item.get("title", []))

        results.append({
            "type": obj_type, "id": item["id"],
            "title": title, "url": item.get("url", ""),
        })
    return {"success": True, "results": results, "count": len(results)}
