"""OS固有のセキュリティ強化 - クロスプラットフォーム対応

起動時にOS固有のセキュリティチェックを実施。
platform_layer経由で各OSの監査を実行。

後方互換性のため、既存のインターフェース(full_mac_audit, run_security_audit)を維持。
"""

import logging

logger = logging.getLogger("shiki.security")


def full_mac_audit() -> dict[str, bool]:
    """OS固有のセキュリティ監査（クロスプラットフォーム対応）"""
    try:
        from platform_layer import get_platform
        platform = get_platform()
        results = platform.security_audit()
    except Exception as e:
        logger.warning(f"セキュリティ監査失敗: {e}")
        results = {}

    for check, passed in results.items():
        if not passed:
            logger.warning(f"Security: {check} = FAILED")
        else:
            logger.info(f"Security: {check} = OK")

    return results


# 後方互換性
run_security_audit = full_mac_audit
