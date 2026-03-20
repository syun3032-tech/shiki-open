"""収益トラッカー - Lancers/CrowdWorks報酬管理

フリーランスプラットフォームの報酬データを取得・記録・集計する。
ブラウザツール（Playwright）でログイン済みセッションを使い、
報酬管理ページからデータをスクレイピング。

ローカルストレージ: .ritsu/revenue/
- history.json: 全履歴（append-only）
- monthly/{YYYY-MM}.json: 月別詳細

セキュリティ:
- ログインセッション未確立時は適切なエラーメッセージを返す
- 金額データはローカルのみ保存（外部送信しない）
- Notion連携はオプション
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("shiki.tools")

# ストレージパス
_REVENUE_DIR = Path(__file__).parent.parent / ".ritsu" / "revenue"
_HISTORY_FILE = _REVENUE_DIR / "history.json"
_MONTHLY_DIR = _REVENUE_DIR / "monthly"

# プラットフォーム設定
_PLATFORMS = {
    "lancers": {
        "name": "Lancers",
        "domain": "lancers.jp",
        "earnings_url": "https://www.lancers.jp/mypage/payment",
        "login_url": "https://www.lancers.jp/user/login",
        "login_check_keywords": ["マイページ", "報酬", "受注", "プロフィール"],
        "not_logged_in_keywords": ["ログイン", "新規会員登録", "パスワード"],
    },
    "crowdworks": {
        "name": "CrowdWorks",
        "domain": "crowdworks.jp",
        "earnings_url": "https://crowdworks.jp/mypage/payments",
        "login_url": "https://crowdworks.jp/login",
        "login_check_keywords": ["マイページ", "報酬", "受注実績"],
        "not_logged_in_keywords": ["ログイン", "新規登録", "パスワード"],
    },
}


def _ensure_dirs():
    """ストレージディレクトリを作成"""
    _REVENUE_DIR.mkdir(parents=True, exist_ok=True)
    _MONTHLY_DIR.mkdir(parents=True, exist_ok=True)


def _load_history() -> list[dict]:
    """履歴データを読み込み"""
    if _HISTORY_FILE.exists():
        try:
            return json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Revenue history load failed: {e}")
    return []


def _save_history(history: list[dict]):
    """履歴データを保存（append-only設計: 既存データは消さない）"""
    _ensure_dirs()
    _HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _save_monthly(year_month: str, data: dict):
    """月別データを保存"""
    _ensure_dirs()
    monthly_file = _MONTHLY_DIR / f"{year_month}.json"
    _MONTHLY_DIR.mkdir(parents=True, exist_ok=True)
    monthly_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parse_amount(text: str) -> int | None:
    """日本語テキストから金額を抽出（円表記）

    例: "123,456円" -> 123456, "¥1,234" -> 1234, "1234" -> 1234
    """
    if not text:
        return None
    # カンマ・スペース除去
    cleaned = text.replace(",", "").replace(" ", "").replace("　", "")
    # 円/¥マーク前後の数字を抽出
    patterns = [
        r"¥?\s*(\d+)\s*円?",
        r"(\d+)\s*円",
        r"¥\s*(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    # 数字のみの場合
    match = re.search(r"(\d+)", cleaned)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return None


def _extract_amounts_from_text(text: str, platform: str) -> dict:
    """ページテキストから報酬関連の金額を抽出

    Returns:
        {
            "current_month": int | None,  # 今月の報酬
            "total": int | None,          # 累計報酬
            "unpaid": int | None,         # 未払い報酬
            "recent_jobs": [{"title": str, "amount": int}],  # 最近の案件
            "raw_matches": [str],         # デバッグ用: マッチした行
        }
    """
    result = {
        "current_month": None,
        "total": None,
        "unpaid": None,
        "recent_jobs": [],
        "raw_matches": [],
    }

    lines = text.split("\n")
    lines = [line.strip() for line in lines if line.strip()]

    for i, line in enumerate(lines):
        lower_line = line.lower()

        # 今月の報酬
        if any(kw in line for kw in ["今月の報酬", "今月の売上", "当月報酬", "今月報酬"]):
            # この行か次の行に金額がある
            amount = _parse_amount(line)
            if amount is None and i + 1 < len(lines):
                amount = _parse_amount(lines[i + 1])
            if amount is not None:
                result["current_month"] = amount
                result["raw_matches"].append(f"今月: {line}")

        # 累計報酬
        if any(kw in line for kw in ["累計報酬", "累計売上", "総報酬", "合計報酬"]):
            amount = _parse_amount(line)
            if amount is None and i + 1 < len(lines):
                amount = _parse_amount(lines[i + 1])
            if amount is not None:
                result["total"] = amount
                result["raw_matches"].append(f"累計: {line}")

        # 未払い報酬
        if any(kw in line for kw in ["未払い", "振込予定", "出金可能", "未出金"]):
            amount = _parse_amount(line)
            if amount is None and i + 1 < len(lines):
                amount = _parse_amount(lines[i + 1])
            if amount is not None:
                result["unpaid"] = amount
                result["raw_matches"].append(f"未払い: {line}")

        # 案件と金額のペア検出（「案件名 ... ¥XX,XXX」パターン）
        amount_in_line = _parse_amount(line)
        if amount_in_line and amount_in_line >= 100:  # 100円以上の金額行
            # 案件タイトルっぽい行を前の行から探す
            if any(kw in line for kw in ["完了", "納品", "検収", "承認", "支払"]):
                # 案件情報を含む行
                title = re.sub(r"[¥\d,円]+", "", line).strip()
                title = re.sub(r"(完了|納品|検収|承認|支払[い済]?)", "", title).strip()
                if len(title) > 2:
                    result["recent_jobs"].append({
                        "title": title[:80],
                        "amount": amount_in_line,
                    })

    return result


def _is_logged_in(page_text: str, platform: str) -> bool:
    """ページテキストからログイン状態を判定"""
    config = _PLATFORMS.get(platform, {})

    # ログイン済みキーワードの有無
    login_keywords = config.get("login_check_keywords", [])
    login_score = sum(1 for kw in login_keywords if kw in page_text)

    # 未ログインキーワードの有無
    not_logged_keywords = config.get("not_logged_in_keywords", [])
    not_logged_score = sum(1 for kw in not_logged_keywords if kw in page_text)

    # ログインフォームが目立つ場合は未ログイン
    if not_logged_score >= 2 and login_score <= 1:
        return False

    # ログイン済みキーワードが多ければログイン済み
    if login_score >= 2:
        return True

    # ページが短すぎる場合はリダイレクト（未ログイン）の可能性
    if len(page_text.strip()) < 100:
        return False

    return login_score > not_logged_score


async def check_revenue(platform: str = "all") -> dict[str, Any]:
    """フリーランスプラットフォームの報酬を確認する

    ブラウザツールでLancers/CrowdWorksの報酬管理ページにアクセスし、
    報酬データを取得してローカルに保存する。

    Args:
        platform: "lancers", "crowdworks", or "all"

    Returns:
        {
            "success": True,
            "data": {
                "lancers": {"current_month": 12345, "total": 100000, ...},
                "crowdworks": {...}
            },
            "summary": "今月の報酬: Lancers ¥12,345 / CrowdWorks ¥67,890 / 合計 ¥80,235"
        }
    """
    from tools.browser import get_page_text, browse_url

    targets = []
    if platform == "all":
        targets = list(_PLATFORMS.keys())
    elif platform in _PLATFORMS:
        targets = [platform]
    else:
        return {
            "success": False,
            "error": f"不明なプラットフォーム: {platform}（lancers / crowdworks / all）",
        }

    results = {}
    errors = []
    now = datetime.now()
    year_month = now.strftime("%Y-%m")

    for pf_key in targets:
        pf_config = _PLATFORMS[pf_key]
        pf_name = pf_config["name"]
        earnings_url = pf_config["earnings_url"]

        try:
            logger.info(f"Revenue check: {pf_name} - {earnings_url}")

            # ブラウザで報酬ページにアクセス
            page_result = await browse_url(earnings_url)

            if "error" in page_result:
                errors.append(f"{pf_name}: ページ取得失敗 - {page_result['error']}")
                results[pf_key] = {"error": page_result["error"]}
                continue

            page_text = page_result.get("text", "")
            page_title = page_result.get("title", "")

            # ログイン状態チェック
            if not _is_logged_in(page_text, pf_key):
                login_msg = (
                    f"{pf_name}にログインしていません。\n"
                    f"open_url_with_profile で {pf_config['login_url']} を開いて"
                    f"ログインしてから再実行してください。\n"
                    f"例: open_url_with_profile(url='{pf_config['login_url']}', profile='個人')"
                )
                errors.append(login_msg)
                results[pf_key] = {"error": "not_logged_in", "message": login_msg}
                continue

            # テキストから金額を抽出
            extracted = _extract_amounts_from_text(page_text, pf_key)

            platform_data = {
                "platform": pf_name,
                "checked_at": now.isoformat(),
                "page_title": page_title,
                "current_month": extracted["current_month"],
                "total": extracted["total"],
                "unpaid": extracted["unpaid"],
                "recent_jobs": extracted["recent_jobs"],
            }

            results[pf_key] = platform_data

            # 履歴に追記（append-only）
            _append_to_history(pf_key, platform_data, now)

            # 月別データを更新
            _update_monthly(year_month, pf_key, platform_data)

            logger.info(
                f"Revenue check OK: {pf_name} - "
                f"今月: {extracted['current_month']}, "
                f"累計: {extracted['total']}, "
                f"未払い: {extracted['unpaid']}"
            )

        except Exception as e:
            logger.error(f"Revenue check failed for {pf_name}: {e}")
            errors.append(f"{pf_name}: {str(e)[:200]}")
            results[pf_key] = {"error": str(e)[:200]}

    # サマリー生成
    summary = _generate_check_summary(results)

    response = {
        "success": len(errors) == 0,
        "data": results,
        "summary": summary,
    }

    if errors:
        response["errors"] = errors
        # 部分的な成功でもsuccessはTrue（一部取得できた場合）
        if any(pf_key in results and "error" not in results[pf_key] for pf_key in targets):
            response["success"] = True
            response["partial"] = True

    return response


def _append_to_history(platform: str, data: dict, timestamp: datetime):
    """履歴に追記（append-only）"""
    history = _load_history()

    entry = {
        "date": timestamp.strftime("%Y-%m-%d"),
        "timestamp": timestamp.isoformat(),
        "platform": platform,
        "current_month": data.get("current_month"),
        "total": data.get("total"),
        "unpaid": data.get("unpaid"),
        "recent_jobs": data.get("recent_jobs", []),
    }

    history.append(entry)
    _save_history(history)


def _update_monthly(year_month: str, platform: str, data: dict):
    """月別データを更新"""
    monthly_file = _MONTHLY_DIR / f"{year_month}.json"

    monthly_data = {}
    if monthly_file.exists():
        try:
            monthly_data = json.loads(monthly_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception):
            monthly_data = {}

    monthly_data[platform] = {
        "last_checked": data.get("checked_at"),
        "current_month": data.get("current_month"),
        "total": data.get("total"),
        "unpaid": data.get("unpaid"),
        "recent_jobs": data.get("recent_jobs", []),
    }

    # 合計計算
    combined_month = 0
    for pf_key, pf_data in monthly_data.items():
        if pf_key.startswith("_"):
            continue
        amount = pf_data.get("current_month")
        if isinstance(amount, (int, float)):
            combined_month += amount
    monthly_data["_combined_month_total"] = combined_month

    _save_monthly(year_month, monthly_data)


def _generate_check_summary(results: dict) -> str:
    """チェック結果のサマリーを生成"""
    parts = []
    total_month = 0
    has_data = False

    for pf_key, data in results.items():
        pf_name = _PLATFORMS.get(pf_key, {}).get("name", pf_key)

        if "error" in data:
            if data.get("error") == "not_logged_in":
                parts.append(f"{pf_name}: 未ログイン")
            else:
                parts.append(f"{pf_name}: エラー")
            continue

        current = data.get("current_month")
        if current is not None:
            parts.append(f"{pf_name} {current:,}円")
            total_month += current
            has_data = True
        else:
            parts.append(f"{pf_name}: 金額取得できず")

    if has_data:
        summary = f"今月の報酬: {' / '.join(parts)}"
        if len(results) > 1:
            summary += f" / 合計 {total_month:,}円"
    elif parts:
        summary = " / ".join(parts)
    else:
        summary = "報酬データを取得できませんでした"

    return summary


async def get_revenue_summary(period: str = "month") -> dict[str, Any]:
    """ローカルに保存された報酬データからサマリーを生成

    Args:
        period: "week" (過去7日), "month" (今月), "all" (全期間)

    Returns:
        {
            "success": True,
            "summary": "今月の合計: ¥80,235 (Lancers: ¥12,345, CrowdWorks: ¥67,890)",
            "total": 80235,
            "by_platform": {"lancers": 12345, "crowdworks": 67890},
            "history_count": 42,
            "period": "month"
        }
    """
    history = _load_history()

    if not history:
        return {
            "success": True,
            "summary": "報酬データがまだありません。check_revenue で最初のデータを取得してください。",
            "total": 0,
            "by_platform": {},
            "history_count": 0,
            "period": period,
        }

    now = datetime.now()

    # 期間フィルタ
    if period == "week":
        cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        filtered = [e for e in history if e.get("date", "") >= cutoff]
        period_label = "過去7日間"
    elif period == "month":
        cutoff = now.strftime("%Y-%m-01")
        filtered = [e for e in history if e.get("date", "") >= cutoff]
        period_label = f"{now.year}年{now.month}月"
    elif period == "all":
        filtered = history
        period_label = "全期間"
    else:
        return {
            "success": False,
            "error": f"不明な期間: {period}（week / month / all）",
        }

    if not filtered:
        return {
            "success": True,
            "summary": f"{period_label}の報酬データがありません。",
            "total": 0,
            "by_platform": {},
            "history_count": 0,
            "period": period,
        }

    # プラットフォーム別集計（最新のcurrent_monthを使用）
    by_platform = {}
    platform_latest = {}  # 各プラットフォームの最新エントリ

    for entry in filtered:
        pf = entry.get("platform", "unknown")
        ts = entry.get("timestamp", "")

        if pf not in platform_latest or ts > platform_latest[pf].get("timestamp", ""):
            platform_latest[pf] = entry

    for pf, entry in platform_latest.items():
        current = entry.get("current_month")
        if current is not None:
            by_platform[pf] = current

    total = sum(by_platform.values())

    # 前回データとの比較（トレンド）
    trend_info = _calculate_trend(history, period, now)

    # サマリーテキスト生成
    platform_parts = []
    for pf, amount in sorted(by_platform.items()):
        pf_name = _PLATFORMS.get(pf, {}).get("name", pf)
        platform_parts.append(f"{pf_name}: {amount:,}円")

    if platform_parts:
        summary = f"{period_label}の報酬合計: {total:,}円 ({', '.join(platform_parts)})"
    else:
        summary = f"{period_label}の報酬データがまだありません"

    if trend_info:
        summary += f"\n{trend_info}"

    # 未払い情報
    unpaid_total = 0
    for entry in platform_latest.values():
        unpaid = entry.get("unpaid")
        if isinstance(unpaid, (int, float)):
            unpaid_total += unpaid
    if unpaid_total > 0:
        summary += f"\n未払い報酬合計: {unpaid_total:,}円"

    return {
        "success": True,
        "summary": summary,
        "total": total,
        "by_platform": by_platform,
        "unpaid_total": unpaid_total,
        "history_count": len(filtered),
        "period": period,
        "period_label": period_label,
        "last_checked": max(
            (e.get("timestamp", "") for e in filtered), default=None
        ),
    }


def _calculate_trend(history: list[dict], period: str, now: datetime) -> str | None:
    """前期間との比較トレンドを計算"""
    if period == "month":
        # 先月のデータと比較
        last_month = now.replace(day=1) - timedelta(days=1)
        last_month_str = last_month.strftime("%Y-%m")
        monthly_file = _MONTHLY_DIR / f"{last_month_str}.json"

        if monthly_file.exists():
            try:
                last_data = json.loads(monthly_file.read_text(encoding="utf-8"))
                last_total = last_data.get("_combined_month_total", 0)
                if last_total > 0:
                    # 今月の最新を取得
                    current_month_entries = [
                        e for e in history
                        if e.get("date", "").startswith(now.strftime("%Y-%m"))
                    ]
                    if current_month_entries:
                        current_by_pf = {}
                        for e in current_month_entries:
                            pf = e.get("platform")
                            cm = e.get("current_month")
                            if pf and cm is not None:
                                current_by_pf[pf] = cm
                        current_total = sum(current_by_pf.values())

                        diff = current_total - last_total
                        if diff > 0:
                            return f"先月比: +{diff:,}円 (先月: {last_total:,}円)"
                        elif diff < 0:
                            return f"先月比: {diff:,}円 (先月: {last_total:,}円)"
                        else:
                            return f"先月と同額 ({last_total:,}円)"
            except (json.JSONDecodeError, Exception):
                pass

    return None


async def push_revenue_to_notion(period: str = "month") -> dict[str, Any]:
    """報酬サマリーをNotionに記録する（オプション機能）

    既存のNotion MCP経由でページを作成する。
    agent/tools_config.pyに登録されたMCPツールを使用。

    Args:
        period: サマリーの期間

    Returns:
        {"success": True, "notion_page_id": "..."}
    """
    summary_result = await get_revenue_summary(period)
    if not summary_result.get("success"):
        return {"success": False, "error": "サマリー取得失敗"}

    # Notion用のデータ構造を返す（実際のMCP呼び出しはエージェントが行う）
    now = datetime.now()
    notion_data = {
        "title": f"収益レポート {now.strftime('%Y/%m/%d')}",
        "summary": summary_result["summary"],
        "total": summary_result["total"],
        "by_platform": summary_result["by_platform"],
        "period": summary_result.get("period_label", period),
    }

    return {
        "success": True,
        "notion_data": notion_data,
        "instruction": (
            "このデータをNotionに保存するには、以下のMCPツールを使用してください:\n"
            "mcp_notion_API-post-page で収益レポートページを作成\n"
            f"親DB: 532a57aa-8f28-83dc-a819-010eab74a64f"
        ),
    }
