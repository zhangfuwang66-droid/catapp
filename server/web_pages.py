"""网页资源从只读程序目录加载；设备上下文只由服务端验证后注入。"""

import json

from index_html import INDEX_HTML
from runtime_paths import SERVER_ROOT

WEB_ROOT = SERVER_ROOT / "web"


def portal_html() -> str:
    return (WEB_ROOT / "portal.html").read_text(encoding="utf-8")


def knowledge_html(mode) -> str:
    return (WEB_ROOT / "knowledge.html").read_text(encoding="utf-8").replace(
        "__KNOWLEDGE_CONTEXT__", json.dumps({"mode": mode}, ensure_ascii=True))


def console_html(device_id=None, *, account_mode=False) -> str:
    context = json.dumps({"mode": "accounts" if account_mode else "local", "device_id": device_id}, ensure_ascii=True)
    return INDEX_HTML.replace("__CYBERMIAO_CONTEXT__", context.replace("<", "\\u003c")).replace(
        "__CYBERMIAO_BODY_CLASS__", "auth-pending" if account_mode else "",
    )
