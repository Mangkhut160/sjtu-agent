from __future__ import annotations

import json

import sjtu_agent.agent.tools as tools


class FakeCanvasClient:
    def __init__(self):
        self.calls = []

    def list_courses(self, include_tabs=False, include_teachers=False):
        self.calls.append(("list_courses", include_tabs, include_teachers))
        return {"ok": True, "count": 1, "courses": [{"course_id": 1, "name": "Signals", "course_code": "ECE2300"}]}

    def resolve_course(self, course):
        if str(course) == "many":
            return {"ok": False, "error": "ambiguous_course", "candidates": [{"course_id": 1, "name": "A"}, {"course_id": 2, "name": "B"}]}
        return {"ok": True, "course": {"course_id": 1, "name": "Signals", "course_code": "ECE2300"}}

    def list_announcements(self, course_id, limit=20, since_days=None):
        self.calls.append(("announcements", course_id, limit, since_days))
        return {"ok": True, "count": 1, "announcements": [{"id": 10, "title": "Exam"}]}

    def list_quizzes(self, course_id, include_past=True, include_assignment_backed=True):
        self.calls.append(("quizzes", course_id, include_past, include_assignment_backed))
        return {"ok": True, "quiz_status": "enabled", "count": 1, "quizzes": [{"quiz_id": 5, "title": "Quiz"}], "warnings": []}

    def get_course_updates(self, course_id, include=None, limit=10):
        self.calls.append(("updates", course_id, include, limit))
        return {"ok": True, "course_id": course_id, "sections": {"announcements": {"count": 0}}, "warnings": []}

    def list_todo(self, limit=20):
        self.calls.append(("todo", limit))
        return {"ok": True, "count": 1, "items": [{"title": "Submit"}]}


def test_list_canvas_courses_tool(monkeypatch):
    fake = FakeCanvasClient()
    monkeypatch.setattr(tools._core, "_make_canvas_client", lambda: fake)

    result = tools.tool_list_canvas_courses(include_tabs=True, include_teachers=True)

    assert result["ok"] is True
    assert result["courses"][0]["name"] == "Signals"
    assert fake.calls == [("list_courses", True, True)]


def test_course_announcements_resolves_course(monkeypatch):
    fake = FakeCanvasClient()
    monkeypatch.setattr(tools._core, "_make_canvas_client", lambda: fake)

    result = tools.tool_get_canvas_course_announcements("ECE2300", limit=5, since_days=3)

    assert result["course"]["course_id"] == 1
    assert result["announcements"][0]["title"] == "Exam"
    assert ("announcements", 1, 5, 3) in fake.calls


def test_course_quizzes_returns_ambiguity_without_fetch(monkeypatch):
    fake = FakeCanvasClient()
    monkeypatch.setattr(tools._core, "_make_canvas_client", lambda: fake)

    result = tools.tool_get_canvas_course_quizzes("many")

    assert result["ok"] is False
    assert result["error"] == "ambiguous_course"
    assert all(call[0] != "quizzes" for call in fake.calls)


def test_run_tool_dispatches_canvas_tools(monkeypatch):
    fake = FakeCanvasClient()
    monkeypatch.setattr(tools._core, "_make_canvas_client", lambda: fake)

    payload = json.loads(tools.run_tool("get_canvas_todo", {"limit": 3}))

    assert payload["ok"] is True
    assert payload["items"][0]["title"] == "Submit"
    assert fake.calls == [("todo", 3)]


def test_tools_catalog_contains_canvas_tools():
    names = {item["function"]["name"] for item in tools.TOOLS if item.get("type") == "function"}

    assert "list_canvas_courses" in names
    assert "get_canvas_course_announcements" in names
    assert "get_canvas_course_quizzes" in names
    assert "get_canvas_course_updates" in names
    assert "get_canvas_todo" in names


def test_configure_canvas_monitor_updates_interval_and_scope(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "canvas_token": "keep-me",
        "canvas_monitor": {
            "enabled": True,
            "interval_seconds": 300,
            "course_ids": [1],
            "course_filters": ["old"],
            "include_announcements": True,
            "include_quizzes": True,
            "notify_channels": ["system"],
        },
    }), encoding="utf-8")
    monkeypatch.setattr(tools._core, "CONFIG_PATH", config_path)

    result = tools.tool_configure_canvas_monitor(
        interval_minutes=10,
        course_filters=["ECE2300", "ECE2700"],
        include_assignments=True,
        notify_channels=["system", "feishu"],
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["canvas_token"] == "keep-me"
    assert saved["canvas_monitor"]["interval_seconds"] == 600
    assert saved["canvas_monitor"]["course_ids"] == []
    assert saved["canvas_monitor"]["course_filters"] == ["ECE2300", "ECE2700"]
    assert saved["canvas_monitor"]["include_assignments"] is True
    assert saved["canvas_monitor"]["notify_channels"] == ["system", "feishu"]
    assert result["ok"] is True
    assert result["config"]["interval_seconds"] == 600
    assert "interval_seconds" in result["updated_fields"]
    assert result["config_path"] == str(config_path)


def test_configure_canvas_monitor_scope_ids_clear_filters(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "canvas_monitor": {
            "course_filters": ["ECE2300"],
        },
    }), encoding="utf-8")
    monkeypatch.setattr(tools._core, "CONFIG_PATH", config_path)

    result = tools.tool_configure_canvas_monitor(course_ids=[92355])

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["canvas_monitor"]["course_ids"] == [92355]
    assert saved["canvas_monitor"]["course_filters"] == []
    assert result["config"]["course_filters"] == []


def test_configure_canvas_monitor_clamps_interval_and_keeps_defaults(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(tools._core, "CONFIG_PATH", config_path)

    result = tools.tool_configure_canvas_monitor(
        enabled=False,
        interval_seconds=5,
        course_ids=[92355],
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    monitor = saved["canvas_monitor"]
    assert monitor["enabled"] is False
    assert monitor["interval_seconds"] == 30
    assert monitor["course_ids"] == [92355]
    assert monitor["include_announcements"] is True
    assert monitor["include_quizzes"] is True
    assert result["config"]["interval_seconds"] == 30
    assert result["notes"]


def test_run_tool_dispatches_configure_canvas_monitor(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(tools._core, "CONFIG_PATH", config_path)

    payload = json.loads(tools.run_tool("configure_canvas_monitor", {"interval_minutes": 2}))

    assert payload["ok"] is True
    assert payload["config"]["interval_seconds"] == 120
    assert "configure_canvas_monitor" in {
        item["function"]["name"]
        for item in tools.TOOLS
        if item.get("type") == "function"
    }
