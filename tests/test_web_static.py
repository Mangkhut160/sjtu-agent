from __future__ import annotations

from pathlib import Path


HTML = Path("sjtu_agent/web/static/index.html").read_text(encoding="utf-8")


def test_web_chat_has_canvas_tool_labels():
    for name in [
        "list_canvas_courses",
        "get_canvas_course_announcements",
        "get_canvas_course_quizzes",
        "get_canvas_course_updates",
        "get_canvas_overview",
        "get_canvas_todo",
        "configure_canvas_monitor",
        "list_canvas_assignments",
        "submit_canvas_assignment",
        "setup_canvas",
    ]:
        assert name in HTML


def test_web_chat_uses_keyed_tool_cards():
    assert "const toolCards = new Map();" in HTML
    assert "toolCards.set(toolId, card);" in HTML
    assert "toolCards.get(toolId)" in HTML
    assert "currentToolCard" not in HTML


def test_web_chat_renders_progress_events():
    for helper in [
        "appendStatusRow",
        "appendToolCard",
        "markToolDone",
        "appendRetryRow",
        "appendLimitRow",
    ]:
        assert f"function {helper}" in HTML


def test_web_chat_handles_structured_done_event():
    assert "evt.done" in HTML
    assert "payload === '[DONE]'" in HTML
