"""動的ツール生成 — 識が実行時に新しいツールを作成・登録する

既存ツールでは対応できないタスクに対して、識自身がPythonの
asyncツール関数を動的に生成し、Gemini Function Callingに登録する。

セキュリティ:
- astモジュールによるコード静的解析
- 危険なモジュール・組み込み関数のブロック（code_executor.pyと同等）
- コードサイズ制限（10KB）
- 動的ツール数上限（20個）
- 全ツールはELEVATEDレベルで登録（LINE通知付き）
"""

import ast
import json
import logging
import re
from datetime import datetime
from pathlib import Path

import google.genai as genai

logger = logging.getLogger("shiki.agent")

# === 設定 ===
_MAX_CODE_SIZE = 10_000  # 10KB
_MAX_DYNAMIC_TOOLS = 20
_STORAGE_DIR = Path(__file__).resolve().parent.parent / ".ritsu" / "dynamic_tools"

# 予約済みツール名（既存ツールとの衝突防止）
_RESERVED_NAMES = frozenset({
    "take_screenshot", "crop_screenshot", "open_app", "open_url",
    "open_url_with_profile", "get_frontmost_app", "get_running_apps",
    "set_volume", "toggle_dark_mode", "show_notification", "type_text",
    "press_key", "scroll", "click", "double_click", "right_click",
    "get_screen_size", "read_file", "write_file", "list_directory",
    "move_file", "run_command", "drag", "get_browser_info",
    "get_window_info", "add_reminder", "list_reminders", "delete_reminder",
    "browse_url", "search_web", "get_page_text", "get_page_elements",
    "interact_page_element", "get_accessibility_tree", "execute_code",
    "update_plan", "delegate_to_claude", "schedule_task",
    "list_scheduled_tasks", "delete_scheduled_task",
    "dispatch_agents", "check_revenue", "get_revenue_summary",
    # ツール生成系自身
    "generate_tool", "list_dynamic_tools", "delete_dynamic_tool",
})

# 危険なモジュール（code_executor.pyと同等）
_BLOCKED_MODULES = frozenset({
    "os", "subprocess", "shutil", "signal", "ctypes",
    "socket", "http", "urllib", "requests", "httpx", "aiohttp",
    "importlib", "runpy", "code", "codeop",
    "multiprocessing", "threading", "concurrent",
    "pickle", "shelve", "marshal",
    "webbrowser", "antigravity",
    "pathlib", "tempfile", "glob",
    "sqlite3", "dbm",
    "sys",
})

# 危険な組み込み関数・属性
_BLOCKED_BUILTINS = frozenset({
    "exec", "eval", "compile", "__import__", "globals", "locals",
    "breakpoint", "exit", "quit",
})

# 危険な属性アクセス
_BLOCKED_ATTRS = frozenset({
    "__subclasses__", "__bases__", "__mro__", "__class__",
    "__globals__", "__code__", "__closure__",
})

# 動的ツールのランタイムレジストリ
_dynamic_tools: dict[str, dict] = {}


# === 名前バリデーション ===
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,49}$")


def _validate_name(name: str) -> str | None:
    """ツール名のバリデーション。エラーがあれば文字列を返す。"""
    if not _NAME_PATTERN.match(name):
        return (
            f"ツール名 '{name}' が不正。"
            "snake_case、3-50文字、小文字英数字とアンダースコアのみ。"
        )
    if name in _RESERVED_NAMES:
        return f"ツール名 '{name}' は予約済み。別の名前を使ってください。"
    return None


# === コード静的解析 ===
def _validate_code(name: str, code: str) -> str | None:
    """コードのAST解析によるセキュリティチェック。エラーがあれば文字列を返す。"""
    if len(code.encode("utf-8")) > _MAX_CODE_SIZE:
        return f"コードサイズが上限（{_MAX_CODE_SIZE // 1000}KB）を超えています"

    # AST解析
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"構文エラー: {e}"

    # トップレベルにasync defが1つだけ存在し、名前が一致すること
    async_defs = [
        node for node in ast.iter_child_nodes(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    ]
    if len(async_defs) == 0:
        return f"async def {name}(...) が見つかりません"
    if len(async_defs) > 1:
        return "async関数は1つだけ定義してください"
    if async_defs[0].name != name:
        return f"関数名が不一致: 定義={async_defs[0].name}, 期待={name}"

    # トップレベルに許可されるノード種別
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.Import, ast.ImportFrom,
                             ast.Assign, ast.AnnAssign, ast.Expr)):
            pass  # OK
        else:
            return f"トップレベルに{type(node).__name__}は使用できません"

    # 全ノードを走査してセキュリティチェック
    for node in ast.walk(tree):
        # importチェック
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _BLOCKED_MODULES:
                    return f"セキュリティ: '{alias.name}' のimportは禁止"

        if isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in _BLOCKED_MODULES:
                    return f"セキュリティ: '{node.module}' からのimportは禁止"

        # 危険な組み込み関数の呼び出し
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in _BLOCKED_BUILTINS:
                    return f"セキュリティ: '{node.func.id}()' は使用できません"
            # open() もブロック
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                return "セキュリティ: 'open()' は使用できません。ファイルI/Oは既存ツールを使ってください"

        # 危険な属性アクセス
        if isinstance(node, ast.Attribute):
            if node.attr in _BLOCKED_ATTRS:
                return f"セキュリティ: '{node.attr}' へのアクセスは禁止"

    return None


# === ツール生成 ===
async def generate_tool(name: str, description: str, code: str) -> dict:
    """動的ツールを生成・登録する。

    Args:
        name: ツール名（snake_case、3-50文字）
        description: ツールの説明（Gemini function callingに表示）
        code: async def関数を定義するPythonコード

    Returns:
        {"success": True, "tool_name": name} or {"success": False, "error": "..."}
    """
    # 1. ツール数上限チェック
    if name not in _dynamic_tools and len(_dynamic_tools) >= _MAX_DYNAMIC_TOOLS:
        return {
            "success": False,
            "error": f"動的ツール数が上限（{_MAX_DYNAMIC_TOOLS}個）に達しています。"
                     "不要なツールを delete_dynamic_tool で削除してください。",
        }

    # 2. 名前バリデーション
    name_error = _validate_name(name)
    if name_error:
        return {"success": False, "error": name_error}

    # 3. コード静的解析
    code_error = _validate_code(name, code)
    if code_error:
        return {"success": False, "error": code_error}

    # 4. コードをexecして関数オブジェクトを取得（コンパイル検証）
    namespace: dict = {}
    try:
        exec(compile(code, f"<dynamic_tool:{name}>", "exec"), namespace)
    except Exception as e:
        return {"success": False, "error": f"コードのコンパイル/実行エラー: {type(e).__name__}: {e}"}

    func = namespace.get(name)
    if func is None:
        return {"success": False, "error": f"関数 '{name}' がnamespaceに見つかりません"}
    if not callable(func):
        return {"success": False, "error": f"'{name}' は呼び出し可能ではありません"}

    # 5. 登録（既存ツールシステムに追加）
    try:
        _register_tool(name, description, func)
    except Exception as e:
        return {"success": False, "error": f"登録エラー: {e}"}

    # 6. 永続化
    tool_data = {
        "name": name,
        "description": description,
        "code": code,
        "created_at": datetime.now().isoformat(),
        "usage_count": 0,
    }
    _dynamic_tools[name] = tool_data
    _save_tool(name, tool_data)

    logger.info(f"Dynamic tool created: {name}")
    return {"success": True, "tool_name": name}


def list_dynamic_tools() -> list[dict]:
    """登録済みの動的ツール一覧を返す。"""
    result = []
    for name, data in _dynamic_tools.items():
        result.append({
            "name": data["name"],
            "description": data["description"],
            "created_at": data["created_at"],
            "usage_count": data["usage_count"],
        })
    return result


def delete_dynamic_tool(name: str) -> bool:
    """動的ツールを削除する。"""
    if name not in _dynamic_tools:
        return False

    # ランタイムレジストリから削除
    del _dynamic_tools[name]

    # TOOL_FUNCTIONS, TOOL_LEVELS, TOOL_STATUS_MESSAGES, _REQUIRED_ARGS から削除
    from agent.tools_config import TOOL_FUNCTIONS, TOOL_STATUS_MESSAGES, _REQUIRED_ARGS
    from security.gate import TOOL_LEVELS
    TOOL_FUNCTIONS.pop(name, None)
    TOOL_LEVELS.pop(name, None)
    TOOL_STATUS_MESSAGES.pop(name, None)
    _REQUIRED_ARGS.pop(name, None)

    # GEMINI_TOOLS から削除（新しいToolオブジェクトを作成）
    from agent.tools_config import GEMINI_TOOLS
    current_declarations = list(GEMINI_TOOLS.function_declarations)
    filtered = [fd for fd in current_declarations if fd.name != name]
    GEMINI_TOOLS.function_declarations.clear()
    GEMINI_TOOLS.function_declarations.extend(filtered)

    # ファイル削除
    storage_path = _STORAGE_DIR / f"{name}.json"
    if storage_path.exists():
        storage_path.unlink()

    logger.info(f"Dynamic tool deleted: {name}")
    return True


def load_dynamic_tools():
    """保存済みの動的ツールを全て読み込み、ランタイムに登録する。

    起動時に discord_bot.py / main.py から呼ばれる。
    """
    if not _STORAGE_DIR.exists():
        logger.info("No dynamic tools directory found, skipping load")
        return

    loaded = 0
    for json_file in sorted(_STORAGE_DIR.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            name = data["name"]
            code = data["code"]
            description = data["description"]

            # バリデーション（破損ファイル対策）
            name_err = _validate_name(name)
            if name_err:
                logger.warning(f"Skipping invalid dynamic tool '{name}': {name_err}")
                continue
            code_err = _validate_code(name, code)
            if code_err:
                logger.warning(f"Skipping unsafe dynamic tool '{name}': {code_err}")
                continue

            # exec して関数を取得
            namespace: dict = {}
            exec(compile(code, f"<dynamic_tool:{name}>", "exec"), namespace)
            func = namespace.get(name)
            if func is None or not callable(func):
                logger.warning(f"Skipping dynamic tool '{name}': function not found after exec")
                continue

            _register_tool(name, description, func)
            _dynamic_tools[name] = data
            loaded += 1
        except Exception as e:
            logger.warning(f"Failed to load dynamic tool from {json_file.name}: {e}")

    if loaded > 0:
        logger.info(f"Loaded {loaded} dynamic tool(s)")


# === 内部ヘルパー ===

def _register_tool(name: str, description: str, func) -> None:
    """ツールをランタイムシステムに登録する。"""
    from agent.tools_config import TOOL_FUNCTIONS, GEMINI_TOOLS, TOOL_STATUS_MESSAGES
    from security.gate import TOOL_LEVELS, ToolLevel

    # TOOL_FUNCTIONS に追加（**kwargs でラップ）
    # 動的ツールの使用回数を記録するラッパー
    async def _wrapper(**kwargs):
        if name in _dynamic_tools:
            _dynamic_tools[name]["usage_count"] = _dynamic_tools[name].get("usage_count", 0) + 1
            _save_tool(name, _dynamic_tools[name])
        return await func(**kwargs)

    TOOL_FUNCTIONS[name] = lambda **kw: _wrapper(**kw)

    # TOOL_LEVELS に追加（ELEVATED = LINE通知付き）
    TOOL_LEVELS[name] = ToolLevel.ELEVATED

    # TOOL_STATUS_MESSAGES に追加
    TOOL_STATUS_MESSAGES[name] = f"動的ツール '{name}' を実行中..."

    # 関数シグネチャからGemini用パラメータスキーマを生成
    params_schema, required_params = _extract_params_schema(func)

    # GEMINI_TOOLS に FunctionDeclaration を追加
    fd = genai.types.FunctionDeclaration(
        name=name,
        description=description,
        parameters=genai.types.Schema(
            type="OBJECT",
            properties=params_schema,
            required=required_params if required_params else None,
        ),
    )
    GEMINI_TOOLS.function_declarations.append(fd)


def _extract_params_schema(func) -> tuple[dict, list[str]]:
    """関数シグネチャからGemini Schema用のproperties dictとrequiredリストを生成する。"""
    import inspect

    sig = inspect.signature(func)
    properties = {}
    required = []

    # Pythonの型アノテーションをGemini Schemaの型にマッピング
    type_map = {
        str: "STRING",
        int: "INTEGER",
        float: "NUMBER",
        bool: "BOOLEAN",
    }

    for param_name, param in sig.parameters.items():
        # selfやkwargsは無視
        if param_name in ("self", "cls"):
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        # 型推定
        annotation = param.annotation
        schema_type = "STRING"  # デフォルト
        if annotation != inspect.Parameter.empty:
            schema_type = type_map.get(annotation, "STRING")

        properties[param_name] = genai.types.Schema(
            type=schema_type,
            description=f"パラメータ: {param_name}",
        )

        # デフォルト値がない = 必須パラメータ
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return properties, required


def _save_tool(name: str, data: dict) -> None:
    """ツールデータをJSONファイルに保存する。"""
    _STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    storage_path = _STORAGE_DIR / f"{name}.json"
    storage_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
