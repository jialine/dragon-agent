# -*- coding: utf-8 -*-
"""web_search 防死循环 + 防过度调研的单元测试。

对应修复：
  1. web_providers.py: Bing 注册前置（DuckDuckGo 国内被墙，每次白等 15s 超时）
  2. server.py: web_search_stop_message 纯函数（连续失败/总次数达阈值强制收尾）
"""
from dragon.gateway.server import (
    web_search_stop_message,
    WEB_SEARCH_FAIL_LIMIT,
    WEB_SEARCH_CALL_LIMIT,
)
from dragon.web_providers import WebSearchRouter


# ── web_search_stop_message 纯函数 ────────────────────────────────────


def test_stop_message_no_trigger():
    """未达阈值：正常搜索中，返回空串（不强制收尾）。"""
    assert web_search_stop_message(0, 0) == ""
    assert web_search_stop_message(2, 5) == ""  # 边界：都差 1 次


def test_stop_message_fail_limit():
    """连续 provider:none 失败达阈值：返回「搜索不可用」提示。"""
    msg = web_search_stop_message(WEB_SEARCH_FAIL_LIMIT, 0)
    assert "连续失败" in msg
    assert "不可用" in msg


def test_stop_message_call_limit():
    """搜索总次数达阈值：返回「搜索够了」提示。"""
    msg = web_search_stop_message(0, WEB_SEARCH_CALL_LIMIT)
    assert "已搜索" in msg
    assert "6 次" in msg


def test_stop_message_fail_priority_over_call():
    """fail 与 call 同时达阈值时，fail（搜索不可用）优先于 call（搜索够了）。"""
    msg = web_search_stop_message(WEB_SEARCH_FAIL_LIMIT, WEB_SEARCH_CALL_LIMIT)
    assert "连续失败" in msg
    assert "已搜索" not in msg


def test_stop_message_fail_boundary():
    """fail 阈值边界：2 次不触发，3 次触发。"""
    assert web_search_stop_message(WEB_SEARCH_FAIL_LIMIT - 1, 0) == ""
    assert web_search_stop_message(WEB_SEARCH_FAIL_LIMIT, 0) != ""


def test_stop_message_call_boundary():
    """call 阈值边界：5 次不触发，6 次触发。"""
    assert web_search_stop_message(0, WEB_SEARCH_CALL_LIMIT - 1) == ""
    assert web_search_stop_message(0, WEB_SEARCH_CALL_LIMIT) != ""


# ── web_providers Bing 前置 ───────────────────────────────────────────


def test_router_registers_bing_before_duckduckgo():
    """Bing 应在 DuckDuckGo 之前注册（国内可用，省 15s 超时）。"""
    router = WebSearchRouter()
    names = list(router.providers.keys())
    assert names.index("bing") < names.index("duckduckgo")


def test_router_list_providers_order():
    """list_providers 返回顺序应与注册顺序一致（bing 在前）。"""
    router = WebSearchRouter()
    names = [p["name"] for p in router.list_providers()]
    assert names.index("bing") < names.index("duckduckgo")
