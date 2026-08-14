# -*- coding: utf-8 -*-
"""死循环修复的单元测试：Bing 解析正则 + web_search 无 provider 报错。

对应修复：
  A. dragon/web_providers.py: _parse_bing_html 正则兼容 Bing 新版 HTML
  B. dragon/tool/builtins/__init__.py: tool_web_search 无 provider 时返回 error
"""
import asyncio
import json

from dragon.web_providers import _parse_bing_html


# ---- 修复 A：Bing 新版 HTML 解析 ----

# Bing 新版：class="b_algo" 后带 data-id iid=... 属性（旧正则抓不到）
BING_NEW_HTML = '''
<li class="b_algo" data-id iid=SERP.5334>
    <h2><a href="https://example.com/1" target="_blank">示例结果一</a></h2>
    <p>这是第一条摘要</p>
</li>
<li class="b_algo" data-id iid=SERP.5335>
    <h2><a href="https://example.com/2" target="_blank">示例结果二</a></h2>
    <p>这是第二条摘要</p>
</li>
'''

# Bing 旧版：class="b_algo"> 直接闭合（回归验证兼容）
BING_OLD_HTML = '''
<li class="b_algo">
    <h2><a href="https://example.com/old" target="_blank">旧格式结果</a></h2>
    <p>旧格式摘要</p>
</li>
'''


def test_parse_bing_html_new_format():
    """修复 A：Bing 新版 HTML（b_algo 后带属性）应能解析出结果。"""
    results = _parse_bing_html(BING_NEW_HTML, max_results=10)
    assert len(results) == 2
    assert results[0].title == "示例结果一"
    assert results[0].url == "https://example.com/1"
    assert results[0].snippet == "这是第一条摘要"


def test_parse_bing_html_old_format():
    """回归：旧版 HTML（b_algo 后直接 >）仍应兼容解析。"""
    results = _parse_bing_html(BING_OLD_HTML, max_results=10)
    assert len(results) == 1
    assert results[0].title == "旧格式结果"
    assert results[0].url == "https://example.com/old"


# ---- 修复 B：web_search 无 provider 时返回 error ----

def test_web_search_no_provider_returns_error(monkeypatch):
    """修复 B：所有 provider 失败(provider=none)时，返回 JSON 应含 error 字段。"""
    import dragon.tool.builtins as builtins

    class FakeRouter:
        def list_providers(self):
            return []

        async def search(self, query, max_results=10, provider=None):
            return ("none", [])

    monkeypatch.setattr(builtins, "_web_search_router", FakeRouter())

    result_json = asyncio.run(builtins.tool_web_search("测试查询"))
    data = json.loads(result_json)

    assert data["provider"] == "none"
    assert "error" in data
    assert data["total"] == 0
    assert data["results"] == []


# ---- 修复 C/D 说明 ----
# server.py 的空结果提前退出、工具调用硬上限、进度日志，耦合在 MessageProcessor
# 的异步 agent 循环里，需要完整 mock provider/tool_registry/feishu 回调才能测，
# 属集成测试范畴，此处不强行单测；逻辑已通过代码审查验证。
