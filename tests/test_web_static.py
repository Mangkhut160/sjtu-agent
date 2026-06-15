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


def _function_block(name: str) -> str:
    match = re.search(
        rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{(?P<body>.*?)\n\}}",
        HTML,
        re.S,
    )
    assert match, f"{name} function not found"
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


def test_web_chat_pairs_legacy_tool_events_without_ids():
    assert re.search(
        r"legacyToolIds\s*=\s*\[\s*\]",
        HTML,
    ), "legacy no-id tool queue not initialized"
    assert re.search(
        r"legacyToolIds\.push\(\s*toolId\s*\)",
        HTML,
    ), "legacy no-id tool starts are not queued"
    assert re.search(
        r"legacyToolIds\.shift\(\s*\)",
        HTML,
    ), "legacy no-id tool ends do not consume the queued start id"


def test_web_chat_escapes_tool_index():
    assert re.search(
        r"const\s+index\s*=\s*tool\.index\s*\|\|\s*['\"]\?['\"]",
        HTML,
    ), "tool index display fallback not normalized"
    assert "escapeHtml(index)" in HTML, "tool index is not escaped in card title"


def test_web_chat_renders_progress_events():
    for helper in [
        "appendStatusRow",
        "appendToolCard",
        "markToolDone",
        "markRunningToolsError",
        "finishRunningStatusRows",
        "appendRetryRow",
        "appendLimitRow",
    ]:
        pattern = rf"(?:function\s+{helper}\s*\(|(?:const|let)\s+{helper}\s*=)"
        assert re.search(pattern, HTML), f"missing helper {helper}"


def test_web_chat_finalizes_running_status_rows():
    assert re.search(
        r"runningStatusRows\s*=\s*\[\s*\]",
        HTML,
    ), "running status rows are not tracked per chat turn"
    assert re.search(
        r"runningStatusRows\.push\(\s*row\s*\)",
        HTML,
    ), "running status rows are not added to the tracked list"
    assert re.search(
        r"finishRunningStatusRows\(\s*['\"]done['\"]\s*\)",
        HTML,
    ), "successful progress does not finalize running status rows"
    assert re.search(
        r"finishRunningStatusRows\(\s*['\"]error['\"]\s*\)",
        HTML,
    ), "terminal failures do not mark running status rows as errors"


def test_web_chat_retry_can_reset_streamed_answer_text():
    retry_match = re.search(
        r"if\s*\(\s*evt\.retry\s*\)\s*\{(?P<body>.*?)appendRetryRow\(\s*evt\.retry\s*\)",
        HTML,
        re.S,
    )
    assert retry_match, "retry event block not found"
    body = retry_match.group("body")

    assert "evt.retry.reset_text" in body
    assert re.search(r"fullText\s*=\s*['\"]{2}", body), (
        "retry reset does not clear streamed assistant text"
    )
    assert re.search(r"firstToken\s*=\s*true", body), (
        "retry reset does not restore first-token state"
    )


def test_web_chat_marks_running_tools_error_on_failure():
    assert re.search(
        r"function\s+markRunningToolsError\s*\(",
        HTML,
    ), "missing helper for marking running tool cards as errors"
    assert re.search(
        r"card\.classList\.remove\(\s*['\"]running['\"]\s*\)",
        HTML,
    ), "running tool card error helper does not remove running state"
    assert re.search(
        r"card\.classList\.add\(\s*['\"]error['\"]\s*\)",
        HTML,
    ), "running tool card error helper does not add error state"


def test_web_chat_handles_structured_done_event():
    assert re.search(r"evt\.done", HTML), "structured done event is not handled"
    assert re.search(
        r"payload\s*===\s*['\"]\[DONE\]['\"]",
        HTML,
    ), "legacy [DONE] payload is not handled"


def test_web_chat_clear_preserves_ui_when_backend_rejects_active_turn():
    body = _function_block("clearChat")

    assert re.search(
        r"const\s+res\s*=\s*await\s+fetch\(\s*['\"]/api/chat/clear['\"]",
        body,
    ), "clearChat does not keep the clear response"
    assert re.search(
        r"if\s*\(\s*!res\.ok\s*\)",
        body,
    ), "clearChat does not handle non-2xx clear responses"
    assert re.search(
        r"showToast\([^)]*['\"]error['\"]",
        body,
        re.S,
    ), "clearChat does not surface backend clear errors"
    assert re.search(
        r"if\s*\(\s*!res\.ok\s*\).*?return\s*;",
        body,
        re.S,
    ), "clearChat must return before clearing UI when backend rejects"
