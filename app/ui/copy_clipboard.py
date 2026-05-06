# -*- coding: utf-8 -*-
"""
在浏览器中写入剪贴板。

HTTP + 局域网 IP 等非安全上下文中 ``navigator.clipboard`` 常不可用或静默失败，
故在 Clipboard API 失败或未提供时回退 ``textarea`` + ``document.execCommand('copy')``。
"""
from __future__ import annotations

import json

from nicegui import ui


def copy_text_to_clipboard(text: str) -> None:
    """
    将文本复制到用户剪贴板（须在用户点击等手势回调中调用）。

    Args:
        text: 要复制的纯文本
    """
    payload = json.dumps(text)
    ui.run_javascript(
        f"""
(() => {{
  const t = {payload};
  function fallback() {{
    const ta = document.createElement('textarea');
    ta.value = t;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0;font-size:16px';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, t.length);
    try {{ document.execCommand('copy'); }}
    finally {{ document.body.removeChild(ta); }}
  }}
  if (window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(t).catch(function() {{ fallback(); }});
  }} else {{
    fallback();
  }}
}})();
"""
    )
