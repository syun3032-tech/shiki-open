"""Agent Loop（心臓部）- ReAct Architecture

Claude Code方式のシンプルなReActループ:
  while ツール呼び出しがある:
      ツール実行 → 結果をフィードバック → 次のアクション
  テキスト応答が来たらループ終了

Plan-and-Executeは廃止。計画と実行を分離せず、
Gemini自身に「次に何をすべきか」を毎ステップ判断させる。
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path

from config import GEMINI_API_KEY, GEMINI_MODEL, MAX_ITERATIONS
from llm import get_client, LLMResponse, ContentPart
from llm.types import LLMConfig, ToolCall
from agent.context import build_system_prompt, build_system_prompt_with_skills
from agent.router import select_model, select_model_for_iteration
from agent.episodic_memory import (
    record_episode, find_relevant_episodes, format_episodes_for_prompt,
)
from agent.tools_config import (
    TOOL_FUNCTIONS, GEMINI_TOOLS, GUI_TOOLS,
    TOOL_STATUS_MESSAGES, GUI_WAIT_TIMES,
    validate_tool_args, scale_coordinates,
    TOOL_LEVELS, ToolLevel,
)
from agent.history import (
    add_to_history, build_history_contents, auto_save_session,
    update_scratchpad, clear_scratchpad,
    compress_old_screenshots, COMPRESS_AFTER_ITERATIONS,
    record_failure, get_failure_patterns,
    record_tool_call,
    load_plan, clear_plan,
)
from security.output_validator import sanitize_response
from security.anomaly_detector import anomaly_detector
from security.gate import SecurityGate
from tools.screenshot import did_screen_change
from agent.skills import find_skill, extract_query_from_message
from agent.playbook import find_playbook, record_playbook, update_playbook, format_as_fewshot
from agent.debug_engine import reset_debug_state, record_debug_failure, get_debug_injection
from agent.wal import wal_write, wal_complete, wal_recover
from agent.correction_detector import detect_correction, extract_correction_content

logger = logging.getLogger("shiki.agent")


# === 失敗分類 ===
def _classify_failure(error: str) -> str:
    """エラーを分類してリトライ戦略を決定
    Returns: "transient" | "permanent" | "permission"
    """
    error_lower = error.lower()
    # 一時的エラー（リトライ可能）
    transient = ("timeout", "タイムアウト", "connection", "temporarily", "rate limit", "503", "429")
    if any(t in error_lower for t in transient):
        return "transient"
    # 権限エラー（別アプローチ必要）
    permission = ("permission", "許可", "ブロック", "blocked", "denied", "forbidden", "403")
    if any(t in error_lower for t in permission):
        return "permission"
    # 恒久的エラー（同じ方法でリトライしても無駄）
    return "permanent"

# LLMクライアント（プロバイダー自動選択）
_llm_client = None

def _get_llm_client():
    global _llm_client
    if _llm_client is None:
        _llm_client = get_client()
    return _llm_client

# SecurityGate（全ツール実行のゲートキーパー）
from config import LOG_DIR
_security_gate = SecurityGate(LOG_DIR)

# 進捗通知用
_progress_callback = None
_last_progress_time = 0.0
_PROGRESS_COOLDOWN = 3.0
_progress_count = 0
_MAX_PROGRESS_PER_TASK = 30

# ReActループタイムアウト（秒）
# delegate_to_claudeが最大900秒かかるため、余裕を持たせる
_REACT_TIMEOUT = 600

# スクリーンショットキャッシュ
_last_screenshot_time = 0.0
_last_screenshot_path: str | None = None
_SCREENSHOT_CACHE_TTL = 1.0  # GUI操作後の変化を確実に捉えるため短縮

# スクラッチパッド更新間隔
_SCRATCHPAD_UPDATE_INTERVAL = 5


def set_progress_callback(callback):
    global _progress_callback, _last_progress_time
    _progress_callback = callback
    _last_progress_time = 0.0


# 後方互換性（main.pyから使用）
_auto_save_session = auto_save_session


async def _execute_tool(tool_name: str, tool_args: dict) -> dict:
    """ツール実行（SecurityGate + バリデーション + キャッシュ + 失敗記録）"""
    global _last_progress_time, _last_screenshot_time, _last_screenshot_path

    tool_fn = TOOL_FUNCTIONS.get(tool_name)
    if not tool_fn:
        return {"error": f"未知のツール: {tool_name}"}

    # 座標スケーリング（スクショ1024px → 実画面サイズ）
    raw_args = dict(tool_args)  # スケーリング前を保存
    tool_args = scale_coordinates(tool_name, tool_args)

    validation_error = validate_tool_args(tool_name, tool_args)
    if validation_error:
        logger.warning(f"Arg validation failed: {tool_name} - {validation_error}")
        return {"error": validation_error}

    # SecurityGate: 権限チェック + 異常検知（TOOL_LEVELSが正）
    tool_level = TOOL_LEVELS.get(tool_name, ToolLevel.DESTRUCTIVE)
    approved, reason = await _security_gate.check_permission(tool_name, tool_args)
    if not approved:
        logger.warning(f"SecurityGate BLOCKED: {tool_name} - {reason}")
        return {"error": f"ブロック: {reason}", "blocked": True}

    # 座標スケーリングされた場合はbefore/afterを出力
    if raw_args != tool_args:
        logger.info(f"Tool call: {tool_name}(raw={raw_args} → scaled={tool_args}) [level={tool_level.value}]")
    else:
        logger.info(f"Tool call: {tool_name}({tool_args}) [level={tool_level.value}]")

    # 進捗通知（5秒クールダウン + 最大5回/タスク）
    global _progress_count
    now = time.time()
    if (_progress_callback
        and (now - _last_progress_time) >= _PROGRESS_COOLDOWN
        and _progress_count < _MAX_PROGRESS_PER_TASK):
        status_msg = TOOL_STATUS_MESSAGES.get(tool_name, "処理中...")
        if tool_name == "open_app" and "app_name" in tool_args:
            status_msg = f"{tool_args['app_name']}を起動中..."
        try:
            await _progress_callback(status_msg)
            _last_progress_time = now
            _progress_count += 1
        except Exception:
            pass

    # GUI操作後はスクショキャッシュを無効化
    if tool_name in GUI_TOOLS:
        _last_screenshot_time = 0.0

    # スクリーンショットキャッシュ
    if tool_name == "take_screenshot":
        now_ss = time.time()
        if _last_screenshot_path and (now_ss - _last_screenshot_time) < _SCREENSHOT_CACHE_TTL:
            logger.info(f"Screenshot cache hit ({now_ss - _last_screenshot_time:.1f}s ago)")
            return {"success": True, "path": _last_screenshot_path, "cached": True}

    # 実行 + ログ記録
    start = time.monotonic()
    result = await tool_fn(**tool_args)
    elapsed_ms = int((time.monotonic() - start) * 1000)
    logger.info(f"Tool result: {str(result)[:200]} ({elapsed_ms}ms)")

    # SecurityGateでログ記録
    _security_gate.action_logger.log(
        tool_name, tool_level, tool_args,
        str(result)[:500], True, elapsed_ms
    )

    if tool_name in ("take_screenshot", "crop_screenshot") and result.get("path"):
        _last_screenshot_time = time.time()
        _last_screenshot_path = result["path"]

    success = not (result.get("error") or (result.get("success") is False))
    if not success:
        error_msg = result.get("error", "unknown")
        failure_type = _classify_failure(error_msg)
        result["_failure_type"] = failure_type
        record_failure(tool_name, tool_args, error_msg)
        # デバッグエンジンに失敗を記録
        try:
            record_debug_failure(tool_name, error_msg, failure_type)
        except Exception:
            pass

    # WAL: ツール実行後
    wal_write("post_tool", tool_name=tool_name, success=success)

    # スキル進化用のツール実行記録
    record_tool_call(tool_name, tool_args, success)

    return result


def _try_record_playbook(user_message: str, tool_calls: list[dict]):
    """成功したタスクをプレイブックとして自動記録"""
    try:
        keywords = re.findall(r'[ァ-ヶー]+|[a-zA-Z]+|[一-龥]+', user_message)
        keywords = [kw.lower() for kw in keywords if len(kw) >= 2]

        if not keywords:
            return

        existing = find_playbook(user_message)
        if existing:
            update_playbook(existing[0]["id"], tool_calls)
            logger.info(f"Playbook updated: {existing[0]['name']}")
        else:
            name = user_message[:20].strip()
            record_playbook(name, keywords, tool_calls)
            logger.info(f"Playbook auto-recorded: {name}")
    except Exception as e:
        logger.warning(f"Playbook recording failed: {e}")


def _adaptive_thinking_budget(model: str, use_tools: bool) -> int:
    """タスク・モデルに応じた思考トークン予算を返す"""
    if "flash" in model:
        return 1024 if use_tools else 512
    # Pro: ツール使用時はしっかり考える、会話時は控えめ
    return 8192 if use_tools else 2048


async def _call_llm(contents, system_prompt: str, use_tools: bool = True, model: str | None = None) -> LLMResponse | None:
    """LLM API呼び出し（プロバイダー非依存）"""
    target_model = model or GEMINI_MODEL
    budget = _adaptive_thinking_budget(target_model, use_tools)

    # ツール定義の変換（GEMINI_TOOLSからToolDefinition形式へ）
    tool_defs = []
    if use_tools:
        tool_defs = _get_tool_definitions()

    config = LLMConfig(
        model=target_model,
        temperature=0.0 if use_tools else 0.7,
        max_tokens=2048,
        system_prompt=system_prompt,
        tools=tool_defs,
        thinking_budget=budget,
    )

    llm = _get_llm_client()
    response = await llm.generate(config, contents)

    if response is None:
        return None

    if response.finish_reason == "safety":
        logger.warning("Response blocked by safety filter")
        return None

    return response


def _get_tool_definitions():
    """GEMINI_TOOLSからToolDefinition形式に変換（キャッシュ付き）"""
    if hasattr(_get_tool_definitions, "_cache"):
        return _get_tool_definitions._cache

    from llm.types import ToolDefinition

    # GEMINI_TOOLSからfunction_declarationsを取得
    tool_defs = []
    for fd in GEMINI_TOOLS.function_declarations:
        # genai.types.Schema → JSON Schema に逆変換
        params = _genai_schema_to_json(fd.parameters) if fd.parameters else {"type": "object", "properties": {}}
        tool_defs.append(ToolDefinition(
            name=fd.name,
            description=fd.description or "",
            parameters=params,
        ))

    _get_tool_definitions._cache = tool_defs
    return tool_defs


def _genai_schema_to_json(schema) -> dict:
    """genai.types.Schema → JSON Schema dict に変換"""
    result = {}
    type_str = str(schema.type).split(".")[-1] if schema.type else "STRING"
    result["type"] = type_str.lower()

    if schema.description:
        result["description"] = schema.description
    if schema.enum:
        result["enum"] = list(schema.enum)
    if schema.properties:
        result["properties"] = {
            k: _genai_schema_to_json(v)
            for k, v in schema.properties.items()
        }
    if schema.required:
        result["required"] = list(schema.required)
    if schema.items:
        result["items"] = _genai_schema_to_json(schema.items)

    return result


async def _handle_auto_screenshot(contents: list, function_calls: list, image_path: str | None) -> str | None:
    """GUI操作後の自動スクリーンショット処理"""
    gui_tools_used = {tc.name for tc in function_calls} & GUI_TOOLS
    if not gui_tools_used:
        return image_path

    if "take_screenshot" in {tc.name for tc in function_calls}:
        return image_path

    # スマートウェイト: ツール種別に応じた待機時間
    wait_times = dict(GUI_WAIT_TIMES)
    for tc in function_calls:
        if tc.name == "press_key":
            key_val = tc.args.get("key", "").lower()
            if key_val in ("return", "enter"):
                wait_times["press_key"] = 1.0

    gui_wait = max(wait_times.get(t, 0.5) for t in gui_tools_used)
    await asyncio.sleep(gui_wait)

    ss_result = await _execute_tool("take_screenshot", {})
    if ss_result.get("path"):
        image_path = ss_result["path"]
        try:
            screen_changed = did_screen_change(image_path)
            change_msg = "画面が変化した。" if screen_changed else "画面に変化なし。操作が効かなかった可能性あり。"
            img_bytes = Path(image_path).read_bytes()
            contents.append({
                "role": "user",
                "parts": [
                    ContentPart(image_bytes=img_bytes, mime_type="image/jpeg"),
                    ContentPart(text=f"[自動スクショ] GUI操作後の画面。{change_msg}期待通りか確認して、次のステップに進んで。"),
                ],
            })
        except (OSError, IOError) as e:
            logger.warning(f"Auto-screenshot content append failed: {e}")

    return image_path


def _append_screenshot_to_contents(contents: list, image_path: str, function_calls: list):
    """手動スクリーンショットの結果をcontentsに追加"""
    if not image_path:
        return
    if not any(tc.name == "take_screenshot" for tc in function_calls):
        return
    try:
        img_bytes = Path(image_path).read_bytes()
        contents.append({
            "role": "user",
            "parts": [
                ContentPart(image_bytes=img_bytes, mime_type="image/jpeg"),
                ContentPart(text="これが今のPC画面。"),
            ],
        })
    except (OSError, IOError) as e:
        logger.warning(f"Screenshot content append failed: {e}")


async def process_message(
    user_message: str,
    image_bytes: bytes | None = None,
    iteration_callback=None,
) -> dict:
    """ユーザーメッセージを処理（ReActループ）

    Claude Code方式:
    1. スキルマッチ（最速パス）
    2. Geminiにメッセージ + 全ツールを渡す
    3. ツール呼び出しがあれば実行 → 結果をフィードバック → 繰り返し
    4. テキスト応答が来たら終了

    Args:
        iteration_callback: イテレーションごとに呼ばれるコールバック。
            async fn(iteration, tool_calls) -> str | None を受け取る。
            文字列を返すとシステムプロンプトに割り込み指示として追加される。
            "ABORT" を返すとループを即座に中断する。
    """
    if anomaly_detector.should_shutdown:
        return {
            "text": "緊急停止中だよ。直接確認してくれるまで動けない。",
            "image_path": None,
        }

    global _progress_count
    _progress_count = 0

    # デバッグエンジンリセット（タスク単位）
    reset_debug_state()

    # WAL: タスク開始記録
    wal_write("task_start", user_message=user_message[:200])

    # 訂正検出（ユーザーがAIの行動を訂正していないか）
    correction = detect_correction(user_message)
    if correction:
        try:
            from agent.history import _conversation_history
            learning = await extract_correction_content(
                _conversation_history[-6:], correction
            )
            if learning:
                from memory.tiered_memory import add_memory
                add_memory(
                    learning["correct_behavior"],
                    learning.get("wrong_behavior", ""),
                    "correction",
                )
        except Exception as e:
            logger.warning(f"Correction processing failed: {e}")

    system_prompt = build_system_prompt_with_skills(user_message)

    # 前回の計画が残っていれば注入（Manus todo.md方式）
    existing_plan = load_plan()
    if existing_plan:
        system_prompt += f"\n\n# 前回の計画（続行 or 更新すること）\n{existing_plan}"
        logger.info("Previous plan injected into system prompt")

    # エピソード記憶の注入（過去の経験から学ぶ）
    episodes = find_relevant_episodes(user_message)
    if episodes:
        ep_text = format_episodes_for_prompt(episodes)
        system_prompt += "\n\n" + ep_text
        logger.info(f"Episodic memory injected: {len(episodes)} episodes")

    # スマートモデルルーティング（GenSpark Claw MoA inspired）
    base_model = select_model(user_message, has_image=image_bytes is not None)

    await add_to_history("user", user_message)
    image_path = None

    _react_start = time.monotonic()

    try:
        # === Phase 0: スキルマッチング（最速パス） ===
        if not image_bytes:
            skill = find_skill(user_message)
            if skill:
                logger.info(f"Skill matched: {skill.get('description', 'unknown')}")
                if _progress_callback:
                    try:
                        await _progress_callback(f"... {skill.get('description', '実行中')}...")
                    except Exception:
                        pass

                skill_error = None
                for step in skill["steps"]:
                    tool_name = step["tool"]
                    if "args_template" in step:
                        args = {}
                        for k, v in step["args_template"].items():
                            if "{query}" in v:
                                query = extract_query_from_message(user_message, skill.get("triggers", []))
                                args[k] = v.replace("{query}", query)
                            else:
                                args[k] = v
                    else:
                        args = step.get("args", {})
                    result = await _execute_tool(tool_name, args)
                    if result.get("error"):
                        skill_error = result["error"]
                        logger.warning(f"Skill step failed: {tool_name} → {skill_error}")
                    if tool_name == "take_screenshot" and result.get("path"):
                        image_path = result["path"]

                # エラー時はハードコード応答ではなくエラーを伝える
                if skill_error and not image_path:
                    response_text = f"ごめん、うまくいかなかった: {skill_error}"
                else:
                    response_text = skill.get("response", "やったよ。")
                await add_to_history("assistant", response_text)
                wal_complete()
                return {"text": response_text, "image_path": image_path}

        # === ReActループ ===
        # プレイブック検索 → Few-shotとしてシステムプロンプトに注入
        matched_playbooks = find_playbook(user_message)
        if matched_playbooks:
            fewshot = format_as_fewshot(matched_playbooks)
            system_prompt = system_prompt + "\n\n" + fewshot
            logger.info(f"Playbook injected: {[pb['name'] for pb in matched_playbooks]}")

        # 最近の失敗パターンを注入
        failure_patterns = get_failure_patterns()
        if failure_patterns:
            recent_failures = failure_patterns[-5:]
            failure_text = "\n".join(
                f"- {f['tool']}({f['args_summary'][:80]}) → 失敗: {f['error'][:80]}"
                for f in recent_failures
            )
            system_prompt += f"\n\n# 最近の失敗（同じ間違いを繰り返さないこと）\n{failure_text}"

        # 会話履歴 + 今回のメッセージでcontentsを構築
        contents = build_history_contents()

        user_parts = []
        if image_bytes:
            user_parts.append(ContentPart(image_bytes=image_bytes, mime_type="image/jpeg"))
        user_parts.append(ContentPart(text=user_message))
        contents.append({"role": "user", "parts": user_parts})

        # ツール依存関係: 先に呼ぶべきツールがないと精度が落ちるツール
        TOOL_DEPENDENCIES: dict[str, str] = {
            "interact_page_element": "get_page_elements",
        }
        # 呼び出し済みツールの追跡（依存チェック用）
        tools_ever_called: set[str] = set()

        # リトライ検知 + ツール呼び出し記録
        tool_call_counts: dict[str, int] = {}
        executed_tool_calls: list[dict] = []
        MAX_RETRY = 3
        consecutive_nulls = 0

        for iteration in range(MAX_ITERATIONS):
            # タイムアウトチェック
            elapsed = time.monotonic() - _react_start
            if elapsed > _REACT_TIMEOUT:
                logger.warning(f"ReAct timeout after {elapsed:.0f}s, {iteration} iterations")
                msg = "ちょっと時間かかりすぎちゃった。途中までだけど結果を返すね。"
                await add_to_history("assistant", msg)
                wal_complete()
                return {"text": msg, "image_path": image_path}

            logger.info(f"ReAct iteration {iteration + 1}/{MAX_ITERATIONS}")

            # イテレーションコールバック（割り込みチェック等）
            if iteration_callback and iteration > 0:
                try:
                    interrupt = await iteration_callback(iteration, executed_tool_calls)
                    if interrupt == "ABORT":
                        msg = "タスクが中断されました。"
                        await add_to_history("assistant", msg)
                        wal_complete()
                        return {"text": msg, "image_path": image_path}
                    elif interrupt:
                        # 割り込み指示をcontentsに注入
                        contents.append({
                            "role": "user",
                            "parts": [ContentPart(text=f"[割り込み指示] {interrupt}")],
                        })
                        logger.info(f"Interrupt injected at iteration {iteration}: {interrupt[:100]}")
                except Exception as e:
                    logger.warning(f"Iteration callback error: {e}")

            # コンテキスト圧縮
            if iteration == COMPRESS_AFTER_ITERATIONS:
                contents = compress_old_screenshots(contents)

            # モデルルーティング（Flashで詰まったらProにエスカレート）
            current_model = select_model_for_iteration(iteration, base_model)

            # デバッグ注入（失敗蓄積時のみ、通常時は空文字）
            try:
                debug_injection = get_debug_injection()
            except Exception as e:
                logger.warning(f"Debug injection failed: {e}")
                debug_injection = ""
            effective_prompt = system_prompt + debug_injection if debug_injection else system_prompt

            # WAL: LLM呼び出し前
            wal_write("pre_llm", iteration=iteration, model=current_model)
            response = await _call_llm(contents, effective_prompt, model=current_model)

            # 空レスポンス → 連続なら即終了
            if response is None:
                consecutive_nulls += 1
                if consecutive_nulls >= 2:
                    fallback = "LLM APIが応答しない...しばらく待ってからもう一度試してみて。"
                    await add_to_history("assistant", fallback)
                    wal_complete()
                    return {"text": fallback, "image_path": image_path}
                logger.warning(f"Null response (consecutive: {consecutive_nulls}), retrying...")
                continue
            consecutive_nulls = 0

            function_calls = response.tool_calls  # list[ToolCall]
            text_parts = [p.text for p in response.parts if p and p.text]

            # === テキスト応答 → ループ終了 ===
            if not function_calls:
                response_text = " ".join(text_parts) if text_parts else ""
                if not response_text:
                    response_text = response.text or ""
                if not response_text:
                    response_text = "やったよ。" if tool_call_counts else "うーん..."
                safe_text, leaks = sanitize_response(response_text)
                if leaks:
                    logger.critical(f"Response contained leaks: {leaks}")
                await add_to_history("assistant", safe_text)

                # プレイブック自動記録（3ステップ以上の成功タスク）
                if len(executed_tool_calls) >= 3:
                    _try_record_playbook(user_message, executed_tool_calls)

                # エピソード記憶に記録
                if executed_tool_calls:
                    tools_used = list(dict.fromkeys(tc["tool"] for tc in executed_tool_calls))
                    record_episode(
                        task=user_message,
                        tools_used=tools_used,
                        outcome=f"成功: {safe_text[:100]}",
                        success=True,
                    )

                clear_scratchpad()
                clear_plan()
                wal_complete()
                return {"text": safe_text, "image_path": image_path}

            # === ツール実行 → 結果をフィードバック ===
            function_response_parts = []

            # リトライ制限チェック
            parsed_calls = []
            for tc in function_calls:
                tool_name = tc.name
                tool_args = dict(tc.args) if tc.args else {}
                call_key = f"{tool_name}:{json.dumps(tool_args, sort_keys=True)}"
                tool_call_counts[call_key] = tool_call_counts.get(call_key, 0) + 1
                # 恒久的エラーは即座に諦める（リトライしても同じ）
                last_failure_type = None
                if tool_call_counts[call_key] > 1:
                    # 前回の結果を確認（ツール名+引数の両方が一致するもの）
                    for tc in reversed(executed_tool_calls):
                        if tc.get("tool") == tool_name and tc.get("args") == tool_args:
                            last_failure_type = tc.get("_failure_type")
                            break
                max_retry = 1 if last_failure_type == "permanent" else MAX_RETRY
                if tool_call_counts[call_key] > max_retry:
                    msg = f"ごめん、{tool_call_counts[call_key]-1}回試したけどうまくいかなかった。"
                    await add_to_history("assistant", msg)
                    record_episode(
                        task=user_message,
                        tools_used=[tc["tool"] for tc in executed_tool_calls[-5:]],
                        outcome=f"失敗: {tool_name}を{MAX_RETRY}回リトライ",
                        success=False,
                        lesson=f"{tool_name}が同じ引数で繰り返し失敗。別のアプローチを試すべき",
                    )
                    wal_complete()
                    return {"text": msg, "image_path": image_path}
                parsed_calls.append((tool_name, tool_args))

            # 依存関係チェック: 前提ツールが呼ばれていなければ警告を注入
            for tool_name, tool_args in parsed_calls:
                dep = TOOL_DEPENDENCIES.get(tool_name)
                if dep and dep not in tools_ever_called:
                    logger.warning(f"Dependency missing: {tool_name} requires {dep} first")
                    # 強制ブロックせず、結果に警告を付与（Geminiが学習する）

            # 並列実行判定: 全ツールがREADレベルなら並列
            all_read_level = all(
                TOOL_LEVELS.get(name) == ToolLevel.READ
                for name, _ in parsed_calls
            )

            if all_read_level and len(parsed_calls) > 1:
                logger.info(f"Parallel execution: {[name for name, _ in parsed_calls]}")
                results = await asyncio.gather(
                    *[_execute_tool(name, args) for name, args in parsed_calls]
                )
                for (tool_name, tool_args), result in zip(parsed_calls, results):
                    tools_ever_called.add(tool_name)
                    # 依存関係警告を結果に注入
                    dep = TOOL_DEPENDENCIES.get(tool_name)
                    if dep and dep not in tools_ever_called:
                        result["_warning"] = f"先に{dep}を呼んでからこのツールを使うと精度が上がる"
                    if tool_name != "take_screenshot":
                        tc_entry = {"tool": tool_name, "args": tool_args}
                        if result.get("_failure_type"):
                            tc_entry["_failure_type"] = result["_failure_type"]
                        executed_tool_calls.append(tc_entry)
                    if tool_name == "take_screenshot" and result.get("path"):
                        image_path = result["path"]
                    function_response_parts.append({
                        "function_response": {"name": tool_name, "response": result}
                    })
            else:
                for tool_name, tool_args in parsed_calls:
                    # 依存関係チェック: 前提ツール未実行なら自動で先に呼ぶ
                    dep = TOOL_DEPENDENCIES.get(tool_name)
                    if dep and dep not in tools_ever_called:
                        # 依存ツールに必要な引数を元ツールから引き継ぐ
                        dep_args = {}
                        for shared_key in ("url",):
                            if shared_key in tool_args:
                                dep_args[shared_key] = tool_args[shared_key]
                        if dep_args:
                            logger.info(f"Auto-calling dependency: {dep}({dep_args}) before {tool_name}")
                            dep_result = await _execute_tool(dep, dep_args)
                            tools_ever_called.add(dep)
                            function_response_parts.append({
                                "function_response": {"name": dep, "response": dep_result}
                            })
                        else:
                            logger.warning(f"Dependency {dep} skipped: no shared args from {tool_name}")

                    result = await _execute_tool(tool_name, tool_args)
                    tools_ever_called.add(tool_name)
                    if tool_name != "take_screenshot":
                        tc_entry = {"tool": tool_name, "args": tool_args}
                        if result.get("_failure_type"):
                            tc_entry["_failure_type"] = result["_failure_type"]
                        executed_tool_calls.append(tc_entry)
                    if tool_name == "take_screenshot" and result.get("path"):
                        image_path = result["path"]
                    function_response_parts.append({
                        "function_response": {"name": tool_name, "response": result}
                    })

            # コンテキストに追加（アシスタントのレスポンス + ツール結果）
            contents.append({"role": "assistant", "parts": response.parts})
            contents.append({"role": "user", "parts": function_response_parts})

            # GUI操作後の自動スクリーンショット
            gui_tools_used = {tc.name for tc in function_calls} & GUI_TOOLS
            if gui_tools_used:
                image_path = await _handle_auto_screenshot(contents, function_calls, image_path)
            elif image_path:
                _append_screenshot_to_contents(contents, image_path, function_calls)

            # スクラッチパッド更新
            if iteration > 0 and iteration % _SCRATCHPAD_UPDATE_INTERVAL == 0:
                last_result_str = str(result)[:300] if 'result' in locals() else ""
                update_scratchpad(user_message, iteration, executed_tool_calls, last_result_str)

        # イテレーション上限
        clear_scratchpad()
        clear_plan()
        wal_complete()
        fallback = "完了したよ。" if tool_call_counts else "処理が長くなりすぎた..."
        await add_to_history("assistant", fallback)
        return {"text": fallback, "image_path": image_path}

    except Exception as e:
        logger.error(f"Agent loop error: {e}", exc_info=True)
        anomaly_detector.record_event("failed_tool_calls", f"agent_loop: {e}")
        wal_complete()
        return {
            "text": f"ごめん、エラーが出た: {type(e).__name__}",
            "image_path": None,
        }
