"""ブラウザ制御ツール - Layer 2: Playwright (ブラウザ特化)

Playwrightでheadless Chromiumを操作。
スクショ不要でページ内容をテキスト取得 → AIはテキスト処理のみでトークン節約。

Accessibility Tree対応:
- ariaSnapshot()でページ構造をYAML形式で取得（200-400トークン）
- 番号付き要素リストでDOM操作（座標推測不要）
- Browser-Use方式: AI→「要素[3]をクリック」→ 正確に実行

ステルス対応（playwright-stealth）:
- WebDriverフラグ除去
- navigator.plugins / languages 偽装
- Chrome DevTools Protocol検出回避
- セッション永続化（storage_state）でログイン維持

セキュリティ:
- URL安全性チェック（security/url_validator.py連携）
- ページ取得タイムアウト（30秒）
- コンテンツサイズ制限（100KB）
- JavaScript実行は操作時のみ有効
- Webコンテンツからの間接的プロンプトインジェクション防御
"""

import asyncio
import logging
import random
import re
from pathlib import Path
from typing import Any

from security.url_validator import validate_url

logger = logging.getLogger("shiki.tools")

# Playwrightのブラウザインスタンス（遅延初期化）
_browser = None
_playwright = None

# 制限値
_PAGE_TIMEOUT_MS = 30_000  # 30秒
_MAX_CONTENT_LENGTH = 100_000  # 100KB
_MAX_LINKS = 50

# アクティブページセッション管理（要素インタラクション用）
_active_sessions: dict[str, dict] = {}  # url -> {page, context, elements}
_MAX_SESSIONS = 3

# セッション永続化パス
_STORAGE_STATE_DIR = Path(__file__).parent.parent / ".ritsu" / "browser_sessions"

# リトライ設定
_MAX_RETRIES = 2
_RETRY_BASE_DELAY = 1.0


async def _ensure_browser():
    """ブラウザインスタンスを遅延初期化（stealth対応）"""
    global _browser, _playwright
    if _browser is None:
        from playwright.async_api import async_playwright

        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        logger.info("Playwright browser launched (headless + stealth args)")
    return _browser


async def _get_page(enable_js: bool = False, use_stealth: bool = False, storage_key: str | None = None):
    """新しいページを取得（セキュリティ強化 + stealth対応）

    Args:
        enable_js: JSを有効にするか。デフォルトFalse（安全）。
        use_stealth: playwright-stealthを適用するか。
        storage_key: セッション永続化キー（ドメイン名等）。指定するとログイン状態を保持。
    """
    browser = await _ensure_browser()

    # セッション永続化: 既存のstorage_stateをロード
    context_kwargs = {
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 720},
        "java_script_enabled": enable_js,
        "accept_downloads": False,
        "permissions": [],
        "service_workers": "block",
        "locale": "ja-JP",
        "timezone_id": "Asia/Tokyo",
    }

    if storage_key:
        state_path = _STORAGE_STATE_DIR / f"{storage_key}.json"
        if state_path.exists():
            context_kwargs["storage_state"] = str(state_path)
            logger.info(f"Loaded browser session: {storage_key}")

    context = await browser.new_context(**context_kwargs)

    # playwright-stealth適用
    if use_stealth:
        try:
            from playwright_stealth import stealth_async
            await stealth_async(context)
            logger.debug("Stealth mode applied")
        except ImportError:
            logger.warning("playwright-stealth not installed, skipping stealth")

    # セキュリティ: ナビゲーション先URLを検証
    async def _on_route(route):
        """全リクエストを検査: 危険URLはブロック"""
        request_url = route.request.url
        if request_url.startswith(("data:", "javascript:", "blob:")):
            logger.warning(f"Blocked dangerous scheme: {request_url[:100]}")
            await route.abort()
            return
        dangerous_ext = (".exe", ".msi", ".dmg", ".pkg", ".scr", ".bat", ".cmd", ".ps1", ".sh", ".app", ".deb", ".rpm")
        if any(request_url.lower().endswith(ext) for ext in dangerous_ext):
            logger.warning(f"Blocked dangerous download: {request_url[:100]}")
            await route.abort()
            return
        await route.continue_()

    await context.route("**/*", _on_route)

    page = await context.new_page()
    return page, context


async def _save_storage_state(context, storage_key: str):
    """セッション状態を永続化"""
    _STORAGE_STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path = _STORAGE_STATE_DIR / f"{storage_key}.json"
    await context.storage_state(path=str(state_path))
    logger.info(f"Browser session saved: {storage_key}")


async def _human_delay(min_ms: int = 200, max_ms: int = 800):
    """人間らしいランダム遅延"""
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def _retry_with_backoff(coro_factory, max_retries: int = _MAX_RETRIES):
    """指数バックオフ付きリトライ

    Args:
        coro_factory: 引数なしでコルーチンを返す関数（呼ぶたびに新しいコルーチンを生成）
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = _RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
                logger.warning(f"Retry {attempt + 1}/{max_retries}: {e} (wait {delay:.1f}s)")
                await asyncio.sleep(delay)
    raise last_error


def _extract_domain(url: str) -> str:
    """URLからドメイン名を抽出（storage_keyとして使用）"""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.netloc.replace(":", "_").replace(".", "_") if parsed.netloc else ""


async def browse_url(url: str) -> dict[str, Any]:
    """URLを開いてページ内容をテキストで取得

    スクショの代わりにテキストでページ情報を返す。トークン効率的。

    Returns:
        {"title": str, "url": str, "text": str, "links": list}
    """
    url_check = validate_url(url)
    if not url_check["safe"]:
        return {"error": f"URL安全性チェック失敗: {url_check.get('reason', '不明')}"}

    page = None
    context = None
    try:
        # 情報取得のみ → JS無効（安全）。レンダリング不足ならJS有効でリトライ。
        page, context = await _get_page(enable_js=False)

        await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS)
        await page.wait_for_timeout(1000)

        title = await page.title()
        text = await _extract_main_text(page)

        # JS無効で中身が取れなかった場合、JS有効+stealth でリトライ（SPA対応）
        if len(text.strip()) < 50:
            await context.close()
            page, context = await _get_page(enable_js=True, use_stealth=True)
            await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS)
            await page.wait_for_timeout(2000)
            title = await page.title()
            text = await _extract_main_text(page)
            logger.info(f"JS+stealth retry for: {url}")

        links = await _extract_links(page)

        return {
            "title": title,
            "url": page.url,
            "text": text[:_MAX_CONTENT_LENGTH],
            "links": links[:_MAX_LINKS],
        }

    except Exception as e:
        logger.error(f"Browse failed: {url} - {e}")
        return {"error": f"ページ取得失敗: {str(e)[:200]}"}
    finally:
        if context:
            await context.close()


async def search_web(query: str) -> dict[str, Any]:
    """Google検索してトップ結果を返す

    Returns:
        {"query": str, "results": [{"title": str, "url": str, "snippet": str}]}
    """
    from urllib.parse import urlencode
    search_url = f"https://www.google.com/search?{urlencode({'q': query, 'hl': 'ja'})}"

    page = None
    context = None
    try:
        # Google検索はJSなしでもHTML結果が取れる
        page, context = await _get_page(enable_js=False)

        await page.goto(search_url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS)
        await page.wait_for_timeout(2000)  # Google結果待ち

        # 検索結果を抽出
        results = []
        search_items = await page.query_selector_all("div.g")

        for item in search_items[:10]:
            try:
                title_el = await item.query_selector("h3")
                link_el = await item.query_selector("a")
                snippet_el = await item.query_selector("div[data-sncf], div.VwiC3b")

                title = await title_el.inner_text() if title_el else ""
                href = await link_el.get_attribute("href") if link_el else ""
                snippet = await snippet_el.inner_text() if snippet_el else ""

                if title and href and href.startswith("http"):
                    results.append({
                        "title": title,
                        "url": href,
                        "snippet": snippet[:200],
                    })
            except Exception:
                continue

        return {
            "query": query,
            "results": results,
        }

    except Exception as e:
        logger.error(f"Search failed: {query} - {e}")
        return {"error": f"検索失敗: {str(e)[:200]}"}
    finally:
        if context:
            await context.close()


async def get_page_text(url: str) -> dict[str, Any]:
    """URLからテキストのみ取得（軽量版）

    browse_urlより軽量。記事の本文取得に最適。
    """
    url_check = validate_url(url)
    if not url_check["safe"]:
        return {"error": f"URL安全性チェック失敗: {url_check.get('reason', '不明')}"}

    page = None
    context = None
    try:
        page, context = await _get_page(enable_js=False)
        await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS)
        await page.wait_for_timeout(1000)

        title = await page.title()
        text = await _extract_main_text(page)

        # JS無効で取れなかったらstealth付きリトライ
        if len(text.strip()) < 50:
            await context.close()
            page, context = await _get_page(enable_js=True, use_stealth=True)
            await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS)
            await page.wait_for_timeout(2000)
            title = await page.title()
            text = await _extract_main_text(page)

        return {
            "title": title,
            "url": page.url,
            "text": text[:_MAX_CONTENT_LENGTH],
        }

    except Exception as e:
        logger.error(f"Get page text failed: {url} - {e}")
        return {"error": f"テキスト取得失敗: {str(e)[:200]}"}
    finally:
        if context:
            await context.close()


async def _extract_main_text(page) -> str:
    """ページからメインテキストを抽出

    article/main要素を優先、なければbody全体。
    """
    for selector in ["article", "main", "[role='main']", ".content", "#content"]:
        try:
            el = await page.query_selector(selector)
            if el:
                text = await el.inner_text()
                if len(text) > 100:
                    return sanitize_web_content(_clean_text(text))
        except Exception:
            continue

    try:
        text = await page.inner_text("body")
        return sanitize_web_content(_clean_text(text))
    except Exception:
        return ""


async def _extract_links(page) -> list[dict]:
    """ページからリンクを抽出"""
    links = []
    try:
        anchors = await page.query_selector_all("a[href]")
        for anchor in anchors[:100]:
            try:
                href = await anchor.get_attribute("href")
                text = (await anchor.inner_text()).strip()
                if href and text and href.startswith("http") and len(text) < 200:
                    links.append({"text": text[:100], "url": href})
            except Exception:
                continue
    except Exception:
        pass
    return links


def _clean_text(text: str) -> str:
    """テキストをクリーンアップ（余分な空白・改行を除去）"""
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


async def get_page_elements(url: str) -> dict[str, Any]:
    """URLを開いてインタラクティブ要素を番号付きリストで返す

    Browser-Use方式: ボタン・リンク・入力欄等に番号を振って返す。
    AIは「要素[3]をクリック」と指定するだけで正確に操作できる。
    stealth + セッション永続化対応。

    Returns:
        {"url": str, "title": str, "elements": [{"index": int, "tag": str, "role": str, "text": str, "type": str}]}
    """
    url_check = validate_url(url)
    if not url_check["safe"]:
        return {"error": f"URL安全性チェック失敗: {url_check.get('reason', '不明')}"}

    page = None
    context = None
    try:
        domain = _extract_domain(url)
        # 操作用 → JS有効 + stealth + セッション永続化
        page, context = await _get_page(
            enable_js=True,
            use_stealth=True,
            storage_key=domain,
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS)
        await _human_delay(1000, 2000)

        title = await page.title()

        # インタラクティブ要素を抽出（Browser-Use方式）
        elements = await _extract_interactive_elements(page)

        # セッション保存（後続のinteract_page_elementで使用）
        await _save_session(url, page, context, elements)

        # セッション永続化（ログイン状態等を保持）
        if domain:
            await _save_storage_state(context, domain)

        return {
            "title": title,
            "url": page.url,
            "element_count": len(elements),
            "elements": [
                {k: v for k, v in el.items() if not k.startswith("_")}
                for el in elements[:100]
            ],
        }

    except Exception as e:
        logger.error(f"Get page elements failed: {url} - {e}")
        if context:
            await context.close()
        return {"error": f"要素取得失敗: {str(e)[:200]}"}


async def interact_page_element(
    url: str, element_index: int, action: str, value: str = ""
) -> dict[str, Any]:
    """番号付き要素に対してアクション実行（人間らしい遅延付き）

    Args:
        url: 対象ページURL（get_page_elementsで開いたもの）
        element_index: 要素番号（get_page_elementsの結果のindex）
        action: "click", "fill", "select" のいずれか
        value: fill/selectの場合の入力値

    Returns:
        {"success": bool, "action": str, "element": dict}
    """
    session = _active_sessions.get(url)
    if not session:
        return {"error": f"セッションが見つかりません。先にget_page_elementsでページを開いてください。URL: {url}"}

    page = session["page"]
    elements = session["elements"]

    if element_index < 0 or element_index >= len(elements):
        return {"error": f"要素番号 {element_index} は範囲外（0〜{len(elements)-1}）"}

    element_info = elements[element_index]
    selector = element_info.get("_selector", "")

    if not selector:
        return {"error": f"要素 [{element_index}] のセレクタが取得できません"}

    try:
        el = await page.query_selector(selector)
        if not el:
            return {"error": f"要素 [{element_index}] が見つかりません（ページが変わった可能性）"}

        if action == "click":
            # 人間らしい遅延: ホバー → 少し待つ → クリック
            await el.hover(timeout=5000)
            await _human_delay(100, 300)
            await el.click(timeout=5000)
            await _human_delay(500, 1500)

            # セキュリティ: クリック後の遷移先URLを検証
            new_url = page.url
            nav_check = validate_url(new_url)
            if not nav_check["safe"]:
                logger.warning(f"Navigation blocked after click: {new_url} - {nav_check.get('reason')}")
                await close_page_session(url)
                return {
                    "error": f"クリック後の遷移先が安全でない: {nav_check.get('reason')}",
                    "blocked_url": new_url,
                }

            # クリック後に要素リストを更新
            new_elements = await _extract_interactive_elements(page)
            session["elements"] = new_elements

            # セッション永続化を更新
            domain = _extract_domain(new_url)
            if domain:
                await _save_storage_state(session["context"], domain)

            return {
                "success": True,
                "action": "click",
                "element": {k: v for k, v in element_info.items() if not k.startswith("_")},
                "page_url": new_url,
                "page_title": await page.title(),
                "new_element_count": len(new_elements),
            }

        elif action == "fill":
            if not value:
                return {"error": "fillアクションにはvalueが必要です"}
            # 人間らしい入力: フォーカス → クリア → 1文字ずつタイプ
            await el.click(timeout=5000)
            await _human_delay(100, 200)
            await el.fill("", timeout=5000)  # クリア
            await el.type(value, delay=random.uniform(30, 80), timeout=10000)
            return {
                "success": True,
                "action": "fill",
                "element": {k: v for k, v in element_info.items() if not k.startswith("_")},
                "value": value[:100],
            }

        elif action == "select":
            if not value:
                return {"error": "selectアクションにはvalueが必要です"}
            await el.select_option(value, timeout=5000)
            return {
                "success": True,
                "action": "select",
                "element": {k: v for k, v in element_info.items() if not k.startswith("_")},
                "value": value,
            }

        else:
            return {"error": f"未知のアクション: {action}（click, fill, selectのみ対応）"}

    except Exception as e:
        logger.error(f"Element interaction failed: [{element_index}] {action} - {e}")
        return {"error": f"操作失敗: {str(e)[:200]}"}


async def get_accessibility_tree(url: str) -> dict[str, Any]:
    """ページのAccessibility Treeをテキストで取得（超トークン効率）

    Playwright ariaSnapshot()を利用。
    スクショ15,000トークン → Accessibility Tree 200-400トークン。

    Returns:
        {"url": str, "title": str, "tree": str}
    """
    url_check = validate_url(url)
    if not url_check["safe"]:
        return {"error": f"URL安全性チェック失敗: {url_check.get('reason', '不明')}"}

    page = None
    context = None
    try:
        page, context = await _get_page(enable_js=False)
        await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS)
        await page.wait_for_timeout(1500)

        title = await page.title()

        # Accessibility Treeを取得（YAML形式）
        tree = await page.locator("body").aria_snapshot()

        if len(tree) > _MAX_CONTENT_LENGTH:
            tree = tree[:_MAX_CONTENT_LENGTH] + "\n... (truncated)"

        return {
            "title": title,
            "url": page.url,
            "tree": tree,
            "tree_length": len(tree),
        }

    except Exception as e:
        logger.error(f"Accessibility tree failed: {url} - {e}")
        if "aria_snapshot" in str(e).lower() or "attribute" in str(e).lower():
            logger.info("ariaSnapshot not available, falling back to text extraction")
            if page:
                try:
                    text = await _extract_main_text(page)
                    return {
                        "title": await page.title() if page else "",
                        "url": url,
                        "tree": f"[フォールバック: テキスト抽出]\n{text[:_MAX_CONTENT_LENGTH]}",
                        "tree_length": len(text),
                        "fallback": True,
                    }
                except Exception:
                    pass
        return {"error": f"Accessibility Tree取得失敗: {str(e)[:200]}"}
    finally:
        if context:
            await context.close()


async def _extract_interactive_elements(page) -> list[dict]:
    """ページからインタラクティブ要素を番号付きで抽出（Browser-Use方式）"""
    elements = []

    selectors = [
        ("a[href]", "link"),
        ("button", "button"),
        ("input:not([type='hidden'])", "input"),
        ("textarea", "textarea"),
        ("select", "select"),
        ("[role='button']", "button"),
        ("[role='link']", "link"),
        ("[role='tab']", "tab"),
        ("[role='menuitem']", "menuitem"),
        ("[onclick]", "clickable"),
    ]

    seen_texts = set()

    for selector, role in selectors:
        try:
            els = await page.query_selector_all(selector)
            for el in els:
                try:
                    if not await el.is_visible():
                        continue

                    text = (await el.inner_text()).strip()[:100]
                    tag = await el.evaluate("el => el.tagName.toLowerCase()")
                    el_type = await el.get_attribute("type") or ""
                    placeholder = await el.get_attribute("placeholder") or ""
                    aria_label = await el.get_attribute("aria-label") or ""
                    href = await el.get_attribute("href") or ""

                    display_text = text or aria_label or placeholder or ""
                    if not display_text and href:
                        display_text = href[:80]

                    if not display_text or len(display_text.strip()) < 1:
                        continue

                    dedup_key = f"{role}:{display_text[:50]}"
                    if dedup_key in seen_texts:
                        continue
                    seen_texts.add(dedup_key)

                    unique_selector = await _generate_selector(el, page)

                    elements.append({
                        "index": len(elements),
                        "tag": tag,
                        "role": role,
                        "text": display_text,
                        "type": el_type,
                        "href": href[:200] if href else "",
                        "_selector": unique_selector,
                    })

                except Exception:
                    continue
        except Exception:
            continue

    return elements


async def _generate_selector(element, page) -> str:
    """要素のユニークCSSセレクタを生成"""
    try:
        selector = await element.evaluate("""el => {
            // IDがあればそれを使う
            if (el.id) return '#' + CSS.escape(el.id);

            // data-testidがあれば
            const testId = el.getAttribute('data-testid');
            if (testId) return '[data-testid="' + testId + '"]';

            // name属性
            const name = el.getAttribute('name');
            if (name) return el.tagName.toLowerCase() + '[name="' + name + '"]';

            // aria-label（CSS.escapeで安全にエスケープ）
            const ariaLabel = el.getAttribute('aria-label');
            if (ariaLabel) return '[aria-label="' + CSS.escape(ariaLabel) + '"]';

            // nth-child フォールバック
            function getPath(el) {
                if (el.id) return '#' + CSS.escape(el.id);
                if (el === document.body) return 'body';
                const parent = el.parentElement;
                if (!parent) return el.tagName.toLowerCase();
                const siblings = Array.from(parent.children).filter(c => c.tagName === el.tagName);
                const index = siblings.indexOf(el) + 1;
                return getPath(parent) + ' > ' + el.tagName.toLowerCase() +
                    (siblings.length > 1 ? ':nth-of-type(' + index + ')' : '');
            }
            return getPath(el);
        }""")
        return selector
    except Exception:
        return ""


async def _save_session(url: str, page, context, elements: list):
    """ページセッションを保存（後続の操作用）"""
    global _active_sessions

    if len(_active_sessions) >= _MAX_SESSIONS:
        oldest_url = next(iter(_active_sessions))
        old_session = _active_sessions.pop(oldest_url)
        try:
            await old_session["context"].close()
        except Exception:
            pass

    _active_sessions[url] = {
        "page": page,
        "context": context,
        "elements": elements,
    }


async def close_page_session(url: str) -> dict[str, Any]:
    """ページセッションを閉じる"""
    session = _active_sessions.pop(url, None)
    if session:
        try:
            await session["context"].close()
        except Exception:
            pass
        return {"success": True, "message": f"セッションを閉じました: {url}"}
    return {"error": f"セッションが見つかりません: {url}"}


def _normalize_homoglyphs(text: str) -> str:
    """UnicodeホモグリフをASCIIに正規化（検出用）

    例: Cyrillic 'а'→'a', 全角'ｉｇｎｏｒｅ'→'ignore'
    """
    import unicodedata
    normalized = unicodedata.normalize("NFKC", text)
    _HOMOGLYPH_MAP = str.maketrans({
        '\u0430': 'a', '\u0435': 'e', '\u043e': 'o', '\u0440': 'p',
        '\u0441': 'c', '\u0443': 'y', '\u0445': 'x', '\u0456': 'i',
        '\u0410': 'A', '\u0415': 'E', '\u041e': 'O', '\u0420': 'P',
        '\u0421': 'C', '\u0422': 'T', '\u041d': 'H', '\u041c': 'M',
    })
    return normalized.translate(_HOMOGLYPH_MAP)


def sanitize_web_content(text: str) -> str:
    """Webコンテンツからプロンプトインジェクション的パターンを除去"""
    # ゼロ幅文字・不可視文字を除去（隠しテキスト対策）
    text = re.sub(r'[\u200b\u200c\u200d\ufeff\u2060\u00ad\u034f\u180e]', '', text)

    # ホモグリフ正規化したテキストで検出（Cyrillic偽装対策）
    normalized = _normalize_homoglyphs(text)

    injection_patterns = [
        # 指示改変系
        r'(?i)(ignore|disregard|forget)\s+(all\s+)?(previous|above|prior|earlier)\s+(instructions?|prompts?|rules?|guidelines?)',
        r'(?i)you\s+are\s+now\s+(a|an|the)\s+',
        r'(?i)from\s+now\s+on\s*,?\s*(you|your|act|behave)',
        r'(?i)(new|updated?|override)\s+(instructions?|prompt|role|persona)\s*:',
        # ロールプレイ/システム偽装
        r'(?i)system\s*:\s*',
        r'(?i)assistant\s*:\s*',
        r'(?i)\[system\]',
        r'(?i)<\|?system\|?>',
        # 出力操作
        r'(?i)do\s+not\s+follow\s+(the|your)\s+(previous|original|above)',
        r'(?i)repeat\s+after\s+me\s*:',
        r'(?i)output\s+(only|exactly)\s*:',
    ]

    for pattern in injection_patterns:
        if re.search(pattern, normalized) or re.search(pattern, text):
            logger.warning(f"Prompt injection in web content: {pattern}")
            text = re.sub(pattern, '[FILTERED]', text, flags=re.IGNORECASE)
            normalized = re.sub(pattern, '[FILTERED]', normalized, flags=re.IGNORECASE)

    return text


async def close_browser():
    """ブラウザを閉じる（シャットダウン時に呼ぶ）"""
    global _browser, _playwright, _active_sessions

    for url, session in _active_sessions.items():
        try:
            await session["context"].close()
        except Exception:
            pass
    _active_sessions.clear()

    if _browser:
        await _browser.close()
        _browser = None
    if _playwright:
        await _playwright.stop()
        _playwright = None
    logger.info("Playwright browser closed")
