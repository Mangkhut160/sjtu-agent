from __future__ import annotations

import re
from pathlib import Path


HTML = (
    Path(__file__).resolve().parents[1] / "sjtu_agent/web/static/index.html"
).read_text(encoding="utf-8")


def _tool_labels_block() -> str:
    match = re.search(r"const\s+TOOL_LABELS\s*=\s*\{(?P<body>.*?)\n\s*\};", HTML, re.S)
    assert match, "TOOL_LABELS object not found"
    return match.group("body")


def test_web_chat_has_canvas_tool_labels():
    labels = {
        "list_canvas_courses": "读取 Canvas 课程",
        "get_canvas_course_announcements": "读取 Canvas 公告",
        "get_canvas_course_quizzes": "读取 Canvas Quiz",
        "get_canvas_course_updates": "汇总 Canvas 课程动态",
        "get_canvas_overview": "汇总 Canvas 总览",
        "get_canvas_todo": "读取 Canvas 待办",
        "configure_canvas_monitor": "配置 Canvas 监控",
        "list_canvas_assignments": "列出 Canvas 作业",
        "submit_canvas_assignment": "上传并提交作业",
        "setup_canvas": "引导配置 Canvas",
    }
    body = _tool_labels_block()
    for name, label in labels.items():
        pattern = rf"{re.escape(name)}\s*:\s*['\"]{re.escape(label)}['\"]"
        assert re.search(pattern, body), (
            f"missing TOOL_LABELS entry for {name} -> {label}"
        )


def test_web_chat_uses_keyed_tool_cards():
    assert re.search(
        r"toolCards\s*=\s*new\s+Map\s*\(",
        HTML,
    ), "toolCards Map not initialized"
    assert re.search(
        r"toolCards\.set\(\s*toolId\s*,\s*card\s*\)",
        HTML,
    ), "toolCards not keyed by toolId on start"
    assert re.search(
        r"toolCards\.get\(\s*toolId\s*\)",
        HTML,
    ), "toolCards not looked up by toolId on end"
    assert "currentToolCard" not in HTML


def test_web_chat_renders_progress_events():
    for helper in [
        "appendStatusRow",
        "appendToolCard",
        "markToolDone",
        "appendRetryRow",
        "appendLimitRow",
    ]:
        pattern = rf"(?:function\s+{helper}\s*\(|(?:const|let)\s+{helper}\s*=)"
        assert re.search(pattern, HTML), f"missing helper {helper}"


def test_web_chat_handles_structured_done_event():
    assert re.search(r"evt\.done", HTML), "structured done event is not handled"
    assert re.search(
        r"payload\s*===\s*['\"]\[DONE\]['\"]",
        HTML,
    ), "legacy [DONE] payload is not handled"
